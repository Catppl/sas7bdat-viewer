from __future__ import annotations

import re

_SAS_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def sas_string(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sas_name(value: object) -> str:
    name = str(value)
    if _SAS_NAME.fullmatch(name):
        return name
    return sas_string(name) + "n"


def sas_filter_operand(operand: dict[str, object]) -> str:
    if operand["type"] == "variable":
        return sas_name(operand["name"])
    if operand["type"] == "function":
        name = str(operand["name"]).upper()
        arguments = ", ".join(
            sas_filter_operand(argument) for argument in operand["arguments"]
        )
        if name not in {"INDEX", "FIND", "UPCASE", "LOWCASE"}:
            raise ValueError(f"Unsupported SAS WHERE function: {name}")
        return f"{name}({arguments})"
    value = operand["value"]
    temporal = {
        "sas_date": "d",
        "sas_datetime": "dt",
        "sas_time": "t",
    }.get(str(operand.get("value_type", "")))
    if temporal is not None:
        return sas_string(value) + temporal
    return sas_string(value) if isinstance(value, str) else repr(value)


def _sas_filter_left(expression: dict[str, object]) -> str:
    if "variable" in expression:
        return sas_name(expression["variable"])
    return sas_filter_operand(expression["left"])


def sas_filter_expression(expression: dict[str, object] | None) -> str:
    """Render a serialized Viewer filter AST as a SAS WHERE expression."""
    if expression is None:
        return ""
    expression_type = expression["type"]
    if expression_type == "boolean":
        left = sas_filter_expression(expression["left"])
        right = sas_filter_expression(expression["right"])
        return f"({left} {expression['operator']} {right})"
    if expression_type == "not":
        return f"not ({sas_filter_expression(expression['expression'])})"
    variable = _sas_filter_left(expression)
    if expression_type == "missing":
        return f"missing({variable})"
    if expression_type == "comparison":
        operator_map = {
            "=": "=",
            "!=": "ne",
            "ne": "ne",
            "^=": "ne",
            "~=": "ne",
            "<>": "ne",
            ">": ">",
            ">=": ">=",
            "<": "<",
            "<=": "<=",
        }
        operator = operator_map.get(str(expression["operator"]).casefold())
        if operator is None:
            raise ValueError(
                f"Unsupported comparison operator: {expression['operator']}"
            )
        if expression["prefix"]:
            operator += ":"
        return f"{variable} {operator} {sas_filter_operand(expression['operand'])}"
    if expression_type == "contains":
        rendered = f"{variable} contains {sas_filter_operand(expression['operand'])}"
        return f"not ({rendered})" if expression["negated"] else rendered
    if expression_type == "between":
        rendered = (
            f"{variable} between {sas_filter_operand(expression['lower'])} "
            f"and {sas_filter_operand(expression['upper'])}"
        )
        return f"not ({rendered})" if expression["negated"] else rendered
    if expression_type == "like":
        rendered = f"{variable} like {sas_filter_operand(expression['pattern'])}"
        if expression["escape"] is not None:
            rendered += f" escape {sas_string(expression['escape'])}"
        return f"not ({rendered})" if expression["negated"] else rendered
    if expression_type == "in":
        values = ", ".join(sas_filter_operand(value) for value in expression["values"])
        operator = "not in" if expression["negated"] else "in"
        return f"{variable} {operator} ({values})"
    raise ValueError(f"Unsupported filter AST node for SAS: {expression_type}")


__all__ = ["sas_filter_expression", "sas_filter_operand", "sas_name", "sas_string"]
