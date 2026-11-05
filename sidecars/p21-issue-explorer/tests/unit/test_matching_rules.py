from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

from p21_issue_explorer.integration import SnapshotReader
from p21_issue_explorer.matching import IssueMatcher
from p21_issue_explorer.matching.normalization import (
    canonical_decimal,
    compare_value,
)
from p21_issue_explorer.models import (
    ComparisonResult,
    IssueStatus,
    P21Issue,
    summarize_rule,
)
from snapshot_factory import create_snapshot


def issue(
    variables: str,
    values: str,
    *,
    rule_id: str = "RULE-1",
    dataset: str = "AE",
) -> P21Issue:
    return P21Issue(
        issue_id="synthetic-1",
        sheet_row=8,
        dataset=dataset,
        record="1",
        count="",
        variables=variables,
        values=values,
        rule_id=rule_id,
        message="Synthetic issue",
        impact="Medium",
        issue_type="Synthetic",
    )


class NormalizationTests(unittest.TestCase):
    def test_numeric_normalization_uses_decimal_semantics(self) -> None:
        for expected, actual in (("1", 1.0), ("1.00", 1), ("1e3", 1000)):
            with self.subTest(expected=expected, actual=actual):
                result, _reason = compare_value(expected, actual, "numeric")
                self.assertIs(result, ComparisonResult.STRICT)
        self.assertEqual(canonical_decimal("1.00"), "1")
        self.assertEqual(canonical_decimal("1e3"), "1000")

    def test_character_leniency_only_ignores_case_and_boundary_whitespace(self) -> None:
        for expected, actual in ((" Fatigue ", "FATIGUE"), ("a-b", "A-B")):
            with self.subTest(expected=expected, actual=actual):
                result, _reason = compare_value(expected, actual, "character")
                self.assertIs(result, ComparisonResult.LENIENT)
        for expected, actual in (("a b", "ab"), ("a-b", "ab"), ("a,b", "a b")):
            with self.subTest(expected=expected, actual=actual):
                result, _reason = compare_value(expected, actual, "character")
                self.assertIs(result, ComparisonResult.MISMATCH)

    def test_null_uses_type_aware_missing_semantics(self) -> None:
        for sas_type, actual in (
            ("numeric", None),
            ("character", None),
            ("character", ""),
        ):
            with self.subTest(sas_type=sas_type, actual=actual):
                result, _reason = compare_value("null", actual, sas_type)
                self.assertIs(result, ComparisonResult.STRICT)
        result, _reason = compare_value("null", 0, "numeric")
        self.assertIs(result, ComparisonResult.MISMATCH)


