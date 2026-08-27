from __future__ import annotations

import math
from dataclasses import dataclass

from .domain import VariableMetadata
from .sas_value_formatter import SasTemporalLiteral, sas_temporal_literal_value
from .where_parser import (
    BetweenPredicate,
    BooleanExpression,
    Comparison,
    ContainsPredicate,
    Expression,
    FunctionOperand,
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

    def _value(
        self,
        variable: VariableMetadata | None,
        kind: str,
        value: object,
    ) -> object:
        if isinstance(value, SasTemporalLiteral):
            if variable is None:
                raise WhereValidationError(
                    "SAS date, datetime, and time literals must be compared "
                    "with a numeric variable that has a matching SAS format."
                )
            try:
                return sas_temporal_literal_value(value, variable)
            except ValueError as error:
                raise WhereValidationError(str(error)) from error
        if kind == "numeric":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise WhereValidationError(
                    f"{variable.name if variable else 'This expression'} is numeric; "
                    "use a numeric value without quotes."
                )
            if not math.isfinite(float(value)):
                raise WhereValidationError(
                    f"{variable.name if variable else 'This expression'} requires a "
                    "finite numeric value."
                )
            return value
        if not isinstance(value, str):
            raise WhereValidationError(
                f"{variable.name if variable else 'This expression'} is character; "
                "put the value in quotes."
            )
        return value

    def _operand(
        self,
        left: VariableMetadata | None,
        kind: str,
        operand: LiteralOperand | VariableOperand | FunctionOperand,
    ) -> tuple[str, list[object], VariableMetadata | None]:
        if isinstance(operand, LiteralOperand):
            return "?", [self._value(left, kind, operand.value)], None
        right_sql, right_parameters, right, right_kind = self._expression(operand)
        if kind != right_kind:
            raise WhereValidationError(
                f"Cannot compare {left.name if left else 'this expression'} ({kind}) "
                f"with {right.name if right else 'this expression'} ({right_kind})."
            )
        return right_sql, right_parameters, right

    def _expression(
        self, expression: VariableOperand | FunctionOperand
    ) -> tuple[str, list[object], VariableMetadata | None, str]:
        if isinstance(expression, VariableOperand):
            variable = self._variable(expression.name)
            return quote_identifier(variable.name), [], variable, variable.kind
        name = expression.name.upper()
        arguments = expression.arguments
        if name in {"UPCASE", "LOWCASE"}:
            if len(arguments) != 1:
                raise WhereValidationError(f"{name}() requires exactly one argument.")
            sql, parameters, _metadata = self._operand(None, "character", arguments[0])
            function = "upper" if name == "UPCASE" else "lower"
            return f"{function}(COALESCE({sql}, ''))", parameters, None, "character"
        if name in {"INDEX", "FIND"}:
            if len(arguments) != 2:
                raise WhereValidationError(f"{name}() requires exactly two arguments.")
            left_sql, left_parameters, _left_metadata = self._operand(
                None, "character", arguments[0]
            )
            right_sql, right_parameters, _right_metadata = self._operand(
                None, "character", arguments[1]
            )
            return (
                f"instr(COALESCE({left_sql}, ''), COALESCE({right_sql}, ''))",
                [*left_parameters, *right_parameters],
                None,
                "numeric",
            )
        raise WhereValidationError(
            f"Unsupported WHERE function: {expression.name}. "
            "Supported functions are INDEX(), FIND(), UPCASE(), and LOWCASE()."
        )

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
            column, left_parameters, variable, kind = self._expression(expression.left)
            operand_sql, parameters, _right = self._operand(
                variable, kind, expression.operand
            )
            if not expression.prefix:
                return f"{column} {expression.operator} {operand_sql}", [
                    *left_parameters,
                    *parameters,
                ]
            if kind != "character":
                raise WhereValidationError(
                    "The ':' prefix modifier requires a character variable or expression."
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
                    [*left_parameters, value, value],
                )
            right, right_parameters, _right_metadata, _right_kind = self._expression(
                expression.operand
            )
            shared_length = (
                f"min(length(COALESCE({column}, '')), length(COALESCE({right}, '')))"
            )
            return (
                (
                    f"substr(COALESCE({column}, ''), 1, {shared_length}) "
                    f"{expression.operator} "
                    f"substr(COALESCE({right}, ''), 1, {shared_length})"
                ),
                [*left_parameters, *right_parameters],
            )
        if isinstance(expression, ContainsPredicate):
            column, left_parameters, variable, kind = self._expression(expression.left)
            if kind != "character":
                raise WhereValidationError(
                    "CONTAINS can only be used with a character variable or expression."
                )
            operand_sql, parameters, _right = self._operand(
                variable, kind, expression.operand
            )
            sql = f"instr(COALESCE({column}, ''), COALESCE({operand_sql}, '')) > 0"
            return (
                f"NOT ({sql})" if expression.negated else sql,
                [*left_parameters, *parameters],
            )
        if isinstance(expression, BetweenPredicate):
            column, left_parameters, variable, kind = self._expression(expression.left)
            lower_sql, lower_parameters, _lower = self._operand(
                variable, kind, expression.lower
            )
            upper_sql, upper_parameters, _upper = self._operand(
                variable, kind, expression.upper
            )
            operator = "NOT BETWEEN" if expression.negated else "BETWEEN"
            return (
                f"{column} {operator} {lower_sql} AND {upper_sql}",
                [*left_parameters, *lower_parameters, *upper_parameters],
            )
        if isinstance(expression, LikePredicate):
            column, left_parameters, variable, kind = self._expression(expression.left)
            if kind != "character":
                raise WhereValidationError(
                    "LIKE can only be used with a character variable or expression."
                )
            pattern = self._value(variable, kind, expression.pattern.value)
            operator = "NOT LIKE" if expression.negated else "LIKE"
            sql = f"{column} {operator} ?"
            parameters = [*left_parameters, pattern]
            if expression.escape is not None:
                sql += " ESCAPE ?"
                parameters.append(expression.escape)
            return sql, parameters
        if isinstance(expression, InPredicate):
            column, left_parameters, variable, kind = self._expression(expression.left)
            values = [
                self._value(variable, kind, operand.value)
                for operand in expression.values
            ]
            placeholders = ", ".join("?" for _ in values)
            operator = "NOT IN" if expression.negated else "IN"
            return (
                f"{column} {operator} ({placeholders})",
                [*left_parameters, *values],
            )
        raise TypeError(f"Unsupported WHERE expression: {type(expression).__name__}")
