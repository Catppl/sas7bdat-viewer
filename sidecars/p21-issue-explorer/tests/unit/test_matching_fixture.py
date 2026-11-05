from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from p21_issue_explorer.integration import SnapshotReader
from p21_issue_explorer.matching import IssueMatcher
from p21_issue_explorer.models import IssueStatus
from p21_issue_explorer.report import P21ReportReader
from snapshot_factory import create_fixture_snapshot

FIXTURES = Path(__file__).parents[1] / "fixtures"


class MatchingFixtureTests(unittest.TestCase):
    def test_frozen_fixture_contract(self) -> None:
        report = P21ReportReader().read(FIXTURES / "p21_synthetic_report.xlsx")
        expected = json.loads(
            (FIXTURES / "expected_matches.json").read_text(encoding="utf-8")
        )
        expected_by_row = {item["sheet_row"]: item for item in expected["issues"]}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            snapshots = {
                domain: SnapshotReader().open(
                    create_fixture_snapshot(root / domain, domain, FIXTURES)
                )
                for domain in ("AE", "LB")
            }
            try:
                for issue in report.issues:
                    with self.subTest(sheet_row=issue.sheet_row, rule=issue.rule_id):
                        result = IssueMatcher().match(issue, snapshots[issue.dataset])
                        contract = expected_by_row[issue.sheet_row]
                        self.assertEqual(result.status.value, contract["status"])
                        self.assertEqual(
                            [item.source_row for item in result.candidates],
                            contract["candidate_source_rows"],
                        )
                        if "expected_where" in contract:
                            self.assertEqual(
                                result.expected_where, contract["expected_where"]
                            )
                        self.assertEqual(
                            result.current_match_count,
                            result.exact_count + result.review_count,
                        )
            finally:
                for snapshot in snapshots.values():
                    snapshot.close()

    def test_matching_result_is_serializable(self) -> None:
        report = P21ReportReader().read(FIXTURES / "p21_synthetic_report.xlsx")
        with (
            tempfile.TemporaryDirectory() as temp,
            SnapshotReader().open(
                create_fixture_snapshot(Path(temp), "AE", FIXTURES)
            ) as snapshot,
        ):
            result = IssueMatcher().match(report.issues[0], snapshot)
            payload = json.dumps(result.to_dict(), ensure_ascii=False)
            self.assertIn(IssueStatus.MATCHED_EXACT.value, payload)

    def test_matching_is_deterministic_for_same_report_and_snapshot(self) -> None:
        report = P21ReportReader().read(FIXTURES / "p21_synthetic_report.xlsx")
        with (
            tempfile.TemporaryDirectory() as temp,
            SnapshotReader().open(
                create_fixture_snapshot(Path(temp), "AE", FIXTURES)
            ) as snapshot,
        ):
            first = [
                IssueMatcher().match(item, snapshot).to_dict()
                for item in report.issues
                if item.dataset == "AE"
            ]
            second = [
                IssueMatcher().match(item, snapshot).to_dict()
                for item in report.issues
                if item.dataset == "AE"
            ]
            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
