from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .domain import DatasetMetadata
from .filter_engine import CompiledFilter, quote_identifier


@dataclass(frozen=True, slots=True)
class StatisticsResult:
    variable: str
    label: str
    subject_count: int | None
    values: dict[str, float | int | None]
    filtered_rows: int
    confidence: float
    base_decimals: int = 0


def observed_decimal_places(value: float) -> int:
    """Infer meaningful decimal places from a normalized numeric value."""
    try:
        decimal = Decimal(str(value)).normalize()
    except (InvalidOperation, ValueError):
        return 0
    return min(4, max(0, -decimal.as_tuple().exponent))


def maximum_observed_decimals(values: list[float]) -> int:
    return max((observed_decimal_places(value) for value in values), default=0)


def qntldef5(values: list[float], probability: float) -> float | None:
    """SAS QNTLDEF=5: empirical distribution function with averaging."""
    if not values:
        return None
    ordered = sorted(values)
    position = len(ordered) * probability
    if position <= 0:
        return ordered[0]
    if position >= len(ordered):
        return ordered[-1]
    integer = int(position)
    if math.isclose(position, integer):
        return (ordered[integer - 1] + ordered[integer]) / 2
    return ordered[integer]


def calculate_value_statistics(
    values: list[float],
    filtered_rows: int,
    subject_count: int | None,
    confidence: float,
    requested: set[str] | None = None,
) -> dict[str, float | int | None]:
    selected = requested or {
        "subjects",
        "n",
        "nmiss",
        "mean",
        "std",
        "stderr",
        "median",
        "q1",
        "q3",
        "min",
        "max",
        "lclm",
        "uclm",
    }
    count = len(values)
    result: dict[str, float | int | None] = {
        "subjects": subject_count,
        "n": count,
        "nmiss": filtered_rows - count,
        "mean": None,
        "std": None,
        "stderr": None,
        "median": qntldef5(values, 0.5) if "median" in selected else None,
        "q1": qntldef5(values, 0.25) if "q1" in selected else None,
        "q3": qntldef5(values, 0.75) if "q3" in selected else None,
        "min": min(values) if values and "min" in selected else None,
        "max": max(values) if values and "max" in selected else None,
        "lclm": None,
        "uclm": None,
    }
    if not values:
        return result
    needs_mean = bool(selected & {"mean", "std", "stderr", "lclm", "uclm"})
    if not needs_mean:
        return result
    mean = math.fsum(values) / count
    if "mean" in selected:
        result["mean"] = mean
    if count <= 1:
        return result
    variance = math.fsum((value - mean) ** 2 for value in values) / (count - 1)
    standard_deviation = math.sqrt(variance)
    standard_error = standard_deviation / math.sqrt(count)
    if "std" in selected:
        result["std"] = standard_deviation
    if "stderr" in selected:
        result["stderr"] = standard_error
    if not selected & {"lclm", "uclm"}:
        return result
    try:
        from scipy.stats import t
    except ImportError as error:
        raise RuntimeError(
            "SciPy is required to calculate PROC MEANS confidence limits."
        ) from error
    critical = float(t.ppf((1 + confidence) / 2, count - 1))
    if "lclm" in selected:
        result["lclm"] = mean - critical * standard_error
    if "uclm" in selected:
        result["uclm"] = mean + critical * standard_error
    return result


def calculate_statistics(
    database_path: Path,
    metadata: DatasetMetadata,
    variable_name: str,
    compiled_filter: CompiledFilter,
    confidence: float = 0.95,
) -> StatisticsResult:
    variables = {variable.name.upper(): variable for variable in metadata.variables}
    try:
        variable = variables[variable_name.upper()]
    except KeyError as error:
        raise ValueError(f"Unknown variable: {variable_name}") from error
    if variable.kind != "numeric":
        raise ValueError(f"PROC MEANS requires a numeric variable: {variable.name}")
    if not 0 < confidence < 1:
        raise ValueError("Confidence level must be between 0 and 1.")

    column = quote_identifier(variable.name)
    where = f" WHERE {compiled_filter.sql}" if compiled_filter.sql else ""
    nonmissing = f"{column} IS NOT NULL"
    filtered_nonmissing = (
        f"{compiled_filter.sql} AND {nonmissing}" if compiled_filter.sql else nonmissing
    )
    uri = database_path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        filtered_rows = int(
            connection.execute(
                f"SELECT count(*) FROM dataset{where}", compiled_filter.parameters
            ).fetchone()[0]
        )
        rows = connection.execute(
            f"SELECT {column} FROM dataset WHERE {filtered_nonmissing}",
            compiled_filter.parameters,
        )
        values = [float(row[0]) for row in rows]
        subject_count: int | None = None
        subject = variables.get("USUBJID")
        if subject is not None:
            subject_column = quote_identifier(subject.name)
            subject_missing = (
                f"{subject_column} IS NOT NULL AND {subject_column} != ''"
                if subject.kind == "character"
                else f"{subject_column} IS NOT NULL"
            )
            subject_count = int(
                connection.execute(
                    "SELECT count(DISTINCT "
                    f"{subject_column}) FROM dataset WHERE {filtered_nonmissing} "
                    f"AND {subject_missing}",
                    compiled_filter.parameters,
                ).fetchone()[0]
            )

    result = calculate_value_statistics(
        values, filtered_rows, subject_count, confidence
    )
    return StatisticsResult(
        variable.name,
        variable.label,
        subject_count,
        result,
        filtered_rows,
        confidence,
        maximum_observed_decimals(values),
    )
