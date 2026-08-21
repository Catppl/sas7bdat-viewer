from __future__ import annotations

import math
from dataclasses import dataclass

from .domain import VariableMetadata
from .where_parser import (
    BetweenPredicate,
    BooleanExpression,
    Comparison,
    ContainsPredicate,
    Expression,
    InPredicate,
    LikePredicate,
    LiteralOperand,
    MissingPredicate,
    UnaryNot,
    VariableOperand,
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

    def _operand(
        self, left: VariableMetadata, operand: LiteralOperand | VariableOperand
    ) -> tuple[str, list[object], VariableMetadata | None]:
        if isinstance(operand, LiteralOperand):
            return "?", [self._value(left, operand.value)], None
        right = self._variable(operand.name)
        if left.kind != right.kind:
            raise WhereValidationError(
                f"Cannot compare {left.name} ({left.kind}) with "
                f"{right.name} ({right.kind})."
            )
        return quote_identifier(right.name), [], right

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
            column = quote_identifier(variable.name)
            operand_sql, parameters, _right = self._operand(
                variable, expression.operand
            )
            if not expression.prefix:
                return f"{column} {expression.operator} {operand_sql}", parameters
            if variable.kind != "character":
                raise WhereValidationError(
                    f"The ':' prefix modifier requires a character variable: {variable.name}"
                )
            if isinstance(expression.operand, LiteralOperand):
                value = parameters[0]
                if value == "":
                    raise WhereValidationError(
                        "A prefix comparison cannot use an empty string."
                    )
                return (
                    (
                        f"substr(COALESCE({column}, ''), 1, length(?)) "
                        f"{expression.operator} ?"
                    ),
                    [value, value],
                )
            right = quote_identifier(self._variable(expression.operand.name).name)
            shared_length = (
                f"min(length(COALESCE({column}, '')), length(COALESCE({right}, '')))"
            )
            return (
                (
                    f"substr(COALESCE({column}, ''), 1, {shared_length}) "
                    f"{expression.operator} "
                    f"substr(COALESCE({right}, ''), 1, {shared_length})"
                ),
                [],
            )
        if isinstance(expression, ContainsPredicate):
            variable = self._variable(expression.variable)
            if variable.kind != "character":
                raise WhereValidationError(
                    f"CONTAINS can only be used with a character variable: {variable.name}"
                )
            operand_sql, parameters, _right = self._operand(
                variable, expression.operand
            )
            sql = (
                f"instr(COALESCE({quote_identifier(variable.name)}, ''), "
                f"COALESCE({operand_sql}, '')) > 0"
            )
            return (f"NOT ({sql})" if expression.negated else sql), parameters
        if isinstance(expression, BetweenPredicate):
            variable = self._variable(expression.variable)
            lower_sql, lower_parameters, _lower = self._operand(
                variable, expression.lower
            )
            upper_sql, upper_parameters, _upper = self._operand(
                variable, expression.upper
            )
            operator = "NOT BETWEEN" if expression.negated else "BETWEEN"
            return (
                f"{quote_identifier(variable.name)} {operator} {lower_sql} AND {upper_sql}",
                [*lower_parameters, *upper_parameters],
            )
        if isinstance(expression, LikePredicate):
            variable = self._variable(expression.variable)
            if variable.kind != "character":
                raise WhereValidationError(
                    f"LIKE can only be used with a character variable: {variable.name}"
                )
            pattern = self._value(variable, expression.pattern.value)
            operator = "NOT LIKE" if expression.negated else "LIKE"
            sql = f"{quote_identifier(variable.name)} {operator} ?"
            parameters = [pattern]
            if expression.escape is not None:
                sql += " ESCAPE ?"
                parameters.append(expression.escape)
            return sql, parameters
        if isinstance(expression, InPredicate):
            variable = self._variable(expression.variable)
            values = [
                self._value(variable, operand.value) for operand in expression.values
            ]
            placeholders = ", ".join("?" for _ in values)
            operator = "NOT IN" if expression.negated else "IN"
            return (
                f"{quote_identifier(variable.name)} {operator} ({placeholders})",
                values,
            )
        raise TypeError(f"Unsupported WHERE expression: {type(expression).__name__}")
