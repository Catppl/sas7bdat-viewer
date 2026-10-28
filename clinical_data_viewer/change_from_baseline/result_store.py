from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from ..domain import DatasetHandle, DatasetMetadata, VariableMetadata
from ..filter_engine import quote_identifier
from ..sqlite_access import connect_result_database
from ..temp_manager import TempManager


def _unique_name(base: str, used: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate.casefold() in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate.casefold())
    return candidate


def _statistic_label(statistic: str) -> str:
    """Keep record count n visually distinct from subject count N."""
    return "n" if statistic.casefold() == "n" else statistic.upper()


@dataclass(frozen=True, slots=True)
class ChangeResultSchema:
    wide_variables: tuple[VariableMetadata, ...]
    long_variables: tuple[VariableMetadata, ...]
    decimal_base: str
    statistic_decimal_offsets: tuple[tuple[str, int], ...]
    statistic_columns: tuple[tuple[str, str], ...]
    drilldown_columns: tuple[tuple[str, str], ...]


def build_result_schema(
    source: DatasetHandle,
    row_variables: tuple[str, ...],
    statistics: tuple[str, ...],
    treatment_levels: tuple[tuple[str, object, str], ...],
    include_total: bool,
    statistic_decimals: tuple[tuple[str, int], ...] = (),
) -> ChangeResultSchema:
    source_fields = {
        variable.name.casefold(): variable for variable in source.metadata.variables
    }
    used = {"item"}
    wide = [VariableMetadata("ITEM", "Item", "character", 200)]
    statistic_columns: list[tuple[str, str]] = []
    drilldown_columns: list[tuple[str, str]] = []
    for name in row_variables:
        source_variable = source_fields[name.casefold()]
        output_name = _unique_name(source_variable.name, used)
        wide.append(
            VariableMetadata(
                output_name,
                source_variable.label or source_variable.name,
                source_variable.kind,
                source_variable.length,
                source_variable.format,
            )
        )
    groups = list(treatment_levels)
    if not groups:
        groups = [("__all__", None, "All")]
    has_treatment_columns = bool(treatment_levels)
    for index, (_key, _value, label) in enumerate(groups, start=1):
        prefix = "" if len(treatment_levels) == 0 else f"TRT_{index}_"
        for statistic in statistics:
            name = _unique_name(f"{prefix}{statistic.upper()}", used)
            display_label = _statistic_label(statistic)
            if has_treatment_columns:
                display_label = f"{label} {display_label}"
            wide.append(VariableMetadata(name, display_label, "numeric"))
            statistic_columns.append((name, statistic))
        name = _unique_name(f"{prefix}SUBJECT_N", used)
        wide.append(
            VariableMetadata(
                name, f"{label} N" if has_treatment_columns else "N", "numeric"
            )
        )
        drilldown_columns.append((name, "SUBJECT_N"))
    if include_total and treatment_levels:
        for statistic in statistics:
            name = _unique_name(f"TOTAL_{statistic.upper()}", used)
            wide.append(
                VariableMetadata(
                    name, f"Total {_statistic_label(statistic)}", "numeric"
                )
            )
            statistic_columns.append((name, statistic))
        name = _unique_name("TOTAL_SUBJECT_N", used)
        wide.append(VariableMetadata(name, "Total N", "numeric"))
        drilldown_columns.append((name, "SUBJECT_N"))

    long_used = {"item", "treatment", "statistic", "value", "small_n", "subject_n"}
    long_row_variables: list[VariableMetadata] = []
    for name in row_variables:
        source_variable = source_fields[name.casefold()]
        output_name = _unique_name(source_variable.name, long_used)
        long_row_variables.append(
            VariableMetadata(
                output_name,
                source_variable.label or source_variable.name,
                source_variable.kind,
                source_variable.length,
                source_variable.format,
            )
        )
    long = [
        VariableMetadata("ITEM", "Item", "character", 200),
        *long_row_variables,
        VariableMetadata("TREATMENT", "Treatment", "character", 200),
        VariableMetadata("STATISTIC", "Statistic", "character", 32),
        VariableMetadata("VALUE", "Value", "numeric"),
        VariableMetadata("SMALL_N", "Non-missing change records", "numeric"),
        VariableMetadata("SUBJECT_N", "Distinct subjects", "numeric"),
    ]
    decimal_base = _unique_name("_display_decimals", used)
    decimals = dict(statistic_decimals)
    decimal_offsets = tuple(
        (name, decimals.get(statistic, 0)) for name, statistic in statistic_columns
    )
    return ChangeResultSchema(
        tuple(wide),
        tuple(long),
        decimal_base,
        decimal_offsets,
        tuple(statistic_columns),
        (*statistic_columns, *drilldown_columns),
    )


