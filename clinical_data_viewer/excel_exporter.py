"""Streaming, read-only Excel export of an applied dataset view."""

from __future__ import annotations

import json
import math
import os
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path

import xlsxwriter

from .data_store import DataStore, _validated_columns, order_clause
from .domain import DatasetHandle, SortSpec, VariableMetadata
from .filter_engine import CompiledFilter, quote_identifier
from .sas_value_formatter import temporal_kind_for_metadata

EXCEL_MAX_ROWS = 1_048_576
LARGE_EXPORT_BYTES = 100_000_000
_EPOCH = datetime(1960, 1, 1)  # noqa: DTZ001 - SAS dates use a timezone-free epoch.
_MANUAL_COLORS = {
    "light purple": "#E8DDFF",
    "light green": "#DFF3E5",
    "light orange": "#FFE4C2",
}


@dataclass(frozen=True, slots=True)
class ExcelExportView:
    handle: DatasetHandle
    columns: tuple[str, ...]
    compiled_filter: CompiledFilter
    sort: SortSpec | None
    applied_filter_text: str = ""
    manual_highlights: tuple[tuple[int, str], ...] = ()
    comparison_rows: frozenset[int] = frozenset()
    comparison_columns: frozenset[str] = frozenset()


def _metadata_selected(view: ExcelExportView) -> list[str]:
    valid = _validated_columns(view.columns, view.handle.metadata)
    excluded = set(view.handle.metadata.export_excluded_columns)
    return [column for column in valid if column not in excluded]


def _selected(view: ExcelExportView) -> list[str]:
    result = _metadata_selected(view)
    if not result:
        raise ValueError("No exportable columns are selected.")
    if len(result) > 16_384:
        raise ValueError("Excel supports at most 16,384 columns per sheet.")
    return result


def _connect(view: ExcelExportView) -> sqlite3.Connection:
    connection = sqlite3.connect(
        view.handle.database_path.resolve().as_uri() + "?mode=ro", uri=True
    )
    connection.execute("PRAGMA case_sensitive_like=ON")
    return connection


def _temporal(
    value: object, variable: VariableMetadata
) -> date | datetime | time | None:
    kind = temporal_kind_for_metadata(variable)
    if kind is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value):
        return None
    try:
        if kind == "date":
            result = (_EPOCH + timedelta(days=value)).date()
            return result if result.year >= 1900 else None
        if kind == "datetime":
            result = _EPOCH + timedelta(seconds=value)
            return result if result.year >= 1900 else None
        # Excel time values are fractions of a day; preserve raw seconds for
        # durations outside one day, which cannot be represented as clock time.
        if kind == "time" and 0 <= value < 86_400:
            origin = datetime(2000, 1, 1) + timedelta(seconds=value)  # noqa: DTZ001
            return origin.time()
    except (OverflowError, ValueError):
        pass
    return None


def _write_metadata_sheet(
    book: xlsxwriter.Workbook,
    metadata,
    selected: list[str],
    title,
) -> None:
    """Write the canonical Metadata sheet shared by both Excel exports."""

    meta_sheet = book.add_worksheet("Metadata")
    for col, label in enumerate(
        ("Name", "Label", "Type", "Length", "Format", "Exported")
    ):
        meta_sheet.write_string(0, col, label, title)
    selected_names = set(selected)
    for row_index, variable in enumerate(metadata.variables, start=1):
        for col, value in enumerate(
            (
                variable.name,
                variable.label,
                variable.kind,
                variable.length,
                variable.format,
                "Yes" if variable.name in selected_names else "No",
            )
        ):
            meta_sheet.write(row_index, col, value)
    meta_sheet.freeze_panes(1, 0)
    meta_sheet.autofilter(0, 0, len(metadata.variables), 5)
    for col, width in enumerate((24, 55, 16, 12, 20, 13)):
        meta_sheet.set_column(col, col, width)


