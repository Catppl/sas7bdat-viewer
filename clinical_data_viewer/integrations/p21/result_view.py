from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass

from ...domain import DatasetHandle, DatasetMetadata
from ...filter_engine import quote_identifier
from ...temp_manager import TempManager

EXACT_COLOR = "#dff3e5"
REVIEW_COLOR = "#fff2b2"


@dataclass(frozen=True, slots=True)
class P21ResultSpec:
    domain: str
    rule_id: str
    source_rows: tuple[int, ...]
    row_colors: tuple[tuple[int, str], ...]
    where_text: str = ""
    expected_where: str = ""
    issue_id: str = ""
    materialized_description: str = ""


class P21ResultBuilder:
    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    def build(
        self,
        source: DatasetHandle,
        spec: P21ResultSpec,
        *,
        cancelled: Callable[[], bool] | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> DatasetHandle:
        directory = self.temp_manager.create_dataset_directory()
        database = directory / "dataset.sqlite"
        variables = source.metadata.variables
        definitions = ", ".join(
            f"{quote_identifier(item.name)} "
            f"{'REAL' if item.kind == 'numeric' else 'TEXT'}"
            for item in variables
        )
        columns = ", ".join(quote_identifier(item.name) for item in variables)
        target: sqlite3.Connection | None = None
        try:
            target = sqlite3.connect(database)
            target.execute(
                "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "
                + definitions
                + ")"
            )
            source_rows = tuple(int(item) for item in spec.source_rows)
            row_count = 0
            if source_rows:
                source_uri = source.database_path.resolve().as_uri() + "?mode=ro"
                with closing(sqlite3.connect(source_uri, uri=True)) as input_connection:
                    input_connection.execute(
                        "CREATE TEMP TABLE selected_source_rows "
                        "(ordinal INTEGER PRIMARY KEY, source_row INTEGER NOT NULL)"
                    )
                    for offset in range(0, len(source_rows), 2_000):
                        if cancelled is not None and cancelled():
                            raise RuntimeError("P21 result creation cancelled.")
                        input_connection.executemany(
                            "INSERT INTO selected_source_rows VALUES (?, ?)",
                            (
                                (ordinal, source_row)
                                for ordinal, source_row in enumerate(
                                    source_rows[offset : offset + 2_000],
                                    start=offset + 1,
                                )
                            ),
                        )
                    cursor = input_connection.execute(
                        f"SELECT selected.ordinal, {columns} FROM dataset AS source "
                        "INNER JOIN selected_source_rows AS selected "
                        "ON selected.source_row = source._source_row "
                        "ORDER BY selected.ordinal"
                    )
                    insert = (
                        "INSERT INTO dataset VALUES ("
                        + ", ".join("?" for _ in range(len(variables) + 1))
                        + ")"
                    )
                    while rows := cursor.fetchmany(2_000):
                        if cancelled is not None and cancelled():
                            raise RuntimeError("P21 result creation cancelled.")
                        target.executemany(insert, rows)
                        row_count += len(rows)
                        if progress is not None:
                            progress(row_count)
                if row_count != len(source_rows):
                    raise RuntimeError(
                        "Current data changed while building the P21 result view; "
                        f"expected {len(source_rows):,} candidate rows but found "
                        f"{row_count:,}."
                    )
            target.execute(
                "CREATE TABLE cache_info (cached_rows INTEGER NOT NULL, "
                "total_rows INTEGER, complete INTEGER NOT NULL)"
            )
            target.execute(
                "INSERT INTO cache_info VALUES (?, ?, 1)", (row_count, row_count)
            )
            target.commit()
            target.close()
            marker = directory / "p21-result.tmp"
            marker.touch()
            return DatasetHandle(
                source.source_path.parent / "P21 Result View.query",
                marker,
                database,
                DatasetMetadata("P21 Result View", row_count, variables),
                row_count,
                True,
                kind="p21-result",
                display_source=f"P21 candidate records from {source.source_path}",
            )
        except BaseException:
            if target is not None:
                target.close()
            self.temp_manager.remove_dataset(directory)
            raise
