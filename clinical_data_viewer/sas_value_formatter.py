"""Metadata-driven SAS date, datetime, and time presentation helpers.

The Viewer deliberately keeps SAS values in their original numeric form in
SQLite.  This module is only for presentation and for converting explicit SAS
temporal literals in a WHERE condition back to their numeric representation.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .domain import VariableMetadata

_SAS_EPOCH_DATE = date(1960, 1, 1)
_SAS_EPOCH_DATETIME = datetime(1960, 1, 1)  # noqa: DTZ001 - SAS datetimes are naive.


@dataclass(frozen=True, slots=True)
class SasTemporalFormat:
    kind: str
    family: str
    width: int | None
    decimals: int


@dataclass(frozen=True, slots=True)
class SasTemporalLiteral:
    text: str
    kind: str


_FORMAT_PREFIXES: tuple[tuple[str, str, str], ...] = (
    ("E8601DT", "datetime", "e8601dt"),
    ("B8601DT", "datetime", "b8601dt"),
    ("DATETIME", "datetime", "datetime"),
    ("E8601DA", "date", "e8601da"),
    ("B8601DA", "date", "b8601da"),
    ("YYMMDDS", "date", "yymmds"),
    ("YYMMDDP", "date", "yymmdp"),
    ("YYMMDDN", "date", "yymmddn"),
    ("YYMMDD", "date", "yymmdd"),
    ("MMDDYY", "date", "mmddyy"),
    ("DDMMYY", "date", "ddmmyy"),
    ("MONYY", "date", "monyy"),
    ("DATE", "date", "date"),
    ("E8601TM", "time", "e8601tm"),
    ("B8601TM", "time", "b8601tm"),
    ("HHMM", "time", "hhmm"),
    ("TIME", "time", "time"),
)
_WIDTH = re.compile(r"^(?P<width>\d+)?(?:\.(?P<decimals>\d+))?\.?$")


def parse_sas_temporal_format(format_text: str) -> SasTemporalFormat | None:
    """Return a supported temporal SAS format, without looking at its name."""
    normalized = str(format_text or "").strip().upper()
    if not normalized:
        return None
    for prefix, kind, family in _FORMAT_PREFIXES:
        if not normalized.startswith(prefix):
            continue
        match = _WIDTH.fullmatch(normalized[len(prefix) :])
        if match is None:
            continue
        return SasTemporalFormat(
            kind=kind,
            family=family,
            width=(
                int(match.group("width")) if match.group("width") is not None else None
            ),
            decimals=int(match.group("decimals") or 0),
        )
    return None


def temporal_kind_for_metadata(metadata: VariableMetadata) -> str | None:
    if metadata.kind != "numeric":
        return None
    parsed = parse_sas_temporal_format(metadata.format)
    return parsed.kind if parsed is not None else None


def format_sas_value(raw_value: object, metadata: VariableMetadata) -> str | None:
    """Format one raw numeric value when metadata declares a temporal format.

    ``None`` means that the caller should retain its existing display behavior.
    No value is modified or persisted by this function.
    """
    parsed = (
        parse_sas_temporal_format(metadata.format)
        if metadata.kind == "numeric"
        else None
    )
    if parsed is None or not _is_finite_number(raw_value):
        return None
    try:
        value = float(raw_value)
        if parsed.kind == "date":
            return _format_date(_SAS_EPOCH_DATE + timedelta(days=int(value)), parsed)
        if parsed.kind == "datetime":
            return _format_datetime(
                _SAS_EPOCH_DATETIME + timedelta(seconds=value), parsed
            )
        return _format_time(value, parsed)
    except (OverflowError, ValueError):
        return None


def sas_temporal_literal_value(
    literal: SasTemporalLiteral, metadata: VariableMetadata
) -> int | float:
    """Convert a parsed SAS temporal literal to the numeric SAS raw value."""
    if metadata.kind != "numeric":
        raise ValueError(
            f"{metadata.name} must be a numeric variable to use a SAS temporal literal."
        )
    expected_kind = temporal_kind_for_metadata(metadata)
    if expected_kind is None:
        raise ValueError(
            f"{metadata.name} has no supported SAS date, datetime, or time format."
        )
    if literal.kind != expected_kind:
        raise ValueError(
            f"{metadata.name} uses a SAS {expected_kind} format; "
            f"use a {expected_kind} literal instead."
        )
    try:
        if literal.kind == "date":
            return (_parse_date_literal(literal.text) - _SAS_EPOCH_DATE).days
        if literal.kind == "datetime":
            return (
                _parse_datetime_literal(literal.text) - _SAS_EPOCH_DATETIME
            ).total_seconds()
        return _parse_time_literal(literal.text)
    except ValueError as error:
        suffix = {"date": "d", "datetime": "dt", "time": "t"}[literal.kind]
        raise ValueError(
            f"Invalid SAS {literal.kind} literal: {literal.text!r}{suffix}"
        ) from error


def _is_finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _format_date(value: date, parsed: SasTemporalFormat) -> str:
    family = parsed.family
    if family == "date":
        year = (
            f"{value.year:04d}"
            if (parsed.width or 9) >= 9
            else f"{value.year % 100:02d}"
        )
        return f"{value.day:02d}{value.strftime('%b').upper()}{year}"
    if family == "monyy":
        year = (
            f"{value.year:04d}"
            if (parsed.width or 7) >= 7
            else f"{value.year % 100:02d}"
        )
        return f"{value.strftime('%b').upper()}{year}"
    if family in {"e8601da", "yymmdd"}:
        return value.strftime("%Y-%m-%d")
    if family == "b8601da" or family == "yymmddn":
        return value.strftime("%Y%m%d")
    if family == "yymmdp":
        return value.strftime("%Y.%m.%d")
    if family == "yymmds":
        return value.strftime("%Y/%m/%d")
    if family == "mmddyy":
        return value.strftime("%m/%d/%Y")
    if family == "ddmmyy":
        return value.strftime("%d/%m/%Y")
    return value.isoformat()


def _format_datetime(value: datetime, parsed: SasTemporalFormat) -> str:
    fraction = _fraction(value.microsecond, parsed.decimals)
    if parsed.family == "datetime":
        year = (
            f"{value.year:04d}"
            if (parsed.width or 18) >= 18
            else f"{value.year % 100:02d}"
        )
        return (
            f"{value.day:02d}{value.strftime('%b').upper()}{year}:"
            f"{value.hour:02d}:{value.minute:02d}:{value.second:02d}{fraction}"
        )
    if parsed.family == "e8601dt":
        return value.strftime("%Y-%m-%dT%H:%M:%S") + fraction
    return value.strftime("%Y%m%dT%H%M%S") + fraction


def _format_time(raw_seconds: float, parsed: SasTemporalFormat) -> str:
    decimals = parsed.decimals
    rounded = Decimal(str(raw_seconds)).quantize(
        Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP
    )
    whole_seconds = int(rounded)
    fraction = _fraction_from_decimal(rounded, decimals)
    hours, remainder = divmod(whole_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if parsed.family == "b8601tm":
        return f"{hours:02d}{minutes:02d}{seconds:02d}{fraction}"
    if parsed.family == "hhmm" or (
        parsed.family == "time" and (parsed.width or 8) <= 5
    ):
        return f"{hours:02d}:{minutes:02d}"
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{fraction}"


def _fraction(microseconds: int, decimals: int) -> str:
    if decimals <= 0:
        return ""
    digits = f"{microseconds:06d}"[:decimals].ljust(decimals, "0")
    return f".{digits}"


def _fraction_from_decimal(value: Decimal, decimals: int) -> str:
    if decimals <= 0:
        return ""
    fraction = abs(value - Decimal(int(value)))
    digits = f"{fraction:.{decimals}f}".split(".", 1)[1]
    return f".{digits}"


def _parse_date_literal(text: str) -> date:
    for pattern in ("%Y-%m-%d", "%Y%m%d", "%d%b%Y", "%d%b%y"):
        try:
            return datetime.strptime(text.upper(), pattern).date()  # noqa: DTZ007
        except ValueError:
            continue
    raise ValueError(text)


def _parse_datetime_literal(text: str) -> datetime:
    normalized = text.replace(" ", "T", 1)
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        for pattern in ("%d%b%Y:%H:%M:%S", "%d%b%y:%H:%M:%S"):
            try:
                return datetime.strptime(text.upper(), pattern)  # noqa: DTZ007
            except ValueError:
                continue
    raise ValueError(text)


def _parse_time_literal(text: str) -> float:
    parsed = datetime.strptime(  # noqa: DTZ007
        text, "%H:%M:%S.%f" if "." in text else "%H:%M:%S"
    )
    return (
        parsed.hour * 3600
        + parsed.minute * 60
        + parsed.second
        + parsed.microsecond / 1_000_000
    )
