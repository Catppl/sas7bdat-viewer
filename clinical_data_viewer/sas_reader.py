from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .domain import CacheProgress, DatasetHandle, DatasetMetadata, VariableMetadata
from .filter_engine import quote_identifier
from .temp_manager import TempManager

ProgressCallback = Callable[[str], None]
CacheProgressCallback = Callable[[CacheProgress], None]


def _import_pyreadstat():
    try:
        import pyreadstat
    except ImportError as error:
        raise RuntimeError(
            "pyreadstat is not installed. Install desktop/Windows dependencies "
            "before opening a SAS dataset."
        ) from error
    return pyreadstat


def normalize_value(value: Any) -> object:
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class SasDatasetReader:
    """Copy the source, expose an initial cache, then append the rest in WAL mode."""

    def __init__(self, temp_manager: TempManager, chunk_size: int = 20_000) -> None:
        self.temp_manager = temp_manager
        self.chunk_size = chunk_size

    @staticmethod
    def _read_function(pyreadstat: Any, dataset_path: Path):
        """Return the pyreadstat reader appropriate for an original SAS format."""
        if dataset_path.suffix.lower() == ".xpt":
            return pyreadstat.read_xport
        return pyreadstat.read_sas7bdat

    @staticmethod
    def _read_options(dataset_path: Path) -> dict[str, object]:
        """Options shared by direct and chunked reads for the selected format."""
        options: dict[str, object] = {
            "output_format": "dict",
            "disable_datetime_conversion": True,
        }
        # read_xport does not accept user_missing; XPT has no SAS special-missing
        # representation for pyreadstat to preserve in the same way as sas7bdat.
        if dataset_path.suffix.lower() == ".sas7bdat":
            options["user_missing"] = True
        return options

    def load(
        self, source_path: Path, progress: ProgressCallback | None = None
    ) -> DatasetHandle:
        """Build the complete cache. Kept for non-UI callers and tests."""
        handle = self.load_initial(source_path, progress)
        if handle.cache_complete:
            return handle
        return self.continue_cache(handle, progress)

    def load_initial(
        self, source_path: Path, progress: ProgressCallback | None = None
    ) -> DatasetHandle:
        notify = progress or (lambda _message: None)

        def copy_progress(copied: int, total: int) -> None:
            percentage = int(copied * 100 / total) if total else 100
            notify(f"Copying source dataset… {percentage}%")

        temporary_path, dataset_directory = self.temp_manager.copy_dataset(
            source_path, copy_progress
        )
        database_path = dataset_directory / "dataset.sqlite"
        try:
            notify("Reading SAS metadata…")
            variables, reported_rows = self._read_metadata(temporary_path)
            notify(f"Loading the first {self.chunk_size:,} rows…")
            first_chunk = self._read_first_chunk(temporary_path)
            cached_rows = self._create_cache(
                database_path, variables, first_chunk, reported_rows
            )
            total_rows = reported_rows if reported_rows is not None else cached_rows
            cache_complete = cached_rows < self.chunk_size or (
                reported_rows is not None and cached_rows >= reported_rows
            )
            metadata = DatasetMetadata(source_path.stem, total_rows, variables)
            return DatasetHandle(
                source_path.resolve(),
                temporary_path,
                database_path,
                metadata,
                cached_rows,
                cache_complete,
            )
        except BaseException:
            self.temp_manager.remove_dataset(dataset_directory)
            raise

    def continue_cache(
        self,
        handle: DatasetHandle,
        progress: ProgressCallback | None = None,
        cache_progress: CacheProgressCallback | None = None,
    ) -> DatasetHandle:
        if handle.cache_complete:
            return handle
        notify = progress or (lambda _message: None)
        report_cache = cache_progress or (lambda _progress: None)
        pyreadstat = _import_pyreadstat()
        variables = handle.metadata.variables
        names = [variable.name for variable in variables]
        columns = ", ".join(quote_identifier(name) for name in names)
        placeholders = ", ".join("?" for _ in names)
        insert_sql = f"INSERT INTO dataset ({columns}) VALUES ({placeholders})"
        cached_rows = handle.cached_row_count
        total_hint = handle.metadata.row_count
        connection = sqlite3.connect(handle.database_path)
        try:
            reader = pyreadstat.read_file_in_chunks(
                self._read_function(pyreadstat, handle.temporary_path),
                str(handle.temporary_path),
                chunksize=self.chunk_size,
                offset=cached_rows,
                **self._read_options(handle.temporary_path),
            )
            for chunk, _meta in reader:
                chunk_length = len(next(iter(chunk.values()), ()))
                rows = zip(*(chunk[name] for name in names))
                connection.executemany(
                    insert_sql,
                    ([normalize_value(value) for value in row] for row in rows),
                )
                cached_rows += chunk_length
                connection.execute(
                    "UPDATE cache_info SET cached_rows = ?", (cached_rows,)
                )
                connection.commit()
                visible_total = max(total_hint, cached_rows)
                notify(f"Caching rows… {cached_rows:,} / {visible_total:,}")
                report_cache(CacheProgress(cached_rows, visible_total))
            total_rows = cached_rows
            connection.execute(
                "UPDATE cache_info SET cached_rows = ?, total_rows = ?, complete = 1",
                (total_rows, total_rows),
            )
            connection.commit()
        finally:
            connection.close()
        complete = replace(
            handle,
            metadata=replace(handle.metadata, row_count=total_rows),
            cached_row_count=total_rows,
            cache_complete=True,
        )
        report_cache(CacheProgress(total_rows, total_rows, True))
        return complete

    def _read_metadata(
        self, dataset_path: Path
    ) -> tuple[tuple[VariableMetadata, ...], int | None]:
        pyreadstat = _import_pyreadstat()
        reader = self._read_function(pyreadstat, dataset_path)
        _data, meta = reader(
            str(dataset_path),
            metadataonly=True,
            **self._read_options(dataset_path),
        )
        names = list(meta.column_names)
        labels = list(meta.column_labels or [])
        readstat_types = getattr(meta, "readstat_variable_types", {}) or {}
        storage_widths = getattr(meta, "variable_storage_width", {}) or {}
        formats = getattr(meta, "original_variable_types", {}) or {}
        variables: list[VariableMetadata] = []
        for index, name in enumerate(names):
            readstat_type = str(readstat_types.get(name, "")).lower()
            kind = "character" if "string" in readstat_type else "numeric"
            raw_length = storage_widths.get(name)
            variables.append(
                VariableMetadata(
                    name=str(name),
                    label=str(labels[index] or "") if index < len(labels) else "",
                    kind=kind,
                    length=int(raw_length) if raw_length is not None else None,
                    format=str(formats.get(name) or ""),
                )
            )
        if not variables:
            raise ValueError("The dataset does not contain any variables.")
        raw_rows = getattr(meta, "number_rows", None)
        reported_rows = int(raw_rows) if raw_rows is not None else None
        return tuple(variables), reported_rows

    def _read_first_chunk(self, dataset_path: Path) -> dict[str, object]:
        pyreadstat = _import_pyreadstat()
        reader = self._read_function(pyreadstat, dataset_path)
        data, _meta = reader(
            str(dataset_path),
            row_limit=self.chunk_size,
            **self._read_options(dataset_path),
        )
        return data

    def _create_cache(
        self,
        database_path: Path,
        variables: tuple[VariableMetadata, ...],
        first_chunk: dict[str, object],
        total_rows: int | None,
    ) -> int:
        connection = sqlite3.connect(database_path)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            definitions = ", ".join(
                f"{quote_identifier(variable.name)} "
                f"{'REAL' if variable.kind == 'numeric' else 'TEXT'}"
                for variable in variables
            )
            connection.execute(
                f"CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, {definitions})"
            )
            names = [variable.name for variable in variables]
            columns = ", ".join(quote_identifier(name) for name in names)
            placeholders = ", ".join("?" for _ in names)
            rows = zip(*(first_chunk.get(name, ()) for name in names))
            connection.executemany(
                f"INSERT INTO dataset ({columns}) VALUES ({placeholders})",
                ([normalize_value(value) for value in row] for row in rows),
            )
            cached_rows = int(
                connection.execute("SELECT count(*) FROM dataset").fetchone()[0]
            )
            complete = int(
                cached_rows < self.chunk_size
                or (total_rows is not None and cached_rows >= total_rows)
            )
            connection.execute(
                "CREATE TABLE cache_info "
                "(cached_rows INTEGER NOT NULL, total_rows INTEGER, "
                "complete INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO cache_info VALUES (?, ?, ?)",
                (cached_rows, total_rows, complete),
            )
            connection.commit()
            return cached_rows
        finally:
            connection.close()
