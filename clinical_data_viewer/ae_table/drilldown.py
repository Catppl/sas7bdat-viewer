from __future__ import annotations

import json
import sqlite3
from contextlib import closing

from ..categorical.drilldown import _and, _exact_clause, _missing_sql, _field
from ..domain import DatasetHandle


class AeTableCell:
    def __init__(self, row_type, soc, pt, treatment): self.row_type, self.soc, self.pt, self.treatment = row_type, soc, pt, treatment


def lookup_cell(result: DatasetHandle, result_source_row: int, column_name: str):
    with closing(sqlite3.connect(result.database_path.resolve().as_uri()+"?mode=ro", uri=True)) as conn:
        row = conn.execute("SELECT row_type,soc_json,pt_json,treatment_json FROM ae_table_cell_map WHERE result_row=? AND column_name=?", (result_source_row, column_name)).fetchone()
    if row is None: return None
    return AeTableCell(row[0], json.loads(row[1]), json.loads(row[2]), None if row[3] is None else json.loads(row[3]))


def build_cell_filter(metadata, config, cell, *, denominator=False):
    clauses = []
    if denominator and config.denominator.type == "population": clauses.append((config.denominator.population_filter.sql, config.denominator.population_filter.parameters))
    else: clauses.append((config.dataset_filter.sql, config.dataset_filter.parameters))
    subject = _field(metadata, config.subject_id_variable); clauses.append((_missing_sql(subject.name, subject.kind, missing=False), ()))
    if cell.treatment is not None: clauses.append(_exact_clause(_field(metadata, config.treatment_variable), cell.treatment))
    if cell.soc is not None: clauses.append(_exact_clause(_field(metadata, config.soc_variable), cell.soc))
    if cell.pt is not None and not denominator: clauses.append(_exact_clause(_field(metadata, config.pt_variable), cell.pt))
    return _and(*clauses)


__all__ = ["AeTableCell", "lookup_cell", "build_cell_filter"]
