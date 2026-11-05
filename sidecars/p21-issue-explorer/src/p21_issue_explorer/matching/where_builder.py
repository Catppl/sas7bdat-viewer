from __future__ import annotations

import re
from collections.abc import Iterable

from .normalization import canonical_decimal, is_expected_missing

SAS_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class WhereBuildError(ValueError):
    pass


def where_condition(variable: str, value: object, sas_type: str) -> str:
    if not SAS_NAME.fullmatch(variable):
        raise WhereBuildError(f"Unsupported SAS variable name: {variable!r}")
    if value is None or (isinstance(value, str) and is_expected_missing(value)):
        return f"MISSING({variable})"
    if sas_type == "numeric":
        return f"{variable} = {canonical_decimal(value)}"
    escaped = str(value).replace("'", "''")
    return f"{variable} = '{escaped}'"


def combine_and(conditions: Iterable[str]) -> str:
    return " AND ".join(condition for condition in conditions if condition)


def combine_or(clauses: Iterable[str]) -> str:
    unique = tuple(dict.fromkeys(clause for clause in clauses if clause))
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    return " OR ".join(f"({clause})" for clause in unique)