class MatchingRuleTests(unittest.TestCase):
    def _match(
        self,
        metadata: dict[str, str],
        rows: list[dict[str, object]],
        current_issue: P21Issue,
    ):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = create_snapshot(Path(temporary.name), "AE", metadata, rows)
        snapshot = SnapshotReader().open(root)
        self.addCleanup(snapshot.close)
        return IssueMatcher().match(current_issue, snapshot)

    def test_dtc_is_matched_as_exact_character_text(self) -> None:
        result = self._match(
            {"USUBJID": "character", "AESTDTC": "character"},
            [{"_source_row": 1, "USUBJID": "S1", "AESTDTC": "2026-03-01"}],
            issue("USUBJID, AESTDTC", "S1, 2026-03-01"),
        )
        self.assertIs(result.status, IssueStatus.MATCHED_EXACT)
        different_precision = self._match(
            {"USUBJID": "character", "AESTDTC": "character"},
            [{"_source_row": 1, "USUBJID": "S1", "AESTDTC": "2026-03-01T00:00"}],
            issue("USUBJID, AESTDTC", "S1, 2026-03-01"),
        )
        self.assertIs(different_precision.status, IssueStatus.NOT_FOUND)

    def test_comma_alignment_handles_multiple_commas(self) -> None:
        for value in ("Headache, severe", "Pain, head, intermittent"):
            with self.subTest(value=value):
                result = self._match(
                    {
                        "USUBJID": "character",
                        "AETERM": "character",
                        "AESEV": "character",
                    },
                    [
                        {
                            "_source_row": 1,
                            "USUBJID": "S1",
                            "AETERM": value,
                            "AESEV": "MILD",
                        }
                    ],
                    issue(
                        "USUBJID, AETERM, AESEV",
                        f"S1, {value}, MILD",
                    ),
                )
                self.assertIs(result.status, IssueStatus.MATCHED_EXACT)
                self.assertEqual([item.source_row for item in result.candidates], [1])

    def test_ambiguous_value_mappings_are_all_run(self) -> None:
        result = self._match(
            {"A": "character", "B": "character"},
            [
                {"_source_row": 1, "A": "x", "B": "y, z"},
                {"_source_row": 2, "A": "x, y", "B": "z"},
            ],
            issue("A, B", "x, y, z"),
        )
        self.assertIs(result.status, IssueStatus.MATCHED_EXACT)
        self.assertTrue(result.ambiguous_value_mapping)
        self.assertEqual([item.source_row for item in result.candidates], [1, 2])

    def test_seq_and_cross_domain_consume_values_but_do_not_match_or_render(
        self,
    ) -> None:
        result = self._match(
            {"USUBJID": "character", "AESEQ": "numeric", "AETERM": "character"},
            [{"_source_row": 1, "USUBJID": "S1", "AESEQ": 999, "AETERM": "Pain"}],
            issue(
                "USUBJID, AESEQ, AETERM, DM:RFICDTC",
                "S1, 1, Pain, 2025-01-01",
            ),
        )
        self.assertIs(result.status, IssueStatus.MATCHED_EXACT)
        self.assertNotIn("AESEQ", result.expected_where)
        self.assertNotIn("RFICDTC", result.expected_where)

    def test_review_threshold_uses_ceiling_for_representative_sizes(self) -> None:
        for count in (1, 2, 3, 4, 5, 10):
            metadata = {f"V{index}": "character" for index in range(count)}
            required = math.ceil(0.8 * count)
            row = {
                "_source_row": 1,
                **{
                    name: (f"x{index}" if index < required else "different")
                    for index, name in enumerate(metadata)
                },
            }
            expected = ", ".join(f"x{index}" for index in range(count))
            result = self._match(
                metadata,
                [row],
                issue(", ".join(metadata), expected),
            )
            expected_status = (
                IssueStatus.MATCHED_EXACT if required == count else IssueStatus.REVIEW
            )
            with self.subTest(count=count, required=required):
                self.assertIs(result.status, expected_status)

    def test_review_threshold_rejects_one_below_required_count(self) -> None:
        for count in (1, 2, 3, 4, 5, 10):
            metadata = {f"V{index}": "character" for index in range(count)}
            required = math.ceil(0.8 * count)
            matched = required - 1
            row = {
                "_source_row": 1,
                **{
                    name: (f"x{index}" if index < matched else "different")
                    for index, name in enumerate(metadata)
                },
            }
            expected = ", ".join(f"x{index}" for index in range(count))
            result = self._match(
                metadata,
                [row],
                issue(", ".join(metadata), expected),
            )
            with self.subTest(count=count, required=required):
                self.assertIs(result.status, IssueStatus.NOT_FOUND)

    def test_usubjid_must_match_even_when_threshold_is_met(self) -> None:
        metadata = {
            "USUBJID": "character",
            "A": "character",
            "B": "character",
            "C": "character",
            "D": "character",
        }
        result = self._match(
            metadata,
            [
                {
                    "_source_row": 1,
                    "USUBJID": "OTHER",
                    "A": "1",
                    "B": "2",
                    "C": "3",
                    "D": "4",
                }
            ],
            issue("USUBJID, A, B, C, D", "S1, 1, 2, 3, 4"),
        )
        self.assertIs(result.status, IssueStatus.NOT_FOUND)

    def test_exact_stops_review_search(self) -> None:
        result = self._match(
            {
                "A": "character",
                "B": "character",
                "C": "character",
                "D": "character",
                "E": "character",
            },
            [
                {"_source_row": 1, "A": "1", "B": "2", "C": "3", "D": "4", "E": "5"},
                {"_source_row": 2, "A": "1", "B": "2", "C": "3", "D": "4", "E": "x"},
            ],
            issue("A, B, C, D, E", "1, 2, 3, 4, 5"),
        )
        self.assertIs(result.status, IssueStatus.MATCHED_EXACT)
        self.assertEqual([item.source_row for item in result.candidates], [1])
        self.assertEqual(result.review_count, 0)

    def test_review_applied_where_omits_mismatch_and_uses_actual_lenient_value(
        self,
    ) -> None:
        result = self._match(
            {
                "USUBJID": "character",
                "A": "character",
                "B": "character",
                "C": "character",
                "D": "character",
            },
            [
                {
                    "_source_row": 1,
                    "USUBJID": "subj-1",
                    "A": "1",
                    "B": "2",
                    "C": "3",
                    "D": "different",
                }
            ],
            issue("USUBJID, A, B, C, D", "SUBJ-1, 1, 2, 3, 4"),
        )
        self.assertIs(result.status, IssueStatus.REVIEW)
        self.assertIn("USUBJID = 'SUBJ-1'", result.expected_where)
        self.assertIn("D = '4'", result.expected_where)
        self.assertIn("USUBJID = 'subj-1'", result.applied_where)
        self.assertNotIn(" AND D =", result.applied_where)

    def test_missing_variable_and_only_ignored_variables_cannot_evaluate(self) -> None:
        missing = self._match(
            {"USUBJID": "character"},
            [{"_source_row": 1, "USUBJID": "S1"}],
            issue("USUBJID, UNKNOWN", "S1, X"),
        )
        ignored = self._match(
            {"AESEQ": "numeric"},
            [{"_source_row": 1, "AESEQ": 1}],
            issue("AESEQ, DM:X", "1, Y"),
        )
        self.assertIs(missing.status, IssueStatus.CANNOT_EVALUATE)
        self.assertIs(ignored.status, IssueStatus.CANNOT_EVALUATE)

    def test_rule_summary_deduplicates_current_source_rows(self) -> None:
        first = self._match(
            {"A": "character"},
            [{"_source_row": 1, "A": "x"}],
            issue("A", "x", rule_id="SHARED"),
        )
        second = self._match(
            {"A": "character"},
            [{"_source_row": 1, "A": "x"}],
            issue("A", "x", rule_id="SHARED"),
        )
        summary = summarize_rule((first, second))
        self.assertEqual(summary.p21_issue_count, 2)
        self.assertEqual(summary.current_exact_record_count, 1)
        self.assertEqual(summary.current_candidate_record_count, 1)


if __name__ == "__main__":
    unittest.main()
