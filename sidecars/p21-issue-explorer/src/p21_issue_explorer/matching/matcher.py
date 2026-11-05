from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable

from p21_issue_explorer.integration import DatasetSnapshot
from p21_issue_explorer.models import (
    CandidateMatch,
    CandidatePath,
    ComparisonResult,
    IssueMatchResult,
    IssueStatus,
    P21Issue,
    VariableComparison,
)

from .normalization import compare_value
from .value_alignment import (
    AlignmentError,
    ValueMapping,
    VariableKind,
    VariableSpec,
    align_values,
    classify_variables,
)
from .where_builder import combine_and, combine_or, where_condition


class MatchCancelled(RuntimeError):
    pass


class IssueMatcher:
    """Pure matching core; it intentionally has no Qt or Viewer dependency."""

    def match(
        self,
        issue: P21Issue,
        snapshot: DatasetSnapshot,
        *,
        cancelled: Callable[[], bool] | None = None,
    ) -> IssueMatchResult:
        if issue.dataset.casefold() != snapshot.manifest.domain.casefold():
            return self._cannot_evaluate(
                issue,
                f"Issue domain {issue.dataset!r} does not match snapshot domain "
                f"{snapshot.manifest.domain!r}.",
            )
        try:
            classified = classify_variables(issue.variables, snapshot)
        except AlignmentError as error:
            return self._cannot_evaluate(issue, str(error))
        if classified.missing_local_variables:
            names = ", ".join(classified.missing_local_variables)
            return self._cannot_evaluate(
                issue, f"{names} is absent from current metadata"
            )
        local_variables = tuple(
            variable
            for variable in classified.variables
            if variable.kind is VariableKind.LOCAL
        )
        if not local_variables:
            return self._cannot_evaluate(
                issue, "Issue has no eligible local variables after exclusions."
            )
        try:
            mappings = align_values(issue.values, classified.variables, snapshot)
        except AlignmentError as error:
            return self._cannot_evaluate(issue, str(error))

        records: list[dict[str, object]] = []
        for index, record in enumerate(
            snapshot.records([item.name for item in local_variables])
        ):
            if index % 512 == 0 and cancelled is not None and cancelled():
                raise MatchCancelled()
            records.append(record)

        exact_paths = self._search_exact(
            records,
            classified.variables,
            mappings,
            snapshot.manifest.source_row_field,
            cancelled,
        )
        if exact_paths:
            used_mappings = self._used_mappings(mappings, exact_paths)
            ambiguous = len(used_mappings) > 1
            candidates = self._build_candidates(exact_paths, IssueStatus.MATCHED_EXACT)
            return IssueMatchResult(
                issue=issue,
                status=IssueStatus.MATCHED_EXACT,
                candidates=candidates,
                expected_where=self._expected_where_for_mappings(
                    classified.variables, used_mappings
                ),
                applied_where=combine_or(item.applied_where for item in candidates),
                ambiguous_value_mapping=ambiguous,
                diagnostics=("Ambiguous Value Mapping",) if ambiguous else (),
            )

        review_paths = self._search_review(
            records,
            classified.variables,
            mappings,
            snapshot.manifest.source_row_field,
            cancelled,
        )
        used_mappings = self._used_mappings(mappings, review_paths)
        ambiguous = len(used_mappings) > 1 if review_paths else len(mappings) > 1
        expected_mappings = used_mappings if review_paths else mappings
        expected_where = self._expected_where_for_mappings(
            classified.variables, expected_mappings
        )
        diagnostics: list[str] = []
        if ambiguous:
            diagnostics.append("Ambiguous Value Mapping")
        if not any(item.name.casefold() == "usubjid" for item in local_variables):
            diagnostics.append("Broad Review: no USUBJID anchor is available")
        if review_paths:
            candidates = self._build_candidates(review_paths, IssueStatus.REVIEW)
            return IssueMatchResult(
                issue=issue,
                status=IssueStatus.REVIEW,
                candidates=candidates,
                expected_where=expected_where,
                applied_where=combine_or(item.applied_where for item in candidates),
                ambiguous_value_mapping=ambiguous,
                diagnostics=tuple(diagnostics),
            )
        return IssueMatchResult(
            issue=issue,
            status=IssueStatus.NOT_FOUND,
            expected_where=expected_where,
            ambiguous_value_mapping=ambiguous,
            diagnostics=tuple(diagnostics),
        )

    @staticmethod
    def _cannot_evaluate(issue: P21Issue, reason: str) -> IssueMatchResult:
        return IssueMatchResult(
            issue=issue,
            status=IssueStatus.CANNOT_EVALUATE,
            diagnostics=(reason,),
        )

    def _search_exact(
        self,
        records: list[dict[str, object]],
        variables: tuple[VariableSpec, ...],
        mappings: tuple[ValueMapping, ...],
        source_row_field: str,
        cancelled: Callable[[], bool] | None,
    ) -> dict[object, list[CandidatePath]]:
        matches: dict[object, list[CandidatePath]] = defaultdict(list)
        for mapping in mappings:
            for index, record in enumerate(records):
                if index % 512 == 0 and cancelled is not None and cancelled():
                    raise MatchCancelled()
                comparisons = self._comparisons(record, variables, mapping)
                if comparisons and all(
                    item.result is ComparisonResult.STRICT for item in comparisons
                ):
                    applied_where = self._applied_where(comparisons)
                    matches[record[source_row_field]].append(
                        CandidatePath(mapping.index, comparisons, applied_where)
                    )
        return matches

    def _search_review(
        self,
        records: list[dict[str, object]],
        variables: tuple[VariableSpec, ...],
        mappings: tuple[ValueMapping, ...],
        source_row_field: str,
        cancelled: Callable[[], bool] | None,
    ) -> dict[object, list[CandidatePath]]:
        local_count = sum(item.kind is VariableKind.LOCAL for item in variables)
        required = math.ceil(0.8 * local_count)
        matches: dict[object, list[CandidatePath]] = defaultdict(list)
        for mapping in mappings:
            for index, record in enumerate(records):
                if index % 512 == 0 and cancelled is not None and cancelled():
                    raise MatchCancelled()
                comparisons = self._comparisons(record, variables, mapping)
                anchor = next(
                    (
                        item
                        for item in comparisons
                        if item.variable.casefold() == "usubjid"
                    ),
                    None,
                )
                if anchor is not None and anchor.result is ComparisonResult.MISMATCH:
                    continue
                matched = sum(
                    item.result is not ComparisonResult.MISMATCH for item in comparisons
                )
                if matched >= required:
                    applied_where = self._applied_where(comparisons)
                    matches[record[source_row_field]].append(
                        CandidatePath(mapping.index, comparisons, applied_where)
                    )
        return matches

    @staticmethod
    def _comparisons(
        record: dict[str, object],
        variables: tuple[VariableSpec, ...],
        mapping: ValueMapping,
    ) -> tuple[VariableComparison, ...]:
        comparisons: list[VariableComparison] = []
        for variable, expected in zip(variables, mapping.values, strict=True):
            if variable.kind is not VariableKind.LOCAL or variable.sas_type is None:
                continue
            actual = record[variable.name]
            result, reason = compare_value(expected, actual, variable.sas_type)
            comparisons.append(
                VariableComparison(
                    variable=variable.name,
                    sas_type=variable.sas_type,
                    expected=expected,
                    actual=actual,
                    result=result,
                    reason=reason,
                )
            )
        return tuple(comparisons)

    @staticmethod
    def _expected_where(
        variables: tuple[VariableSpec, ...], mapping: ValueMapping
    ) -> str:
        return combine_and(
            where_condition(variable.name, expected, variable.sas_type)
            for variable, expected in zip(variables, mapping.values, strict=True)
            if variable.kind is VariableKind.LOCAL and variable.sas_type is not None
        )

    @classmethod
    def _expected_where_for_mappings(
        cls,
        variables: tuple[VariableSpec, ...],
        mappings: tuple[ValueMapping, ...],
    ) -> str:
        return combine_or(
            cls._expected_where(variables, mapping) for mapping in mappings
        )

    @staticmethod
    def _used_mappings(
        mappings: tuple[ValueMapping, ...],
        paths: dict[object, list[CandidatePath]],
    ) -> tuple[ValueMapping, ...]:
        indexes = {
            path.mapping_index
            for candidate_paths in paths.values()
            for path in candidate_paths
        }
        return tuple(mapping for mapping in mappings if mapping.index in indexes)

    @staticmethod
    def _applied_where(comparisons: tuple[VariableComparison, ...]) -> str:
        conditions: list[str] = []
        for comparison in comparisons:
            if comparison.result is ComparisonResult.MISMATCH:
                continue
            value = (
                comparison.actual
                if comparison.result is ComparisonResult.LENIENT
                else comparison.expected
            )
            conditions.append(
                where_condition(comparison.variable, value, comparison.sas_type)
            )
        return combine_and(conditions)

    @staticmethod
    def _build_candidates(
        paths: dict[object, list[CandidatePath]], status: IssueStatus
    ) -> tuple[CandidateMatch, ...]:
        candidates: list[CandidateMatch] = []
        for source_row in sorted(paths, key=str):
            deduplicated: dict[tuple[int, str], CandidatePath] = {}
            for path in paths[source_row]:
                deduplicated.setdefault((path.mapping_index, path.applied_where), path)
            unique_paths = tuple(deduplicated.values())
            candidates.append(
                CandidateMatch(
                    source_row=source_row,
                    status=status,
                    paths=unique_paths,
                    applied_where=combine_or(
                        path.applied_where for path in unique_paths
                    ),
                )
            )
        return tuple(candidates)
