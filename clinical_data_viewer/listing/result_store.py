from __future__ import annotations

import sqlite3
from pathlib import Path

from ..domain import DatasetHandle, DatasetMetadata, VariableMetadata
from ..filter_engine import quote_identifier
from ..temp_manager import TempManager


class ListingResultWriter:
    def __init__(self, database_path: Path, variables: tuple[VariableMetadata, ...]):
        self.database_path = database_path
        self.variables = variables
        self.connection = sqlite3.connect(database_path)
        definitions = ", ".join(
            f"{quote_identifier(variable.name)} {'REAL' if variable.kind == 'numeric' else 'TEXT'}"
            for variable in variables
        )
        self.connection.execute(
            f"CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, {definitions})"
        )
        self.count = 0

    def add(self, source_row: int, values: tuple[object, ...]) -> None:
        names = ", ".join(
            quote_identifier(variable.name) for variable in self.variables
        )
        placeholders = ", ".join("?" for _ in self.variables)
        self.connection.execute(
            f"INSERT INTO dataset (_source_row, {names}) VALUES (?, {placeholders})",
            (source_row, *values),
        )
        self.count += 1

    def finish(
        self, directory: Path, source: DatasetHandle, *, display_source: str
    ) -> DatasetHandle:
        self.connection.execute(
            "CREATE TABLE cache_info (cached_rows INTEGER,total_rows INTEGER,complete INTEGER)"
        )
        self.connection.execute(
            "INSERT INTO cache_info VALUES (?,?,1)", (self.count, self.count)
        )
        self.connection.commit()
        self.connection.close()
        marker = directory / "listing-result.tmp"
        marker.touch()
        metadata = DatasetMetadata(
            f"Listing - {source.metadata.name}",
            self.count,
            self.variables,
            display_column_names=tuple(
                (variable.name, variable.label or variable.name)
                for variable in self.variables
            ),
        )
        return DatasetHandle(
            source.source_path.parent / f"Listing - {source.metadata.name}",
            marker,
            self.database_path,
            metadata,
            self.count,
            True,
            kind="listing",
            display_source=display_source,
        )

    def abort(self, manager: TempManager, directory: Path) -> None:
        self.connection.close()
        manager.remove_dataset(directory)
