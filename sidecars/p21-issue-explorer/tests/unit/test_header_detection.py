from __future__ import annotations

import unittest

from p21_issue_explorer.report.header_detection import (
    HeaderDetectionError,
    find_required_header,
)


class HeaderDetectionTests(unittest.TestCase):
    def test_finds_case_insensitive_trimmed_header_on_nonfixed_row(self) -> None:
        rows = [
            ("Synthetic report",),
            (None,),
            (
                " dataset ",
                "RECORD",
                "Count",
                "Variables",
                "Values",
                "Rule ID",
                "Message",
                "Impact",
                "Type",
            ),
        ]

        result = find_required_header(rows)

        self.assertEqual(result.row_number, 3)
        self.assertEqual(result.column_indexes["Dataset"], 0)
        self.assertEqual(result.column_indexes["Rule ID"], 5)

    def test_reports_missing_required_columns_from_best_candidate(self) -> None:
        rows = [
            ("Dataset", "Record", "Count", "Variables", "Values"),
        ]

        with self.assertRaises(HeaderDetectionError) as context:
            find_required_header(rows)

        self.assertEqual(context.exception.row_number, 1)
        self.assertIn("Rule ID", context.exception.missing_columns)
        self.assertIn("Type", context.exception.missing_columns)

    def test_reports_duplicate_required_columns(self) -> None:
        rows = [
            (
                "Dataset",
                "dataset ",
                "Record",
                "Count",
                "Variables",
                "Values",
                "Rule ID",
                "Message",
                "Impact",
                "Type",
            )
        ]

        with self.assertRaises(HeaderDetectionError) as context:
            find_required_header(rows)

        self.assertEqual(context.exception.duplicate_columns, ("Dataset",))

    def test_respects_scan_limit(self) -> None:
        rows = [("preamble",)] * 100 + [
            (
                "Dataset",
                "Record",
                "Count",
                "Variables",
                "Values",
                "Rule ID",
                "Message",
                "Impact",
                "Type",
            )
        ]

        with self.assertRaises(HeaderDetectionError):
            find_required_header(rows, scan_limit=100)


if __name__ == "__main__":
    unittest.main()
