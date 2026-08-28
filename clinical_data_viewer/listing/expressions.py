from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime

from ..domain import VariableMetadata
from ..sas_value_formatter import format_sas_value

_DEFAULT_CHARACTER_LENGTH = 200
_MAX_CHARACTER_LENGTH = 32_767
_FORMAT_WIDTH = re.compile(r"(?P<width>\d+)$")


class ListingExpressionError(ValueError):
    pass


_TOKEN = re.compile(
    r"\s*(?:(?P<string>'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")|(?P<number>\d+(?:\.\d+)?)|(?P<op>\|\||>=|<=|<>|!=|[+\-*/(),])|(?P<name>[A-Za-z_][A-Za-z0-9_]*\.?))"
)


@dataclass(frozen=True, slots=True)
class Token:
    kind: str
    value: str


def _tokens(text: str) -> Iterator[Token]:
    position = 0
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            raise ListingExpressionError(
                f"Invalid expression token at position {position + 1}."
            )
        position = match.end()
        kind = next(
            name for name, value in match.groupdict().items() if value is not None
        )
        value = match.group(kind)
        if kind == "string":
            quote = value[0]
            yield Token("string", value[1:-1].replace(quote * 2, quote))
        else:
            yield Token(kind, value)
    yield Token("eof", "")


class ExpressionParser:
    def __init__(self, text: str, variables: tuple[VariableMetadata, ...]):
        self.tokens = list(_tokens(text))
        self.position = 0
        self.variables = {variable.name.casefold(): variable for variable in variables}

    @property
    def current(self) -> Token:
        return self.tokens[self.position]

    def consume(self, value: str | None = None) -> Token:
        token = self.current
        if value is not None and token.value.casefold() != value.casefold():
            raise ListingExpressionError(f'Expected "{value}" in expression.')
        self.position += 1
        return token

    def parse(self) -> dict[str, object]:
        output = self.concat()
        if self.current.kind != "eof":
            raise ListingExpressionError(f'Unexpected token "{self.current.value}".')
        return output

    def concat(self) -> dict[str, object]:
        output = self.additive()
        while self.current.value == "||":
            self.consume("||")
            output = {"type": "concat", "left": output, "right": self.additive()}
        return output

    def additive(self) -> dict[str, object]:
        output = self.multiplicative()
        while self.current.value in {"+", "-"}:
            operator = self.consume().value
            output = {
                "type": "binary",
                "operator": operator,
                "left": output,
                "right": self.multiplicative(),
            }
        return output

    def multiplicative(self) -> dict[str, object]:
        output = self.factor()
        while self.current.value in {"*", "/"}:
            operator = self.consume().value
            output = {
                "type": "binary",
                "operator": operator,
                "left": output,
                "right": self.factor(),
            }
        return output

    def factor(self) -> dict[str, object]:
        token = self.current
        if token.value in {"+", "-"}:
            operator = self.consume().value
            return {"type": "unary", "operator": operator, "value": self.factor()}
        if token.value == "(":
            self.consume("(")
            output = self.concat()
            self.consume(")")
            return output
        if token.kind == "string":
            return {
                "type": "literal",
                "value": self.consume().value,
                "kind": "character",
            }
        if token.kind == "number":
            value = self.consume().value
            return {
                "type": "literal",
                "value": float(value) if "." in value else int(value),
                "kind": "numeric",
            }
        if token.kind != "name":
            raise ListingExpressionError("Expected a variable, value, or function.")
        name = self.consume().value
        if self.current.value != "(":
            variable = self.variables.get(name.casefold())
            if variable is None:
                raise ListingExpressionError(f"Unknown variable: {name}")
            return {"type": "variable", "name": variable.name, "kind": variable.kind}
        self.consume("(")
        upper = name.upper().rstrip(".")
        arguments: list[dict[str, object]] = []
        if upper in {"PUT", "INPUT"}:
            arguments.append(self.concat())
            self.consume(",")
            if self.current.kind not in {"name", "number"}:
                raise ListingExpressionError(
                    f"{upper}() requires a SAS format/informat."
                )
            arguments.append({"type": "format", "value": self.consume().value})
        elif self.current.value != ")":
            arguments.append(self.concat())
            while self.current.value == ",":
                self.consume(",")
                arguments.append(self.concat())
        self.consume(")")
        if upper not in {
            "CATS",
            "CATX",
            "STRIP",
            "UPCASE",
            "LOWCASE",
            "SUBSTR",
            "SCAN",
            "COALESCE",
            "COALESCEC",
            "PUT",
            "INPUT",
        }:
            raise ListingExpressionError(
                f"Unsupported Listing expression function: {upper}()."
            )
        return {"type": "function", "name": upper, "arguments": arguments}


