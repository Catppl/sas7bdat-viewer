from __future__ import annotations

import json
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateError

from ...resources import resource_path

_STATISTIC_KEYS = {
    "SUBJECT_N": "subjects",
    "N": "n",
    "NMISS": "nmiss",
    "MEAN": "mean",
    "SD": "std",
    "SE": "stderr",
    "MEDIAN": "median",
    "Q1": "q1",
    "Q3": "q3",
    "MIN": "min",
    "MAX": "max",
    "LCLM": "lclm",
    "UCLM": "uclm",
}


def _r_string(value: object) -> str:
    """Render a JSON string literal, which is also a safe R string literal."""
    return json.dumps(str(value), ensure_ascii=False)


def _r_literal(value: object) -> str:
    if isinstance(value, str):
        return _r_string(value)
    if value is None:
        return "NA"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return repr(value)


def _r_column(name: object) -> str:
    return f"data[[{_r_string(name)}]]"


def _r_filter_operand(operand: dict[str, object]) -> str:
    if operand["type"] == "variable":
        return _r_column(operand["name"])
    return _r_literal(operand["value"])


def _r_filter_expression(
    expression: dict[str, object] | None,
    variable_types: dict[str, str],
) -> str:
    """Render the saved Python WHERE AST without reparsing its display text."""
    if expression is None:
        return "rep(TRUE, nrow(data))"
    expression_type = expression["type"]
    if expression_type == "boolean":
        operator = "&" if expression["operator"] == "and" else "|"
        left = _r_filter_expression(expression["left"], variable_types)
        right = _r_filter_expression(expression["right"], variable_types)
        return f"({left} {operator} {right})"
    if expression_type == "not":
        return f"(!({_r_filter_expression(expression['expression'], variable_types)}))"

    variable = str(expression["variable"])
    variable_type = variable_types[variable]
    column = _r_column(variable)
    if expression_type == "missing":
        return f"cde_missing({column}, {_r_string(variable_type)})"
    if expression_type == "comparison":
        operand = _r_filter_operand(expression["operand"])
        if expression["prefix"]:
            return (
                "cde_prefix_compare("
                f"{column}, {operand}, {_r_string(expression['operator'])})"
            )
        return f"cde_compare({column}, {operand}, {_r_string(expression['operator'])})"
    if expression_type == "contains":
        rendered = f"cde_contains({column}, {_r_filter_operand(expression['operand'])})"
        return f"(!{rendered})" if expression["negated"] else rendered
    if expression_type == "between":
        rendered = (
            "cde_between("
            f"{column}, {_r_filter_operand(expression['lower'])}, "
            f"{_r_filter_operand(expression['upper'])})"
        )
        return f"(!{rendered})" if expression["negated"] else rendered
    if expression_type == "like":
        rendered = (
            "cde_like("
            f"{column}, {_r_filter_operand(expression['pattern'])}, "
            f"{_r_literal(expression['escape'])})"
        )
        return f"(!{rendered})" if expression["negated"] else rendered
    if expression_type == "in":
        values = ", ".join(_r_filter_operand(value) for value in expression["values"])
        rendered = f"cde_in({column}, c({values}))"
        return f"(!{rendered})" if expression["negated"] else rendered
    raise ValueError(f"Unsupported filter AST node for R: {expression_type}")


class RProcMeansGenerator:
    """Render an R program that follows the Python PROC MEANS v1 contract."""

    def __init__(self, template_directory: Path | None = None) -> None:
        directory = template_directory or resource_path(
            "clinical_data_viewer/codegen/r/templates"
        )
        self.environment = Environment(
            loader=FileSystemLoader(str(directory)),
            undefined=StrictUndefined,
            autoescape=False,
            keep_trailing_newline=True,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.environment.filters["r_string"] = _r_string

    def generate(self, configuration: dict[str, object]) -> str:
        if configuration.get("type") != "proc_means":
            raise ValueError("The configuration is not a PROC MEANS configuration.")
        if configuration.get("version") != 3:
            raise ValueError("R generation requires PROC MEANS configuration v3.")

        context = configuration.copy()
        input_format = str(context["input"].get("format", "")).casefold()
        if input_format not in {"sas7bdat", "xpt"}:
            raise ValueError(f"Unsupported input format for R generation: {input_format}")
        statistics = list(context["statistics"])
        unknown_statistics = sorted(set(statistics) - set(_STATISTIC_KEYS))
        if unknown_statistics:
            raise ValueError(
                "Unsupported PROC MEANS statistics: " + ", ".join(unknown_statistics)
            )
        if not context["analysis_variables"]:
            raise ValueError("Select at least one Analysis Variable.")

        variables = context["variables"]
        variable_types = {
            name: str(metadata["type"]) for name, metadata in variables.items()
        }
        requested_columns = [
            {
                "name": name,
                "key": _STATISTIC_KEYS[name],
                "is_count": name in {"SUBJECT_N", "N", "NMISS"},
            }
            for name in statistics
        ]
        result_columns = [
            {
                "name": name,
                "constructor": "numeric()"
                if variable_types[name] == "numeric"
                else "character()",
            }
            for name in [*context["by_variables"], *context["class_variables"]]
        ]
        result_columns.extend(
            [
                {"name": "ANALYSIS_VARIABLE", "constructor": "character()"},
                {"name": "ANALYSIS_LABEL", "constructor": "character()"},
            ]
        )
        result_columns.extend(
            {
                "name": item["name"],
                "constructor": "integer()" if item["is_count"] else "numeric()",
            }
            for item in requested_columns
        )
        display_offsets = context["display"]["decimal_offsets"]
        decimal_statistics = [
            {
                "name": name,
                "offset": int(display_offsets.get(name, 0)),
            }
            for name in statistics
            if name not in {"SUBJECT_N", "N", "NMISS"}
        ]
        context.update(
            {
                "input_format": input_format,
                "variable_types": variable_types,
                "variables_in_order": [
                    {
                        "name": name,
                        "type": variable_types[name],
                        "label": variables[name]["label"],
                    }
                    for name in variables
                ],
                "group_variables": [
                    *context["by_variables"],
                    *context["class_variables"],
                ],
                "requested_columns": requested_columns,
                "result_columns": result_columns,
                "decimal_statistics": decimal_statistics,
                "filter_expression": _r_filter_expression(
                    context["filter"]["ast"], variable_types
                ),
                "subject_variable": context["calculation"]["subject_count"]["variable"],
                "confidence": float(context["calculation"]["confidence"]),
                "maximum_decimals": int(
                    context["display"]["decimal_inference"]["maximum_decimals"]
                ),
                "source_path": context["input"]["source_path"],
                "output_object": context["targets"]["r"]["output_object"],
            }
        )
        try:
            return self.environment.get_template("proc_means.R.j2").render(**context)
        except (OSError, TemplateError) as error:
            raise ValueError(f"Unable to render the R template: {error}") from error
