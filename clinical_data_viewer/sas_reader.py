from __future__ import annotations

import math
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .domain import DatasetHandle, DatasetMetadata, VariableMetadata
from .filter_engine import quote_identifier
from .temp_manager import TempManager

ProgressCallback = Callable[[str], None]


def _import_pyreadstat():
    try:
        import pyreadstat
    except ImportError as error:
        raise RuntimeError(
            "pyreadstat is not installed. Install desktop/Windows dependencies before opening a SAS dataset."
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
    """Copies a source dataset, then builds a disk-backed read-only query cache."""

    def __init__(self, temp_manager: TempManager, chunk_size: int = 20_000) -> None:
        self.temp_manager = temp_manager
        self.chunk_size = chunk_size

    def load(
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
            variables = self._read_variables(temporary_path)
            notify("Building local query cache…")
            row_count = self._build_cache(
                temporary_path, database_path, variables, notify
            )
            metadata = DatasetMetadata(source_path.stem, row_count, variables)
            return DatasetHandle(
                source_path.resolve(), temporary_path, database_path, metadata
            )
        except BaseException:
            self.temp_manager.remove_dataset(dataset_directory)
            raise

    def _read_variables(self, dataset_path: Path) -> tuple[VariableMetadata, ...]:
        pyreadstat = _import_pyreadstat()
        _data, meta = pyreadstat.read_sas7bdat(
            str(dataset_path),
            metadataonly=True,
            output_format="dict",
            user_missing=True,
            disable_datetime_conversion=True,
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
        return tuple(variables)

    def _build_cache(
        self,
        dataset_path: Path,
        database_path: Path,
        variables: tuple[VariableMetadata, ...],
        progress: ProgressCallback,
    ) -> int:
        pyreadstat = _import_pyreadstat()
        partial = database_path.with_suffix(".sqlite.part")
        if partial.exists():
            partial.unlink()
        connection = sqlite3.connect(partial)
        row_count = 0
        try:
            connection.execute("PRAGMA journal_mode=OFF")
            connection.execute("PRAGMA synchronous=OFF")
            definitions = ", ".join(
                f"{quote_identifier(variable.name)} {'REAL' if variable.kind == 'numeric' else 'TEXT'}"
                for variable in variables
            )
            connection.execute(
                f"CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, {definitions})"
            )
            names = [variable.name for variable in variables]
            columns = ", ".join(quote_identifier(name) for name in names)
            placeholders = ", ".join("?" for _ in names)
            insert_sql = f"INSERT INTO dataset ({columns}) VALUES ({placeholders})"
            reader = pyreadstat.read_file_in_chunks(
                pyreadstat.read_sas7bdat,
                str(dataset_path),
                chunksize=self.chunk_size,
                output_format="dict",
                user_missing=True,
                disable_datetime_conversion=True,
            )
            for chunk, _meta in reader:
                first = next(iter(chunk.values()), ())
                chunk_length = len(first)
                rows = zip(*(chunk[name] for name in names))
                connection.executemany(
                    insert_sql,
                    ([normalize_value(value) for value in row] for row in rows),
                )
                row_count += chunk_length
                connection.commit()
                progress(f"Caching rows… {row_count:,}")
            connection.execute(
                "CREATE INDEX dataset_source_order ON dataset(_source_row)"
            )
            connection.execute("CREATE TABLE cache_info (row_count INTEGER NOT NULL)")
            connection.execute("INSERT INTO cache_info VALUES (?)", (row_count,))
            connection.commit()
        finally:
            connection.close()
        os.replace(partial, database_path)
        return row_count
