from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from ..domain import DatasetHandle, DatasetMetadata, VariableMetadata
from ..filter_engine import quote_identifier
from ..temp_manager import TempManager
from .models import CategoricalConfig


def _unique(base: str, used: set[str]) -> str:
    value = base
    suffix = 2
    while value.casefold() in used:
        value = f"{base}_{suffix}"
        suffix += 1
    used.add(value.casefold())
    return value


class CategoricalResultWriter:
    def __init__(
        self,
        database_path: Path,
        context_variables: tuple[VariableMetadata, ...],
        treatment_levels: tuple[tuple[str, str], ...],
        config: CategoricalConfig,
    ) -> None:
        self.database_path = database_path
        self.config = config
        self.context_variables = context_variables
        used: set[str] = set()
        self.item_level_column = _unique("ITEM_LEVEL", used)
        self.treatment_columns: list[tuple[str | None, str, str]] = []
        for position, (key, label) in enumerate(treatment_levels, start=1):
            # SQLite/WHERE identifiers remain stable even when formatted treatment
            # labels contain spaces or punctuation.  The label retains the human value.
            self.treatment_columns.append((key, _unique(f"TRT_{position}", used), label))
        if config.include_total:
            self.treatment_columns.append((None, _unique("TOTAL", used), "Total"))
        self.variables = (
            VariableMetadata(
                self.item_level_column, "Categorical item and level", "character"
            ),
            *(
                VariableMetadata(column, f"{label} n (%)", "character")
                for _key, column, label in self.treatment_columns
            ),
        )
        self.connection = sqlite3.connect(database_path)
        definitions = [
            f"{quote_identifier(variable.name)} TEXT" for variable in self.variables
        ]
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(
            "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "
            + ", ".join(definitions)
            + ")"
        )
        self.connection.execute(
            "CREATE TABLE categorical_cell_map ("
            "result_row INTEGER NOT NULL, column_name TEXT NOT NULL, "
            "item_variable TEXT NOT NULL, context_json TEXT NOT NULL, "
            "level_json TEXT NOT NULL, treatment_json TEXT, "
            "PRIMARY KEY (result_row, column_name))"
        )
        self.connection.execute(
            "CREATE TABLE categorical_long ("
            "row_order INTEGER NOT NULL, trt_order INTEGER NOT NULL, "
            "item_variable TEXT NOT NULL, item_label TEXT NOT NULL, "
            "context_json TEXT NOT NULL, level_json TEXT NOT NULL, "
            "treatment_json TEXT, freq INTEGER NOT NULL, denom INTEGER NOT NULL, "
            "pct REAL)"
        )
        self.row_count = 0
        self._long_row_order = 0

    def _insert_display_row(
        self,
        item_level: str,
        cells: dict[str | None, tuple[str, str]],
    ) -> int:
        columns = [variable.name for variable in self.variables]
        values: list[object] = [item_level]
        values.extend(
            cells.get(key, ("", ""))[0]
            for key, _column, _label in self.treatment_columns
        )
        self.connection.execute(
            "INSERT INTO dataset ("
            + ", ".join(quote_identifier(column) for column in columns)
            + ") VALUES ("
            + ", ".join("?" for _column in columns)
            + ")",
            values,
        )
        self.row_count += 1
        return int(self.connection.execute("SELECT last_insert_rowid()").fetchone()[0])

    def add_header(self, item_level: str) -> None:
        self._insert_display_row(item_level, {})
        if self.row_count % 1_000 == 0:
            self.connection.commit()

    def add_level_row(
        self,
        item_level: str,
        item: str,
        item_label: str,
        cells: dict[str | None, tuple[str, str]],
        long_cells: dict[str | None, tuple[int, int]],
        *,
        context_json: str,
        level_json: str,
        treatment_json: dict[str | None, str],
    ) -> None:
        result_row = self._insert_display_row(item_level, cells)
        for treatment, column, _label in self.treatment_columns:
            if treatment not in cells:
                continue
            self.connection.execute(
                "INSERT INTO categorical_cell_map VALUES (?, ?, ?, ?, ?, ?)",
                (
                    result_row,
                    column,
                    item,
                    context_json,
                    level_json,
                    treatment_json[treatment],
                ),
            )
        self._long_row_order += 1
        for trt_order, (treatment, (freq, denom)) in enumerate(
            long_cells.items(), start=1
        ):
            self.connection.execute(
                "INSERT INTO categorical_long VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self._long_row_order,
                    trt_order,
                    item,
                    item_label,
                    context_json,
                    level_json,
                    treatment_json[treatment],
                    freq,
                    denom,
                    (freq * 100.0 / denom) if denom else None,
                ),
            )
        if self.row_count % 1_000 == 0:
            self.connection.commit()

    def finish(
        self, directory: Path, source: DatasetHandle
    ) -> DatasetHandle:
        self.connection.execute(
            "CREATE TABLE cache_info (cached_rows INTEGER NOT NULL, total_rows INTEGER, complete INTEGER NOT NULL)"
        )
        self.connection.execute(
            "INSERT INTO cache_info VALUES (?, ?, 1)", (self.row_count, self.row_count)
        )
        self.connection.commit()
        self.connection.close()
        marker = directory / "categorical-result.tmp"
        marker.touch()
        return DatasetHandle(
            source.source_path.parent / f"Categorical Table - {source.metadata.name}",
            marker,
            self.database_path,
            DatasetMetadata(
                f"Categorical Table - {source.metadata.name}",
                self.row_count,
                self.variables,
                display_column_names=(
                    (self.item_level_column, "Item / Level"),
                    *(
                        (column, f"{label} n (%)")
                        for _key, column, label in self.treatment_columns
                    ),
                ),
                categorical_item_level_column=self.item_level_column,
            ),
            self.row_count,
            True,
            kind="categorical",
            display_source=f"Categorical Table from {source.source_path}",
        )

    def abort(self, manager: TempManager, directory: Path) -> None:
        self.connection.close()
        manager.remove_dataset(directory)


