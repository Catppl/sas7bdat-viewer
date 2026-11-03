from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass

from ..domain import DatasetHandle, DatasetMetadata, VariableMetadata
from ..filter_engine import CompiledFilter, quote_identifier
from .models import OrderedLevel, ShiftTableConfig


@dataclass(frozen=True, slots=True)
class ShiftTableCell:
    parameter: object
    visit: object
    treatment: object | None
    baseline_grade: object | None
    post_grade: object | None
    cell_role: str
    is_overall: bool


@dataclass(frozen=True, slots=True)
class ShiftTableHeader:
    treatment: object | None
    is_overall: bool


def lookup_cell(
    result: DatasetHandle, result_source_row: int, column_name: str
) -> ShiftTableCell | None:
    with closing(
        sqlite3.connect(result.database_path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        row = connection.execute(
            "SELECT parameter_json, visit_json, treatment_json, baseline_grade_json, "
            "post_grade_json, cell_role, is_overall FROM shift_table_cell_map "
            "WHERE result_row = ? AND column_name = ?",
            (result_source_row, column_name),
        ).fetchone()
    if row is None:
        return None
    return ShiftTableCell(
        json.loads(row[0]),
        json.loads(row[1]),
        json.loads(row[2]) if row[2] is not None else None,
        json.loads(row[3]) if row[3] is not None else None,
        json.loads(row[4]) if row[4] is not None else None,
        row[5],
        bool(row[6]),
    )


def lookup_header(result: DatasetHandle, column_name: str) -> ShiftTableHeader | None:
    with closing(
        sqlite3.connect(result.database_path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        row = connection.execute(
            "SELECT treatment_json, is_overall FROM shift_table_header_map WHERE column_name = ?",
            (column_name,),
        ).fetchone()
    if row is None:
        return None
    return ShiftTableHeader(
        json.loads(row[0]) if row[0] is not None else None,
        bool(row[1]),
    )


def _fields(metadata: DatasetMetadata) -> dict[str, VariableMetadata]:
    return {variable.name.casefold(): variable for variable in metadata.variables}


def _missing_sql(variable: VariableMetadata, *, missing: bool) -> str:
    column = quote_identifier(variable.name)
    expression = (
        f"({column} IS NULL OR {column} = '')"
        if variable.kind == "character"
        else f"{column} IS NULL"
    )
    return expression if missing else f"NOT ({expression})"


def _literal(value: object, variable: VariableMetadata) -> str:
    if variable.kind == "character":
        return '"' + str(value).replace('"', '""') + '"'
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{variable.name} requires a numeric value.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{variable.name} requires a finite value.")
    return format(numeric, ".15g")


def _in_sql(variable: VariableMetadata, levels: tuple[OrderedLevel, ...]) -> str:
    return (
        "("
        + " or ".join(
            f"{variable.name} = {_literal(level.raw_value, variable)}"
            for level in levels
        )
        + ")"
    )


def build_cell_filter(
    metadata: DatasetMetadata,
    config: ShiftTableConfig,
    cell: ShiftTableCell,
) -> CompiledFilter:
    fields = _fields(metadata)
    clauses: list[str] = []
    parameters: list[object] = []
    if config.compiled_filter.sql:
        clauses.append(f"({config.compiled_filter.sql})")
        parameters.extend(config.compiled_filter.parameters)
    for name, value in (
        (config.analysis_flag_variable, config.analysis_flag_value),
        (config.parameter_variable, cell.parameter),
        (config.visit_variable, cell.visit),
    ):
        clauses.append(f"{quote_identifier(fields[name.casefold()].name)} = ?")
        parameters.append(value)
    treatment = fields[config.treatment_variable.casefold()]
    if cell.is_overall:
        placeholders = ", ".join("?" for _ in config.treatments)
        clauses.append(f"{quote_identifier(treatment.name)} IN ({placeholders})")
        parameters.extend(level.raw_value for level in config.treatments)
    else:
        clauses.append(f"{quote_identifier(treatment.name)} = ?")
        parameters.append(cell.treatment)
    subject = fields[config.subject_variable.casefold()]
    baseline = fields[config.baseline_grade_variable.casefold()]
    post = fields[config.post_grade_variable.casefold()]
    clauses.append(_missing_sql(subject, missing=False))
    clauses.append(_missing_sql(baseline, missing=False))
    clauses.append(_missing_sql(post, missing=False))
    if cell.cell_role in {"detail", "row_total"}:
        clauses.append(f"{quote_identifier(baseline.name)} = ?")
        parameters.append(cell.baseline_grade)
    if cell.cell_role in {"detail", "column_total"}:
        clauses.append(f"{quote_identifier(post.name)} = ?")
        parameters.append(cell.post_grade)
    return CompiledFilter(" AND ".join(clauses), tuple(parameters))


def build_cell_where_text(
    metadata: DatasetMetadata,
    config: ShiftTableConfig,
    cell: ShiftTableCell,
) -> str:
    fields = _fields(metadata)
    clauses: list[str] = []
    if config.filter_text.strip():
        clauses.append(f"({config.filter_text.strip()})")
    for name, value in (
        (config.analysis_flag_variable, config.analysis_flag_value),
        (config.parameter_variable, cell.parameter),
        (config.visit_variable, cell.visit),
    ):
        variable = fields[name.casefold()]
        clauses.append(f"{variable.name} = {_literal(value, variable)}")
    treatment = fields[config.treatment_variable.casefold()]
    if cell.is_overall:
        clauses.append(_in_sql(treatment, config.treatments))
    else:
        clauses.append(f"{treatment.name} = {_literal(cell.treatment, treatment)}")
    subject = fields[config.subject_variable.casefold()]
    baseline = fields[config.baseline_grade_variable.casefold()]
    post = fields[config.post_grade_variable.casefold()]
    clauses.extend(
        (
            f"not missing({subject.name})",
            f"not missing({baseline.name})",
            f"not missing({post.name})",
        )
    )
    if cell.cell_role in {"detail", "row_total"}:
        clauses.append(f"{baseline.name} = {_literal(cell.baseline_grade, baseline)}")
    if cell.cell_role in {"detail", "column_total"}:
        clauses.append(f"{post.name} = {_literal(cell.post_grade, post)}")
    return " and ".join(clauses)


def build_header_filter(
    metadata: DatasetMetadata,
    config: ShiftTableConfig,
    header: ShiftTableHeader,
) -> CompiledFilter:
    fields = _fields(metadata)
    clauses: list[str] = []
    parameters: list[object] = []
    if config.population_filter.sql:
        clauses.append(f"({config.population_filter.sql})")
        parameters.extend(config.population_filter.parameters)
    name = config.population_treatment_variable or config.treatment_variable
    treatment = fields[name.casefold()]
    if header.is_overall:
        placeholders = ", ".join("?" for _ in config.treatments)
        clauses.append(f"{quote_identifier(treatment.name)} IN ({placeholders})")
        parameters.extend(level.raw_value for level in config.treatments)
    else:
        clauses.append(f"{quote_identifier(treatment.name)} = ?")
        parameters.append(header.treatment)
    subject = fields[config.population_subject_variable.casefold()]
    clauses.append(_missing_sql(subject, missing=False))
    return CompiledFilter(" AND ".join(clauses), tuple(parameters))


def build_header_where_text(
    metadata: DatasetMetadata,
    config: ShiftTableConfig,
    header: ShiftTableHeader,
) -> str:
    fields = _fields(metadata)
    clauses: list[str] = []
    if config.population_filter_text.strip():
        clauses.append(f"({config.population_filter_text.strip()})")
    name = config.population_treatment_variable or config.treatment_variable
    treatment = fields[name.casefold()]
    if header.is_overall:
        clauses.append(_in_sql(treatment, config.treatments))
    else:
        clauses.append(f"{treatment.name} = {_literal(header.treatment, treatment)}")
    subject = fields[config.population_subject_variable.casefold()]
    clauses.append(f"not missing({subject.name})")
    return " and ".join(clauses)


__all__ = [
    "ShiftTableCell",
    "ShiftTableHeader",
    "build_cell_filter",
    "build_cell_where_text",
    "build_header_filter",
    "build_header_where_text",
    "lookup_cell",
    "lookup_header",
]
