from __future__ import annotations

import json
from pathlib import Path

from ..domain import DatasetHandle, DatasetMetadata
from ..filter_ast import serialize_filter_ast
from .expressions import parse_expression
from .models import ListingConfig


def _input(handle: DatasetHandle) -> dict[str, object]:
    is_merge = handle.kind == "merge"
    suffix = handle.source_path.suffix.lower().lstrip(".")
    return {
        "kind": handle.kind,
        "format": "merge" if is_merge else suffix,
        "dataset": handle.metadata.name,
        "source_path": None if is_merge else str(handle.source_path),
        "source_directory": None if is_merge else str(handle.source_path.parent),
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


def build_listing_configuration(
    source: DatasetHandle,
    config: ListingConfig,
    resolved_metadata: DatasetMetadata,
    adsl: DatasetHandle | None = None,
) -> dict[str, object]:
    config.validate_basic()
    merge = config.merge_adsl
    merge_block: dict[str, object] = {
        "enabled": merge.enabled,
        "by": [merge.by_variable],
        "keep": list(merge.keep),
        "drop": list(merge.drop),
        "duplicate_policy": merge.duplicate_policy,
        "rename_map": dict(merge.rename_map),
    }
    if merge.enabled and adsl is not None:
        merge_block["input"] = _input(adsl)
        merge_block["variables"] = _variables(adsl.metadata)
        merge_block["source_variables"] = _variables(source.metadata)
    columns = []
    for column in config.columns:
        expression = parse_expression(
            column.expression_text, resolved_metadata.variables
        )
        columns.append(
            {
                "expression": {"text": column.expression_text, "ast": expression},
                "output_name": column.output_name,
                "label": column.label,
                "format": column.format,
                "sort": {
                    "order": column.sort_order,
                    "direction": column.sort_direction.casefold(),
                },
                "report": {
                    "include": column.include_in_report,
                    "type": column.report_type.casefold(),
                    "width_percent": 0,
                },
                "post_process": {
                    "division_by_zero": "missing"
                    if column.division_by_zero_missing
                    else "error"
                },
            }
        )
    return {
        "type": "listing",
        "version": 1,
        "input": _input(source),
        "variables": _variables(resolved_metadata),
        "merge_adsl": merge_block,
        "data_filter": {
            "language": "sas_like",
            "text": config.data_filter_text,
            "ast": serialize_filter_ast(
                config.data_filter_text, resolved_metadata.variables
            ),
        },
        "columns": columns,
        "sort": {"stable_tie_breaker": "_listing_row"},
        "report": {
            "line_size": config.line_size,
            "width_method": "metadata_weighted_visible_columns",
        },
        "calculation": {
            "reference_engine": "python_listing_v1",
            "output_type": "expression_inferred",
            "character_length": "metadata_or_expression_inferred",
        },
        "targets": {
            "sas": {
                "source_library": "analysis",
                "source_member": None
                if source.kind == "merge"
                else source.metadata.name.lower(),
                "output_dataset": "work.listing_sorted",
            }
        },
    }


def listing_configuration_json(configuration: dict[str, object]) -> str:
    return json.dumps(configuration, indent=2, ensure_ascii=False) + "\n"


def write_listing_configuration(path: Path, *args, **kwargs) -> dict[str, object]:
    configuration = build_listing_configuration(*args, **kwargs)
    path.write_text(listing_configuration_json(configuration), encoding="utf-8")
    return configuration
