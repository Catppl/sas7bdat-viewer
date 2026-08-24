from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .domain import (
    ComparedRow,
    DatasetMetadata,
    DistinctValuesResult,
    FindResult,
    PageResult,
    RowComparisonResult,
    SortSpec,
)
from .filter_engine import CompiledFilter, quote_identifier


def _validated_columns(
    columns: list[str] | tuple[str, ...], metadata: DatasetMetadata
) -> list[str]:
    allowed = {variable.name for variable in metadata.variables}
    requested = list(columns)
    if not requested or any(column not in allowed for column in requested):
        raise ValueError("At least one valid visible variable is required.")
    return requested


def order_clause(sort: SortSpec | None, metadata: DatasetMetadata) -> str:
    return f" ORDER BY {order_expression(sort, metadata)}"


def order_expression(sort: SortSpec | None, metadata: DatasetMetadata) -> str:
    if sort is None:
        return "_source_row ASC"
    allowed = {variable.name for variable in metadata.variables}
    if sort.variable not in allowed:
        raise ValueError(f"Unknown sort variable: {sort.variable}")
    direction = "ASC" if sort.ascending else "DESC"
    return f"{quote_identifier(sort.variable)} {direction}, _source_row ASC"


class DataStore:
    def query_page(
        self,
        database_path: Path,
        metadata: DatasetMetadata,
        columns: list[str] | tuple[str, ...],
        compiled_filter: CompiledFilter,
        sort: SortSpec | None,
        offset: int,
        limit: int,
        known_count: int | None = None,
    ) -> PageResult:
        selected = _validated_columns(columns, metadata)
        where = f" WHERE {compiled_filter.sql}" if compiled_filter.sql else ""
        with closing(
            sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as connection:
            connection.execute("PRAGMA case_sensitive_like=ON")
            filtered_count = known_count
            if filtered_count is None:
                filtered_count = int(
                    connection.execute(
                        f"SELECT count(*) FROM dataset{where}",
                        compiled_filter.parameters,
                    ).fetchone()[0]
                )
            select = ", ".join(quote_identifier(column) for column in selected)
            sql = f"SELECT {select} FROM dataset{where}{order_clause(sort, metadata)} LIMIT ? OFFSET ?"
            rows = connection.execute(
                sql, (*compiled_filter.parameters, int(limit), int(offset))
            ).fetchall()
        return PageResult(tuple(tuple(row) for row in rows), filtered_count)

    def find_text(
        self,
        database_path: Path,
        metadata: DatasetMetadata,
        columns: list[str] | tuple[str, ...],
        compiled_filter: CompiledFilter,
        sort: SortSpec | None,
        text: str,
        start_row: int,
        *,
        forward: bool = True,
    ) -> FindResult | None:
        selected = _validated_columns(columns, metadata)
        if not text:
            return None
        where = f" WHERE {compiled_filter.sql}" if compiled_filter.sql else ""
        select = ", ".join(quote_identifier(column) for column in selected)
        matches = " OR ".join(
            f"instr(lower(CAST(COALESCE({quote_identifier(column)}, '') AS TEXT)), "
            "lower(?)) > 0"
            for column in selected
        )
        comparison = ">" if forward else "<"
        direction = "ASC" if forward else "DESC"
        sql = (
            "WITH current_view AS ("
            f"SELECT ROW_NUMBER() OVER (ORDER BY {order_expression(sort, metadata)}) - 1 "
            f"AS _view_row, {select} FROM dataset{where}"
            ") "
            f"SELECT * FROM current_view WHERE _view_row {comparison} ? "
            f"AND ({matches}) ORDER BY _view_row {direction} LIMIT 1"
        )

        def query(connection: sqlite3.Connection, boundary: int):
            parameters = (
                *compiled_filter.parameters,
                boundary,
                *(text for _column in selected),
            )
            return connection.execute(sql, parameters).fetchone()

        with closing(
            sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as connection:
            row = query(connection, start_row)
            if row is None:
                wrap_boundary = -1 if forward else metadata.row_count
                row = query(connection, wrap_boundary)
        if row is None:
            return None
        needle = text.casefold()
        matched_column = selected[0]
        for column, value in zip(selected, row[1:], strict=True):
            if needle in ("" if value is None else str(value)).casefold():
                matched_column = column
                break
        return FindResult(int(row[0]), matched_column)

    def distinct_values(
        self,
        database_path: Path,
        metadata: DatasetMetadata,
        variable_name: str,
        compiled_filter: CompiledFilter,
        *,
        limit: int = 2_000,
    ) -> DistinctValuesResult:
        variable_by_name = {
            variable.name.upper(): variable for variable in metadata.variables
        }
        try:
            variable = variable_by_name[variable_name.upper()]
        except KeyError as error:
            raise ValueError(f"Unknown variable: {variable_name}") from error
        column = quote_identifier(variable.name)
        where = f" WHERE {compiled_filter.sql}" if compiled_filter.sql else ""
        missing = (
            f"({column} IS NULL OR {column} = '')"
            if variable.kind == "character"
            else f"{column} IS NULL"
        )
        nonmissing_where = (
            f" WHERE ({compiled_filter.sql}) AND NOT ({missing})"
            if compiled_filter.sql
            else f" WHERE NOT ({missing})"
        )
        with closing(
            sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as connection:
            total_distinct = int(
                connection.execute(
                    "SELECT count(*) FROM ("
                    f"SELECT DISTINCT {column} FROM dataset{nonmissing_where})",
                    compiled_filter.parameters,
                ).fetchone()[0]
            )
            has_missing = bool(
                connection.execute(
                    f"SELECT EXISTS(SELECT 1 FROM dataset{where} "
                    f"{'AND' if where else 'WHERE'} {missing} LIMIT 1)",
                    compiled_filter.parameters,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"SELECT DISTINCT {column} FROM dataset{nonmissing_where} "
                f"ORDER BY {column} LIMIT ?",
                (*compiled_filter.parameters, limit),
            ).fetchall()
        return DistinctValuesResult(
            tuple(row[0] for row in rows),
            has_missing,
            total_distinct,
            total_distinct > limit,
        )

    def compare_view_rows(
        self,
        database_path: Path,
        metadata: DatasetMetadata,
        compiled_filter: CompiledFilter,
        sort: SortSpec | None,
        view_rows: list[int] | tuple[int, ...],
    ) -> RowComparisonResult:
        requested = sorted({int(row) for row in view_rows})
        if len(requested) < 2:
            raise ValueError("Select at least two rows to compare.")
        if len(requested) > 20:
            raise ValueError("Row comparison supports at most 20 rows at a time.")
        columns = [variable.name for variable in metadata.variables]
        select = ", ".join(quote_identifier(column) for column in columns)
        where = f" WHERE {compiled_filter.sql}" if compiled_filter.sql else ""
        placeholders = ", ".join("?" for _row in requested)
        sql = (
            "WITH current_view AS ("
            f"SELECT ROW_NUMBER() OVER (ORDER BY {order_expression(sort, metadata)}) - 1 "
            f"AS _view_row, {select} FROM dataset{where}"
            ") SELECT * FROM current_view WHERE _view_row IN ("
            f"{placeholders}) ORDER BY _view_row"
        )
        with closing(
            sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as connection:
            rows = connection.execute(
                sql, (*compiled_filter.parameters, *requested)
            ).fetchall()
        compared = tuple(ComparedRow(int(row[0]), tuple(row[1:])) for row in rows)
        differing: list[str] = []
        if compared:
            metadata_by_name = {
                variable.name: variable for variable in metadata.variables
            }
            for index, variable_name in enumerate(columns):
                variable = metadata_by_name[variable_name]
                values = [
                    None
                    if row.values[index] is None
                    or (variable.kind == "character" and row.values[index] == "")
                    else row.values[index]
                    for row in compared
                ]
                first = values[0]
                if any(value != first for value in values[1:]):
                    differing.append(variable_name)
        return RowComparisonResult(compared, tuple(differing))