class ExcelExporter:
    def estimate_bytes(self, view: ExcelExportView) -> tuple[int, int]:
        """Estimate the uncompressed data payload; this is not XLSX file size."""
        columns = _selected(view)
        where = DataStore._where_clause(view.handle.metadata, view.compiled_filter)
        select = ", ".join(quote_identifier(column) for column in columns)
        with closing(_connect(view)) as connection:
            count = int(
                connection.execute(
                    f"SELECT count(*) FROM dataset{where}",
                    view.compiled_filter.parameters,
                ).fetchone()[0]
            )
            sample = connection.execute(
                f"SELECT {select} FROM dataset{where} LIMIT 200",
                view.compiled_filter.parameters,
            ).fetchall()
        if not sample:
            return count, 0
        average = sum(
            sum(
                len(str(value).encode("utf-8")) if value is not None else 0
                for value in row
            )
            + 16 * len(columns)
            for row in sample
        ) / len(sample)
        return count, int(average * count)

    def export(
        self,
        view: ExcelExportView,
        destination: Path,
        progress: Callable[[str], None] | None = None,
    ) -> int:
        selected = _selected(view)
        variables = {
            variable.name: variable for variable in view.handle.metadata.variables
        }
        where = DataStore._where_clause(view.handle.metadata, view.compiled_filter)
        metadata = view.handle.metadata
        extra = [
            name
            for name in (metadata.diff_columns_column, metadata.row_warning_column)
            if name
        ]
        select = ", ".join(
            quote_identifier(name) for name in ["_source_row", *selected, *extra]
        )
        sql = f"SELECT {select} FROM dataset{where}{order_clause(view.sort, metadata)}"
        manual = dict(view.manual_highlights)
        notify = progress or (lambda _message: None)
        exported = 0
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.stem}-", suffix=".part.xlsx", dir=destination.parent
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with closing(_connect(view)) as connection:
                count = int(
                    connection.execute(
                        f"SELECT count(*) FROM dataset{where}",
                        view.compiled_filter.parameters,
                    ).fetchone()[0]
                )
                if count >= EXCEL_MAX_ROWS:
                    raise ValueError(
                        "Excel can hold at most 1,048,575 data rows in one Data sheet."
                    )
                with xlsxwriter.Workbook(
                    str(temporary),
                    {
                        "constant_memory": True,
                        "strings_to_formulas": False,
                        "strings_to_urls": False,
                    },
                ) as book:
                    title = book.add_format({"bold": True, "bg_color": "#DCEAF6"})
                    date_format = book.add_format({"num_format": "yyyy-mm-dd"})
                    datetime_format = book.add_format(
                        {"num_format": "yyyy-mm-dd hh:mm:ss"}
                    )
                    time_format = book.add_format({"num_format": "hh:mm:ss"})
                    colors = {
                        "warning": book.add_format({"bg_color": "#FFD9D9"}),
                        "comparison": book.add_format({"bg_color": "#FFF2B2"}),
                        **{
                            name: book.add_format({"bg_color": color})
                            for name, color in _MANUAL_COLORS.items()
                        },
                    }
                    temporal_formats = {
                        (color_name, kind): book.add_format(
                            {"bg_color": fill, "num_format": number_format}
                        )
                        for color_name, fill in {
                            "warning": "#FFD9D9",
                            "comparison": "#FFF2B2",
                            **_MANUAL_COLORS,
                        }.items()
                        for kind, number_format in {
                            "date": "yyyy-mm-dd",
                            "datetime": "yyyy-mm-dd hh:mm:ss",
                            "time": "hh:mm:ss",
                        }.items()
                    }
                    info = book.add_worksheet("Export Info")
                    details = [
                        ("Dataset", metadata.name),
                        (
                            "Source",
                            view.handle.display_source or str(view.handle.source_path),
                        ),
                        (
                            "Exported At",
                            datetime.now().astimezone().isoformat(timespec="seconds"),
                        ),
                        ("Applied Filter", view.applied_filter_text or "(none)"),
                        (
                            "Sort",
                            (
                                f"{view.sort.variable} {'ASC' if view.sort.ascending else 'DESC'}"
                            )
                            if view.sort
                            else "(source order)",
                        ),
                        ("Filtered Rows", count),
                    ]
                    for row_index, (label, value) in enumerate(details):
                        info.write_string(row_index, 0, label, title)
                        info.write(row_index, 1, value)
                    info.write_string(len(details) + 1, 0, "Exported Columns", title)
                    for row_index, name in enumerate(selected, start=len(details) + 2):
                        info.write_string(row_index, 0, name)
                    info.set_column(0, 0, 22)
                    info.set_column(1, 1, 75)

                    _write_metadata_sheet(book, metadata, selected, title)

                    data = book.add_worksheet("Data")
                    widths = [len(name) for name in selected]
                    for col, name in enumerate(selected):
                        data.write_string(0, col, name, title)
                    cursor = connection.execute(sql, view.compiled_filter.parameters)
                    while batch := cursor.fetchmany(2_000):
                        for record in batch:
                            exported += 1
                            if exported >= EXCEL_MAX_ROWS:
                                raise ValueError("Excel Data sheet row limit exceeded.")
                            source_row = int(record[0])
                            values = record[1 : 1 + len(selected)]
                            extras = record[1 + len(selected) :]
                            diff = (
                                frozenset(json.loads(extras[0] or "[]"))
                                if metadata.diff_columns_column
                                else frozenset()
                            )
                            warning = (
                                bool(extras[int(bool(metadata.diff_columns_column))])
                                if metadata.row_warning_column
                                else False
                            )
                            for col, (name, value) in enumerate(
                                zip(selected, values, strict=True)
                            ):
                                color_name = None
                                if warning or name in metadata.warning_columns:
                                    color_name = "warning"
                                elif name in diff or (
                                    exported - 1 in view.comparison_rows
                                    and name in view.comparison_columns
                                ):
                                    color_name = "comparison"
                                elif source_row in manual:
                                    candidate = manual[source_row].casefold()
                                    color_name = (
                                        candidate if candidate in colors else None
                                    )
                                color = colors.get(color_name)
                                temporal = _temporal(value, variables[name])
                                if temporal is not None:
                                    kind = temporal_kind_for_metadata(variables[name])
                                    default = {
                                        "date": date_format,
                                        "datetime": datetime_format,
                                        "time": time_format,
                                    }[kind]
                                    fmt = (
                                        temporal_formats[(color_name, kind)]
                                        if color_name
                                        else default
                                    )
                                    data.write_datetime(exported, col, temporal, fmt)
                                    display = str(temporal)
                                elif isinstance(value, str):
                                    if len(value) > 32_767:
                                        raise ValueError(
                                            f"Text in {name} exceeds Excel's 32,767-character cell limit."
                                        )
                                    data.write_string(exported, col, value, color)
                                    display = value
                                else:
                                    data.write(exported, col, value, color)
                                    display = "" if value is None else str(value)
                                widths[col] = min(
                                    70, max(widths[col], min(len(display), 70))
                                )
                        notify(f"Exporting Excel rows… {exported:,}")
                    data.freeze_panes(1, 0)
                    data.autofilter(0, 0, exported, len(selected) - 1)
                    for col, width in enumerate(widths):
                        data.set_column(col, col, min(72, max(12, width + 2)))
            os.replace(temporary, destination)
            return exported
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def export_metadata(self, view: ExcelExportView, destination: Path) -> int:
        """Export only the same Metadata sheet used by the full workbook."""

        selected = _metadata_selected(view)
        metadata = view.handle.metadata
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.stem}-",
            suffix=".part.xlsx",
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with xlsxwriter.Workbook(
                str(temporary),
                {
                    "constant_memory": True,
                    "strings_to_formulas": False,
                    "strings_to_urls": False,
                },
            ) as book:
                title = book.add_format({"bold": True, "bg_color": "#DCEAF6"})
                _write_metadata_sheet(book, metadata, selected, title)
            os.replace(temporary, destination)
            return len(metadata.variables)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
