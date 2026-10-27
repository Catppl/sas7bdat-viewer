from __future__ import annotations

import json
from pathlib import Path

from ..domain import DatasetHandle
from ..filter_ast import serialize_filter_ast
from ..filter_engine import FilterEngine
from .models import ProcMeansVerticalConfig


def _input(handle: DatasetHandle | None) -> dict[str, object] | None:
    if handle is None:
        return None
    return {
        "kind": handle.kind,
        "format": "merge" if handle.kind == "merge" else handle.source_path.suffix.lower().lstrip("."),
        "dataset": handle.metadata.name,
        "source_path": None if handle.kind == "merge" else str(handle.source_path),
        "source_directory": None if handle.kind == "merge" else str(handle.source_path.parent),
    }


def _variables(handle: DatasetHandle) -> dict[str, dict[str, object]]:
    return {
        variable.name: {
            "type": variable.kind,
            "label": variable.label,
            "length": variable.length,
            "format": variable.format,
        }
        for variable in handle.metadata.variables
    }


def _filter(text: str, handle: DatasetHandle) -> dict[str, object]:
    FilterEngine(handle.metadata.variables).compile(text)
    return {
        "language": "sas_like",
        "text": text,
        "ast": serialize_filter_ast(text, handle.metadata.variables),
    }


def build_proc_means_vertical_configuration(
    source: DatasetHandle,
    config: ProcMeansVerticalConfig,
    population: DatasetHandle | None,
) -> dict[str, object]:
    config.validate(source.metadata, population.metadata if population else None)
    labels = {value.casefold(): label for value, label in config.item_labels}
    source_member = source.metadata.name.casefold()
    population_member = population.metadata.name.casefold() if population else source_member
    return {
        "type": "proc_means_vertical",
        "version": 1,
        "input": _input(source),
        "population": {
            "input": _input(population),
            "variables": _variables(population) if population else {},
            "filter": _filter(config.population_filter_text, population)
            if population
            else {"language": "sas_like", "text": "", "ast": {}},
        },
        "variables": {
            "analysis": list(config.analysis_variables),
            "group": list(config.group_variables),
            "row_group": list(config.row_group_variables),
            "treatment": config.treatment_variable or None,
            "population_treatment": config.population_treatment_variable or None,
        },
        "variable_metadata": _variables(source),
        "dataset_filter": _filter(config.filter_text, source),
        "item": {"labels": labels, "order": list(config.analysis_variables)},
        "statistics": list(config.statistics),
        "total": {"enabled": config.include_total, "method": "recompute"},
        "display": {
            # Keep the existing JSON value for the original column layout;
            # ``rows`` is the new opt-in layout.
            "layout": "rows" if config.output_layout == "rows" else "vertical",
            "rounding": "half_up",
            "maximum_decimals": 4,
            "base_decimals": dict(config.base_decimals),
            "statistic_decimals": dict(config.statistic_decimals),
        },
        "calculation": {
            "reference_engine": "python_proc_means_vertical_v1",
            "confidence": config.confidence,
            "alpha": round(1.0 - config.confidence, 12),
            "n": "nonmissing_analysis_records",
            "N": "distinct_subjects_from_population",
            "total_method": "recompute_distinct_subjects",
        },
        "targets": {
            "sas": {
                "source_library": "analysis",
                "source_member": source_member,
                "population_library": "analysis",
                "population_member": population_member,
                "output_dataset": "work.pm_vertical",
            }
        },
    }


def write_proc_means_vertical_configuration(
    path: Path,
    source: DatasetHandle,
    config: ProcMeansVerticalConfig,
    population: DatasetHandle | None,
) -> dict[str, object]:
    configuration = build_proc_means_vertical_configuration(source, config, population)
    path.write_text(json.dumps(configuration, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return configuration


__all__ = [
    "build_proc_means_vertical_configuration",
    "write_proc_means_vertical_configuration",
]
