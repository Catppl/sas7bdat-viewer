from __future__ import annotations

import json
from pathlib import Path

from ..domain import DatasetHandle
from ..filter_ast import serialize_filter_ast
from ..filter_engine import FilterEngine
from .models import STATISTIC_COLUMN_NAMES, ProcMeansConfig


def build_proc_means_configuration(
    source: DatasetHandle, config: ProcMeansConfig
) -> dict[str, object]:
    config.validate(source.metadata)
    FilterEngine(source.metadata.variables).compile(config.filter_text)
    subject_variable = next(
        (
            variable.name
            for variable in source.metadata.variables
            if variable.name.casefold() == "usubjid"
        ),
        None,
    )
    confidence = float(config.confidence)
    source_member = source.metadata.name.lower()
    return {
        "type": "proc_means",
        "version": 3,
        "input": {
            "format": "sas7bdat",
            "dataset": source.metadata.name,
            "source_path": str(source.source_path),
            "source_directory": str(source.source_path.parent),
        },
        "variables": {
            variable.name: {
                "type": variable.kind,
                "label": variable.label,
                "length": variable.length,
                "format": variable.format,
            }
            for variable in source.metadata.variables
        },
        "filter": {
            "language": "sas_like",
            "text": config.filter_text,
            "ast": serialize_filter_ast(config.filter_text, source.metadata.variables),
        },
        "analysis_variables": list(config.analysis_variables),
        "by_variables": list(config.by_variables),
        "class_variables": list(config.class_variables),
        "statistics": [STATISTIC_COLUMN_NAMES[key] for key in config.statistics],
        "calculation": {
            "reference_engine": "python_proc_means_v1",
            "mean_method": "python_math_fsum",
            "sd_method": "sample_n_minus_1",
            "standard_error_method": "sd_divided_by_sqrt_n",
            "quantile_method": "python_qntldef5_v1",
            "confidence_interval_method": "student_t_two_sided",
            "confidence": confidence,
            "alpha": round(1.0 - confidence, 12),
            "group_missing": {
                "numeric": [None],
                "character": [None, ""],
            },
            "n": "nonmissing_analysis_values",
            "nmiss": "group_row_count_minus_n",
            "subject_count": {
                "variable": subject_variable,
                "distinct": True,
                "requires_nonmissing_analysis": True,
                "requires_nonmissing_subject": True,
            },
            "include_missing_by": True,
            "include_missing_class": True,
            "class_combinations": "nway_only",
            "empty_ungrouped_result": True,
        },
        "output": {
            "layout": "long",
            "numeric_values": "full_precision",
            "display_values": "formatted",
        },
        "display": {
            "decimal_group_variables": list(config.decimal_group_variables),
            "decimal_inference": {
                "mode": "runtime_from_filtered_input",
                "reference_engine": "python",
                "method": "observed_decimal_places_v1",
                "aggregate": "maximum",
                "maximum_decimals": 4,
            },
            "decimal_offsets": {
                STATISTIC_COLUMN_NAMES.get(key, key.upper()): value
                for key, value in config.decimal_offsets
            },
            "rounding": {
                "mode": "half_up",
                "preserve_trailing_zeros": True,
            },
        },
        "targets": {
            "sas": {
                "source_library": "analysis",
                "source_member": source_member,
                "output_dataset": "work.proc_means_result",
            },
            "r": {
                "output_object": "proc_means_result",
            },
        },
    }


def proc_means_configuration_json(configuration: dict[str, object]) -> str:
    return json.dumps(configuration, indent=2, ensure_ascii=False) + "\n"


def write_proc_means_configuration(
    path: Path, source: DatasetHandle, config: ProcMeansConfig
) -> dict[str, object]:
    configuration = build_proc_means_configuration(source, config)
    path.write_text(
        proc_means_configuration_json(configuration),
        encoding="utf-8",
    )
    return configuration
