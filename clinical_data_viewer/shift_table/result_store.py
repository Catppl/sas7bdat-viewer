from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from ..domain import DatasetHandle, DatasetMetadata, VariableMetadata
from ..filter_engine import quote_identifier
from ..temp_manager import TempManager


@dataclass(frozen=True, slots=True)
class ShiftColumn:
    name: str
    label: str
    treatment: object | None
    post_grade: object | None
    is_overall: bool
    cell_role: str


@dataclass(frozen=True, slots=True)
class ShiftCellValue:
    subject_n: int
    denominator_n: int
    percent_value: float | None
    display_value: str


LONG_VARIABLES = (
    VariableMetadata("ROW_ORDER", "Row order", "numeric"),
    VariableMetadata("PARAMETER_ORDER", "Parameter order", "numeric"),
    VariableMetadata("PARAMETER_RAW_JSON", "Parameter raw value JSON"),
    VariableMetadata("PARAMETER_LABEL", "Parameter label"),
    VariableMetadata("VISIT_ORDER", "Visit order", "numeric"),
    VariableMetadata("VISIT_RAW_JSON", "Visit raw value JSON"),
    VariableMetadata("VISIT_LABEL", "Visit label"),
    VariableMetadata("TREATMENT_ORDER", "Treatment order", "numeric"),
    VariableMetadata("TREATMENT_RAW_JSON", "Treatment raw value JSON"),
    VariableMetadata("TREATMENT_LABEL", "Treatment label"),
    VariableMetadata("BASELINE_GRADE_ORDER", "Baseline grade order", "numeric"),
    VariableMetadata("BASELINE_GRADE_RAW_JSON", "Baseline grade raw value JSON"),
    VariableMetadata("BASELINE_GRADE_LABEL", "Baseline grade label"),
    VariableMetadata("POST_GRADE_ORDER", "Post grade order", "numeric"),
    VariableMetadata("POST_GRADE_RAW_JSON", "Post grade raw value JSON"),
    VariableMetadata("POST_GRADE_LABEL", "Post grade label"),
    VariableMetadata("CELL_ROLE", "Cell role"),
    VariableMetadata("IS_OVERALL", "Overall treatment", "numeric"),
    VariableMetadata("SUBJECT_N", "Subject n", "numeric"),
    VariableMetadata("DENOMINATOR_N", "Denominator n", "numeric"),
    VariableMetadata("PERCENT_VALUE", "Percent", "numeric"),
    VariableMetadata("DISPLAY_VALUE", "Display value"),
)


