from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class P21Issue:
    """One eligible row from the P21 Issue Details worksheet."""

    issue_id: str
    sheet_row: int
    dataset: str
    record: str
    count: str
    variables: str
    values: str
    rule_id: str
    message: str
    impact: str
    issue_type: str


@dataclass(frozen=True, slots=True)
class P21ImportDiagnostics:
    """Deterministic counts describing how a report was imported."""

    header_row: int
    scanned_data_rows: int
    blank_rows: int
    eligible_issue_rows: int
    ignored_count_present_rows: int
    ignored_record_blank_rows: int


@dataclass(frozen=True, slots=True)
class P21Report:
    """Parsed first-version view of a P21 report."""

    source_path: Path
    fingerprint: str
    worksheet_name: str
    issues: tuple[P21Issue, ...]
    diagnostics: P21ImportDiagnostics
    domains: tuple[str, ...]
