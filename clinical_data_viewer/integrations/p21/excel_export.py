from __future__ import annotations

import os
import re
import sqlite3
import tempfile
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import xlsxwriter

from ...domain import DatasetHandle
from ...excel_exporter import EXCEL_MAX_ROWS, _temporal
from ...filter_engine import quote_identifier
from ...sas_value_formatter import temporal_kind_for_metadata

_INVALID_SHEET_CHARACTERS = re.compile(r"[\\/?*\[\]:]")
_METADATA_COLUMNS = (
    "_P21_RULE_ID",
    "_P21_MATCH_STATUS",
    "_P21_ISSUE_ID",
    "_P21_SHEET_ROW",
)


@dataclass(frozen=True, slots=True)
class P21ExportRow:
    source_row: int
    status: str
    issue_id: str
    issue_sheet_row: int


@dataclass(frozen=True, slots=True)
class P21RuleExport:
    rule_id: str
    p21_issue_count: int
    rows: tuple[P21ExportRow, ...]


@dataclass(frozen=True, slots=True)
class P21DomainExportSpec:
    domain: str
    report_name: str
    source_fingerprint: str
    rules: tuple[P21RuleExport, ...]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> P21DomainExportSpec:
        domain = payload.get("domain")
        report_name = payload.get("report_name", "")
        source_fingerprint = payload.get("source_fingerprint")
        raw_rules = payload.get("rules")
        if not isinstance(domain, str) or not domain.strip():
            raise ValueError("P21 export requires a domain.")
        if (
            not isinstance(report_name, str)
            or not isinstance(source_fingerprint, str)
            or not source_fingerprint
            or not isinstance(raw_rules, list)
        ):
            raise TypeError("P21 export report name or rules are invalid.")
        rules: list[P21RuleExport] = []
        for raw_rule in raw_rules:
            if not isinstance(raw_rule, dict):
                raise TypeError("P21 export rules must be objects.")
            rule_id = raw_rule.get("rule_id")
            issue_count = raw_rule.get("p21_issue_count")
            raw_rows = raw_rule.get("rows")
            if (
                not isinstance(rule_id, str)
                or not rule_id.strip()
                or not isinstance(issue_count, int)
                or issue_count < 0
                or not isinstance(raw_rows, list)
            ):
                raise ValueError("P21 export rule metadata is invalid.")
            rows: list[P21ExportRow] = []
            for raw_row in raw_rows:
                if not isinstance(raw_row, dict):
                    raise TypeError("P21 export rows must be objects.")
                try:
                    source_row = int(raw_row["source_row"])
                    issue_sheet_row = int(raw_row["issue_sheet_row"])
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError(
                        "P21 export row identifiers are invalid."
                    ) from error
                status = raw_row.get("status")
                issue_id = raw_row.get("issue_id")
                if (
                    source_row < 1
                    or issue_sheet_row < 1
                    or not isinstance(status, str)
                    or not isinstance(issue_id, str)
                ):
                    raise ValueError("P21 export row metadata is invalid.")
                rows.append(P21ExportRow(source_row, status, issue_id, issue_sheet_row))
            rules.append(P21RuleExport(rule_id, issue_count, tuple(rows)))
        return cls(domain.strip(), report_name, source_fingerprint, tuple(rules))


