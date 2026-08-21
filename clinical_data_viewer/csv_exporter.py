from __future__ import annotations

import csv
import sqlite3
from collections.abc import Callable
from contextlib import closing
from pathlib import Path

from .data_store import _validated_columns, order_clause
from .domain import DatasetHandle, SortSpec
from .filter_engine import CompiledFilter, quote_identifier


class CsvExporter:
    def export(
        self,
        handle: DatasetHandle,
        destination: Path,
        columns: list[str] | tuple[str, ...],
        compiled_filter: CompiledFilter,
        sort: SortSpec | None,
        progress: Callable[[str], None] | None = None,
    ) -> int:
        notify = progress or (lambda _message: None)
        selected = _validated_columns(columns, handle.metadata)
        where = f" WHERE {compiled_filter.sql}" if compiled_filter.sql else ""
        select = ", ".join(quote_identifier(column) for column in selected)
        sql = (
            f"SELECT {select} FROM dataset{where}{order_clause(sort, handle.metadata)}"
        )
        temporary = destination.with_name(destination.name + ".part")
        exported = 0
        try:
            with (
                closing(
                    sqlite3.connect(
                        handle.database_path.resolve().as_uri() + "?mode=ro", uri=True
                    )
                ) as connection,
                temporary.open("w", encoding="utf-8-sig", newline="") as stream,
            ):
                connection.execute("PRAGMA case_sensitive_like=ON")
                writer = csv.writer(stream, lineterminator="\n")
                writer.writerow(selected)
                cursor = connection.execute(sql, compiled_filter.parameters)
                while True:
                    rows = cursor.fetchmany(2_000)
                    if not rows:
                        break
                    # Keep row structure while converting SQLite NULL to an empty CSV field.
                    writer.writerows(
                        tuple("" if value is None else value for value in row)
                        for row in rows
                    )
                    exported += len(rows)
                    notify(f"Exporting rows… {exported:,}")
            temporary.replace(destination)
            return exported
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
