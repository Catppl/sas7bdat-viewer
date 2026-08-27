from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import replace
from pathlib import Path
from typing import Any

from .domain import CacheProgress, DatasetHandle, DatasetMetadata, VariableMetadata
from .filter_engine import quote_identifier
from .temp_manager import TempManager
from .xpt_reader import XptSequentialReader

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
    """Copy sources, cache the first rows, then append in WAL mode."""

    DEFAULT_SAS_INITIAL_ROWS = 5_000
    DEFAULT_XPT_INITIAL_ROWS = 2_500
    DEFAULT_CACHE_CHUNK_ROWS = 20_000

    def __init__(
        self,
        temp_manager: TempManager,
        chunk_size: int | None = None,
        *,
        sas_initial_chunk_size: int = DEFAULT_SAS_INITIAL_ROWS,
        xpt_initial_chunk_size: int = DEFAULT_XPT_INITIAL_ROWS,
        cache_chunk_size: int = DEFAULT_CACHE_CHUNK_ROWS,
        xpt_reader_factory=XptSequentialReader,
    ) -> None:
        """Create a reader with format-specific first-screen chunk sizes.

        ``chunk_size`` remains as a compact test/backwards-compatibility
        override. When supplied it applies to every chunk type.
        """
        if chunk_size is not None:
            sas_initial_chunk_size = chunk_size
            xpt_initial_chunk_size = chunk_size
            cache_chunk_size = chunk_size
        if min(sas_initial_chunk_size, xpt_initial_chunk_size, cache_chunk_size) < 1:
            raise ValueError("Dataset cache chunk sizes must be positive.")
        self.temp_manager = temp_manager
        self.sas_initial_chunk_size = sas_initial_chunk_size
        self.xpt_initial_chunk_size = xpt_initial_chunk_size
        self.cache_chunk_size = cache_chunk_size
        self.xpt_reader_factory = xpt_reader_factory

    @staticmethod
    def _read_options() -> dict[str, object]:
        return {
            "output_format": "dict",
            "disable_datetime_conversion": True,
            "user_missing": True,
        }

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
        source_path = source_path.resolve(strict=True)
        source_size_bytes = source_path.stat().st_size

        def copy_progress(copied: int, total: int) -> None:
            percentage = int(copied * 100 / total) if total else 100
            notify(f"Copying source dataset… {percentage}%")

        temporary_path, dataset_directory = self.temp_manager.copy_dataset(
            source_path, copy_progress
        )
        database_path = dataset_directory / "dataset.sqlite"
        try:
            if temporary_path.suffix.lower() == ".xpt":
                return self._load_initial_xpt(
                    source_path,
                    source_size_bytes,
                    temporary_path,
                    database_path,
                    notify,
                )
            return self._load_initial_sas7bdat(
                source_path,
                source_size_bytes,
                temporary_path,
                database_path,
                notify,
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
        if handle.temporary_path.suffix.lower() == ".xpt":
            return self._continue_xpt_cache(handle, notify, report_cache)
        return self._continue_sas_cache(handle, notify, report_cache)

    def _load_initial_sas7bdat(
        self,
        source_path: Path,
        source_size_bytes: int,
        temporary_path: Path,
        database_path: Path,
        notify: ProgressCallback,
    ) -> DatasetHandle:
        notify("Reading SAS metadata…")
        variables, reported_rows = self._read_sas_metadata(temporary_path)
        notify(f"Loading the first {self.sas_initial_chunk_size:,} rows…")
        first_chunk = self._read_sas_first_chunk(temporary_path)
        names = [variable.name for variable in variables]
        cached_rows = self._create_cache(
            database_path,
            variables,
            self._mapping_rows(first_chunk, names),
            self._mapping_length(first_chunk),
            reported_rows,
            self.sas_initial_chunk_size,
        )
        return self._initial_handle(
            source_path,
            source_size_bytes,
            temporary_path,
            database_path,
            variables,
            reported_rows,
            cached_rows,
            self.sas_initial_chunk_size,
        )

    def _load_initial_xpt(
        self,
        source_path: Path,
        source_size_bytes: int,
        temporary_path: Path,
        database_path: Path,
        notify: ProgressCallback,
    ) -> DatasetHandle:
        notify("Reading XPT metadata…")
        with self.xpt_reader_factory(temporary_path) as reader:
            variables = reader.variables
            reported_rows = reader.total_rows
            notify(f"Loading the first {self.xpt_initial_chunk_size:,} rows…")
            first_chunk = reader.read_chunk(self.xpt_initial_chunk_size)
            cached_rows = self._create_cache(
                database_path,
                variables,
                self._frame_rows(
                    first_chunk, [variable.name for variable in variables]
                ),
                0 if first_chunk is None else len(first_chunk),
                reported_rows,
                self.xpt_initial_chunk_size,
            )
        return self._initial_handle(
            source_path,
            source_size_bytes,
            temporary_path,
            database_path,
            variables,
            reported_rows,
            cached_rows,
            self.xpt_initial_chunk_size,
        )

    def _initial_handle(
        self,
        source_path: Path,
        source_size_bytes: int,
        temporary_path: Path,
        database_path: Path,
        variables: tuple[VariableMetadata, ...],
        reported_rows: int | None,
        cached_rows: int,
        requested_rows: int,
    ) -> DatasetHandle:
        total_rows = reported_rows if reported_rows is not None else cached_rows
        cache_complete = cached_rows < requested_rows or (
            reported_rows is not None and cached_rows >= reported_rows
        )
        return DatasetHandle(
            source_path,
            temporary_path,
            database_path,
            DatasetMetadata(source_path.stem, total_rows, variables),
            cached_rows,
            cache_complete,
            source_size_bytes=source_size_bytes,
            total_rows_known=reported_rows is not None,
        )

    def _continue_sas_cache(
        self,
        handle: DatasetHandle,
        notify: ProgressCallback,
        report_cache: CacheProgressCallback,
    ) -> DatasetHandle:
        pyreadstat = _import_pyreadstat()
        variables = handle.metadata.variables
        names = [variable.name for variable in variables]
        cached_rows = handle.cached_row_count
        total_hint = handle.metadata.row_count if handle.total_rows_known else None
        connection = sqlite3.connect(handle.database_path)
        try:
            reader = pyreadstat.read_file_in_chunks(
                pyreadstat.read_sas7bdat,
                str(handle.temporary_path),
                chunksize=self.cache_chunk_size,
                offset=cached_rows,
                **self._read_options(),
            )
            for chunk, _meta in reader:
                chunk_length = self._mapping_length(chunk)
                self._insert_rows(connection, names, self._mapping_rows(chunk, names))
                cached_rows += chunk_length
                self._commit_cache_progress(connection, cached_rows)
                self._report_cache_progress(
                    notify, report_cache, cached_rows, total_hint
                )
        finally:
            connection.close()
        return self._finish_cache(handle, cached_rows, report_cache)

    def _continue_xpt_cache(
        self,
        handle: DatasetHandle,
        notify: ProgressCallback,
        report_cache: CacheProgressCallback,
    ) -> DatasetHandle:
        variables = handle.metadata.variables
        names = [variable.name for variable in variables]
        cached_rows = handle.cached_row_count
        connection = sqlite3.connect(handle.database_path)
        try:
            with self.xpt_reader_factory(handle.temporary_path) as reader:
                if tuple(names) != tuple(reader.column_names):
                    raise ValueError(
                        "The XPT column layout changed while rebuilding the cache."
                    )
                total_hint = reader.total_rows
                self._skip_xpt_rows(reader, cached_rows)
                while True:
                    chunk = reader.read_chunk(self.cache_chunk_size)
                    if chunk is None or len(chunk) == 0:
                        break
                    self._insert_rows(connection, names, self._frame_rows(chunk, names))
                    cached_rows += len(chunk)
                    self._commit_cache_progress(connection, cached_rows)
                    self._report_cache_progress(
                        notify, report_cache, cached_rows, total_hint
                    )
        finally:
            connection.close()
        return self._finish_cache(handle, cached_rows, report_cache)

    def _skip_xpt_rows(self, reader: XptSequentialReader, expected_rows: int) -> None:
        remaining = expected_rows
        while remaining:
            chunk = reader.read_chunk(min(self.cache_chunk_size, remaining))
            actual_rows = 0 if chunk is None else len(chunk)
            if actual_rows == 0:
                raise ValueError(
                    "The XPT file ended before its existing cache could be skipped."
                )
            remaining -= actual_rows

    def _finish_cache(
        self,
        handle: DatasetHandle,
        cached_rows: int,
        report_cache: CacheProgressCallback,
    ) -> DatasetHandle:
        connection = sqlite3.connect(handle.database_path)
        try:
            connection.execute(
                "UPDATE cache_info SET cached_rows = ?, total_rows = ?, complete = 1",
                (cached_rows, cached_rows),
            )
            connection.commit()
        finally:
            connection.close()
        complete = replace(
            handle,
            metadata=replace(handle.metadata, row_count=cached_rows),
            cached_row_count=cached_rows,
            cache_complete=True,
            total_rows_known=True,
        )
        report_cache(CacheProgress(cached_rows, cached_rows, True, True))
        return complete

    def _read_sas_metadata(
        self, dataset_path: Path
    ) -> tuple[tuple[VariableMetadata, ...], int | None]:
        pyreadstat = _import_pyreadstat()
        _data, meta = pyreadstat.read_sas7bdat(
            str(dataset_path),
            metadataonly=True,
            **self._read_options(),
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

    def _read_sas_first_chunk(self, dataset_path: Path) -> dict[str, object]:
        pyreadstat = _import_pyreadstat()
        data, _meta = pyreadstat.read_sas7bdat(
            str(dataset_path),
            row_limit=self.sas_initial_chunk_size,
            **self._read_options(),
        )
        return data

    def _create_cache(
        self,
        database_path: Path,
        variables: tuple[VariableMetadata, ...],
        rows: Iterable[tuple[object, ...]],
        cached_rows: int,
        total_rows: int | None,
        requested_rows: int,
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
            self._insert_rows(connection, names, rows)
            complete = int(
                cached_rows < requested_rows
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

    @staticmethod
    def _mapping_length(chunk: dict[str, object]) -> int:
        return len(next(iter(chunk.values()), ()))

    @staticmethod
    def _mapping_rows(
        chunk: dict[str, object], names: list[str]
    ) -> Iterable[tuple[object, ...]]:
        return zip(*(chunk.get(name, ()) for name in names))

    @staticmethod
    def _frame_rows(frame: Any, names: list[str]) -> Iterable[tuple[object, ...]]:
        if frame is None:
            return ()
        return frame.reindex(columns=names).itertuples(index=False, name=None)

    @staticmethod
    def _insert_rows(
        connection: sqlite3.Connection,
        names: list[str],
        rows: Iterable[tuple[object, ...]],
    ) -> None:
        columns = ", ".join(quote_identifier(name) for name in names)
        placeholders = ", ".join("?" for _ in names)
        connection.executemany(
            f"INSERT INTO dataset ({columns}) VALUES ({placeholders})",
            ([normalize_value(value) for value in row] for row in rows),
        )

    @staticmethod
    def _commit_cache_progress(
        connection: sqlite3.Connection, cached_rows: int
    ) -> None:
        connection.execute("UPDATE cache_info SET cached_rows = ?", (cached_rows,))
        connection.commit()

    @staticmethod
    def _report_cache_progress(
        notify: ProgressCallback,
        report_cache: CacheProgressCallback,
        cached_rows: int,
        total_rows: int | None,
    ) -> None:
        if total_rows is None:
            notify(f"Caching rows… {cached_rows:,} rows")
            report_cache(CacheProgress(cached_rows, cached_rows, False, False))
            return
        visible_total = max(total_rows, cached_rows)
        notify(f"Caching rows… {cached_rows:,} / {visible_total:,}")
        report_cache(CacheProgress(cached_rows, visible_total, False, True))