class ChangeFromBaselineResultWriter:
    def __init__(self, database_path: Path, schema: ChangeResultSchema) -> None:
        self.database_path = database_path
        self.schema = schema
        self.connection = connect_result_database(database_path)
        self.row_count = 0
        self.long_count = 0
        wide_defs = ", ".join(
            f"{quote_identifier(variable.name)} "
            f"{'REAL' if variable.kind == 'numeric' else 'TEXT'}"
            for variable in schema.wide_variables
        )
        long_defs = ", ".join(
            f"{quote_identifier(variable.name)} "
            f"{'REAL' if variable.kind == 'numeric' else 'TEXT'}"
            for variable in schema.long_variables
        )
        wide_defs += f", {quote_identifier(schema.decimal_base)} INTEGER NOT NULL"
        self.connection.execute(
            "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, " + wide_defs + ")"
        )
        self.connection.execute(
            "CREATE TABLE change_from_baseline_long (_source_row INTEGER PRIMARY KEY, "
            + long_defs
            + ")"
        )
        self.connection.execute(
            "CREATE TABLE change_from_baseline_cell_map ("
            "result_row INTEGER NOT NULL, column_name TEXT NOT NULL, "
            "item_json TEXT NOT NULL, row_values_json TEXT NOT NULL, "
            "treatment_json TEXT, statistic TEXT NOT NULL, "
            "PRIMARY KEY (result_row, column_name))"
        )

    def add_wide(self, values: tuple[object, ...], base_decimals: int = 0) -> int:
        names = ", ".join(quote_identifier(v.name) for v in self.schema.wide_variables)
        placeholders = ", ".join("?" for _ in self.schema.wide_variables)
        names += ", " + quote_identifier(self.schema.decimal_base)
        placeholders += ", ?"
        self.row_count += 1
        self.connection.execute(
            f"INSERT INTO dataset (_source_row, {names}) VALUES (?, {placeholders})",
            (self.row_count, *values, base_decimals),
        )
        return self.row_count

    def add_long(self, values: tuple[object, ...]) -> None:
        names = ", ".join(quote_identifier(v.name) for v in self.schema.long_variables)
        placeholders = ", ".join("?" for _ in self.schema.long_variables)
        self.long_count += 1
        self.connection.execute(
            f"INSERT INTO change_from_baseline_long (_source_row, {names}) VALUES (?, {placeholders})",
            (self.long_count, *values),
        )

    def add_cell_map(
        self,
        result_row: int,
        column_name: str,
        item: object,
        row_values: tuple[tuple[str, object], ...],
        treatment: object | None,
        statistic: str,
    ) -> None:
        self.connection.execute(
            "INSERT INTO change_from_baseline_cell_map "
            "(result_row, column_name, item_json, row_values_json, "
            "treatment_json, statistic) VALUES (?, ?, ?, ?, ?, ?)",
            (
                result_row,
                column_name,
                json.dumps(item, ensure_ascii=False),
                json.dumps(dict(row_values), ensure_ascii=False),
                json.dumps(treatment, ensure_ascii=False)
                if treatment is not None
                else None,
                statistic,
            ),
        )

    def finish(
        self,
        directory: Path,
        source: DatasetHandle,
        configuration_path: Path,
    ) -> DatasetHandle:
        self.connection.execute(
            "CREATE TABLE cache_info (cached_rows INTEGER,total_rows INTEGER,complete INTEGER)"
        )
        self.connection.execute(
            "INSERT INTO cache_info VALUES (?,?,1)", (self.row_count, self.row_count)
        )
        self.connection.commit()
        self.connection.close()
        marker = directory / "change-from-baseline-result.tmp"
        marker.touch()
        metadata = DatasetMetadata(
            f"Change from Baseline - {source.metadata.name}",
            self.row_count,
            self.schema.wide_variables,
            decimal_base_column=self.schema.decimal_base,
            statistic_decimal_offsets=self.schema.statistic_decimal_offsets,
            change_from_baseline_statistic_keys=self.schema.drilldown_columns,
            display_column_names=tuple(
                (variable.name, variable.label or variable.name)
                for variable in self.schema.wide_variables
            ),
        )
        return DatasetHandle(
            source.source_path.parent
            / f"Change from Baseline - {source.metadata.name}",
            marker,
            self.database_path,
            metadata,
            self.row_count,
            True,
            kind="change_from_baseline",
            display_source=f"Change from Baseline from {source.source_path}",
            configuration_path=configuration_path,
        )

    def abort(self, manager: TempManager, directory: Path) -> None:
        try:
            self.connection.close()
        except sqlite3.Error:
            pass
        manager.remove_dataset(directory)


