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
    selection_description: str = "Materialized P21 candidate selection"

    @classmethod
    def from_results(
        cls,
        domain: str,
        summary: RuleMatchSummary,
        results: tuple[IssueMatchResult, ...],
    ) -> RuleOpenRequest:
        status_by_row: dict[object, IssueStatus] = {}
        for result in results:
            for candidate in result.candidates:
                existing = status_by_row.get(candidate.source_row)
                if existing is None or candidate.status is IssueStatus.MATCHED_EXACT:
                    status_by_row[candidate.source_row] = candidate.status
        return cls(
            domain=domain,
            rule_id=summary.rule_id,
            source_rows=summary.candidate_source_rows,
            row_styles=tuple(
                CandidateRowStyle(source_row, status_by_row[source_row])
                for source_row in summary.candidate_source_rows
            ),
        )
