from __future__ import annotations

import json
import sqlite3
from contextlib import closing

from ..categorical.drilldown import _and, _exact_clause, _field, _missing_sql
from ..domain import DatasetHandle
from ..filter_engine import quote_identifier


class AeTableCell:
    def __init__(self, row_type, soc, pt, treatment):
        self.row_type, self.soc, self.pt, self.treatment = row_type, soc, pt, treatment


def _hierarchy_clause(metadata, variable_name: str, value: object, config):
    """Match the engine's merged Uncoded level back to source values."""
    variable = _field(metadata, variable_name)
    if config.hierarchy_missing_policy == "uncoded" and value == "Uncoded":
        # The engine maps missing and the literal value Uncoded to one level.
        return (
            f"({_missing_sql(variable.name, variable.kind, missing=True)} OR {quote_identifier(variable.name)} = ?)",
            ("Uncoded",),
        )
    return _exact_clause(variable, value)


def lookup_cell(result: DatasetHandle, result_source_row: int, column_name: str):
    with closing(
        sqlite3.connect(result.database_path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as conn:
        row = conn.execute(
            "SELECT row_type,soc_json,pt_json,treatment_json FROM ae_table_cell_map WHERE result_row=? AND column_name=?",
            (result_source_row, column_name),
        ).fetchone()
    if row is None:
        return None
    return AeTableCell(
        row[0],
        json.loads(row[1]),
        json.loads(row[2]),
        None if row[3] is None else json.loads(row[3]),
    )


def build_cell_filter(metadata, config, cell, *, denominator=False):
    clauses = []
    if denominator and config.denominator.type == "population":
        clauses.append(
            (
                config.denominator.population_filter.sql,
                config.denominator.population_filter.parameters,
            )
        )
    else:
        clauses.append((config.dataset_filter.sql, config.dataset_filter.parameters))
    subject = _field(metadata, config.subject_id_variable)
    clauses.append((_missing_sql(subject.name, subject.kind, missing=False), ()))
    if cell.treatment is not None:
        treatment_variable = (
            config.denominator.population_treatment_variable
            or config.treatment_variable
            if denominator and config.denominator.type == "population"
            else config.treatment_variable
        )
        clauses.append(
            _exact_clause(_field(metadata, treatment_variable), cell.treatment)
        )
    # Denominator subjects represent the denominator universe for the whole
    # table.  SOC/PT are numerator-only row constraints and must never leak
    # into denominator drill-downs (the population dataset may not even have
    # those variables).
    if not denominator:
        if cell.soc is not None:
            clauses.append(
                _hierarchy_clause(metadata, config.soc_variable, cell.soc, config)
            )
        if cell.pt is not None:
            clauses.append(
                _hierarchy_clause(metadata, config.pt_variable, cell.pt, config)
            )
    return _and(*clauses)


__all__ = ["AeTableCell", "build_cell_filter", "lookup_cell"]