def parse_expression(
    text: str, variables: tuple[VariableMetadata, ...]
) -> dict[str, object]:
    if not text.strip():
        raise ListingExpressionError("Listing expression cannot be empty.")
    return ExpressionParser(text, variables).parse()


def references(expression: dict[str, object]) -> set[str]:
    kind = expression["type"]
    if kind == "variable":
        return {str(expression["name"])}
    if kind in {"literal", "format"}:
        return set()
    if kind in {"concat", "binary"}:
        return references(expression["left"]) | references(expression["right"])
    if kind == "unary":
        return references(expression["value"])
    return set().union(*(references(argument) for argument in expression["arguments"]))


def infer_kind(expression: dict[str, object]) -> str:
    kind = expression["type"]
    if kind in {"variable", "literal"}:
        return str(expression["kind"])
    if kind == "concat":
        return "character"
    if kind in {"binary", "unary"}:
        return "numeric"
    name = str(expression["name"]).upper()
    return "numeric" if name in {"INPUT", "COALESCE"} else "character"


def _metadata_for(
    expression: dict[str, object], metadata: dict[str, VariableMetadata]
) -> VariableMetadata | None:
    if expression.get("type") != "variable":
        return None
    return metadata.get(str(expression.get("name", "")).casefold())


def _format_width(format_text: str) -> int | None:
    normalized = str(format_text or "").strip()
    if not normalized:
        return None
    base = normalized.rstrip(".").split(".", 1)[0]
    match = _FORMAT_WIDTH.search(base)
    if match is None:
        return None
    return int(match.group("width"))


def _display_length(
    expression: dict[str, object], metadata: dict[str, VariableMetadata]
) -> int:
    length = infer_length(expression, metadata)
    if length is not None:
        return length
    variable = _metadata_for(expression, metadata)
    if variable is not None:
        return _format_width(variable.format) or 32
    return 32


def infer_length(
    expression: dict[str, object], metadata: dict[str, VariableMetadata]
) -> int | None:
    """Infer a safe character length for an expression.

    Direct character variables retain their metadata length.  Derived
    character expressions reserve room for their inputs and explicit PUT()
    formats, with a bounded fallback when the source metadata is incomplete.
    Numeric expressions do not have a character length.
    """
    if infer_kind(expression) != "character":
        return None
    node = str(expression.get("type", ""))
    if node == "literal":
        length = len(str(expression.get("value", "")))
    elif node == "variable":
        variable = _metadata_for(expression, metadata)
        length = (
            variable.length
            if variable is not None and variable.length
            else _DEFAULT_CHARACTER_LENGTH
        )
    elif node == "concat":
        length = _display_length(expression["left"], metadata) + _display_length(
            expression["right"], metadata
        )
    elif node == "function":
        name = str(expression.get("name", "")).upper()
        arguments = [
            argument
            for argument in expression.get("arguments", ())
            if argument.get("type") != "format"
        ]
        if name == "PUT":
            format_node = expression["arguments"][1]
            length = _format_width(str(format_node.get("value", ""))) or 32
        elif name in {"STRIP", "UPCASE", "LOWCASE", "SCAN"} and arguments:
            length = _display_length(arguments[0], metadata)
        elif name == "SUBSTR" and arguments:
            length = _display_length(arguments[0], metadata)
            if len(expression.get("arguments", ())) >= 3:
                length_node = expression["arguments"][2]
                if length_node.get("type") == "literal":
                    try:
                        length = int(length_node["value"])
                    except (TypeError, ValueError):
                        pass
        elif name == "COALESCEC":
            length = max(
                (
                    infer_length(argument, metadata) or _DEFAULT_CHARACTER_LENGTH
                    for argument in arguments
                ),
                default=_DEFAULT_CHARACTER_LENGTH,
            )
        elif name in {"CATS", "CATX"}:
            value_arguments = arguments[1:] if name == "CATX" else arguments
            length = sum(
                _display_length(argument, metadata)
                for argument in value_arguments
            )
            if name == "CATX" and arguments:
                length += _display_length(arguments[0], metadata)
            length = length or _DEFAULT_CHARACTER_LENGTH
        else:
            length = _DEFAULT_CHARACTER_LENGTH
    else:
        length = _DEFAULT_CHARACTER_LENGTH
    return max(1, min(_MAX_CHARACTER_LENGTH, int(length)))


