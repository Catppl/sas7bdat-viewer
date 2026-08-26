from __future__ import annotations

import json
from dataclasses import dataclass

from ..categorical.drilldown import CategoricalQueryBuilder, _and, _exact_clause, _field, _missing_sql
from ..domain import DatasetHandle, DatasetMetadata
from ..filter_engine import quote_identifier
from .models import RuleBasedConfig, RuleBasedRow


@dataclass(frozen=True, slots=True)
class RuleBasedCell:
    row_id: str
    treatment: object | None


def lookup_cell(result: DatasetHandle, result_source_row: int, column_name: str) -> RuleBasedCell | None:
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(result.database_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        row = connection.execute(
            "SELECT row_id, treatment_json FROM rule_based_cell_map "
            "WHERE result_row = ? AND column_name = ?",
            (result_source_row, column_name),
        ).fetchone()
    if row is None:
        return None
    return RuleBasedCell(row[0], json.loads(row[1]))


def build_cell_filter(
    metadata: DatasetMetadata,
    config: RuleBasedConfig,
    row: RuleBasedRow,
    treatment_value: object | None,
    *,
    denominator: bool = False,
) -> tuple[str, tuple[object, ...]]:
    fields = {variable.name.casefold(): variable for variable in metadata.variables}
    treatment = fields[config.treatment_variable.casefold()]
    clauses = [
        (config.dataset_filter.sql, config.dataset_filter.parameters),
    ]
    if not denominator:
        clauses.append((row.row_filter.sql, row.row_filter.parameters))
    elif config.denominator.type == "nonmissing":
        analysis = fields[config.denominator.analysis_value_variable.casefold()]
        clauses.append((_missing_sql(analysis.name, analysis.kind, missing=False), ()))
    if treatment_value is not None:
        clauses.append(_exact_clause(treatment, treatment_value))
    return _and(*clauses)


def build_population_cell_filter(
    metadata: DatasetMetadata,
    config: RuleBasedConfig,
    row: RuleBasedRow,
    treatment_value: object | None,
) -> tuple[str, tuple[object, ...]]:
    fields = {variable.name.casefold(): variable for variable in metadata.variables}
    treatment = fields[config.treatment_variable.casefold()]
    clauses = [(config.denominator.population_filter.sql, config.denominator.population_filter.parameters)]
    if treatment_value is not None:
        clauses.append(_exact_clause(treatment, treatment_value))
    return _and(*clauses)


__all__ = ["CategoricalQueryBuilder", "RuleBasedCell", "build_cell_filter", "build_population_cell_filter", "lookup_cell"]
