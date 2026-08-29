from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from ..domain import DatasetHandle, DatasetMetadata, VariableMetadata
from ..filter_ast import serialize_filter_ast
from ..filter_engine import FilterEngine
from .models import CategoricalConfig

_SOURCE_KINDS = frozenset({"sas", "merge"})


def _input(source: DatasetHandle) -> dict[str, object]:
    if source.kind not in _SOURCE_KINDS:
        raise ValueError(
            f'Categorical configuration does not support source kind "{source.kind}".'
        )
    is_merge = source.kind == "merge"
    suffix = source.source_path.suffix.lower().lstrip(".")
    if not is_merge and suffix not in {"sas7bdat", "xpt"}:
        raise ValueError(
            f'Categorical configuration does not support source format "{suffix}".'
        )
    return {
        "kind": source.kind,
        "format": "merge" if is_merge else suffix,
        "dataset": source.metadata.name,
        "source_path": None if is_merge else str(source.source_path),
        "source_directory": None if is_merge else str(source.source_path.parent),
    }


def _variables(metadata: DatasetMetadata) -> dict[str, dict[str, object]]:
    return {
        variable.name: {
            "type": variable.kind,
            "label": variable.label,
            "length": variable.length,
            "format": variable.format,
        }
        for variable in metadata.variables
    }


def _filter(text: str, variables: tuple[VariableMetadata, ...]) -> dict[str, object]:
    FilterEngine(variables).compile(text)
    return {
        "language": "sas_like",
        "text": text,
        "ast": serialize_filter_ast(text, variables),
    }


