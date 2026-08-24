from __future__ import annotations

import math
import sqlite3
from contextlib import closing
from dataclasses import dataclass
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

    count = len(values)
    missing = filtered_rows - count
    result: dict[str, float | int | None] = {
        "subjects": subject_count,
        "n": count,
        "nmiss": missing,
        "mean": None,
        "std": None,
        "stderr": None,
        "median": qntldef5(values, 0.5),
        "q1": qntldef5(values, 0.25),
        "q3": qntldef5(values, 0.75),
        "min": min(values) if values else None,
        "max": max(values) if values else None,
        "lclm": None,
        "uclm": None,
    }
    if values:
        mean = math.fsum(values) / count
        result["mean"] = mean
        if count > 1:
            variance = math.fsum((value - mean) ** 2 for value in values) / (count - 1)
            standard_deviation = math.sqrt(variance)
            standard_error = standard_deviation / math.sqrt(count)
            result["std"] = standard_deviation
            result["stderr"] = standard_error
            try:
                from scipy.stats import t
            except ImportError as error:
                raise RuntimeError(
                    "SciPy is required to calculate PROC MEANS confidence limits."
                ) from error
            critical = float(t.ppf((1 + confidence) / 2, count - 1))
            result["lclm"] = mean - critical * standard_error
            result["uclm"] = mean + critical * standard_error
    return StatisticsResult(
        variable.name,
        variable.label,
        subject_count,
        result,
        filtered_rows,
        confidence,
    )
