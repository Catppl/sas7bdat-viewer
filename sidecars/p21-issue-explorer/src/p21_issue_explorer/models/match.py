from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum

from .issue import P21Issue


class IssueStatus(StrEnum):
    MATCHED_EXACT = "Matched – Exact"
    REVIEW = "Review – Partial/Lenient Match"
    NOT_FOUND = "Not Found in Current Data"
    CANNOT_EVALUATE = "Cannot Evaluate"


class ComparisonResult(StrEnum):
    STRICT = "strict"
    LENIENT = "lenient"
    MISMATCH = "mismatch"


@dataclass(frozen=True, slots=True)
class VariableComparison:
    variable: str
    sas_type: str
    expected: str
    actual: object
    result: ComparisonResult
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CandidatePath:
    mapping_index: int
    comparisons: tuple[VariableComparison, ...]
    applied_where: str


@dataclass(frozen=True, slots=True)
class CandidateMatch:
    source_row: object
    status: IssueStatus
    paths: tuple[CandidatePath, ...]
    applied_where: str


@dataclass(frozen=True, slots=True)
class IssueMatchResult:
    issue: P21Issue
    status: IssueStatus
    candidates: tuple[CandidateMatch, ...] = ()
    expected_where: str = ""
    applied_where: str = ""
    ambiguous_value_mapping: bool = False
    diagnostics: tuple[str, ...] = ()

    @property
    def exact_count(self) -> int:
        return sum(
            candidate.status is IssueStatus.MATCHED_EXACT
            for candidate in self.candidates
        )

    @property
    def review_count(self) -> int:
        return sum(
            candidate.status is IssueStatus.REVIEW for candidate in self.candidates
        )

    @property
    def current_match_count(self) -> int:
        return len(self.candidates)

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value.update(
            {
                "status": self.status.value,
                "exact_count": self.exact_count,
                "review_count": self.review_count,
                "current_match_count": self.current_match_count,
            }
        )
        for candidate in value["candidates"]:
            candidate["status"] = candidate["status"].value
            for path in candidate["paths"]:
                for comparison in path["comparisons"]:
                    comparison["result"] = comparison["result"].value
        return value


@dataclass(frozen=True, slots=True)
class RuleMatchSummary:
    rule_id: str
    p21_issue_count: int
    matched_issue_count: int
    partial_review_issue_count: int
    not_found_issue_count: int
    cannot_evaluate_issue_count: int
    current_exact_record_count: int
    current_review_record_count: int
    current_candidate_record_count: int
    candidate_source_rows: tuple[object, ...]


def summarize_rule(results: tuple[IssueMatchResult, ...]) -> RuleMatchSummary:
    if not results:
        raise ValueError("At least one issue result is required.")
    rule_id = results[0].issue.rule_id
    if any(result.issue.rule_id != rule_id for result in results):
        raise ValueError("All issue results must have the same Rule ID.")
    domain = results[0].issue.dataset.casefold()
    if any(result.issue.dataset.casefold() != domain for result in results):
        raise ValueError("All issue results must have the same domain.")
    exact_rows = {
        candidate.source_row
        for result in results
        for candidate in result.candidates
        if candidate.status is IssueStatus.MATCHED_EXACT
    }
    review_rows = {
        candidate.source_row
        for result in results
        for candidate in result.candidates
        if candidate.status is IssueStatus.REVIEW
    }
    all_rows = exact_rows | review_rows
    return RuleMatchSummary(
        rule_id=rule_id,
        p21_issue_count=len(results),
        matched_issue_count=sum(
            result.status is IssueStatus.MATCHED_EXACT for result in results
        ),
        partial_review_issue_count=sum(
            result.status is IssueStatus.REVIEW for result in results
        ),
        not_found_issue_count=sum(
            result.status is IssueStatus.NOT_FOUND for result in results
        ),
        cannot_evaluate_issue_count=sum(
            result.status is IssueStatus.CANNOT_EVALUATE for result in results
        ),
        current_exact_record_count=len(exact_rows),
        current_review_record_count=len(review_rows),
        current_candidate_record_count=len(all_rows),
        candidate_source_rows=tuple(sorted(all_rows, key=str)),
    )