class ShiftTableResultWriter:
    def __init__(self, database_path: Path, columns: tuple[ShiftColumn, ...]) -> None:
        self.database_path = database_path
        self.columns = columns
        self.connection = sqlite3.connect(database_path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.row_count = 0
        self.long_row_count = 0
        definitions = ", ".join(
            f"{quote_identifier(column.name)} TEXT" for column in columns
        )
        self.connection.execute(
            "CREATE TABLE dataset ("
            "_source_row INTEGER PRIMARY KEY, PARAMETER TEXT, VISIT TEXT, BASELINE_GRADE TEXT"
            + (", " + definitions if definitions else "")
            + ")"
        )
        self.connection.execute(
            "CREATE TABLE shift_table_long ("
            "row_order INTEGER PRIMARY KEY, parameter_order INTEGER NOT NULL, "
            "parameter_raw_json TEXT NOT NULL, parameter_label TEXT NOT NULL, "
            "visit_order INTEGER NOT NULL, visit_raw_json TEXT NOT NULL, visit_label TEXT NOT NULL, "
            "treatment_order INTEGER NOT NULL, treatment_raw_json TEXT, treatment_label TEXT NOT NULL, "
            "baseline_grade_order INTEGER NOT NULL, baseline_grade_raw_json TEXT, baseline_grade_label TEXT NOT NULL, "
            "post_grade_order INTEGER NOT NULL, post_grade_raw_json TEXT, post_grade_label TEXT NOT NULL, "
            "cell_role TEXT NOT NULL, is_overall INTEGER NOT NULL, subject_n INTEGER NOT NULL, "
            "denominator_n INTEGER NOT NULL, percent_value REAL, display_value TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE shift_table_cell_map ("
            "result_row INTEGER NOT NULL, column_name TEXT NOT NULL, "
            "parameter_json TEXT NOT NULL, visit_json TEXT NOT NULL, treatment_json TEXT, "
            "baseline_grade_json TEXT, post_grade_json TEXT, cell_role TEXT NOT NULL, "
            "is_overall INTEGER NOT NULL, PRIMARY KEY (result_row, column_name))"
        )
        self.connection.execute(
            "CREATE TABLE shift_table_header_map ("
            "column_name TEXT PRIMARY KEY, treatment_json TEXT, is_overall INTEGER NOT NULL)"
        )
        for column in columns:
            self.connection.execute(
                "INSERT OR IGNORE INTO shift_table_header_map VALUES (?, ?, ?)",
                (
                    column.name,
                    json.dumps(column.treatment, ensure_ascii=False)
                    if column.treatment is not None
                    else None,
                    int(column.is_overall),
                ),
            )

    def add_matrix_row(
        self,
        *,
        parameter_order: int,
        parameter_raw: object,
        parameter_label: str,
        visit_order: int,
        visit_raw: object,
        visit_label: str,
        baseline_order: int,
        baseline_raw: object | None,
        baseline_label: str,
        cells: dict[
            str, tuple[ShiftColumn, int, str, int, object | None, str, ShiftCellValue]
        ],
    ) -> int:
        self.row_count += 1
        names = ["PARAMETER", "VISIT", "BASELINE_GRADE"] + [
            column.name for column in self.columns
        ]
        placeholders = ", ".join("?" for _ in range(len(names) + 1))
        column_sql = ", ".join(quote_identifier(name) for name in names)
        self.connection.execute(
            f"INSERT INTO dataset (_source_row, {column_sql}) VALUES ({placeholders})",
            (
                self.row_count,
                parameter_label,
                visit_label,
                baseline_label,
                *(cells[column.name][-1].display_value for column in self.columns),
            ),
        )
        for column in self.columns:
            (
                _spec,
                treatment_order,
                treatment_label,
                post_order,
                post_raw,
                post_label,
                value,
            ) = cells[column.name]
            role = column.cell_role
            if baseline_raw is None:
                role = "grand_total" if column.post_grade is None else "column_total"
            self.long_row_count += 1
            self.connection.execute(
                "INSERT INTO shift_table_long VALUES ("
                + ", ".join("?" for _ in range(22))
                + ")",
                (
                    self.long_row_count,
                    parameter_order,
                    json.dumps(parameter_raw, ensure_ascii=False),
                    parameter_label,
                    visit_order,
                    json.dumps(visit_raw, ensure_ascii=False),
                    visit_label,
                    treatment_order,
                    json.dumps(column.treatment, ensure_ascii=False)
                    if column.treatment is not None
                    else None,
                    treatment_label,
                    baseline_order,
                    json.dumps(baseline_raw, ensure_ascii=False)
                    if baseline_raw is not None
                    else None,
                    baseline_label,
                    post_order,
                    json.dumps(post_raw, ensure_ascii=False)
                    if post_raw is not None
                    else None,
                    post_label,
                    role,
                    int(column.is_overall),
                    value.subject_n,
                    value.denominator_n,
                    value.percent_value,
                    value.display_value,
                ),
            )
            self.connection.execute(
                "INSERT INTO shift_table_cell_map VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    self.row_count,
                    column.name,
                    json.dumps(parameter_raw, ensure_ascii=False),
                    json.dumps(visit_raw, ensure_ascii=False),
                    json.dumps(column.treatment, ensure_ascii=False)
                    if column.treatment is not None
                    else None,
                    json.dumps(baseline_raw, ensure_ascii=False)
                    if baseline_raw is not None
                    else None,
                    json.dumps(column.post_grade, ensure_ascii=False)
                    if column.post_grade is not None
                    else None,
                    role,
                    int(column.is_overall),
                ),
            )
        return self.row_count

    def finish(
        self,
        directory: Path,
        source: DatasetHandle,
        configuration_path: Path,
        *,
        exclusion_message: str = "",
    ) -> DatasetHandle:
        self.connection.execute(
            "CREATE TABLE cache_info (cached_rows INTEGER NOT NULL, total_rows INTEGER, complete INTEGER NOT NULL)"
        )
        self.connection.execute(
            "INSERT INTO cache_info VALUES (?, ?, 1)",
            (self.row_count, self.row_count),
        )
        self.connection.commit()
        self.connection.close()
        marker = directory / "shift-table-result.tmp"
        marker.touch()
        variables = (
            VariableMetadata("PARAMETER", "Parameter"),
            VariableMetadata("VISIT", "Visit"),
            VariableMetadata("BASELINE_GRADE", "Baseline Grade"),
            *(VariableMetadata(column.name, column.label) for column in self.columns),
        )
        metadata = DatasetMetadata(
            f"Shift Table - {source.metadata.name}",
            self.row_count,
            variables,
            display_column_names=tuple(
                (variable.name, variable.label) for variable in variables
            ),
            shift_table_drilldown_columns=tuple(column.name for column in self.columns),
            warning_column_messages=(
                (("SHIFT_EXCLUSIONS", exclusion_message),) if exclusion_message else ()
            ),
        )
        return DatasetHandle(
            source.source_path.parent / f"Shift Table - {source.metadata.name}",
            marker,
            self.database_path,
            metadata,
            self.row_count,
            True,
            kind="shift_table",
            display_source=f"Shift Table from {source.source_path}",
            configuration_path=configuration_path,
        )

    def abort(self, temp_manager: TempManager, directory: Path) -> None:
        try:
            self.connection.close()
        except sqlite3.Error:
            pass
        temp_manager.remove_dataset(directory)


