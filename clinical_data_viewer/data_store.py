from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .domain import DatasetMetadata, PageResult, SortSpec
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
    if sort is None:
        return " ORDER BY _source_row ASC"
    allowed = {variable.name for variable in metadata.variables}
    if sort.variable not in allowed:
        raise ValueError(f"Unknown sort variable: {sort.variable}")
    direction = "ASC" if sort.ascending else "DESC"
    return f" ORDER BY {quote_identifier(sort.variable)} {direction}, _source_row ASC"


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