class ChangeFromBaselineLongResultBuilder:
    """Materialize the saved long calculation table as a normal result Tab."""

    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    def run(self, result: DatasetHandle, source: DatasetHandle) -> DatasetHandle:
        directory = self.temp_manager.create_dataset_directory()
        database = directory / "dataset.sqlite"
        source_fields = {
            variable.name.casefold(): variable for variable in source.metadata.variables
        }
        with closing(sqlite3.connect(result.database_path)) as schema_connection:
            columns = [
                row[1]
                for row in schema_connection.execute(
                    "PRAGMA table_info(change_from_baseline_long)"
                )
                if row[1] != "_source_row"
            ]
        variables: list[VariableMetadata] = []
        for name in columns:
            if name.casefold() == "item":
                variables.append(VariableMetadata(name, "Item", "character", 200))
            elif name.casefold() in source_fields:
                variables.append(source_fields[name.casefold()])
            elif name.casefold() == "treatment":
                variables.append(VariableMetadata(name, "Treatment", "character", 200))
            elif name.casefold() == "statistic":
                variables.append(VariableMetadata(name, "Statistic", "character", 32))
            elif name.casefold() in {"value", "small_n", "subject_n"}:
                variables.append(
                    VariableMetadata(name, name.replace("_", " ").title(), "numeric")
                )
            else:
                variables.append(VariableMetadata(name))
        target: sqlite3.Connection | None = None
        try:
            target = sqlite3.connect(database)
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
            with closing(
                sqlite3.connect(
                    result.database_path.resolve().as_uri() + "?mode=ro", uri=True
                )
            ) as connection:
                rows = connection.execute(
                    "SELECT "
                    + ", ".join(quote_identifier(name) for name in columns)
                    + " FROM change_from_baseline_long ORDER BY _source_row"
                ).fetchall()
            placeholders = ", ".join("?" for _column in variables)
            target.executemany(
                "INSERT INTO dataset ("
                + ", ".join(quote_identifier(variable.name) for variable in variables)
                + ") VALUES ("
                + placeholders
                + ")",
                rows,
            )
            target.execute(
                "CREATE TABLE cache_info (cached_rows INTEGER NOT NULL, total_rows INTEGER, complete INTEGER NOT NULL)"
            )
            target.execute(
                "INSERT INTO cache_info VALUES (?, ?, 1)", (len(rows), len(rows))
            )
            target.commit()
            target.close()
            target = None
            marker = directory / "change-from-baseline-long-result.tmp"
            marker.touch()
            return DatasetHandle(
                source.source_path.parent
                / f"Change from Baseline Long - {source.metadata.name}",
                marker,
                database,
                DatasetMetadata(
                    f"Change from Baseline Long - {source.metadata.name}",
                    len(rows),
                    tuple(variables),
                ),
                len(rows),
                True,
                kind="change_from_baseline_long",
                display_source=f"Change from Baseline long result from {source.source_path}",
            )
        except BaseException:
            if target is not None:
                target.close()
            self.temp_manager.remove_dataset(directory)
            raise


__all__ = [
    "ChangeFromBaselineLongResultBuilder",
    "ChangeFromBaselineResultWriter",
    "ChangeResultSchema",
    "build_result_schema",
]
