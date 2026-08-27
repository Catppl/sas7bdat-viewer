from __future__ import annotations

from .domain import VariableMetadata
from .sas_value_formatter import SasTemporalLiteral
from .where_parser import (
    BetweenPredicate,
    BooleanExpression,
    Comparison,
    ContainsPredicate,
    Expression,
    FunctionOperand,
    InPredicate,
    LikePredicate,
    MissingPredicate,
    Operand,
    UnaryNot,
    VariableOperand,
    parse_where,
)


class FilterAstSerializer:
    """Serialize the Python WHERE parser tree into language-neutral JSON data."""

    def __init__(self, variables: tuple[VariableMetadata, ...]) -> None:
        self.variables = {
            variable.name.casefold(): variable.name for variable in variables
        }

    def serialize_text(self, text: str) -> dict[str, object] | None:
        if not text.strip():
            return None
        return self.serialize(parse_where(text))

    def _variable(self, name: str) -> str:
        return self.variables.get(name.casefold(), name)

    def _operand(self, operand: Operand) -> dict[str, object]:
        if isinstance(operand, VariableOperand):
            return {
                "type": "variable",
                "name": self._variable(operand.name),
            }
        if isinstance(operand, FunctionOperand):
            return {
                "type": "function",
                "name": operand.name.casefold(),
                "arguments": [
                    self._operand(argument) for argument in operand.arguments
                ],
            }
        value = operand.value
        if isinstance(value, SasTemporalLiteral):
            return {
                "type": "literal",
                "value_type": f"sas_{value.kind}",
                "value": value.text,
            }
        return {
            "type": "literal",
            "value_type": "character" if isinstance(value, str) else "numeric",
            "value": value,
        }

    def serialize(self, expression: Expression) -> dict[str, object]:
        if isinstance(expression, BooleanExpression):
            return {
                "type": "boolean",
                "operator": expression.operator.casefold(),
                "left": self.serialize(expression.left),
                "right": self.serialize(expression.right),
            }
        if isinstance(expression, UnaryNot):
            return {
                "type": "not",
                "expression": self.serialize(expression.expression),
            }
        if isinstance(expression, MissingPredicate):
            return {
                "type": "missing",
                "variable": self._variable(expression.variable),
            }
        if isinstance(expression, Comparison):
            output: dict[str, object] = {
                "type": "comparison",
                "operator": expression.operator,
                "operand": self._operand(expression.operand),
                "prefix": expression.prefix,
            }
            self._write_left(output, expression.left)
            return output
        if isinstance(expression, ContainsPredicate):
            output = {
                "type": "contains",
                "operand": self._operand(expression.operand),
                "negated": expression.negated,
            }
            self._write_left(output, expression.left)
            return output
        if isinstance(expression, BetweenPredicate):
            output = {
                "type": "between",
                "lower": self._operand(expression.lower),
                "upper": self._operand(expression.upper),
                "negated": expression.negated,
            }
            self._write_left(output, expression.left)
            return output
        if isinstance(expression, LikePredicate):
            output = {
                "type": "like",
                "pattern": self._operand(expression.pattern),
                "escape": expression.escape,
                "negated": expression.negated,
            }
            self._write_left(output, expression.left)
            return output
        if isinstance(expression, InPredicate):
            output = {
                "type": "in",
                "values": [self._operand(value) for value in expression.values],
                "negated": expression.negated,
            }
            self._write_left(output, expression.left)
            return output
        raise TypeError(
            f"Unsupported WHERE expression for JSON: {type(expression).__name__}"
        )

    def _write_left(
        self, output: dict[str, object], left: VariableOperand | FunctionOperand
    ) -> None:
        """Keep v1 variable predicates stable; functions use an explicit node."""
        if isinstance(left, VariableOperand):
            output["variable"] = self._variable(left.name)
        else:
            output["left"] = self._operand(left)


def serialize_filter_ast(
    text: str, variables: tuple[VariableMetadata, ...]
) -> dict[str, object] | None:
    return FilterAstSerializer(variables).serialize_text(text)
