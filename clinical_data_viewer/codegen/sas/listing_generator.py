from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from ...domain import VariableMetadata
from ...listing.expressions import infer_length
from ...listing.models import is_reserved_listing_name
from ...resources import resource_path
from .filter_renderer import sas_filter_expression, sas_name, sas_string


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(  # noqa: TRY004 - generator validation is user-facing
            f"Listing configuration block {name} must be an object."
        )
    return value


def _input(value: object, name: str) -> Mapping[str, object]:
    block = _mapping(value, name)
    if block.get("kind") == "merge":
        raise ValueError(
            "SAS code generation for merged Listing sources is not available yet."
        )
    if block.get("kind") != "sas":
        raise ValueError(f"{name}.kind must be sas.")
    if block.get("format") not in {"sas7bdat", "xpt"}:
        raise ValueError(f"Unsupported Listing {name} format.")
    return block


def _kind(expression: Mapping[str, object]) -> str:
    node = expression["type"]
    if node in {"variable", "literal"}:
        return str(expression["kind"])
    if node == "concat":
        return "character"
    if node in {"binary", "unary"}:
        return "numeric"
    return (
        "numeric"
        if str(expression["name"]).upper() in {"INPUT", "COALESCE"}
        else "character"
    )


def _variable_block(
    variables: Mapping[str, object], name: object
) -> Mapping[str, object]:
    target = str(name).casefold()
    for variable_name, value in variables.items():
        if str(variable_name).casefold() == target:
            return _mapping(value, "variable metadata")
    return {}


def _variable_metadata(
    variables: Mapping[str, object]
) -> dict[str, VariableMetadata]:
    resolved: dict[str, VariableMetadata] = {}
    for name, value in variables.items():
        block = _mapping(value, "variables")
        length = block.get("length")
        resolved[str(name).casefold()] = VariableMetadata(
            str(name),
            str(block.get("label") or ""),
            str(block.get("type") or block.get("kind") or "character"),
            int(length) if length is not None else None,
            str(block.get("format") or ""),
        )
    return resolved


def _literal(value: object) -> str:
    return sas_string(value) if isinstance(value, str) else repr(value)


def _expression(
    expression: Mapping[str, object],
    variables: Mapping[str, object],
    *,
    character: bool = False,
) -> str:
    node = str(expression["type"])
    if node == "literal":
        result = _literal(expression["value"])
    elif node == "variable":
        result = sas_name(expression["name"])
        if character and _kind(expression) == "numeric":
            variable = _variable_block(variables, expression["name"])
            format_text = str(variable.get("format") or "").strip()
            return (
                f"strip(put({result}, {format_text}))"
                if format_text
                else f"strip(put({result}, best32.))"
            )
    elif node == "unary":
        result = f"{expression['operator']}({_expression(_mapping(expression['value'], 'unary.value'), variables)})"
    elif node in {"concat", "binary"}:
        left = _expression(
            _mapping(expression["left"], "left"), variables, character=node == "concat"
        )
        right = _expression(
            _mapping(expression["right"], "right"),
            variables,
            character=node == "concat",
        )
        result = f"{left} {expression.get('operator', '||')} {right}"
    elif node == "function":
        name = str(expression["name"]).upper()
        args = [
            _mapping(value, "function argument") for value in expression["arguments"]
        ]
        if name == "PUT":
            result = (
                f"strip(put({_expression(args[0], variables)}, {args[1]['value']}))"
            )
        elif name == "INPUT":
            result = f"input({_expression(args[0], variables, character=True)}, {args[1]['value']})"
        else:
            rendered = ", ".join(
                _expression(
                    arg,
                    variables,
                    character=name
                    in {
                        "CATS",
                        "CATX",
                        "STRIP",
                        "UPCASE",
                        "LOWCASE",
                        "SUBSTR",
                        "SCAN",
                        "COALESCEC",
                    },
                )
                for arg in args
            )
            result = f"{name.lower()}({rendered})"
    else:
        raise ValueError(f"Unsupported Listing expression AST node: {node}")
    if character and _kind(expression) == "numeric":
        return f"strip(put({result}, best32.))"
    return result


def _division_denominator(
    expression: Mapping[str, object], variables: Mapping[str, object]
) -> str | None:
    """Return a safe DATA-step guard for the supported post-process form."""
    if expression.get("type") != "binary" or expression.get("operator") != "/":
        return None
    return _expression(_mapping(expression["right"], "binary.right"), variables)


