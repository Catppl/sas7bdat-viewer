from __future__ import annotations

import math
from dataclasses import dataclass

from .domain import VariableMetadata
from .filter_engine import CompiledFilter, quote_identifier


@dataclass(frozen=True, slots=True)
class ColumnFilterSpec:
    variable: str
    mode: str
    values: tuple[object, ...] = ()
    include_missing: bool = True
    operator: str = "="
    lower: object | None = None
    upper: object | None = None


def _literal_text(value: object, variable: VariableMetadata) -> str:
    if variable.kind == "character":
        escaped = str(value).replace('"', '""')
        return f'"{escaped}"'
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{variable.name} requires a numeric filter value.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{variable.name} requires a finite numeric filter value.")
    return format(numeric, ".15g")


def render_column_filter(spec: ColumnFilterSpec, variable: VariableMetadata) -> str:
    """Render an interactive filter as canonical SAS-like WHERE text."""
    name = variable.name
    missing = f"missing({name})"
    not_missing = f"not missing({name})"
    if spec.mode in {"include", "exclude"}:
        values = ", ".join(_literal_text(value, variable) for value in spec.values)
        if spec.mode == "include":
            value_filter = f"{name} in ({values})" if values else ""
            if value_filter and spec.include_missing:
                return f"({value_filter} or {missing})"
            if value_filter:
                return value_filter
            if spec.include_missing:
                return missing
            return f"({name} != {name})"
        value_filter = f"{name} not in ({values})" if values else ""
        if value_filter and spec.include_missing:
            return f"({value_filter} or {missing})"
        if value_filter:
            return f"({value_filter} and {not_missing})"
        return "" if spec.include_missing else not_missing
    if spec.mode == "between":
        return (
            f"{name} between {_literal_text(spec.lower, variable)} "
            f"and {_literal_text(spec.upper, variable)}"
        )
    if spec.mode == "condition":
        return f"{name} {spec.operator} {_literal_text(spec.lower, variable)}"
    if spec.mode == "contains":
        return f"{name} contains {_literal_text(spec.lower, variable)}"
    raise ValueError(f"Unsupported column filter mode: {spec.mode}")


def compose_where_text(
    manual_where: str,
    column_filters: dict[str, ColumnFilterSpec],
    variables: tuple[VariableMetadata, ...],
) -> str:
    metadata = {variable.name.upper(): variable for variable in variables}
    parts: list[str] = []
    manual = manual_where.strip()
    if manual:
        parts.append(manual)
    for variable_name, spec in column_filters.items():
        variable = metadata.get(variable_name.upper())
        if variable is None:
            continue
        rendered = render_column_filter(spec, variable)
        if rendered:
            parts.append(rendered)
    if len(parts) == 1:
        return parts[0]
    if manual:
        parts[0] = f"({parts[0]})"
    return " and ".join(parts)


def _missing_sql(variable: VariableMetadata) -> str:
    column = quote_identifier(variable.name)
    if variable.kind == "character":
        return f"({column} IS NULL OR {column} = '')"
    return f"{column} IS NULL"


def compile_column_filter(
    spec: ColumnFilterSpec, variable: VariableMetadata
) -> CompiledFilter:
    column = quote_identifier(variable.name)
    missing = _missing_sql(variable)
    if spec.mode in {"include", "exclude"}:
        placeholders = ", ".join("?" for _value in spec.values)
        if spec.mode == "include":
            parts: list[str] = []
            parameters: list[object] = []
            if spec.values:
                parts.append(f"{column} IN ({placeholders})")
                parameters.extend(spec.values)
            if spec.include_missing:
                parts.append(missing)
            return CompiledFilter(
                "(" + " OR ".join(parts) + ")" if parts else "0",
                tuple(parameters),
            )
        parts = []
        parameters = []
        if spec.values:
            parts.append(f"({column} NOT IN ({placeholders}) OR {missing})")
            parameters.extend(spec.values)
        if not spec.include_missing:
            parts.append(f"NOT ({missing})")
        return CompiledFilter(
            "(" + " AND ".join(parts) + ")" if parts else "",
            tuple(parameters),
        )
    if spec.mode == "between":
        return CompiledFilter(
            f"({column} >= ? AND {column} <= ?)", (spec.lower, spec.upper)
        )
    if spec.mode == "condition":
        operators = {"=", "!=", ">", ">=", "<", "<="}
        if spec.operator not in operators:
            raise ValueError(f"Unsupported column filter operator: {spec.operator}")
        return CompiledFilter(f"{column} {spec.operator} ?", (spec.lower,))
    if spec.mode == "contains":
        return CompiledFilter(f"instr(COALESCE({column}, ''), ?) > 0", (spec.lower,))
    raise ValueError(f"Unsupported column filter mode: {spec.mode}")


def combine_filters(
    where_filter: CompiledFilter,
    column_filters: dict[str, ColumnFilterSpec],
    variables: tuple[VariableMetadata, ...],
) -> CompiledFilter:
    metadata = {variable.name.upper(): variable for variable in variables}
    parts: list[str] = []
    parameters: list[object] = []
    if where_filter.sql:
        parts.append(f"({where_filter.sql})")
        parameters.extend(where_filter.parameters)
    for variable_name, spec in column_filters.items():
        variable = metadata.get(variable_name.upper())
        if variable is None:
            continue
        compiled = compile_column_filter(spec, variable)
        if compiled.sql:
            parts.append(f"({compiled.sql})")
            parameters.extend(compiled.parameters)
    return CompiledFilter(" AND ".join(parts), tuple(parameters))
