from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class WhereSyntaxError(ValueError):
    def __init__(self, message: str, text: str, position: int) -> None:
        line = text.count("\n", 0, position) + 1
        line_start = text.rfind("\n", 0, position) + 1
        column = position - line_start + 1
        super().__init__(f"{message} (line {line}, column {column})")
        self.position = position
        self.line = line
        self.column = column


class TokenKind(Enum):
    IDENTIFIER = auto()
    STRING = auto()
    NUMBER = auto()
    OPERATOR = auto()
    LPAREN = auto()
    RPAREN = auto()
    COMMA = auto()
    EOF = auto()


@dataclass(frozen=True, slots=True)
class Token:
    kind: TokenKind
    value: object
    position: int


@dataclass(frozen=True, slots=True)
class LiteralOperand:
    value: object


@dataclass(frozen=True, slots=True)
class VariableOperand:
    name: str


Operand = LiteralOperand | VariableOperand


@dataclass(frozen=True, slots=True)
class Comparison:
    variable: str
    operator: str
    operand: Operand
    prefix: bool = False


@dataclass(frozen=True, slots=True)
class InPredicate:
    variable: str
    values: tuple[LiteralOperand, ...]
    negated: bool = False


@dataclass(frozen=True, slots=True)
class ContainsPredicate:
    variable: str
    operand: Operand
    negated: bool = False


@dataclass(frozen=True, slots=True)
class BetweenPredicate:
    variable: str
    lower: Operand
    upper: Operand
    negated: bool = False


@dataclass(frozen=True, slots=True)
class LikePredicate:
    variable: str
    pattern: LiteralOperand
    escape: str | None = None
    negated: bool = False


@dataclass(frozen=True, slots=True)
class MissingPredicate:
    variable: str


@dataclass(frozen=True, slots=True)
class UnaryNot:
    expression: Expression


@dataclass(frozen=True, slots=True)
class BooleanExpression:
    left: Expression
    operator: str
    right: Expression


Expression = (
    Comparison
    | InPredicate
    | ContainsPredicate
    | BetweenPredicate
    | LikePredicate
    | MissingPredicate
    | UnaryNot
    | BooleanExpression
)


COMPARISON_MNEMONICS = {
    "EQ": "=",
    "NE": "!=",
    "GT": ">",
    "LT": "<",
    "GE": ">=",
    "LE": "<=",
}