class ShiftTableLongResultBuilder:
    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    def run(self, result: DatasetHandle, source: DatasetHandle) -> DatasetHandle:
        directory = self.temp_manager.create_dataset_directory()
        database = directory / "dataset.sqlite"
        target: sqlite3.Connection | None = None
        try:
            target = sqlite3.connect(database)
            definitions = ", ".join(
                f"{quote_identifier(variable.name)} "
                f"{'REAL' if variable.kind == 'numeric' else 'TEXT'}"
                for variable in LONG_VARIABLES
            )
            target.execute(
                "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "
                + definitions
                + ")"
            )
            columns = ", ".join(
                quote_identifier(variable.name) for variable in LONG_VARIABLES
            )
            with closing(
                sqlite3.connect(
                    result.database_path.resolve().as_uri() + "?mode=ro", uri=True
                )
            ) as connection:
                rows = connection.execute(
                    "SELECT row_order, "
                    + columns
                    + " FROM shift_table_long ORDER BY row_order"
                ).fetchall()
            placeholders = ", ".join("?" for _ in range(len(LONG_VARIABLES) + 1))
            target.executemany(
                "INSERT INTO dataset VALUES (" + placeholders + ")", rows
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
            marker = directory / "shift-table-long.tmp"
            marker.touch()
            return DatasetHandle(
                source.source_path.parent / "Shift Table Long Result",
                marker,
                database,
                DatasetMetadata("Shift Table Long Result", len(rows), LONG_VARIABLES),
                len(rows),
                True,
                kind="shift_table_long",
                display_source=f"Shift Table long result from {source.source_path}",
            )
        except BaseException:
            if target is not None:
                target.close()
            self.temp_manager.remove_dataset(directory)
            raise


__all__ = [
    "LONG_VARIABLES",
    "ShiftCellValue",
    "ShiftColumn",
    "ShiftTableLongResultBuilder",
    "ShiftTableResultWriter",
]
