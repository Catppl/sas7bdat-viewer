from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from openpyxl import Workbook
from p21_issue_explorer.report import (
    HeaderDetectionError,
    MissingWorksheetError,
    P21ReportReader,
    WorkbookReadError,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"


class P21ReportReaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reader = P21ReportReader()

    def test_reads_only_eligible_issue_details_rows(self) -> None:
        report = self.reader.read(FIXTURES / "p21_synthetic_report.xlsx")

        self.assertEqual(report.worksheet_name, "Issue Details")
        self.assertEqual(report.diagnostics.header_row, 7)
        self.assertEqual(report.diagnostics.eligible_issue_rows, 9)
        self.assertEqual(report.diagnostics.ignored_count_present_rows, 1)
        self.assertEqual(report.diagnostics.ignored_record_blank_rows, 1)
        self.assertEqual(report.domains, ("AE", "LB"))
        self.assertEqual(len(report.issues), 9)
        self.assertTrue(report.issues[0].issue_id.endswith(":8"))
        self.assertEqual(len(report.fingerprint), 64)

    def test_preserves_comma_containing_values_for_matching_phase(self) -> None:
        report = self.reader.read(FIXTURES / "p21_synthetic_report.xlsx")

        issue = next(item for item in report.issues if item.rule_id == "SYNTH-COMMA")
        self.assertEqual(
            issue.values,
            "SYNTH-001, SUBJ-002, Headache, severe, MILD, 2026-01-02",
        )

    def test_requires_exact_issue_details_worksheet_name(self) -> None:
        with self.assertRaisesRegex(
            MissingWorksheetError, "Issue Details.*Available worksheets"
        ):
            self.reader.read(FIXTURES / "p21_missing_issue_details.xlsx")

    def test_finds_issue_details_by_logical_name_in_non_first_sheet(self) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "multiple-sheets.xlsx"
            workbook = Workbook()
            workbook.active.title = "Summary"
            workbook.create_sheet("Configuration")
            worksheet = workbook.create_sheet("\u00a0Issue Details\u00a0")
            worksheet.append(
                [
                    "Dataset",
                    "Record",
                    "Count",
                    "Variables",
                    "Values",
                    "Rule ID",
                    "Message",
                    "Impact",
                    "Type",
                ]
            )
            worksheet.append(
                ["AE", "1", None, "AETERM", "Headache", "RULE-1", "Demo", "", ""]
            )
            workbook.save(source)
            workbook.close()

            report = self.reader.read(source)

        self.assertEqual(report.worksheet_name, "\u00a0Issue Details\u00a0")
        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].rule_id, "RULE-1")

    def test_reports_missing_required_column(self) -> None:
        with self.assertRaises(HeaderDetectionError) as context:
            self.reader.read(FIXTURES / "p21_missing_column.xlsx")

        self.assertEqual(context.exception.missing_columns, ("Type",))

    def test_reports_duplicate_required_column(self) -> None:
        with self.assertRaises(HeaderDetectionError) as context:
            self.reader.read(FIXTURES / "p21_duplicate_column.xlsx")

        self.assertEqual(context.exception.duplicate_columns, ("Dataset",))

    def test_missing_file_is_a_user_facing_error(self) -> None:
        with self.assertRaisesRegex(WorkbookReadError, "does not exist"):
            self.reader.read(FIXTURES / "not-present.xlsx")

    def test_corrupt_workbook_is_a_user_facing_error(self) -> None:
        with TemporaryDirectory() as directory:
            corrupt = Path(directory) / "corrupt.xlsx"
            corrupt.write_text("not an xlsx archive", encoding="utf-8")

            with self.assertRaisesRegex(WorkbookReadError, "Unable to read"):
                self.reader.read(corrupt)


if __name__ == "__main__":
    unittest.main()
