from __future__ import annotations

import csv
import json
import unittest
from pathlib import Path

from p21_issue_explorer.report import P21ReportReader

FIXTURES = Path(__file__).parents[1] / "fixtures"


class FixtureContractTests(unittest.TestCase):
    def test_expected_matches_reference_report_issues_and_current_rows(self) -> None:
        report = P21ReportReader().read(FIXTURES / "p21_synthetic_report.xlsx")
        expected = json.loads(
            (FIXTURES / "expected_matches.json").read_text(encoding="utf-8")
        )
        issues_by_row = {issue.sheet_row: issue for issue in report.issues}
        current_rows = {
            "AE": self._source_rows("synthetic_current_ae.csv"),
            "LB": self._source_rows("synthetic_current_lb.csv"),
        }

        self.assertEqual(expected["contract_version"], 1)
        self.assertEqual(
            {item["sheet_row"] for item in expected["issues"]},
            set(issues_by_row),
        )
        for item in expected["issues"]:
            issue = issues_by_row[item["sheet_row"]]
            self.assertEqual(item["rule_id"], issue.rule_id)
            self.assertTrue(
                set(item["candidate_source_rows"]).issubset(current_rows[issue.dataset])
            )

    @staticmethod
    def _source_rows(filename: str) -> set[int]:
        with (FIXTURES / filename).open(encoding="utf-8", newline="") as stream:
            return {int(row["_source_row"]) for row in csv.DictReader(stream)}


if __name__ == "__main__":
    unittest.main()