_FORMAT_WIDTH = re.compile(r"(?P<width>\d+)(?:\.\d+)?\.?$")


def _format_width(format_text: object) -> int | None:
    match = _FORMAT_WIDTH.search(str(format_text or "").strip())
    return int(match.group("width")) if match is not None else None


def _report_width_weight(column: Mapping[str, object]) -> int:
    """Estimate display characters without leaking sizing logic into SAS."""
    label_length = len(str(column.get("label") or column.get("output") or ""))
    if column.get("kind") == "character":
        content_length = int(column.get("length") or 20)
    else:
        content_length = _format_width(column.get("format")) or 12
    return max(8, min(80, max(label_length, content_length)))


def _allocate_width_percentages(weights: list[int]) -> list[str]:
    """Allocate metadata-weighted percentages totalling exactly 99%."""
    count = len(weights)
    if count == 0:
        return []
    increment = Decimal("0.01")
    total = Decimal(99)
    equal_width = (total / count).quantize(increment, rounding=ROUND_DOWN)
    if equal_width <= 0:
        raise ValueError("Listing has too many visible PROC REPORT columns.")

    minimum = min(Decimal(5), equal_width)
    widths = [minimum] * count
    remaining_width = total - sum(widths, Decimal(0))
    safe_weights = [max(1, int(weight)) for weight in weights]
    total_weight = Decimal(sum(safe_weights))
    raw_extras = [
        remaining_width * Decimal(weight) / total_weight for weight in safe_weights
    ]
    extras = [
        extra.quantize(increment, rounding=ROUND_DOWN) for extra in raw_extras
    ]
    widths = [width + extra for width, extra in zip(widths, extras, strict=True)]

    remaining_steps = int((total - sum(widths, Decimal(0))) / increment)
    remainder_order = sorted(
        range(count),
        key=lambda index: (raw_extras[index] - extras[index], -index),
        reverse=True,
    )
    for index in remainder_order[:remaining_steps]:
        widths[index] += increment
    return [format(value, "f").rstrip("0").rstrip(".") for value in widths]


