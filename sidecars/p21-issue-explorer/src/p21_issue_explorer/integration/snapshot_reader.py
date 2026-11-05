from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from .snapshot_contract import (
    SnapshotContractError,
    SnapshotManifest,
    SnapshotVariable,
)


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


@dataclass(slots=True)
class DatasetSnapshot:
    root: Path
    manifest: SnapshotManifest
    _connection: sqlite3.Connection
    _on_close: Callable[[], None] | None = None
    _closed: bool = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._connection.close()
        if self._on_close is not None:
            self._on_close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def variable(self, name: str) -> SnapshotVariable | None:
        return self.manifest.variable(name)

    def records(
        self, variable_names: Sequence[str] | None = None
    ) -> Iterator[dict[str, object]]:
        variables = self._resolve_variables(variable_names)
        columns = [self.manifest.source_row_field, *(item.name for item in variables)]
        sql = (
            "SELECT "
            + ", ".join(_quote_identifier(name) for name in columns)
            + " FROM "
            + _quote_identifier(self.manifest.table_name)
            + " ORDER BY "
            + _quote_identifier(self.manifest.source_row_field)
        )
        cursor = self._connection.execute(sql)
        for row in cursor:
            yield dict(zip(columns, row, strict=True))

    def distinct_values(self, variable_name: str) -> tuple[object, ...]:
        variable = self.variable(variable_name)
        if variable is None:
            raise SnapshotContractError(f"Unknown snapshot variable: {variable_name}")
        table = _quote_identifier(self.manifest.table_name)
        column = _quote_identifier(variable.name)
        rows = self._connection.execute(
            f"SELECT DISTINCT {column} FROM {table} ORDER BY {column}"
        )
        return tuple(row[0] for row in rows)

    def _resolve_variables(
        self, variable_names: Sequence[str] | None
    ) -> tuple[SnapshotVariable, ...]:
        if variable_names is None:
            return self.manifest.variables
        resolved: list[SnapshotVariable] = []
        seen: set[str] = set()
        for name in variable_names:
            variable = self.variable(name)
            if variable is None:
                raise SnapshotContractError(f"Unknown snapshot variable: {name}")
            folded = variable.name.casefold()
            if folded not in seen:
                resolved.append(variable)
                seen.add(folded)
        return tuple(resolved)


class SnapshotReader:
    """Validate and open Dataset Snapshot v1 without write access."""

    def open(self, root: Path | str) -> DatasetSnapshot:
        root_path = Path(root)
        manifest = SnapshotManifest.read(root_path / "manifest.json")
        database_path = root_path / "records.sqlite"
        if not database_path.is_file():
            raise SnapshotContractError(f"Snapshot database not found: {database_path}")
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                f"{database_path.resolve().as_uri()}?mode=ro",
                uri=True,
                check_same_thread=False,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            self._validate_database(connection, manifest)
            return DatasetSnapshot(root_path, manifest, connection)
        except (sqlite3.Error, SnapshotContractError) as error:
            if connection is not None:
                connection.close()
            if isinstance(error, SnapshotContractError):
                raise
            raise SnapshotContractError(
                f"Invalid snapshot database: {error}"
            ) from error

    @staticmethod
    def _validate_database(
        connection: sqlite3.Connection, manifest: SnapshotManifest
    ) -> None:
        table_row = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (manifest.table_name,),
        ).fetchone()
        if table_row is None:
            raise SnapshotContractError(
                f"Snapshot table {manifest.table_name!r} does not exist."
            )
        table = _quote_identifier(manifest.table_name)
        columns = {
            row[1].casefold(): (row[1], str(row[2]).upper())
            for row in connection.execute(f"PRAGMA table_info({table})")
        }
        expected = [
            manifest.source_row_field,
            *(item.name for item in manifest.variables),
        ]
        missing = [name for name in expected if name.casefold() not in columns]
        if missing:
            raise SnapshotContractError(
                "Snapshot database is missing columns: " + ", ".join(missing)
            )
        type_mismatches = [
            f"{variable.name} ({columns[variable.name.casefold()][1]} != "
            f"{variable.sqlite_type})"
            for variable in manifest.variables
            if columns[variable.name.casefold()][1] != variable.sqlite_type
        ]
        if type_mismatches:
            raise SnapshotContractError(
                "Snapshot SQLite type mismatch: " + ", ".join(type_mismatches)
            )
        actual_count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if actual_count != manifest.source_row_count:
            raise SnapshotContractError(
                "Snapshot row count mismatch: manifest declares "
                f"{manifest.source_row_count}, database contains {actual_count}."
            )
        source_row = _quote_identifier(manifest.source_row_field)
        distinct_count = connection.execute(
            f"SELECT COUNT(DISTINCT {source_row}) FROM {table}"
        ).fetchone()[0]
        null_count = connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {source_row} IS NULL"
        ).fetchone()[0]
        if distinct_count != actual_count or null_count:
            raise SnapshotContractError(
                f"{manifest.source_row_field} must be non-null and unique."
            )
        unique_source_index = False
        for index in connection.execute(f"PRAGMA index_list({table})"):
            if not index[2]:
                continue
            index_name = _quote_identifier(index[1])
            indexed_columns = [
                row[2].casefold()
                for row in connection.execute(f"PRAGMA index_info({index_name})")
            ]
            if indexed_columns == [manifest.source_row_field.casefold()]:
                unique_source_index = True
                break
        if not unique_source_index:
            raise SnapshotContractError(
                f"{manifest.source_row_field} must have a dedicated unique index."
            )