class Lexer:
    def __init__(self, text: str) -> None:
        self.text = text
        self.position = 0

    def tokens(self) -> list[Token]:
        result: list[Token] = []
        while self.position < len(self.text):
            char = self.text[self.position]
            if char.isspace():
                self.position += 1
                continue
            start = self.position
            if char in "'\"":
                result.append(Token(TokenKind.STRING, self._string(char), start))
            elif char.isalpha() or char == "_":
                result.append(Token(TokenKind.IDENTIFIER, self._identifier(), start))
            elif (
                char.isdigit()
                or char == "."
                or (char in "+-" and self._next_is_number())
            ):
                result.append(Token(TokenKind.NUMBER, self._number(), start))
            elif char == "(":
                self.position += 1
                result.append(Token(TokenKind.LPAREN, char, start))
            elif char == ")":
                self.position += 1
                result.append(Token(TokenKind.RPAREN, char, start))
            elif char == ",":
                self.position += 1
                result.append(Token(TokenKind.COMMA, char, start))
            elif char in "=!^><~?:&|":
                result.append(Token(TokenKind.OPERATOR, self._operator(), start))
            else:
                raise WhereSyntaxError(
                    f"Unexpected character {char!r}", self.text, start
                )
        result.append(Token(TokenKind.EOF, None, len(self.text)))
        return result

    def _next_is_number(self) -> bool:
        return self.position + 1 < len(self.text) and (
            self.text[self.position + 1].isdigit()
            or self.text[self.position + 1] == "."
        )

    def _identifier(self) -> str:
        start = self.position
        while self.position < len(self.text) and (
            self.text[self.position].isalnum() or self.text[self.position] == "_"
        ):
            self.position += 1
        return self.text[start : self.position]

    def _string(self, quote: str) -> str:
        self.position += 1
        result: list[str] = []
        while self.position < len(self.text):
            char = self.text[self.position]
            if char == quote:
                if (
                    self.position + 1 < len(self.text)
                    and self.text[self.position + 1] == quote
                ):
                    result.append(quote)
                    self.position += 2
                    continue
                self.position += 1
                return "".join(result)
            if char == "\\" and self.position + 1 < len(self.text):
                result.append(self.text[self.position + 1])
                self.position += 2
            else:
                result.append(char)
                self.position += 1
        raise WhereSyntaxError("Unterminated string literal", self.text, self.position)

    def _number(self) -> int | float:
        start = self.position
        if self.text[self.position] in "+-":
            self.position += 1
        digits = 0
        while self.position < len(self.text) and self.text[self.position].isdigit():
            digits += 1
            self.position += 1
        if self.position < len(self.text) and self.text[self.position] == ".":
            self.position += 1
            while self.position < len(self.text) and self.text[self.position].isdigit():
                digits += 1
                self.position += 1
        if digits == 0:
            raise WhereSyntaxError("Invalid number", self.text, start)
        if self.position < len(self.text) and self.text[self.position] in "eE":
            self.position += 1
            if self.position < len(self.text) and self.text[self.position] in "+-":
                self.position += 1
            exponent_start = self.position
            while self.position < len(self.text) and self.text[self.position].isdigit():
                self.position += 1
            if self.position == exponent_start:
                raise WhereSyntaxError("Invalid numeric exponent", self.text, start)
        raw = self.text[start : self.position]
        return (
            float(raw) if any(mark in raw.lower() for mark in (".", "e")) else int(raw)
        )

    def _operator(self) -> str:
        start = self.position
        char = self.text[self.position]
        self.position += 1
        if self.position < len(self.text):
            pair = char + self.text[self.position]
            if pair in {"!=", "^=", "~=", "<>", ">=", "<=", "||", "!!"}:
                self.position += 1
                if pair in {"||", "!!"}:
                    raise WhereSyntaxError(
                        "String concatenation is not supported in this version",
                        self.text,
                        start,
                    )
                operator = pair
            else:
                operator = char
        else:
            operator = char
        if self.position < len(self.text) and self.text[self.position] == ":":
            if operator not in {"=", "!=", "^=", "~=", "<>", ">", ">=", "<", "<="}:
                raise WhereSyntaxError(
                    "The ':' modifier requires a comparison operator", self.text, start
                )
            self.position += 1
            operator += ":"
        return operator


class Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.stream = Lexer(text).tokens()
        self.index = 0

    def parse(self) -> Expression:
        if not self.text.strip():
            raise WhereSyntaxError("WHERE condition is empty", self.text, 0)
        expression = self._or_expression()
        self._expect(TokenKind.EOF, "Unexpected text after condition")
        return expression

    @property
    def current(self) -> Token:
        return self.stream[self.index]

    def _advance(self) -> Token:
        token = self.current
        self.index += 1
        return token

    def _keyword(self, value: str) -> bool:
        return (
            self.current.kind is TokenKind.IDENTIFIER
            and str(self.current.value).upper() == value
        )

    def _operator(self, values: set[str]) -> bool:
        return (
            self.current.kind is TokenKind.OPERATOR
            and str(self.current.value) in values
        )

    def _consume_keyword(self, value: str) -> bool:
        if self._keyword(value):
            self._advance()
            return True
        return False

    def _consume_operator(self, values: set[str]) -> str | None:
        if self._operator(values):
            return str(self._advance().value)
        return None

    def _expect(self, kind: TokenKind, message: str) -> Token:
        if self.current.kind is not kind:
            raise WhereSyntaxError(message, self.text, self.current.position)
        return self._advance()

    def _or_expression(self) -> Expression:
        expression = self._and_expression()
        while self._consume_keyword("OR") or self._consume_operator({"|", "!"}):
            expression = BooleanExpression(expression, "OR", self._and_expression())
        return expression

    def _and_expression(self) -> Expression:
        expression = self._not_expression()
        while self._consume_keyword("AND") or self._consume_operator({"&"}):
            expression = BooleanExpression(expression, "AND", self._not_expression())
        return expression

    def _not_expression(self) -> Expression:
        if self._consume_keyword("NOT") or self._consume_operator({"^", "~"}):
            return UnaryNot(self._not_expression())
        if self.current.kind is TokenKind.LPAREN:
            self._advance()
            expression = self._or_expression()
            self._expect(TokenKind.RPAREN, "Expected ')' to close condition")
            return expression
        return self._predicate()

    def _predicate(self) -> Expression:
        if self._consume_keyword("MISSING"):
            self._expect(TokenKind.LPAREN, "Expected '(' after MISSING")
            variable = self._expect(
                TokenKind.IDENTIFIER, "Expected a variable name in MISSING()"
            ).value
            self._expect(TokenKind.RPAREN, "Expected ')' after variable name")
            return MissingPredicate(str(variable))

        variable = str(
            self._expect(
                TokenKind.IDENTIFIER, "Expected a variable name or MISSING()"
            ).value
        )

        if self._consume_keyword("IS"):
            negated = self._consume_keyword("NOT")
            if not (self._consume_keyword("NULL") or self._consume_keyword("MISSING")):
                raise WhereSyntaxError(
                    "Expected NULL or MISSING after IS",
                    self.text,
                    self.current.position,
                )
            expression: Expression = MissingPredicate(variable)
            return UnaryNot(expression) if negated else expression

        negated = self._consume_keyword("NOT")
        if self._consume_keyword("IN"):
            return self._in_predicate(variable, negated)
        if self._consume_keyword("BETWEEN"):
            lower = self._operand()
            if not self._consume_keyword("AND"):
                raise WhereSyntaxError(
                    "Expected AND inside BETWEEN", self.text, self.current.position
                )
            return BetweenPredicate(variable, lower, self._operand(), negated)
        if self._consume_keyword("LIKE"):
            pattern = self._literal_operand("LIKE requires a quoted string pattern")
            if not isinstance(pattern.value, str):
                raise WhereSyntaxError(
                    "LIKE requires a quoted string pattern",
                    self.text,
                    self.current.position,
                )
            escape = None
            if self._consume_keyword("ESCAPE"):
                escape_operand = self._literal_operand(
                    "ESCAPE requires one quoted character"
                )
                if (
                    not isinstance(escape_operand.value, str)
                    or len(escape_operand.value) != 1
                ):
                    raise WhereSyntaxError(
                        "ESCAPE requires one quoted character",
                        self.text,
                        self.current.position,
                    )
                escape = escape_operand.value
            return LikePredicate(variable, pattern, escape, negated)
        if self._consume_keyword("CONTAINS") or self._consume_operator({"?"}):
            return ContainsPredicate(variable, self._operand(), negated)
        if negated:
            raise WhereSyntaxError(
                "Expected IN, BETWEEN, LIKE, CONTAINS, or ? after NOT",
                self.text,
                self.current.position,
            )

        operator, prefix = self._comparison_operator()
        return Comparison(variable, operator, self._operand(), prefix)

    def _comparison_operator(self) -> tuple[str, bool]:
        if self.current.kind is TokenKind.IDENTIFIER:
            mnemonic = str(self.current.value).upper()
            if mnemonic in COMPARISON_MNEMONICS:
                self._advance()
                prefix = bool(self._consume_operator({":"}))
                return COMPARISON_MNEMONICS[mnemonic], prefix
        if self.current.kind is TokenKind.OPERATOR:
            raw = str(self.current.value)
            base = raw.removesuffix(":")
            if base in {"=", "!=", "^=", "~=", "<>", ">", ">=", "<", "<="}:
                self._advance()
                normalized = "!=" if base in {"^=", "~=", "<>"} else base
                return normalized, raw.endswith(":")
        raise WhereSyntaxError(
            "Expected a comparison operator, IN, BETWEEN, LIKE, CONTAINS, or ?",
            self.text,
            self.current.position,
        )

    def _in_predicate(self, variable: str, negated: bool) -> InPredicate:
        self._expect(TokenKind.LPAREN, "Expected '(' after IN")
        values = [self._literal_operand("IN values must be quoted strings or numbers")]
        while self.current.kind is TokenKind.COMMA:
            self._advance()
            values.append(
                self._literal_operand("IN values must be quoted strings or numbers")
            )
        self._expect(TokenKind.RPAREN, "Expected ')' after IN values")
        return InPredicate(variable, tuple(values), negated)

    def _operand(self) -> Operand:
        if self.current.kind in {TokenKind.STRING, TokenKind.NUMBER}:
            return LiteralOperand(self._advance().value)
        if self.current.kind is TokenKind.IDENTIFIER:
            return VariableOperand(str(self._advance().value))
        raise WhereSyntaxError(
            "Expected a quoted string, number, or variable name",
            self.text,
            self.current.position,
        )

    def _literal_operand(self, message: str) -> LiteralOperand:
        if self.current.kind in {TokenKind.STRING, TokenKind.NUMBER}:
            return LiteralOperand(self._advance().value)
        raise WhereSyntaxError(message, self.text, self.current.position)


def parse_where(text: str) -> Expression:
    return Parser(text).parse()
