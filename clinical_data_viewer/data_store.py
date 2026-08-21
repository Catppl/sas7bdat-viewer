from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .domain import DatasetMetadata, FindResult, PageResult, SortSpec
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
