from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from ..domain import DatasetHandle, DatasetMetadata, VariableMetadata
from ..filter_ast import serialize_filter_ast
from ..filter_engine import FilterEngine
from .models import RuleBasedConfig

_SOURCE_KINDS = frozenset({"sas", "merge"})


def _input_block(source: DatasetHandle) -> dict[str, object]:
    if source.kind not in _SOURCE_KINDS:
        raise ValueError(
            f'Rule-based configuration does not support source kind "{source.kind}".'
        )
    is_merge = source.kind == "merge"
    suffix = source.source_path.suffix.lower().lstrip(".")
    if not is_merge and suffix not in {"sas7bdat", "xpt"}:
        raise ValueError(
            f'Rule-based configuration does not support source format "{suffix}".'
        )
    return {
        "kind": source.kind,
        "format": "merge" if is_merge else (suffix or "sas7bdat"),
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


def _filter_block(
    text: str, variables: tuple[VariableMetadata, ...]
) -> dict[str, object]:
    # Compile first so configuration generation never emits a filter the
    # current Python engine cannot execute.  The AST is the language-neutral
    # contract; compiled SQL is deliberately not persisted.
    FilterEngine(variables).compile(text)
    return {
        "language": "sas_like",
        "text": text,
        "ast": serialize_filter_ast(text, variables),
    }


def _level(value: object, label: object) -> dict[str, object]:
    if value is None:
        raise ValueError("Resolved treatment levels cannot contain missing values.")
    return {"value": value, "label": str(label)}


def _resolved_levels(
    levels: Iterable[tuple[str, object, str] | Mapping[str, object]],
) -> list[dict[str, object]]:
    resolved: list[dict[str, object]] = []
    seen: set[str] = set()
    for entry in levels:
        if isinstance(entry, Mapping):
            if "value" not in entry:
                raise ValueError("Resolved treatment level is missing its value.")
            value = entry["value"]
            label = entry.get("label", value)
        else:
            try:
                _key, value, label = entry
            except (TypeError, ValueError) as error:
                raise ValueError("Invalid resolved treatment level.") from error
        key = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        if key in seen:
            raise ValueError("Resolved treatment levels must be unique.")
        seen.add(key)
        resolved.append(_level(value, label))
    return resolved


def build_rule_based_configuration(
    source: DatasetHandle,
    config: RuleBasedConfig,
    population: DatasetHandle | None = None,
    resolved_treatment_levels: Iterable[
        tuple[str, object, str] | Mapping[str, object]
    ] = (),
) -> dict[str, object]:
    """Build the strict Rule-based Table configuration contract v1."""

    config.validate(source.metadata, population.metadata if population else None)
    if config.subject_id_variable.casefold() != "usubjid":
        raise ValueError("Rule-based Table count variable must be USUBJID.")
    dataset_filter = _filter_block(
        config.dataset_filter_text, source.metadata.variables
    )
    rows = [
        {
            "id": row.row_id,
            "item": row.item,
            "indent": row.indent,
            "filter": _filter_block(row.row_filter_text, source.metadata.variables),
        }
        for row in config.rows
    ]
    treatment_levels = _resolved_levels(resolved_treatment_levels)
    denominator: dict[str, object]
    if config.denominator.type == "same_universe":
        denominator = {"type": "same_universe"}
    elif config.denominator.type == "nonmissing":
        denominator = {
            "type": "nonmissing",
            "analysis_value_variable": config.denominator.analysis_value_variable,
        }
    else:
        if population is None:
            raise ValueError("Population metadata is required for Population N.")
        denominator = {
            "type": "population",
            "population": {
                "input": _input_block(population),
                "variables": _variables(population.metadata),
                "filter": _filter_block(
                    config.denominator.population_filter_text,
                    population.metadata.variables,
                ),
            },
        }
    source_member = None if source.kind == "merge" else source.metadata.name.lower()
    configuration: dict[str, object] = {
        "type": "rule_based_table",
        "version": 1,
        "input": _input_block(source),
        "variables": _variables(source.metadata),
        "dataset_filter": dataset_filter,
        "rows": rows,
        "count": {"type": "distinct", "variable": "USUBJID"},
        "treatment": {
            "variable": config.treatment_variable,
            "missing_policy": "error",
            "level_order": "resolved",
            "resolved_levels": treatment_levels,
        },
        "denominator": denominator,
        "total": {
            "enabled": config.include_total,
            "method": "recompute_distinct_subjects",
        },
        "calculation": {
            "reference_engine": "python_rule_based_v1",
            "numerator": "distinct_subjects",
            "subject_missing": "exclude",
            "treatment_missing": "error",
            "percent_method": "freq_divided_by_denom_times_100",
            "total_method": "recompute_distinct_subjects",
        },
        "display": {
            "percent_digits": config.percent_digits,
            "rounding": "half_up",
            "zero_denominator_display": "0 (—)",
        },
        "targets": {
            "sas": {
                "source_library": "analysis",
                "source_member": source_member,
                "output_dataset": "work.rule_based_result",
            }
        },
    }
    return configuration


def rule_based_configuration_json(configuration: dict[str, object]) -> str:
    return json.dumps(configuration, indent=2, ensure_ascii=False) + "\n"


def write_rule_based_configuration(
    path: Path,
    source: DatasetHandle,
    config: RuleBasedConfig,
    population: DatasetHandle | None = None,
    resolved_treatment_levels: Iterable[
        tuple[str, object, str] | Mapping[str, object]
    ] = (),
) -> dict[str, object]:
    configuration = build_rule_based_configuration(
        source,
        config,
        population,
        resolved_treatment_levels,
    )
    path.write_text(
        rule_based_configuration_json(configuration),
        encoding="utf-8",
    )
    return configuration


__all__ = [
    "build_rule_based_configuration",
    "rule_based_configuration_json",
    "write_rule_based_configuration",
]