_EPOCH_DATE = date(1960, 1, 1)
_EPOCH_DATETIME = datetime(1960, 1, 1, tzinfo=UTC)
_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and value == "")


def _display(
    value: object, metadata: VariableMetadata | None, format_text: str = ""
) -> str:
    if _missing(value):
        return ""
    fmt = format_text or (metadata.format if metadata else "")
    if fmt and (
        (metadata is not None and metadata.kind == "numeric")
        or isinstance(value, (int, float))
    ):
        formatted = format_sas_value(
            value, VariableMetadata("_", kind="numeric", format=fmt)
        )
        if formatted is not None:
            return formatted
    normalized = fmt.strip().upper().rstrip(".")
    numeric = float(value) if isinstance(value, (int, float)) else None
    if numeric is not None and re.fullmatch(r"\d+\.\d+", normalized):
        width, digits = normalized.split(".")
        return f"{numeric:{int(width)}.{int(digits)}f}".strip()
    if numeric is not None and numeric.is_integer():
        return str(int(numeric))
    return str(value)


def _input(value: object, informat: str) -> float | None:
    if _missing(value):
        return None
    text = str(value).strip()
    name = informat.strip().upper().rstrip(".")
    try:
        if name.startswith(("E8601DA", "YYMMDD")):
            return float((date.fromisoformat(text[:10]) - _EPOCH_DATE).days)
        if name.startswith("MMDDYY"):
            month, day, year = (int(part) for part in text[:10].split("/"))
            return float((date(year, month, day) - _EPOCH_DATE).days)
        if name.startswith("DDMMYY"):
            day, month, year = (int(part) for part in text[:10].split("/"))
            return float((date(year, month, day) - _EPOCH_DATE).days)
        if name.startswith("DATE"):
            day = int(text[:2])
            month = _MONTHS[text[2:5].upper()]
            year = int(text[5:])
            return float((date(year, month, day) - _EPOCH_DATE).days)
        if name.startswith(("E8601DT", "ANYDTDTM")):
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return (parsed.astimezone(UTC) - _EPOCH_DATETIME).total_seconds()
        if name.startswith(("E8601TM", "TIME", "HHMM")):
            pieces = [float(part) for part in text.split(":")]
            return (
                pieces[0] * 3600
                + pieces[1] * 60
                + (pieces[2] if len(pieces) > 2 else 0)
            )
        if name.startswith("ANYDTDTE"):
            return float((date.fromisoformat(text[:10]) - _EPOCH_DATE).days)
    except ValueError as error:
        raise ListingExpressionError(
            f'INPUT() cannot read "{text}" using {informat}.'
        ) from error
    raise ListingExpressionError(f"Unsupported INPUT() informat: {informat}")


