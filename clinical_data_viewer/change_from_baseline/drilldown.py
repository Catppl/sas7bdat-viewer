from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass

from ..categorical.drilldown import (
    CategoricalQueryBuilder,
    _and,
    _exact_clause,
    _field,
    _missing_sql,
)
from ..domain import DatasetHandle


@dataclass(frozen=True, slots=True)
class ChangeFromBaselineCell:
    """Source-group identity represented by one result statistic cell."""

    item: object
    row_values: tuple[tuple[str, object], ...]
    treatment: object | None
    statistic: str


def lookup_cell(
    result: DatasetHandle, result_source_row: int, column_name: str
) -> ChangeFromBaselineCell | None:
    with closing(
        sqlite3.connect(result.database_path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        row = connection.execute(
            "SELECT item_json, row_values_json, treatment_json, statistic "
            "FROM change_from_baseline_cell_map "
            "WHERE result_row = ? AND column_name = ?",
            (result_source_row, column_name),
        ).fetchone()
    if row is None:
        return None
    return ChangeFromBaselineCell(
        json.loads(row[0]),
        tuple(json.loads(row[1]).items()),
        json.loads(row[2]) if row[2] is not None else None,
        row[3],
    )


def build_cell_filter(metadata, config, cell: ChangeFromBaselineCell, *, subjects=False):
    """Build the exact source universe used by a Change from Baseline cell."""
    clauses: list[tuple[str, tuple[object, ...]]] = []
    for compiled in (config.data_filter, config.postbaseline_filter):
        if compiled.sql:
            clauses.append((compiled.sql, compiled.parameters))

    item = _field(metadata, config.item_variable)
    clauses.append(_exact_clause(item, cell.item))
    for name, value in cell.row_values:
        clauses.append(_exact_clause(_field(metadata, name), value))
    if cell.treatment is not None:
        clauses.append(
            _exact_clause(_field(metadata, config.treatment_variable), cell.treatment)
        )

    change = _field(metadata, config.change_variable)
    clauses.append((_missing_sql(change.name, change.kind, missing=False), ()))
    if subjects:
        subject = _field(metadata, "USUBJID")
        clauses.append((_missing_sql(subject.name, subject.kind, missing=False), ()))
    return _and(*clauses)


__all__ = [
    "CategoricalQueryBuilder",
    "ChangeFromBaselineCell",
    "build_cell_filter",
    "lookup_cell",
]
