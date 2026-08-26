from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

from ..domain import DatasetHandle
from ..filter_engine import quote_identifier
from ..temp_manager import TempManager
from .models import MergeDatasetsConfig, MergeResult, MergeSummary
from .result_store import MergeResultWriter, build_result_schema

ProgressCallback = Callable[[str], None]


def _source_uri(path: Path) -> str:
    return path.resolve().as_uri() + "?mode=ro"


class MergeDatasetsEngine:
    """Run safe two-dataset joins without loading complete datasets into pandas."""

    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    def validate(
        self,
        left: DatasetHandle,
        right: DatasetHandle,
        config: MergeDatasetsConfig,
    ) -> tuple[str, ...]:
        config.validate()
        if left is right or left.database_path == right.database_path:
            raise ValueError("Left and Right datasets must be different.")
        if not left.cache_complete or not right.cache_complete:
            raise ValueError("Both datasets must be fully cached before merging.")
        left_variables = {
            variable.name.casefold(): variable for variable in left.metadata.variables
        }
        right_variables = {
            variable.name.casefold(): variable for variable in right.metadata.variables
        }
        resolved: list[str] = []
        for requested in config.by_variables:
            key = requested.casefold()
            if key not in left_variables or key not in right_variables:
                missing = "Left" if key not in left_variables else "Right"
                raise ValueError(
                    f'BY variable "{requested}" is missing from {missing} dataset.'
                )
            left_variable = left_variables[key]
            right_variable = right_variables[key]
            if left_variable.kind != right_variable.kind:
                raise ValueError(
                    f'BY variable "{requested}" has incompatible types: '
                    f"Left: {left_variable.kind}; Right: {right_variable.kind}."
                )
            resolved.append(left_variable.name)
        if len({name.casefold() for name in resolved}) != len(resolved):
            raise ValueError("BY variables must be unique.")
        return tuple(resolved)

    @staticmethod
    def _attach(
        connection: sqlite3.Connection, alias: str, handle: DatasetHandle
    ) -> None:
        connection.execute(
            f"ATTACH DATABASE ? AS {alias}", (_source_uri(handle.database_path),)
        )

    @staticmethod
    def _valid_value(alias: str, variable, *, side: str) -> str:
        column = f"{alias}.{quote_identifier(variable)}"
        if side == "character":
            return f"{column} IS NOT NULL AND length(trim({column})) > 0"
        return f"{column} IS NOT NULL"

    def _join_condition(
        self,
        left: DatasetHandle,
        right: DatasetHandle,
        by_variables: tuple[str, ...],
    ) -> str:
        left_by = {
            variable.name.casefold(): variable for variable in left.metadata.variables
        }
        right_by = {
            variable.name.casefold(): variable for variable in right.metadata.variables
        }
        parts = []
        for name in by_variables:
            left_variable = left_by[name.casefold()]
            right_variable = right_by[name.casefold()]
            left_column = f"l.{quote_identifier(left_variable.name)}"
            right_column = f"r.{quote_identifier(right_variable.name)}"
            valid_left = self._valid_value(
                "l", left_variable.name, side=left_variable.kind
            )
            valid_right = self._valid_value(
                "r", right_variable.name, side=right_variable.kind
            )
            parts.append(
                f"({valid_left} AND {valid_right} AND {left_column} = {right_column})"
            )
        return " AND ".join(parts)

    @staticmethod
    def _key_columns(alias: str, variables: tuple[str, ...]) -> str:
        return ", ".join(
            f"{alias}.{quote_identifier(variable)}" for variable in variables
        )

    def _duplicate_key_count(
        self,
        connection: sqlite3.Connection,
        alias: str,
        variables: tuple[str, ...],
        metadata,
    ) -> int:
        group_columns = self._key_columns("source", variables)
        valid = " AND ".join(
            self._valid_value(
                "source",
                variable,
                side=next(
                    item.kind
                    for item in metadata.variables
                    if item.name.casefold() == variable.casefold()
                ),
            )
            for variable in variables
        )
        return int(
            connection.execute(
                f"SELECT count(*) FROM (SELECT {group_columns} FROM {alias}.dataset AS source "
                f"WHERE {valid} GROUP BY {group_columns} HAVING count(*) > 1)"
            ).fetchone()[0]
        )

    def inspect(
        self,
        left: DatasetHandle,
        right: DatasetHandle,
        config: MergeDatasetsConfig,
    ) -> MergeSummary:
        by_variables = self.validate(left, right, config)
        connection = sqlite3.connect(":memory:")
        try:
            self._attach(connection, "left_db", left)
            self._attach(connection, "right_db", right)
            left_dupes = self._duplicate_key_count(
                connection, "left_db", by_variables, left.metadata
            )
            right_dupes = self._duplicate_key_count(
                connection, "right_db", by_variables, right.metadata
            )
            left_by = {
                variable.name.casefold(): variable
                for variable in left.metadata.variables
            }
            left_group = self._key_columns(
                "l", tuple(left_by[name.casefold()].name for name in by_variables)
            )
            condition = self._join_condition(left, right, by_variables)
            many_to_many = int(
                connection.execute(
                    "SELECT count(*) FROM ("
                    f"SELECT {left_group} FROM left_db.dataset AS l "
                    f"JOIN right_db.dataset AS r ON {condition} "
                    f"GROUP BY {left_group} "
                    "HAVING count(DISTINCT l._source_row) > 1 "
                    "AND count(DISTINCT r._source_row) > 1"
                    ")"
                ).fetchone()[0]
            )
            return MergeSummary(
                left.metadata.row_count,
                right.metadata.row_count,
                left_dupes,
                right_dupes,
                many_to_many,
            )
        finally:
            connection.close()

    def run(
        self,
        left: DatasetHandle,
        right: DatasetHandle,
        config: MergeDatasetsConfig,
        progress: ProgressCallback | None = None,
    ) -> MergeResult:
        notify = progress or (lambda _message: None)
        by_variables = self.validate(left, right, config)
        schema, right_mapping = build_result_schema(
            left.metadata, right.metadata, by_variables
        )
        sort_by = self._resolve_sort(schema, config)
        result_directory = self.temp_manager.create_dataset_directory()
        database_path = result_directory / "dataset.sqlite"
        writer = MergeResultWriter(database_path, schema)
        connection = writer.connection
        try:
            notify("Preparing SQLite merge…")
            self._attach(connection, "left_db", left)
            self._attach(connection, "right_db", right)
            left_by = {
                variable.name.casefold(): variable
                for variable in left.metadata.variables
            }
            right_by = {
                variable.name.casefold(): variable
                for variable in right.metadata.variables
            }
            resolved_left_by = tuple(
                left_by[name.casefold()].name for name in by_variables
            )
            condition = self._join_condition(left, right, by_variables)
            select: list[str] = []
            for left_name in resolved_left_by:
                right_name = right_by[left_name.casefold()].name
                select.append(
                    f"CASE WHEN l._source_row IS NULL "
                    f"THEN r.{quote_identifier(right_name)} "
                    f"ELSE l.{quote_identifier(left_name)} END AS "
                    f"{quote_identifier(left_name)}"
                )
            by_set = {name.casefold() for name in resolved_left_by}
            for variable in left.metadata.variables:
                if variable.name.casefold() in by_set:
                    continue
                select.append(
                    f"l.{quote_identifier(variable.name)} AS {quote_identifier(variable.name)}"
                )
            for source_name, output_name, _kind in right_mapping:
                select.append(
                    f"r.{quote_identifier(source_name)} AS {quote_identifier(output_name)}"
                )
            status = quote_identifier(schema.status_column)
            left_source = quote_identifier(schema.left_source_row_column)
            right_source = quote_identifier(schema.right_source_row_column)
            select.extend(
                (
                    (
                        f"CASE WHEN l._source_row IS NULL THEN 'RIGHT_ONLY' "
                        f"WHEN r._source_row IS NULL THEN 'LEFT_ONLY' "
                        f"ELSE 'MATCHED' END AS {status}"
                    ),
                    f"l._source_row AS {left_source}",
                    f"r._source_row AS {right_source}",
                )
            )
            insert_columns = (
                "INSERT INTO dataset ("
                + ", ".join(
                    quote_identifier(variable.name) for variable in schema.all_variables
                )
                + ") "
            )
            select_sql = ", ".join(select)
            user_order = self._sort_clause(sort_by, schema)
            left_default_order = (
                f"{quote_identifier(schema.left_source_row_column)} ASC, "
                f"{quote_identifier(schema.right_source_row_column)} ASC"
            )
            right_default_order = (
                f"{quote_identifier(schema.right_source_row_column)} ASC, "
                f"{quote_identifier(schema.left_source_row_column)} ASC"
            )
            left_from = (
                "FROM left_db.dataset AS l LEFT JOIN right_db.dataset AS r ON "
                + condition
            )
            right_from = (
                "FROM right_db.dataset AS r LEFT JOIN left_db.dataset AS l ON "
                + condition
            )
            left_join_query = (
                insert_columns
                + "SELECT "
                + select_sql
                + " "
                + left_from
                + " ORDER BY "
                + (user_order or left_default_order)
            )
            if config.join_type == "left":
                queries = (left_join_query,)
            elif config.join_type == "inner":
                queries = (
                    insert_columns
                    + "SELECT "
                    + select_sql
                    + " FROM left_db.dataset AS l INNER JOIN right_db.dataset AS r ON "
                    + condition
                    + " ORDER BY "
                    + (user_order or left_default_order),
                )
            elif config.join_type == "right":
                queries = (
                    insert_columns
                    + "SELECT "
                    + select_sql
                    + " "
                    + right_from
                    + " ORDER BY "
                    + (user_order or right_default_order),
                )
            else:
                full_default_order = (
                    f"CASE WHEN {quote_identifier(schema.left_source_row_column)} "
                    "IS NULL THEN 1 ELSE 0 END ASC, "
                    f"{quote_identifier(schema.left_source_row_column)} ASC, "
                    f"{quote_identifier(schema.right_source_row_column)} ASC"
                )
                right_only_select = (
                    "SELECT "
                    + select_sql
                    + " "
                    + right_from
                    + " WHERE l._source_row IS NULL"
                )
                full_query = (
                    insert_columns
                    + "SELECT * FROM ("
                    + "SELECT "
                    + select_sql
                    + " "
                    + left_from
                    + " UNION ALL "
                    + right_only_select
                    + ") AS merged_rows ORDER BY "
                    + (user_order or full_default_order)
                )
                queries = (full_query,)
            notify("Joining source datasets…")
            for query in queries:
                connection.execute(query)
            connection.commit()
            summary = self._result_summary(
                connection,
                schema.status_column,
                left,
                right,
                by_variables,
            )
            handle = writer.finish(
                result_directory, left, right, summary, by_variables, config.join_type
            )
            return MergeResult(handle, summary)
        except BaseException:
            writer.abort()
            self.temp_manager.remove_dataset(result_directory)
            raise

    @staticmethod
    def _resolve_sort(
        schema, config: MergeDatasetsConfig
    ) -> tuple[tuple[str, str], ...]:
        available = {
            variable.name.casefold(): variable.name for variable in schema.all_variables
        }
        resolved: list[tuple[str, str]] = []
        for item in config.sort_by:
            output_name = available.get(item.variable.casefold())
            if output_name is None:
                raise ValueError(
                    f'Sort variable "{item.variable}" does not exist in the Merge Result.'
                )
            resolved.append((output_name, item.direction))
        return tuple(resolved)

    @staticmethod
    def _sort_clause(sort_by: tuple[tuple[str, str], ...], schema) -> str:
        if not sort_by:
            return ""
        parts = [
            f"{quote_identifier(variable)} {direction}"
            for variable, direction in sort_by
        ]
        parts.extend(
            (
                f"{quote_identifier(schema.left_source_row_column)} ASC",
                f"{quote_identifier(schema.right_source_row_column)} ASC",
            )
        )
        return ", ".join(parts)

    def _result_summary(
        self,
        connection: sqlite3.Connection,
        status_column: str,
        left: DatasetHandle,
        right: DatasetHandle,
        by_variables: tuple[str, ...],
    ) -> MergeSummary:
        duplicate_summary = self.inspect(
            left,
            right,
            MergeDatasetsConfig(by_variables, "left"),
        )
        counts = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                f"SELECT {quote_identifier(status_column)}, count(*) FROM dataset "
                f"GROUP BY {quote_identifier(status_column)}"
            )
        }
        return MergeSummary(
            left.metadata.row_count,
            right.metadata.row_count,
            duplicate_summary.left_duplicate_keys,
            duplicate_summary.right_duplicate_keys,
            duplicate_summary.many_to_many_keys,
            merged_rows=sum(counts.values()),
            matched_rows=counts.get("MATCHED", 0),
            left_only_rows=counts.get("LEFT_ONLY", 0),
            right_only_rows=counts.get("RIGHT_ONLY", 0),
        )
