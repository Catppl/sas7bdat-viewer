from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

from p21_issue_explorer.models import ComparisonResult


def is_expected_missing(value: str) -> bool:
    return value.strip().casefold() == "null"


def is_actual_missing(value: object, sas_type: str) -> bool:
    if value is None:
        return True
    return sas_type == "character" and isinstance(value, str) and value == ""


def parse_decimal(value: object) -> Decimal:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"Not a numeric value: {value!r}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"Not a finite numeric value: {value!r}")
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"Not a numeric value: {value!r}") from error
    if not result.is_finite():
        raise ValueError(f"Not a finite numeric value: {value!r}")
    return result


def canonical_decimal(value: object) -> str:
    number = parse_decimal(value)
    if number == 0:
        return "0"
    text = format(number.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def numeric_token_is_valid(value: str) -> bool:
    if is_expected_missing(value):
        return True
    try:
        parse_decimal(value)
    except ValueError:
        return False
    return True


def compare_value(
    expected: str, actual: object, sas_type: str
) -> tuple[ComparisonResult, str]:
    if is_expected_missing(expected):
        if is_actual_missing(actual, sas_type):
            return ComparisonResult.STRICT, "missing value"
        return ComparisonResult.MISMATCH, "expected missing value"
    if is_actual_missing(actual, sas_type):
        return ComparisonResult.MISMATCH, "current value is missing"
    if sas_type == "numeric":
        try:
            matched = parse_decimal(expected) == parse_decimal(actual)
        except ValueError:
            return ComparisonResult.MISMATCH, "invalid numeric value"
        return (
            (ComparisonResult.STRICT, "numeric values are equivalent")
            if matched
            else (ComparisonResult.MISMATCH, "numeric values differ")
        )
    expected_text = expected
    actual_text = str(actual)
    if expected_text == actual_text:
        return ComparisonResult.STRICT, "character values are identical"
    if expected_text.strip().casefold() == actual_text.strip().casefold():
        reasons: list[str] = []
        if expected_text.strip() != actual_text.strip():
            reasons.append("case")
        if expected_text != expected_text.strip() or actual_text != actual_text.strip():
            reasons.append("leading/trailing whitespace")
        return ComparisonResult.LENIENT, " and ".join(
            reasons
        ) or "lenient character match"
    return ComparisonResult.MISMATCH, "character values differ"