class SasListingGenerator:
    """Render readable, reusable SAS from Listing JSON v1."""

    def __init__(self, template_directory: Path | None = None) -> None:
        directory = template_directory or resource_path(
            "clinical_data_viewer/codegen/sas/templates"
        )
        self.environment = Environment(
            loader=FileSystemLoader(str(directory)),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
        )
        self.environment.filters["sas_name"] = sas_name
        self.environment.filters["sas_string"] = sas_string

    def generate(self, configuration: Mapping[str, object]) -> str:
        if configuration.get("type") != "listing" or configuration.get("version") != 1:
            raise ValueError("SAS generation requires Listing configuration v1.")
        required = {
            "input",
            "variables",
            "merge_adsl",
            "data_filter",
            "columns",
            "report",
            "targets",
        }
        if missing := sorted(required - set(configuration)):
            raise ValueError("Listing configuration is missing: " + ", ".join(missing))
        source = _input(configuration["input"], "input")
        variables = _mapping(configuration["variables"], "variables")
        merge = _mapping(configuration["merge_adsl"], "merge_adsl")
        adsl = (
            _input(merge["input"], "merge_adsl.input") if merge.get("enabled") else None
        )
        merge_variables = _mapping(merge.get("variables", {}), "merge_adsl.variables")
        source_variables = _mapping(
            merge.get("source_variables", variables), "merge_adsl.source_variables"
        )
        keep = {str(value).casefold() for value in merge.get("keep", [])}
        drop = {str(value).casefold() for value in merge.get("drop", [])}
        by = str((merge.get("by") or ["USUBJID"])[0])
        rename_map = {
            str(old).casefold(): str(new)
            for old, new in _mapping(
                merge.get("rename_map", {}), "merge_adsl.rename_map"
            ).items()
        }
        source_names = {str(name).casefold() for name in source_variables}
        adsl_projection = []
        if adsl:
            for name in merge_variables:
                key = str(name).casefold()
                if key == by.casefold():
                    continue
                if (keep and key not in keep) or key in drop:
                    continue
                if key in source_names:
                    if merge.get("duplicate_policy") == "ignore":
                        continue
                    output = rename_map.get(key)
                    if not output:
                        raise ValueError(f"Unresolved duplicate ADSL variable: {name}.")
                else:
                    output = str(name)
                if is_reserved_listing_name(output):
                    raise ValueError(
                        f'ADSL rename target "{output}" uses a reserved Listing name.'
                    )
                adsl_projection.append({"source": str(name), "output": output})
        data_filter = _mapping(configuration["data_filter"], "data_filter")
        if data_filter.get("language") != "sas_like":
            raise ValueError("Listing Data Filter must use sas_like.")
        columns = list(configuration["columns"])
        if not columns:
            raise ValueError("Listing configuration has no columns.")
        variable_metadata = _variable_metadata(variables)
        prepared = []
        visible_report_columns = []
        sort_columns = []
        for index, raw in enumerate(columns, 1):
            column = _mapping(raw, "column")
            expression = _mapping(
                _mapping(column["expression"], "expression")["ast"], "expression.ast"
            )
            output = str(column["output_name"])
            if is_reserved_listing_name(output):
                raise ValueError(
                    f'Listing Output Name "{output}" uses a reserved name.'
                )
            report = _mapping(column["report"], "report")
            sort = _mapping(column["sort"], "sort")
            post = _mapping(column["post_process"], "post_process")
            kind = _kind(expression)
            format_text = str(column.get("format") or "").strip()
            if not format_text and expression.get("type") == "variable":
                format_text = variable_metadata.get(
                    str(expression.get("name", "")).casefold(),
                    VariableMetadata("_"),
                ).format
            item = {
                "index": index,
                "output": output,
                "label": str(column.get("label") or output),
                "format": format_text,
                "kind": kind,
                "length": infer_length(expression, variable_metadata),
                "expression": _expression(expression, variables),
                "char_expression": _expression(expression, variables, character=True),
                "report": bool(report.get("include")),
                "report_type": str(report.get("type", "display")).lower(),
                "division_missing": post.get("division_by_zero") == "missing",
                "division_denominator": _division_denominator(expression, variables),
            }
            if item["division_missing"] and item["division_denominator"] is None:
                raise ValueError(
                    "Division-by-zero post-process currently requires a direct A / B expression."
                )
            prepared.append(item)
            if item["report"]:
                visible_report_columns.append(item)
            if sort.get("order") is not None:
                sort_columns.append(
                    {
                        **item,
                        "order": int(sort["order"]),
                        "direction": str(sort.get("direction", "asc")).lower(),
                    }
                )
        if not visible_report_columns:
            raise ValueError("Listing needs at least one PROC REPORT column.")
        sort_columns.sort(key=lambda item: item["order"])
        sort_indexes = {item["index"] for item in sort_columns}
        report_columns = []
        for item in sort_columns:
            report_columns.append(
                {
                    **item,
                    "report_type": "order",
                    "noprint": not item["report"],
                }
            )
        report_columns.extend(
            {**item, "noprint": False}
            for item in visible_report_columns
            if item["index"] not in sort_indexes
        )
        line_size = int(
            _mapping(configuration["report"], "report").get("line_size", 132)
        )
        displayed_report_columns = [
            item for item in report_columns if not item["noprint"]
        ]
        for item, width_percent in zip(
            displayed_report_columns,
            _allocate_width_percentages(
                [_report_width_weight(item) for item in displayed_report_columns]
            ),
            strict=True,
        ):
            item["width_percent"] = width_percent
        context = {
            "source": source,
            "adsl": adsl,
            "merge": merge,
            "by": by,
            "adsl_projection": adsl_projection,
            "columns": prepared,
            "report_columns": report_columns,
            "sort_columns": sort_columns,
            "data_filter": sas_filter_expression(data_filter.get("ast")),
            "source_library": _mapping(
                _mapping(configuration["targets"], "targets")["sas"], "targets.sas"
            ).get("source_library", "analysis"),
            "source_member": _mapping(
                _mapping(configuration["targets"], "targets")["sas"], "targets.sas"
            ).get("source_member"),
            "output_dataset": str(
                _mapping(
                    _mapping(configuration["targets"], "targets")["sas"],
                    "targets.sas",
                ).get("output_dataset")
                or "work.listing"
            ),
            "line_size": line_size,
        }
        try:
            return self.environment.get_template("listing.sas.j2").render(**context)
        except (OSError, TemplateError) as error:
            raise ValueError(
                f"Unable to render Listing SAS template: {error}"
            ) from error
