from __future__ import annotations

import json
from pathlib import Path

from ..domain import DatasetHandle
from ..filter_ast import serialize_filter_ast
from ..filter_engine import FilterEngine
from .models import (
    BaselineCategoricalItem,
    BaselineCharacteristicsConfig,
    BaselineContinuousItem,
)


def _input(source: DatasetHandle) -> dict[str, object]:
    is_merge = source.kind == "merge"
    return {
        "kind": source.kind,
        "format": "merge"
        if is_merge
        else source.source_path.suffix.lower().lstrip("."),
        "dataset": source.metadata.name,
        "source_path": None if is_merge else str(source.source_path),
        "source_directory": None if is_merge else str(source.source_path.parent),
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


def _filter(source: DatasetHandle, text: str) -> dict[str, object]:
    FilterEngine(source.metadata.variables).compile(text)
    return {
        "language": "sas_like",
        "text": text,
        "ast": serialize_filter_ast(text, source.metadata.variables),
    }


def build_baseline_characteristics_configuration(
    source: DatasetHandle,
    config: BaselineCharacteristicsConfig,
    *,
    runtime_header_n: int | None = None,
) -> dict[str, object]:
    config.validate(source.metadata)
    metadata = {
        variable.name.casefold(): variable for variable in source.metadata.variables
    }
    items: list[dict[str, object]] = []
    for order, item in enumerate(config.items, start=1):
        variable = metadata[item.variable.casefold()]
        common: dict[str, object] = {
            "order": order,
            "variable": variable.name,
            "storage_type": variable.kind,
            "label": item.label,
        }
        if isinstance(item, BaselineCategoricalItem):
            level_labels = dict(item.level_labels)
            items.append(
                {
                    **common,
                    "role": "categorical",
                    "count": "distinct_subjects",
                    "denominator": "header_n",
                    "missing": "exclude",
                    "level_order": {
                        "method": item.level_sort_method,
                        **(
                            {"values": list(item.level_order)}
                            if item.level_sort_method == "custom"
                            else {}
                        ),
                        **(
                            {
                                "labels": [
                                    {"value": value, "label": label}
                                    for value, label in item.level_labels
                                ]
                            }
                            if level_labels
                            else {}
                        ),
                    },
                    "percent_digits": item.percent_digits,
                }
            )
        else:
            assert isinstance(item, BaselineContinuousItem)
            items.append(
                {
                    **common,
                    "role": "continuous",
                    "unit": item.unit,
                    "statistics": [
                        *item.statistics,
                    ],
                    "statistic_decimals": dict(item.statistic_decimals),
                    "missing": "exclude_from_statistics",
                }
            )

    source_member = None if source.kind == "merge" else source.metadata.name.casefold()
    return {
        "type": "baseline_characteristics",
        "version": 1,
        "input": _input(source),
        "variable_metadata": _variables(source),
        "dataset_filter": _filter(source, config.filter_text),
        "analysis_universe": {
            "subject_variable": config.subject_id_variable,
            "treatment_variable": config.treatment_variable or None,
            "subject_missing": "error",
            "duplicate_subject_rows": "error",
            "runtime_header_n": runtime_header_n,
        },
        "items": items,
        "calculation": {
            "reference_engine": "python_baseline_characteristics_v1",
            "categorical_count": "distinct_subjects",
            "categorical_denominator": "header_n",
            "continuous_n": "distinct_subjects_with_nonmissing_value",
            "continuous_statistics": "raw_nonmissing_values",
            "standard_deviation": "sample_n_minus_1",
            "quantiles": "qntldef5",
        },
        "sorting": {
            "items": "configured_order",
            "categorical_levels": "configured_per_item",
            "continuous_statistics": "fixed_shell_order",
        },
        "display": {
            "layout": "treatment_columns" if config.treatment_variable else "single_summary_column",
            "header_label": config.header_label,
            "rounding": "half_up",
            "maximum_decimals": 4,
            "missing_statistic": "-",
            "zero_denominator": "0 (-)",
            "footnote": False,
        },
        "drilldown": {
            "categorical": ["numerator_subjects", "denominator_subjects"],
            "continuous": "nonmissing_source_rows",
        },
        "output": {
            "layout": "wide_and_long",
            "wide_columns": ["ROW_LABEL", "VALUE"],
            "long_dataset": "baseline_characteristics_long",
        },
        "targets": {
            "sas": {
                "source_library": "analysis",
                "source_member": source_member,
                "output_dataset": "work.baseline_characteristics",
                "long_output_dataset": "work.baseline_characteristics_long",
            }
        },
    }


def write_baseline_characteristics_configuration(
    path: Path,
    source: DatasetHandle,
    config: BaselineCharacteristicsConfig,
    *,
    runtime_header_n: int | None = None,
) -> dict[str, object]:
    configuration = build_baseline_characteristics_configuration(
        source,
        config,
        runtime_header_n=runtime_header_n,
    )
    path.write_text(
        json.dumps(configuration, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return configuration


__all__ = [
    "build_baseline_characteristics_configuration",
    "write_baseline_characteristics_configuration",
]
