from __future__ import annotations

import hashlib
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException

from ..models import P21ImportDiagnostics, P21Issue, P21Report
from .header_detection import HeaderDetectionError, find_required_header

ISSUE_DETAILS_SHEET = "Issue Details"


class P21ReportError(RuntimeError):
    """Base class for user-facing report import errors."""


class MissingWorksheetError(P21ReportError):
    pass


class WorkbookReadError(P21ReportError):
    pass


class P21ReportReader:
    def __init__(self, *, header_scan_limit: int = 100) -> None:
        if header_scan_limit < 1:
            raise ValueError("header_scan_limit must be positive")
        self.header_scan_limit = header_scan_limit

    def read(self, source_path: str | Path) -> P21Report:
        path = Path(source_path).expanduser().resolve()
        if not path.is_file():
            raise WorkbookReadError(f"P21 report does not exist: {path.name}")

        fingerprint = _sha256(path)
        workbook = None
        try:
            workbook = load_workbook(
                filename=path,
                read_only=True,
                data_only=True,
                keep_links=False,
            )
            if ISSUE_DETAILS_SHEET not in workbook.sheetnames:
                raise MissingWorksheetError(
                    f'Worksheet "{ISSUE_DETAILS_SHEET}" was not found.'
                )
            worksheet = workbook[ISSUE_DETAILS_SHEET]
            header = find_required_header(
                worksheet.iter_rows(
                    min_row=1,
                    max_row=self.header_scan_limit,
                    values_only=True,
                ),
                scan_limit=self.header_scan_limit,
            )
            return self._read_issues(path, fingerprint, worksheet, header)
        except (MissingWorksheetError, HeaderDetectionError):
            raise
        except (BadZipFile, InvalidFileException, OSError, ValueError) as exc:
            raise WorkbookReadError(f"Unable to read P21 report: {exc}") from exc
        finally:
            if workbook is not None:
                workbook.close()

    def _read_issues(self, path, fingerprint, worksheet, header) -> P21Report:
        issues: list[P21Issue] = []
        scanned_data_rows = 0
        blank_rows = 0
        ignored_count_present_rows = 0
        ignored_record_blank_rows = 0

        for row_number, row in enumerate(
            worksheet.iter_rows(min_row=header.row_number + 1, values_only=True),
            start=header.row_number + 1,
        ):
            if _row_is_blank(row):
                blank_rows += 1
                continue
            scanned_data_rows += 1
            values = {
                name: _value_at(row, index)
                for name, index in header.column_indexes.items()
            }
            if not _is_blank(values["Count"]):
                ignored_count_present_rows += 1
                continue
            if _is_blank(values["Record"]):
                ignored_record_blank_rows += 1
                continue

            issues.append(
                P21Issue(
                    issue_id=f"{fingerprint}:{row_number}",
                    sheet_row=row_number,
                    dataset=_text(values["Dataset"]).strip(),
                    record=_text(values["Record"]).strip(),
                    count=_text(values["Count"]).strip(),
                    variables=_text(values["Variables"]).strip(),
                    values=_text(values["Values"]),
                    rule_id=_text(values["Rule ID"]).strip(),
                    message=_text(values["Message"]).strip(),
                    impact=_text(values["Impact"]).strip(),
                    issue_type=_text(values["Type"]).strip(),
                )
            )

        domains = tuple(
            sorted(
                {issue.dataset for issue in issues if issue.dataset},
                key=str.casefold,
            )
        )
        diagnostics = P21ImportDiagnostics(
            header_row=header.row_number,
            scanned_data_rows=scanned_data_rows,
            blank_rows=blank_rows,
            eligible_issue_rows=len(issues),
            ignored_count_present_rows=ignored_count_present_rows,
            ignored_record_blank_rows=ignored_record_blank_rows,
        )
        return P21Report(
            source_path=path,
            fingerprint=fingerprint,
            worksheet_name=ISSUE_DETAILS_SHEET,
            issues=tuple(issues),
            diagnostics=diagnostics,
            domains=domains,
        )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_is_blank(row: tuple[Any, ...]) -> bool:
    return all(_is_blank(value) for value in row)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _value_at(row: tuple[Any, ...], index: int) -> Any:
    return row[index] if index < len(row) else None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)