def _levels(
    levels: Iterable[tuple[str, object, str] | Mapping[str, object]],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for level in levels:
        if isinstance(level, Mapping):
            if "value" not in level:
                raise ValueError("Resolved treatment level is missing its value.")
            value = level["value"]
            label = level.get("label", value)
        else:
            try:
                _key, value, label = level
            except (TypeError, ValueError) as error:
                raise ValueError("Invalid resolved treatment level.") from error
        if value is None:
            raise ValueError("Resolved treatment levels cannot contain missing values.")
        key = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        if key in seen:
            raise ValueError("Resolved treatment levels must be unique.")
        seen.add(key)
        result.append({"value": value, "label": str(label)})
    return result


def build_categorical_configuration(
    source: DatasetHandle,
    config: CategoricalConfig,
    population: DatasetHandle | None = None,
    resolved_treatment_levels: Iterable[
        tuple[str, object, str] | Mapping[str, object]
    ] = (),
) -> dict[str, object]:
    """Build the fixed Categorical Table configuration contract v1."""

    config.validate(source.metadata, population.metadata if population else None)
    count_type = {
        "distinct_subject": "distinct_subjects",
        "record": "records",
    }[config.count_type]
    subject_missing = (
        "exclude_for_eligibility"
        if config.denominator.type == "baseline_postbaseline"
        else "exclude"
        if config.count_type == "distinct_subject"
        else "not_applicable"
    )
    items = [
        {
            "variable": item.variable,
            "label": item.label or item.variable,
            "context_variables": list(item.context_variables),
            "missing_level": {
                "include": item.include_missing_level,
                "label": "(Missing)",
            },
            "level_order": {"method": "runtime_value_ascending"},
        }
        for item in config.items
    ]
    denominator: dict[str, object]
    if config.denominator.type == "population":
        if population is None:
            raise ValueError("Population metadata is required for Population N.")
        population_treatment = (
            config.denominator.population_treatment_variable
            or config.treatment_variable
        )
        denominator = {
            "type": "population",
            "population": {
                "input": _input(population),
                "variables": _variables(population.metadata),
                "treatment_variable": population_treatment,
                "filter": _filter(
                    config.denominator.population_filter_text,
                    population.metadata.variables,
                ),
            },
        }
    elif config.denominator.type == "nonmissing":
        denominator = {
            "type": "nonmissing",
            "analysis_value_variable": config.denominator.analysis_value_variable,
            "base_filter": "numerator.filter",
        }
    else:
        denominator = {
            "type": "baseline_postbaseline",
            "analysis_value_variable": config.denominator.analysis_value_variable,
            "baseline_filter": _filter(
                config.denominator.baseline_filter_text,
                source.metadata.variables,
            ),
            "postbaseline_filter": _filter(
                config.denominator.postbaseline_filter_text,
                source.metadata.variables,
            ),
            "eligibility": {
                "base_filter": "numerator.filter",
                "match_variables": "treatment_subject_and_item_context",
                "baseline_analysis_nonmissing": True,
                "postbaseline_analysis_nonmissing": True,
                "numerator_source": "eligible_postbaseline_records",
                "denominator_source": "eligible_postbaseline_records",
            },
        }
    source_member = None if source.kind == "merge" else source.metadata.name.lower()
    sas_target: dict[str, object] = {
        "source_library": "analysis",
        "source_member": source_member,
        "output_dataset": "work.cat_result",
        "long_output_dataset": "work.cat_long",
    }
    if config.denominator.type == "population":
        assert population is not None
        sas_target.update(
            {
                "population_library": "pop",
                "population_member": (
                    None if population.kind == "merge" else population.metadata.name.lower()
                ),
            }
        )
    return {
        "type": "categorical_table",
        "version": 1,
        "input": _input(source),
        "variables": _variables(source.metadata),
        "numerator": {
            "filter": _filter(config.numerator_filter_text, source.metadata.variables)
        },
        "items": items,
        "count": {
            "type": count_type,
            "subject_variable": config.subject_id_variable,
            "subject_missing": subject_missing,
        },
        "treatment": {
            "source_variable": config.treatment_variable,
            "missing_policy": "error",
            "level_order": "resolved",
            "resolved_levels": _levels(resolved_treatment_levels),
        },
        "denominator": denominator,
        "total": {
            "enabled": config.include_total,
            "method": "recompute_from_analysis_universe",
        },
        "sort": {
            "items": "configured_order",
            "contexts": {
                "method": "runtime_value_ascending",
                "character_collation": "case_insensitive",
                "numeric_order": "numeric",
                "missing": "last",
            },
            "levels": {
                "method": "runtime_value_ascending",
                "character_collation": "case_insensitive",
                "numeric_order": "numeric",
                "missing": "last",
            },
        },
        "calculation": {
            "reference_engine": "python_categorical_v1",
            "numerator": count_type,
            "numerator_filter_scope": "source_only",
            "denominator_filter_scope": "independent",
            "item_filter_applies_to_denominator": False,
            "subject_missing": subject_missing,
            "treatment_missing": "error",
            "percent_method": "freq_divided_by_denom_times_100",
            "total_method": "recompute_from_analysis_universe",
        },
        "display": {
            "percent_digits": config.percent_digits,
            "rounding": "half_up",
            "zero_denominator_display": "0 (—)",
            "level_indent_spaces": 4,
            "header_rows": True,
        },
        "output": {
            "layout": "wide_and_long",
            "wide": {
                "item_column": "item",
                "item_label": "Event",
                "treatment_column_pattern": "col{index}",
                "treatment_label_pattern": "{label} n (%)",
            },
            "long": {
                "columns": [
                    "item_order",
                    "item_variable",
                    "item_label",
                    "context",
                    "level",
                    "treatment",
                    "trt_order",
                    "freq",
                    "denom",
                    "pct",
                ]
            },
        },
        "targets": {"sas": sas_target},
    }


def categorical_configuration_json(configuration: dict[str, object]) -> str:
    return json.dumps(configuration, indent=2, ensure_ascii=False) + "\n"


def write_categorical_configuration(
    path: Path,
    source: DatasetHandle,
    config: CategoricalConfig,
    population: DatasetHandle | None = None,
    resolved_treatment_levels: Iterable[
        tuple[str, object, str] | Mapping[str, object]
    ] = (),
) -> dict[str, object]:
    configuration = build_categorical_configuration(
        source,
        config,
        population,
        resolved_treatment_levels,
    )
    path.write_text(categorical_configuration_json(configuration), encoding="utf-8")
    return configuration


__all__ = [
    "build_categorical_configuration",
    "categorical_configuration_json",
    "write_categorical_configuration",
]
