from __future__ import annotations

from dataclasses import dataclass

from .match import IssueMatchResult, IssueStatus, RuleMatchSummary


@dataclass(frozen=True, slots=True)
class CandidateRowStyle:
    source_row: object
    status: IssueStatus


@dataclass(frozen=True, slots=True)
class IssueOpenRequest:
    domain: str
    rule_id: str
    issue_id: str
    status: IssueStatus
    source_rows: tuple[object, ...]
    row_styles: tuple[CandidateRowStyle, ...]
    expected_where: str
    applied_where: str
    where_text: str

    @classmethod
    def from_result(cls, result: IssueMatchResult) -> IssueOpenRequest:
        rows = tuple(candidate.source_row for candidate in result.candidates)
        return cls(
            domain=result.issue.dataset,
            rule_id=result.issue.rule_id,
            issue_id=result.issue.issue_id,
            status=result.status,
            source_rows=rows,
            row_styles=tuple(
                CandidateRowStyle(candidate.source_row, candidate.status)
                for candidate in result.candidates
            ),
            expected_where=result.expected_where,
            applied_where=result.applied_where,
            where_text=result.applied_where or result.expected_where,
        )


@dataclass(frozen=True, slots=True)
class RuleOpenRequest:
    domain: str
    rule_id: str
    source_rows: tuple[object, ...]
    row_styles: tuple[CandidateRowStyle, ...]
    p21_issue_count: int = 0
    exact_record_count: int = 0
    review_record_count: int = 0
    selection_description: str = "Materialized P21 candidate selection"

    @classmethod
    def from_results(
        cls,
        domain: str,
        summary: RuleMatchSummary,
        results: tuple[IssueMatchResult, ...],
    ) -> RuleOpenRequest:
        rows = tuple(
            candidate.source_row
            for result in results
            for candidate in result.candidates
        )
        styles = tuple(
            CandidateRowStyle(candidate.source_row, candidate.status)
            for result in results
            for candidate in result.candidates
        )
        return cls(
            domain=domain,
            rule_id=summary.rule_id,
            source_rows=rows,
            row_styles=styles,
            p21_issue_count=summary.p21_issue_count,
            exact_record_count=summary.current_exact_record_count,
            review_record_count=summary.current_review_record_count,
        )


@dataclass(frozen=True, slots=True)
class ExportCandidateRow:
    source_row: object
    status: IssueStatus
    issue_id: str
    issue_sheet_row: int


@dataclass(frozen=True, slots=True)
class ExportRuleGroup:
    rule_id: str
    p21_issue_count: int
    rows: tuple[ExportCandidateRow, ...]


@dataclass(frozen=True, slots=True)
class DomainExportRequest:
    domain: str
    report_name: str
    source_fingerprint: str
    rules: tuple[ExportRuleGroup, ...]

    @classmethod
    def from_results(
        cls,
        domain: str,
        report_name: str,
        source_fingerprint: str,
        grouped_results: tuple[tuple[str, tuple[IssueMatchResult, ...]], ...],
    ) -> DomainExportRequest:
        rules = []
        for rule_id, results in grouped_results:
            rows = tuple(
                ExportCandidateRow(
                    source_row=candidate.source_row,
                    status=candidate.status,
                    issue_id=result.issue.issue_id,
                    issue_sheet_row=result.issue.sheet_row,
                )
                for result in results
                for candidate in result.candidates
            )
            rules.append(ExportRuleGroup(rule_id, len(results), rows))
        return cls(domain, report_name, source_fingerprint, tuple(rules))