class CategoricalLongResultBuilder:
    """Materialize the authoritative long calculation table as a normal Tab."""

    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    def run(
        self,
        result: DatasetHandle,
        source: DatasetHandle,
        context_variables: tuple[VariableMetadata, ...],
    ) -> DatasetHandle:
        directory = self.temp_manager.create_dataset_directory()
        database_path = directory / "dataset.sqlite"
        variables = (
            VariableMetadata("ITEM", "Categorical item", "character"),
            VariableMetadata("ITEM_LABEL", "Categorical item label", "character"),
            *context_variables,
            VariableMetadata("LEVEL", "Level", "character"),
            VariableMetadata("TRT", "Treatment", "character"),
            VariableMetadata("FREQ", "Frequency", "numeric"),
            VariableMetadata("DENOM", "Denominator", "numeric"),
            VariableMetadata("PCT", "Percent", "numeric"),
        )
        target: sqlite3.Connection | None = None
        try:
            target = sqlite3.connect(database_path)
            definitions = ", ".join(
                f"{quote_identifier(variable.name)} "
                f"{'REAL' if variable.kind == 'numeric' else 'TEXT'}"
                for variable in variables
            )
            target.execute(
                "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "
                + definitions
                + ")"
            )
            columns = [variable.name for variable in variables]
            insert = (
                "INSERT INTO dataset ("
                + ", ".join(quote_identifier(column) for column in columns)
                + ") VALUES ("
                + ", ".join("?" for _column in columns)
                + ")"
            )
            source_uri = result.database_path.resolve().as_uri() + "?mode=ro"
            row_count = 0
            with closing(sqlite3.connect(source_uri, uri=True)) as connection:
                cursor = connection.execute(
                    "SELECT item_variable, item_label, context_json, level_json, "
                    "treatment_json, freq, denom, pct FROM categorical_long "
                    "ORDER BY row_order, trt_order"
                )
                while rows := cursor.fetchmany(2_000):
                    values = []
                    for row in rows:
                        context = json.loads(row[2])
                        values.append(
                            (
                                row[0],
                                row[1],
                                *(context.get(variable.name) for variable in context_variables),
                                "(Missing)" if json.loads(row[3]) is None else str(json.loads(row[3])),
                                "Total" if row[4] is None else _display_json(row[4]),
                                row[5],
                                row[6],
                                row[7],
                            )
                        )
                    target.executemany(insert, values)
                    row_count += len(values)
                    if row_count % 10_000 == 0:
                        target.commit()
            target.execute(
                "CREATE TABLE cache_info (cached_rows INTEGER NOT NULL, total_rows INTEGER, complete INTEGER NOT NULL)"
            )
            target.execute(
                "INSERT INTO cache_info VALUES (?, ?, 1)", (row_count, row_count)
            )
            target.commit()
            target.close()
            target = None
            marker = directory / "categorical-long-result.tmp"
            marker.touch()
            return DatasetHandle(
                source.source_path.parent / f"Categorical Long - {source.metadata.name}",
                marker,
                database_path,
                DatasetMetadata(f"Categorical Long - {source.metadata.name}", row_count, variables),
                row_count,
                True,
                kind="categorical_long",
                display_source=f"Categorical long result from {source.source_path}",
            )
        except BaseException:
            if target is not None:
                target.close()
            self.temp_manager.remove_dataset(directory)
            raise


def _display_json(value: str) -> str:
    decoded = json.loads(value)
    return "(Missing)" if decoded is None else str(decoded)
