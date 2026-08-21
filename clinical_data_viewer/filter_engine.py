from __future__ import annotations

import math
from dataclasses import dataclass

from .domain import VariableMetadata
from .where_parser import (
    BooleanExpression,
    Comparison,
    ContainsPredicate,
    Expression,
    InPredicate,
    MissingPredicate,
    UnaryNot,
    parse_where,
)


class WhereValidationError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CompiledFilter:
    sql: str
    parameters: tuple[object, ...]


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


class FilterEngine:
    def __init__(
        self, variables: tuple[VariableMetadata, ...] | list[VariableMetadata]
    ) -> None:
        self._variables = {variable.name.upper(): variable for variable in variables}

    def compile(self, where_text: str) -> CompiledFilter:
        if not where_text.strip():
            return CompiledFilter("", ())
        sql, parameters = self._compile(parse_where(where_text))
        return CompiledFilter(sql, tuple(parameters))

    def _variable(self, name: str) -> VariableMetadata:
        try:
            return self._variables[name.upper()]
        except KeyError as error:
            raise WhereValidationError(f"Unknown variable: {name}") from error

    def _value(self, variable: VariableMetadata, value: object) -> object:
        if variable.kind == "numeric":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise WhereValidationError(
                    f"{variable.name} is numeric; use a numeric value without quotes."
                )
            if not math.isfinite(float(value)):
                raise WhereValidationError(
                    f"{variable.name} requires a finite numeric value."
                )
            return value
        if not isinstance(value, str):
            raise WhereValidationError(
                f"{variable.name} is character; put the value in quotes."
            )
        return value

    def _compile(self, expression: Expression) -> tuple[str, list[object]]:
        if isinstance(expression, BooleanExpression):
            left_sql, left_parameters = self._compile(expression.left)
            right_sql, right_parameters = self._compile(expression.right)
            return f"({left_sql} {expression.operator} {right_sql})", [
                *left_parameters,
                *right_parameters,
            ]
        if isinstance(expression, UnaryNot):
            sql, parameters = self._compile(expression.expression)
            return f"(NOT ({sql}))", parameters
        if isinstance(expression, MissingPredicate):
            variable = self._variable(expression.variable)
            column = quote_identifier(variable.name)
            if variable.kind == "character":
                return f"({column} IS NULL OR {column} = '')", []
            return f"({column} IS NULL)", []
        if isinstance(expression, Comparison):
            variable = self._variable(expression.variable)
            operator = "!=" if expression.operator == "^=" else expression.operator
            return f"{quote_identifier(variable.name)} {operator} ?", [
                self._value(variable, expression.value)
            ]
        if isinstance(expression, ContainsPredicate):
            variable = self._variable(expression.variable)
            if variable.kind != "character":
                raise WhereValidationError(
                    f"CONTAINS can only be used with a character variable: {variable.name}"
                )
            value = self._value(variable, expression.value)
            return f"instr(COALESCE({quote_identifier(variable.name)}, ''), ?) > 0", [
                value
            ]
        if isinstance(expression, InPredicate):
            variable = self._variable(expression.variable)
            values = [self._value(variable, value) for value in expression.values]
            placeholders = ", ".join("?" for _ in values)
            operator = "NOT IN" if expression.negated else "IN"
            return (
                f"{quote_identifier(variable.name)} {operator} ({placeholders})",
                values,
            )
        raise TypeError(f"Unsupported WHERE expression: {type(expression).__name__}")
