from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path

from ..domain import DatasetHandle, DatasetMetadata
from ..filter_ast import serialize_filter_ast
from ..filter_engine import FilterEngine
from .models import AeTableConfig


def _input(source: DatasetHandle) -> dict[str, object]:
    if source.kind not in {"sas", "merge"}:
        raise ValueError(f'AE Table configuration does not support source kind "{source.kind}".')
    merge = source.kind == "merge"
    suffix = source.source_path.suffix.lower().lstrip(".")
    if not merge and suffix not in {"sas7bdat", "xpt"}:
        raise ValueError(f'AE Table configuration does not support source format "{suffix}".')
    return {"kind": source.kind, "format": "merge" if merge else suffix,
            "dataset": source.metadata.name, "source_path": None if merge else str(source.source_path),
            "source_directory": None if merge else str(source.source_path.parent)}


def _variables(metadata: DatasetMetadata) -> dict[str, dict[str, object]]:
    return {v.name: {"type": v.kind, "label": v.label, "length": v.length, "format": v.format}
            for v in metadata.variables}


def _filter(text: str, variables) -> dict[str, object]:
    FilterEngine(variables).compile(text)
    return {"language": "sas_like", "text": text, "ast": serialize_filter_ast(text, variables)}


def _levels(levels: Iterable[tuple[str, object, str] | Mapping[str, object]]) -> list[dict[str, object]]:
    out = []
    seen = set()
    for level in levels:
        if isinstance(level, Mapping):
            value, label = level.get("value"), level.get("label", level.get("value"))
        else:
            _key, value, label = level
        if value is None:
            raise ValueError("Resolved treatment levels cannot contain missing values.")
        key = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
        if key in seen:
            raise ValueError("Resolved treatment levels must be unique.")
        seen.add(key)
        out.append({"value": value, "label": str(label)})
    return out


def build_ae_table_configuration(source: DatasetHandle, config: AeTableConfig,
                                 population: DatasetHandle | None = None,
                                 resolved_treatment_levels=(), resolved_hierarchy=()) -> dict[str, object]:
    config.validate(source.metadata, population.metadata if population else None)
    denominator: dict[str, object] = {"type": config.denominator.type}
    if config.denominator.type == "population":
        if population is None:
            raise ValueError("Population metadata is required for Population N.")
        denominator["population"] = {"input": _input(population), "variables": _variables(population.metadata),
                                      "filter": _filter(config.denominator.population_filter_text, population.metadata.variables)}
    hierarchy = [{"row_type": row["row_type"], "soc": row.get("soc"), "pt": row.get("pt"),
                  "item": row["item"], "indent": row["indent"]} for row in resolved_hierarchy]
    source_member = None if source.kind == "merge" else source.metadata.name.lower()
    return {
        "type": "ae_soc_pt_table", "version": 1, "input": _input(source),
        "variables": _variables(source.metadata),
        "dataset_filter": _filter(config.dataset_filter_text, source.metadata.variables),
        "hierarchy": {"type": "soc_pt", "soc_variable": config.soc_variable, "pt_variable": config.pt_variable,
                       "soc_missing": "exclude", "pt_missing": "exclude_from_pt_only"},
        "count": {"type": "distinct", "variable": "USUBJID"},
        "treatment": {"variable": config.treatment_variable, "missing_policy": "error", "level_order": "resolved",
                       "resolved_levels": _levels(resolved_treatment_levels)},
        "denominator": denominator,
        "rows": {"include_any_ae": config.include_any_ae, "any_ae_label": config.any_ae_label},
        "sort": {"soc": {"by": "total_frequency", "direction": "desc", "tie_breaker": "alphabetical"},
                 "pt": {"by": "total_frequency", "direction": "desc", "tie_breaker": "alphabetical"}},
        "resolved_hierarchy": hierarchy,
        "total": {"enabled": config.include_total, "method": "recompute_distinct_subjects"},
        "calculation": {"reference_engine": "python_ae_soc_pt_v1", "numerator": "distinct_subjects",
                         "soc_count": "recompute_distinct_subjects", "pt_count": "recompute_distinct_subjects",
                         "subject_missing": "exclude", "treatment_missing": "error",
                         "percent_method": "freq_divided_by_denom_times_100", "total_method": "recompute_distinct_subjects"},
        "display": {"percent_digits": config.percent_digits, "rounding": "half_up", "zero_denominator_display": "0 (—)", "pt_indent_spaces": 4},
        "targets": {"sas": {"source_library": "analysis", "source_member": source_member, "output_dataset": "work.ae_soc_pt"}},
    }


def ae_table_configuration_json(configuration: dict[str, object]) -> str:
    return json.dumps(configuration, indent=2, ensure_ascii=False) + "\n"


def write_ae_table_configuration(path: Path, source: DatasetHandle, config: AeTableConfig,
                                 population: DatasetHandle | None = None, resolved_treatment_levels=(), resolved_hierarchy=()) -> dict[str, object]:
    value = build_ae_table_configuration(source, config, population, resolved_treatment_levels, resolved_hierarchy)
    path.write_text(ae_table_configuration_json(value), encoding="utf-8")
    return value
