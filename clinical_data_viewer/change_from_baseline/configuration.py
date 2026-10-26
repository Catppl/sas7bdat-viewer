from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from ..domain import DatasetHandle
from ..filter_ast import serialize_filter_ast
from ..filter_engine import FilterEngine
from .models import ChangeFromBaselineConfig


def _input(source: DatasetHandle) -> dict[str, object]:
    if source.kind not in {"sas", "merge"}:
        raise ValueError(f'Unsupported Change source kind "{source.kind}".')
    suffix = source.source_path.suffix.lower().lstrip(".")
    return {
        "kind": source.kind,
        "format": "merge" if source.kind == "merge" else suffix,
        "dataset": source.metadata.name,
        "source_path": None if source.kind == "merge" else str(source.source_path),
        "source_directory": None
        if source.kind == "merge"
        else str(source.source_path.parent),
    }


def _variables(source: DatasetHandle) -> dict[str, dict[str, object]]:
    return {
        variable.name: {
            "type": variable.kind,
            "label": variable.label,
            "length": variable.length,
            "format": variable.format,
        }
        for variable in source.metadata.variables
    }


def _filter(text: str, source: DatasetHandle) -> dict[str, object]:
    FilterEngine(source.metadata.variables).compile(text)
    return {
        "language": "sas_like",
        "text": text,
        "ast": serialize_filter_ast(text, source.metadata.variables),
    }


def build_change_from_baseline_configuration(
    source: DatasetHandle,
    config: ChangeFromBaselineConfig,
    *,
    resolved_treatment_levels: Iterable[dict[str, object]] = (),
    item_order: Iterable[object] = (),
) -> dict[str, object]:
    config.validate(source.metadata)
    labels = {str(value): label for value, label in config.item_labels}
    return {
        "type": "change_from_baseline_horizontal",
        "version": 1,
        "input": _input(source),
        "variables": {
            "subject": "USUBJID",
            "change": config.change_variable,
            "item": config.item_variable,
            "row": list(config.row_variables),
            "treatment": config.treatment_variable or None,
        },
        # Keep the editable role mapping compact while also preserving the
        # source metadata needed by standalone code generators.
        "variable_metadata": _variables(source),
        "dataset_filter": _filter(config.data_filter_text, source),
        "postbaseline_filter": _filter(config.postbaseline_filter_text, source),
        "item": {
            "labels": labels,
            "order_source": "where_literals_or_default",
            "resolved_order": list(item_order),
        },
        "row_sort": [
            {"variable": name, "direction": "asc" if ascending else "desc"}
            for name, ascending in config.row_sort
        ],
        "statistics": [key.upper() for key in config.statistics],
        "treatment": {
            "level_order": "resolved",
            "resolved_levels": list(resolved_treatment_levels),
        },
        "total": {
            "enabled": config.include_total,
            "method": "recompute_distinct_subjects",
        },
        "calculation": {
            "reference_engine": "python_change_from_baseline_v1",
            "change_source": "existing_dataset_variable",
            "small_n": "nonmissing_change_records",
            "large_n": "distinct_subjects",
            "subject_missing": "exclude",
            "change_missing": "exclude",
            "treatment_missing": "error",
            "duplicate_change_records": "warning_continue",
        },
        "display": {
            "rounding": "half_up",
            "maximum_decimals": 4,
            "statistic_decimals": {
                key.upper(): decimals for key, decimals in config.statistic_decimals
            },
        },
        "targets": {"sas": {"output_dataset": "work.change_from_baseline"}},
    }


def change_from_baseline_configuration_json(configuration: dict[str, object]) -> str:
    return json.dumps(configuration, indent=2, ensure_ascii=False) + "\n"


def write_change_from_baseline_configuration(
    path: Path,
    source: DatasetHandle,
    config: ChangeFromBaselineConfig,
    *,
    resolved_treatment_levels: Iterable[dict[str, object]] = (),
    item_order: Iterable[object] = (),
) -> dict[str, object]:
    configuration = build_change_from_baseline_configuration(
        source,
        config,
        resolved_treatment_levels=resolved_treatment_levels,
        item_order=item_order,
    )
    path.write_text(
        change_from_baseline_configuration_json(configuration), encoding="utf-8"
    )
    return configuration


__all__ = [
    "build_change_from_baseline_configuration",
    "change_from_baseline_configuration_json",
    "write_change_from_baseline_configuration",
]