def _character_value(
    expression: dict[str, object],
    row: dict[str, object],
    metadata: dict[str, VariableMetadata],
    *,
    division_by_zero_missing: bool,
) -> str:
    value = evaluate(
        expression,
        row,
        metadata,
        division_by_zero_missing=division_by_zero_missing,
    )
    variable = (
        metadata.get(str(expression.get("name", "")).casefold())
        if expression["type"] == "variable"
        else None
    )
    return _display(value, variable)


def evaluate(
    expression: dict[str, object],
    row: dict[str, object],
    metadata: dict[str, VariableMetadata],
    *,
    division_by_zero_missing: bool = False,
) -> object:
    kind = expression["type"]
    if kind == "literal":
        return expression["value"]
    if kind == "variable":
        return row.get(str(expression["name"]))
    if kind == "unary":
        value = evaluate(
            expression["value"],
            row,
            metadata,
            division_by_zero_missing=division_by_zero_missing,
        )
        return (
            None
            if _missing(value)
            else (value if expression["operator"] == "+" else -value)
        )
    if kind in {"concat", "binary"}:
        if kind == "concat":
            return _character_value(
                expression["left"],
                row,
                metadata,
                division_by_zero_missing=division_by_zero_missing,
            ) + _character_value(
                expression["right"],
                row,
                metadata,
                division_by_zero_missing=division_by_zero_missing,
            )
        left = evaluate(
            expression["left"],
            row,
            metadata,
            division_by_zero_missing=division_by_zero_missing,
        )
        right = evaluate(
            expression["right"],
            row,
            metadata,
            division_by_zero_missing=division_by_zero_missing,
        )
        if _missing(left) or _missing(right):
            return None
        op = expression["operator"]
        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            if right == 0:
                if division_by_zero_missing:
                    return None
                raise ListingExpressionError("Division by zero in Listing expression.")
            return left / right
    name = str(expression["name"])
    arguments = [
        evaluate(
            argument, row, metadata, division_by_zero_missing=division_by_zero_missing
        )
        if argument["type"] != "format"
        else argument["value"]
        for argument in expression["arguments"]
    ]
    character_arguments = [
        _character_value(
            argument,
            row,
            metadata,
            division_by_zero_missing=division_by_zero_missing,
        )
        if argument["type"] != "format"
        else str(argument["value"])
        for argument in expression["arguments"]
    ]
    if name == "CATS":
        return "".join(value.strip() for value in character_arguments)
    if name == "CATX":
        if not arguments:
            return ""
        delimiter = character_arguments[0]
        return delimiter.join(
            value.strip()
            for value, raw in zip(character_arguments[1:], arguments[1:], strict=True)
            if not _missing(raw)
        )
    if name == "STRIP":
        return character_arguments[0].strip()
    if name == "UPCASE":
        return character_arguments[0].upper()
    if name == "LOWCASE":
        return character_arguments[0].lower()
    if name == "SUBSTR":
        value, start = character_arguments[0], int(arguments[1])
        return (
            value[start - 1 :]
            if len(arguments) == 2
            else value[start - 1 : start - 1 + int(arguments[2])]
        )
    if name == "SCAN":
        parts = character_arguments[0].split(
            character_arguments[2] if len(arguments) > 2 else " "
        )
        index = int(arguments[1]) - 1
        return parts[index] if 0 <= index < len(parts) else ""
    if name == "COALESCE":
        return next((value for value in arguments if not _missing(value)), None)
    if name == "COALESCEC":
        return next((value for value in arguments if not _missing(value)), "")
    if name == "PUT":
        value, fmt = arguments
        source = expression["arguments"][0]
        source_metadata = (
            metadata.get(str(source.get("name", "")).casefold())
            if source["type"] == "variable"
            else None
        )
        return _display(value, source_metadata, str(fmt))
    if name == "INPUT":
        return _input(arguments[0], str(arguments[1]))
    raise ListingExpressionError(f"Unsupported Listing expression function: {name}.")
