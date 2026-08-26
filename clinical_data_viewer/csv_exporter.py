from __future__ import annotations

import csv
import sqlite3
from collections.abc import Callable
from contextlib import closing
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from .data_store import DataStore, _validated_columns, order_clause
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
        highlight_rows: dict[int, str] | None = None,
    ) -> int:
        notify = progress or (lambda _message: None)
        requested = _validated_columns(columns, handle.metadata)
        excluded = set(handle.metadata.export_excluded_columns)
        selected = [column for column in requested if column not in excluded]
        if not selected:
            raise ValueError(
                "No exportable columns are selected. Advanced comparison fields "
                "are never exported."
            )
        where = DataStore._where_clause(handle.metadata, compiled_filter)
        select = ", ".join(quote_identifier(column) for column in selected)
        decimal_base = handle.metadata.decimal_base_column
        if decimal_base:
            select += ", " + quote_identifier(decimal_base)
        include_highlights = bool(highlight_rows)
        select = "_source_row, " + select
        decimal_offsets = dict(handle.metadata.statistic_decimal_offsets)
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
                writer.writerow([*selected, "HIGHLIGHT"] if include_highlights else selected)
                cursor = connection.execute(sql, compiled_filter.parameters)
                while True:
                    rows = cursor.fetchmany(2_000)
                    if not rows:
                        break
                    # Keep row structure while converting SQLite NULL to an empty CSV field.
                    writer.writerows(
                        self._format_export_row(
                            row,
                            selected,
                            decimal_offsets,
                            bool(decimal_base),
                            highlight_rows if include_highlights else None,
                        )
                        for row in rows
                    )
                    exported += len(rows)
                    notify(f"Exporting rows… {exported:,}")
            temporary.replace(destination)
            return exported
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _format_export_row(
        row: tuple[object, ...],
        columns: list[str],
        decimal_offsets: dict[str, int],
        has_decimal_base: bool,
        highlight_rows: dict[int, str] | None = None,
    ) -> tuple[object, ...]:
        source_row = int(row[0])
        data = row[1:]
        base = int(data[-1] or 0) if has_decimal_base else 0
        values = data[:-1] if has_decimal_base else data
        formatted: list[object] = []
        for column, value in zip(columns, values, strict=True):
            if value is None:
                formatted.append("")
            elif column not in decimal_offsets or not isinstance(value, (int, float)):
                formatted.append(value)
            else:
                decimals = min(4, max(0, base + decimal_offsets[column]))
                quantum = Decimal(1).scaleb(-decimals)
                rounded = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
                formatted.append(f"{rounded:.{decimals}f}")
        if highlight_rows is not None:
            formatted.append(highlight_rows.get(source_row, ""))
        return tuple(formatted)
