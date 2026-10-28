from __future__ import annotations

import json
import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass

from ..domain import DatasetHandle, DatasetMetadata
from ..filter_engine import CompiledFilter, quote_identifier
from .models import BaselineCharacteristicsConfig


@dataclass(frozen=True, slots=True)
class BaselineCharacteristicsCell:
    item_kind: str
    variable: str
    level: object | None
    statistic_key: str | None
    treatment_level: object | None = None


def lookup_cell(
    result: DatasetHandle, result_source_row: int, column_name: str
) -> BaselineCharacteristicsCell | None:
    with closing(
        sqlite3.connect(result.database_path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        row = connection.execute(
            "SELECT item_kind, variable, level_json, statistic_key, treatment_level_json "
            "FROM baseline_characteristics_cell_map "
            "WHERE result_row = ? AND column_name = ?",
            (result_source_row, column_name),
        ).fetchone()
    if row is None:
        return None
    return BaselineCharacteristicsCell(
        row[0],
        row[1],
        json.loads(row[2]) if row[2] is not None else None,
        row[3],
        json.loads(row[4]) if row[4] is not None else None,
    )


def _missing_sql(name: str, kind: str, *, missing: bool) -> str:
    column = quote_identifier(name)
    expression = (
        f"({column} IS NULL OR {column} = '')"
        if kind == "character"
        else f"{column} IS NULL"
    )
    return expression if missing else f"NOT ({expression})"


def _literal(value: object, kind: str) -> str:
    if kind == "character":
        return '"' + str(value).replace('"', '""') + '"'
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("A numeric categorical level must be numeric.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("A numeric categorical level must be finite.")
    return format(numeric, ".15g")


def build_cell_filter(
    metadata: DatasetMetadata,
    config: BaselineCharacteristicsConfig,
    cell: BaselineCharacteristicsCell,
    *,
    denominator: bool = False,
) -> CompiledFilter:
    fields = {variable.name.casefold(): variable for variable in metadata.variables}
    variable = fields[cell.variable.casefold()]
    clauses: list[str] = []
    parameters: list[object] = []
    if config.compiled_filter.sql:
        clauses.append(f"({config.compiled_filter.sql})")
        parameters.extend(config.compiled_filter.parameters)
    if config.treatment_variable and cell.treatment_level is not None:
        treatment = fields[config.treatment_variable.casefold()]
        if cell.treatment_level is None:
            clauses.append(_missing_sql(treatment.name, treatment.kind, missing=True))
        else:
            clauses.append(f"{quote_identifier(treatment.name)} = ?")
            parameters.append(cell.treatment_level)
    if cell.item_kind == "categorical" and not denominator:
        if cell.level is None:
            clauses.append(_missing_sql(variable.name, variable.kind, missing=True))
        else:
            clauses.append(f"{quote_identifier(variable.name)} = ?")
            parameters.append(cell.level)
    elif cell.item_kind == "continuous":
        clauses.append(_missing_sql(variable.name, variable.kind, missing=False))
    return CompiledFilter(" AND ".join(clauses), tuple(parameters))


def build_cell_where_text(
    metadata: DatasetMetadata,
    config: BaselineCharacteristicsConfig,
    cell: BaselineCharacteristicsCell,
    *,
    denominator: bool = False,
) -> str:
    fields = {variable.name.casefold(): variable for variable in metadata.variables}
    variable = fields[cell.variable.casefold()]
    clauses: list[str] = []
    if config.filter_text.strip():
        clauses.append(f"({config.filter_text.strip()})")
    if config.treatment_variable and cell.treatment_level is not None:
        treatment = fields[config.treatment_variable.casefold()]
        clauses.append(f"{treatment.name} = {_literal(cell.treatment_level, treatment.kind)}")
    if cell.item_kind == "categorical" and not denominator:
        if cell.level is None:
            clauses.append(f"missing({variable.name})")
        else:
            clauses.append(f"{variable.name} = {_literal(cell.level, variable.kind)}")
    elif cell.item_kind == "continuous":
        clauses.append(f"not missing({variable.name})")
    return " and ".join(clauses)


__all__ = [
    "BaselineCharacteristicsCell",
    "build_cell_filter",
    "build_cell_where_text",
    "lookup_cell",
]
