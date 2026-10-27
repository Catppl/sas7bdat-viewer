from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace

from ..domain import DatasetHandle, DatasetMetadata
from ..filter_engine import CompiledFilter
from ..proc_means.drilldown import (
    build_drilldown_filter,
    build_drilldown_where_text,
)
from .models import ProcMeansVerticalConfig


@dataclass(frozen=True, slots=True)
class ProcMeansVerticalCell:
    analysis_variable: str
    statistic_key: str
    statistic_label: str
    group_values: tuple[object, ...] | None


def lookup_cell(
    result: DatasetHandle, result_source_row: int, column_name: str
) -> ProcMeansVerticalCell | None:
    with closing(
        sqlite3.connect(result.database_path.resolve().as_uri() + "?mode=ro", uri=True)
    ) as connection:
        row = connection.execute(
            "SELECT analysis_variable, statistic_key, statistic_label, "
            "group_values_json FROM proc_means_vertical_cell_map "
            "WHERE result_row = ? AND column_name = ?",
            (result_source_row, column_name),
        ).fetchone()
    if row is None:
        return None
    return ProcMeansVerticalCell(
        row[0],
        row[1],
        row[2],
        tuple(json.loads(row[3])) if row[3] is not None else None,
    )


def _drilldown_context(
    config: ProcMeansVerticalConfig,
    cell: ProcMeansVerticalCell,
) -> tuple[ProcMeansVerticalConfig, dict[str, object]]:
    if cell.group_values is None:
        return replace(config, group_variables=(), row_group_variables=()), {}
    row_variables = tuple(config.row_group_variables)
    all_variables = (*row_variables, *config.group_variables)
    if len(cell.group_values) == len(all_variables):
        selected_variables = all_variables
    elif len(cell.group_values) == len(row_variables):
        selected_variables = row_variables
    else:
        raise ValueError("The vertical PROC MEANS result group mapping is invalid.")
    drilldown_config = replace(
        config,
        group_variables=selected_variables,
        row_group_variables=(),
    )
    return drilldown_config, dict(zip(selected_variables, cell.group_values, strict=True))


def build_cell_filter(
    metadata: DatasetMetadata,
    config: ProcMeansVerticalConfig,
    cell: ProcMeansVerticalCell,
) -> CompiledFilter:
    drilldown_config, group_values = _drilldown_context(config, cell)
    return build_drilldown_filter(
        metadata,
        drilldown_config,
        group_values,
        cell.analysis_variable,
        cell.statistic_key,
    )


def build_cell_where_text(
    metadata: DatasetMetadata,
    config: ProcMeansVerticalConfig,
    cell: ProcMeansVerticalCell,
) -> str:
    drilldown_config, group_values = _drilldown_context(config, cell)
    return build_drilldown_where_text(
        metadata,
        drilldown_config,
        group_values,
        cell.analysis_variable,
        cell.statistic_key,
    )


__all__ = [
    "ProcMeansVerticalCell",
    "build_cell_filter",
    "build_cell_where_text",
    "lookup_cell",
]
