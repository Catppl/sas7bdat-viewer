from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

REQUIRED_COLUMNS = (
    "Dataset",
    "Record",
    "Count",
    "Variables",
    "Values",
    "Rule ID",
    "Message",
    "Impact",
    "Type",
)

_CANONICAL_BY_KEY = {name.casefold(): name for name in REQUIRED_COLUMNS}


@dataclass(frozen=True, slots=True)
class HeaderMatch:
    row_number: int
    column_indexes: dict[str, int]


class HeaderDetectionError(ValueError):
    def __init__(
        self,
        *,
        row_number: int | None,
        missing_columns: tuple[str, ...],
        duplicate_columns: tuple[str, ...],
    ) -> None:
        self.row_number = row_number
        self.missing_columns = missing_columns
        self.duplicate_columns = duplicate_columns
        details: list[str] = []
        if missing_columns:
            details.append("missing: " + ", ".join(missing_columns))
        if duplicate_columns:
            details.append("duplicated: " + ", ".join(duplicate_columns))
        location = f"near worksheet row {row_number}" if row_number else "in first rows"
        message = "Required Issue Details header was not found " + location
        if details:
            message += " (" + "; ".join(details) + ")"
        super().__init__(message)


def normalize_header(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip().casefold()


def find_required_header(
    rows: Iterable[Sequence[Any]],
    *,
    scan_limit: int = 100,
) -> HeaderMatch:
    """Find the first valid required header and report the best invalid candidate."""

    best_row_number: int | None = None
    best_score = -1
    best_counts: Counter[str] = Counter()

    for row_number, row in enumerate(rows, start=1):
        if row_number > scan_limit:
            break
        normalized = [normalize_header(value) for value in row]
        counts = Counter(value for value in normalized if value in _CANONICAL_BY_KEY)
        score = sum(1 for key in _CANONICAL_BY_KEY if counts[key] > 0)
        duplicate_count = sum(max(0, count - 1) for count in counts.values())

        if score == len(REQUIRED_COLUMNS) and duplicate_count == 0:
            indexes = {
                _CANONICAL_BY_KEY[key]: normalized.index(key)
                for key in _CANONICAL_BY_KEY
            }
            return HeaderMatch(row_number, indexes)

        if score > best_score:
            best_row_number = row_number
            best_score = score
            best_counts = counts

    if best_score <= 0:
        raise HeaderDetectionError(
            row_number=None,
            missing_columns=REQUIRED_COLUMNS,
            duplicate_columns=(),
        )

    missing = tuple(
        canonical
        for key, canonical in _CANONICAL_BY_KEY.items()
        if best_counts[key] == 0
    )
    duplicates = tuple(
        canonical
        for key, canonical in _CANONICAL_BY_KEY.items()
        if best_counts[key] > 1
    )
    raise HeaderDetectionError(
        row_number=best_row_number,
        missing_columns=missing,
        duplicate_columns=duplicates,
    )