class P21DomainExcelExporter:
    def export(
        self,
        source: DatasetHandle,
        spec: P21DomainExportSpec,
        destination: Path,
        *,
        progress: Callable[[str], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> int:
        variables = source.metadata.variables
        if len(variables) + len(_METADATA_COLUMNS) > 16_384:
            raise ValueError("P21 Excel export exceeds Excel's column limit.")
        notify = progress or (lambda _message: None)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.stem}-",
            suffix=".part.xlsx",
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        exported = 0
        try:
            with closing(
                sqlite3.connect(
                    source.database_path.resolve().as_uri() + "?mode=ro", uri=True
                )
            ) as connection:
                connection.execute(
                    "CREATE TEMP TABLE p21_export_selection ("
                    "ordinal INTEGER PRIMARY KEY, source_row INTEGER NOT NULL, "
                    "status TEXT NOT NULL, issue_id TEXT NOT NULL, "
                    "issue_sheet_row INTEGER NOT NULL)"
                )
                with xlsxwriter.Workbook(
                    str(temporary),
                    {
                        "constant_memory": True,
                        "strings_to_formulas": False,
                        "strings_to_urls": False,
                    },
                ) as book:
                    formats = _formats(book)
                    plans = _sheet_plans(spec.rules)
                    _write_summary(book, spec, plans, formats["title"])
                    _write_export_info(
                        book,
                        source,
                        spec,
                        formats["title"],
                        sum(len(r.rows) for r in spec.rules),
                    )
                    select = ", ".join(
                        f"source.{quote_identifier(variable.name)}"
                        for variable in variables
                    )
                    for rule, chunks in plans:
                        for sheet_name, rows in chunks:
                            if cancelled is not None and cancelled():
                                raise RuntimeError("P21 Excel export cancelled.")
                            connection.execute("DELETE FROM p21_export_selection")
                            connection.executemany(
                                "INSERT INTO p21_export_selection VALUES (?, ?, ?, ?, ?)",
                                (
                                    (
                                        ordinal,
                                        row.source_row,
                                        row.status,
                                        row.issue_id,
                                        row.issue_sheet_row,
                                    )
                                    for ordinal, row in enumerate(rows, start=1)
                                ),
                            )
                            cursor = connection.execute(
                                f"SELECT {select}, selection.status, "
                                "selection.issue_id, selection.issue_sheet_row "
                                "FROM p21_export_selection AS selection "
                                "INNER JOIN dataset AS source "
                                "ON source._source_row = selection.source_row "
                                "ORDER BY selection.ordinal"
                            )
                            written = _write_rule_sheet(
                                book,
                                sheet_name,
                                rule.rule_id,
                                variables,
                                cursor,
                                formats,
                                cancelled,
                            )
                            if written != len(rows):
                                raise RuntimeError(
                                    f"Current {spec.domain} data changed during export; "
                                    f"expected {len(rows):,} rows for {rule.rule_id} but "
                                    f"found {written:,}."
                                )
                            exported += written
                            notify(
                                f"Exporting P21 Excel… {exported:,} rows "
                                f"({rule.rule_id})"
                            )
            os.replace(temporary, destination)
            return exported
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise


def _formats(book: xlsxwriter.Workbook) -> dict[str, Any]:
    fills = {"exact": "#DFF3E5", "review": "#FFF2B2"}
    result: dict[str, Any] = {
        "title": book.add_format({"bold": True, "bg_color": "#DCEAF6"}),
        "exact": book.add_format({"bg_color": fills["exact"]}),
        "review": book.add_format({"bg_color": fills["review"]}),
    }
    for status, fill in fills.items():
        for kind, number_format in {
            "date": "yyyy-mm-dd",
            "datetime": "yyyy-mm-dd hh:mm:ss",
            "time": "hh:mm:ss",
        }.items():
            result[f"{status}_{kind}"] = book.add_format(
                {"bg_color": fill, "num_format": number_format}
            )
    return result


def _sheet_plans(
    rules: tuple[P21RuleExport, ...],
) -> list[tuple[P21RuleExport, list[tuple[str, tuple[P21ExportRow, ...]]]]]:
    used = {"export summary", "export info"}
    plans = []
    limit = EXCEL_MAX_ROWS - 1
    for rule in rules:
        chunks = []
        for offset in range(0, len(rule.rows), limit):
            rows = rule.rows[offset : offset + limit]
            part = offset // limit + 1 if len(rule.rows) > limit else None
            chunks.append((_sheet_name(rule.rule_id, len(rows), used, part=part), rows))
        plans.append((rule, chunks))
    return plans


def _sheet_name(
    rule_id: str,
    row_count: int,
    used: set[str],
    *,
    part: int | None = None,
) -> str:
    cleaned = _INVALID_SHEET_CHARACTERS.sub("_", rule_id).strip(" '") or "Rule"
    part_suffix = f"-{part}" if part is not None else ""
    count_suffix = f" ({row_count})"
    collision = 1
    while True:
        collision_suffix = "" if collision == 1 else f"~{collision}"
        available = 31 - len(part_suffix + collision_suffix + count_suffix)
        candidate = (
            cleaned[: max(1, available)] + part_suffix + collision_suffix + count_suffix
        )
        key = candidate.casefold()
        if key not in used:
            used.add(key)
            return candidate
        collision += 1


def _write_summary(
    book: xlsxwriter.Workbook,
    spec: P21DomainExportSpec,
    plans: list[tuple[P21RuleExport, list[tuple[str, tuple[P21ExportRow, ...]]]]],
    title,
) -> None:
    sheet = book.add_worksheet("Export Summary")
    headers = (
        "Rule ID",
        "P21 Issue Count",
        "Exact Export Rows",
        "Review Export Rows",
        "Total Export Rows",
        "Worksheets",
    )
    for column, header in enumerate(headers):
        sheet.write_string(0, column, header, title)
    for row_index, (rule, chunks) in enumerate(plans, start=1):
        exact = sum("Exact" in row.status for row in rule.rows)
        review = len(rule.rows) - exact
        values = (
            rule.rule_id,
            rule.p21_issue_count,
            exact,
            review,
            len(rule.rows),
            ", ".join(name for name, _rows in chunks),
        )
        for column, value in enumerate(values):
            sheet.write(row_index, column, value)
    sheet.freeze_panes(1, 0)
    sheet.autofilter(0, 0, len(plans), len(headers) - 1)
    for column, width in enumerate((28, 18, 18, 20, 18, 48)):
        sheet.set_column(column, column, width)


def _write_export_info(
    book: xlsxwriter.Workbook,
    source: DatasetHandle,
    spec: P21DomainExportSpec,
    title,
    row_count: int,
) -> None:
    sheet = book.add_worksheet("Export Info")
    values = (
        ("Domain", spec.domain),
        ("P21 Report", spec.report_name),
        ("Current Dataset", source.metadata.name),
        ("Source", source.display_source or str(source.source_path)),
        ("Exported At", datetime.now().astimezone().isoformat(timespec="seconds")),
        ("Export Rows", row_count),
        ("Duplicate Policy", "Preserved per P21 issue and matched current record"),
    )
    for row_index, (label, value) in enumerate(values):
        sheet.write_string(row_index, 0, label, title)
        sheet.write(row_index, 1, value)
    sheet.set_column(0, 0, 22)
    sheet.set_column(1, 1, 80)


def _write_rule_sheet(
    book: xlsxwriter.Workbook,
    sheet_name: str,
    rule_id: str,
    variables,
    cursor,
    formats: dict[str, Any],
    cancelled: Callable[[], bool] | None,
) -> int:
    sheet = book.add_worksheet(sheet_name)
    headers = [variable.name for variable in variables] + list(_METADATA_COLUMNS)
    widths = [len(name) for name in headers]
    for column, header in enumerate(headers):
        sheet.write_string(0, column, header, formats["title"])
    written = 0
    while batch := cursor.fetchmany(2_000):
        if cancelled is not None and cancelled():
            raise RuntimeError("P21 Excel export cancelled.")
        for record in batch:
            written += 1
            values = record[: len(variables)]
            status, issue_id, issue_sheet_row = record[len(variables) :]
            style_name = "exact" if "Exact" in status else "review"
            base_format = formats[style_name]
            for column, (variable, value) in enumerate(
                zip(variables, values, strict=True)
            ):
                temporal = _temporal(value, variable)
                if temporal is not None:
                    kind = temporal_kind_for_metadata(variable)
                    sheet.write_datetime(
                        written,
                        column,
                        temporal,
                        formats[f"{style_name}_{kind}"],
                    )
                    display = str(temporal)
                elif isinstance(value, str):
                    if len(value) > 32_767:
                        raise ValueError(
                            f"Text in {variable.name} exceeds Excel's cell limit."
                        )
                    sheet.write_string(written, column, value, base_format)
                    display = value
                else:
                    sheet.write(written, column, value, base_format)
                    display = "" if value is None else str(value)
                widths[column] = min(70, max(widths[column], len(display)))
            metadata = (rule_id, status, issue_id, issue_sheet_row)
            for offset, value in enumerate(metadata, start=len(variables)):
                sheet.write(written, offset, value, base_format)
                widths[offset] = min(70, max(widths[offset], len(str(value))))
    sheet.freeze_panes(1, 0)
    sheet.autofilter(0, 0, written, len(headers) - 1)
    for column, width in enumerate(widths):
        sheet.set_column(column, column, min(72, max(12, width + 2)))
    return written
