from __future__ import annotations

import json
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
    if metadata.pair_id_column and metadata.side_order_column:
        pair = quote_identifier(metadata.pair_id_column)
        side = quote_identifier(metadata.side_order_column)
        if sort is None:
            return f"{pair} ASC, {side} ASC"
        allowed = {variable.name for variable in metadata.variables}
        if sort.variable not in allowed:
            raise ValueError(f"Unknown sort variable: {sort.variable}")
        column = quote_identifier(sort.variable)
        direction = "ASC" if sort.ascending else "DESC"
        # Sort pairs by their Main value while preserving Main -> QC adjacency.
        pair_value = (
            f"(SELECT paired.{column} FROM dataset AS paired "
            f"WHERE paired.{pair} = dataset.{pair} "
            f"ORDER BY paired.{side} ASC LIMIT 1)"
        )
        return f"{pair_value} {direction}, {pair} ASC, {side} ASC"
    if sort is None:
        return "_source_row ASC"
    allowed = {variable.name for variable in metadata.variables}
    if sort.variable not in allowed:
        raise ValueError(f"Unknown sort variable: {sort.variable}")
    direction = "ASC" if sort.ascending else "DESC"
    return f"{quote_identifier(sort.variable)} {direction}, _source_row ASC"


class DataStore:
    @staticmethod
    def _where_clause(
        metadata: DatasetMetadata, compiled_filter: CompiledFilter
    ) -> str:
        if not compiled_filter.sql:
            return ""
        if metadata.pair_id_column:
            pair = quote_identifier(metadata.pair_id_column)
            return (
                f" WHERE {pair} IN (SELECT DISTINCT {pair} FROM dataset "
                f"WHERE {compiled_filter.sql})"
            )
        return f" WHERE {compiled_filter.sql}"

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
        where = self._where_clause(metadata, compiled_filter)
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
            include_highlights = bool(metadata.diff_columns_column)
            if include_highlights:
                select += ", " + quote_identifier(metadata.diff_columns_column or "")
            include_row_warnings = bool(metadata.row_warning_column)
            if include_row_warnings:
                select += ", " + quote_identifier(metadata.row_warning_column or "")
            sql = f"SELECT {select} FROM dataset{where}{order_clause(sort, metadata)} LIMIT ? OFFSET ?"
            raw_rows = connection.execute(
                sql, (*compiled_filter.parameters, int(limit), int(offset))
            ).fetchall()
        highlights: tuple[frozenset[str], ...] = ()
        hidden_count = int(include_highlights) + int(include_row_warnings)
        if hidden_count:
            rows = tuple(tuple(row[:-hidden_count]) for row in raw_rows)
        else:
            rows = tuple(tuple(row) for row in raw_rows)
        if include_highlights:
            highlight_index = -2 if include_row_warnings else -1
            highlights = tuple(
                frozenset(json.loads(row[highlight_index] or "[]")) for row in raw_rows
            )
        row_warnings = (
            tuple(bool(row[-1]) for row in raw_rows) if include_row_warnings else ()
        )
        return PageResult(rows, filtered_count, highlights, row_warnings)

    def source_row_view_index(
        self,
        database_path: Path,
        metadata: DatasetMetadata,
        compiled_filter: CompiledFilter,
        sort: SortSpec | None,
        source_row: int,
    ) -> int | None:
        where = self._where_clause(metadata, compiled_filter)
        sql = (
            "WITH current_view AS ("
            f"SELECT _source_row, ROW_NUMBER() OVER (ORDER BY "
            f"{order_expression(sort, metadata)}) - 1 AS _view_row "
            f"FROM dataset{where}) "
            "SELECT _view_row FROM current_view WHERE _source_row = ?"
        )
        with closing(
            sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as connection:
            row = connection.execute(
                sql, (*compiled_filter.parameters, int(source_row))
            ).fetchone()
        return None if row is None else int(row[0])

    def compare_navigation_target(
        self,
        database_path: Path,
        metadata: DatasetMetadata,
        compiled_filter: CompiledFilter,
        sort: SortSpec | None,
        view_row: int,
    ) -> tuple[str, int] | None:
        side = metadata.compare_side_column
        source_obs = metadata.source_obs_column
        if side is None or source_obs is None:
            return None
        where = self._where_clause(metadata, compiled_filter)
        sql = (
            "WITH current_view AS ("
            f"SELECT ROW_NUMBER() OVER (ORDER BY {order_expression(sort, metadata)}) - 1 "
            f"AS _view_row, {quote_identifier(side)}, {quote_identifier(source_obs)} "
            f"FROM dataset{where}) "
            "SELECT * FROM current_view WHERE _view_row = ?"
        )
        with closing(
            sqlite3.connect(database_path.resolve().as_uri() + "?mode=ro", uri=True)
        ) as connection:
            row = connection.execute(
                sql, (*compiled_filter.parameters, int(view_row))
            ).fetchone()
        if row is None or row[2] is None:
            return None
        return str(row[1]), int(row[2])

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
        where = self._where_clause(metadata, compiled_filter)
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
        where = self._where_clause(metadata, compiled_filter)
        missing = (
            f"({column} IS NULL OR {column} = '')"
            if variable.kind == "character"
            else f"{column} IS NULL"
        )
        if compiled_filter.sql and metadata.pair_id_column:
            pair = quote_identifier(metadata.pair_id_column)
            nonmissing_where = (
                f" WHERE {pair} IN (SELECT DISTINCT {pair} FROM dataset WHERE "
                f"{compiled_filter.sql}) AND NOT ({missing})"
            )
        elif compiled_filter.sql:
            nonmissing_where = f" WHERE ({compiled_filter.sql}) AND NOT ({missing})"
        else:
            nonmissing_where = f" WHERE NOT ({missing})"
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
        where = self._where_clause(metadata, compiled_filter)
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
