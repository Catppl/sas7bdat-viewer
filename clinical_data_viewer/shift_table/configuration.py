from __future__ import annotations

import json
from pathlib import Path

from ..domain import DatasetHandle
from ..filter_ast import serialize_filter_ast
from ..filter_engine import FilterEngine
from .models import OrderedLevel, ShiftTableConfig


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


def _levels(values: tuple[OrderedLevel, ...]) -> list[dict[str, object]]:
    return [
        {"raw": level.raw_value, "label": level.display_label, "order": level.order}
        for level in sorted(values, key=lambda item: item.order)
    ]


def build_shift_table_configuration(
    source: DatasetHandle,
    config: ShiftTableConfig,
    *,
    population: DatasetHandle | None = None,
) -> dict[str, object]:
    config.validate(source.metadata, population.metadata if population else None)
    population_source = population or source
    population_treatment = (
        config.population_treatment_variable or config.treatment_variable
    )
    return {
        "type": "shift_table",
        "version": 1,
        "input": _input(source),
        "variable_metadata": _variables(source),
        "dataset_filter": _filter(source, config.filter_text),
        "variables": {
            "subject": config.subject_variable,
            "parameter": config.parameter_variable,
            "visit": config.visit_variable,
            "treatment": config.treatment_variable,
            "baseline_grade": config.baseline_grade_variable,
            "post_grade": config.post_grade_variable,
            "analysis_flag": config.analysis_flag_variable,
            "analysis_flag_value": config.analysis_flag_value,
        },
        "ordering": {
            "parameters": _levels(config.parameters),
            "visits": _levels(config.visits),
            "treatments": _levels(config.treatments),
            "grade_levels": _levels(config.grade_levels),
        },
        "population": {
            "input": _input(population_source),
            "variable_metadata": _variables(population_source),
            "subject_variable": config.population_subject_variable,
            "treatment_variable": population_treatment,
            "filter": _filter(population_source, config.population_filter_text),
        },
        "calculation": {
            "reference_engine": "python_shift_table_v1",
            "count": "distinct_subjects",
            "denominator": "parameter_visit_treatment_complete_grade_subjects",
            "duplicate_policy": "warn_and_continue",
            "duplicates_confirmed": config.allow_duplicate_records,
            "missing_grade_policy": "exclude_with_summary",
            "total_policy": "independent_distinct_subject",
            "include_overall_total": config.include_overall_total,
        },
        "display": {
            "parameter_layout": "vertical_sections",
            "visit_layout": "vertical_sections",
            "treatment_layout": "across_columns",
            "percent_digits": config.percent_digits,
            "rounding": "half_up",
            "zero_denominator": "0 (-)",
            "footnote": False,
        },
        "drilldown": {
            "enabled": True,
            "analysis_cells": True,
            "population_headers": True,
        },
        "targets": {
            "sas": {
                "source_library": "adam",
                "source_member": None
                if source.kind == "merge"
                else source.metadata.name.casefold(),
                "population_library": "adam",
                "population_member": None
                if population_source.kind == "merge"
                else population_source.metadata.name.casefold(),
                "output_dataset": "work.shift_table",
                "long_output_dataset": "work.shift_table_long",
            }
        },
    }


def write_shift_table_configuration(
    path: Path,
    source: DatasetHandle,
    config: ShiftTableConfig,
    *,
    population: DatasetHandle | None = None,
) -> dict[str, object]:
    configuration = build_shift_table_configuration(
        source, config, population=population
    )
    path.write_text(
        json.dumps(configuration, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return configuration


__all__ = [
    "build_shift_table_configuration",
    "write_shift_table_configuration",
]
