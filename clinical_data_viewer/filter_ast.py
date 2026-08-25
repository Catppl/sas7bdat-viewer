from __future__ import annotations

from .domain import VariableMetadata
from .where_parser import (
    BetweenPredicate,
    BooleanExpression,
    Comparison,
    ContainsPredicate,
    Expression,
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
        value = operand.value
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
            return {
                "type": "comparison",
                "variable": self._variable(expression.variable),
                "operator": expression.operator,
                "operand": self._operand(expression.operand),
                "prefix": expression.prefix,
            }
        if isinstance(expression, ContainsPredicate):
            return {
                "type": "contains",
                "variable": self._variable(expression.variable),
                "operand": self._operand(expression.operand),
                "negated": expression.negated,
            }
        if isinstance(expression, BetweenPredicate):
            return {
                "type": "between",
                "variable": self._variable(expression.variable),
                "lower": self._operand(expression.lower),
                "upper": self._operand(expression.upper),
                "negated": expression.negated,
            }
        if isinstance(expression, LikePredicate):
            return {
                "type": "like",
                "variable": self._variable(expression.variable),
                "pattern": self._operand(expression.pattern),
                "escape": expression.escape,
                "negated": expression.negated,
            }
        if isinstance(expression, InPredicate):
            return {
                "type": "in",
                "variable": self._variable(expression.variable),
                "values": [self._operand(value) for value in expression.values],
                "negated": expression.negated,
            }
        raise TypeError(
            f"Unsupported WHERE expression for JSON: {type(expression).__name__}"
        )


def serialize_filter_ast(
    text: str, variables: tuple[VariableMetadata, ...]
) -> dict[str, object] | None:
    return FilterAstSerializer(variables).serialize_text(text)
