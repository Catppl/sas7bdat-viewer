from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from p21_issue_explorer.models import (
    CandidateMatch,
    IssueMatchResult,
    IssueStatus,
    P21Issue,
    RuleOpenRequest,
    summarize_rule,
)
from p21_issue_explorer.ui.issue_table import STATUS_COLORS, IssueTableModel
from p21_issue_explorer.ui.rule_summary import RuleSummaryWidget
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication


def _issue(row: int, message: str = "Synthetic message") -> P21Issue:
    return P21Issue(
        issue_id=f"issue-{row}",
        sheet_row=row,
        dataset="AE",
        record=str(row),
        count="",
        variables="USUBJID, AETERM",
        values=f"SUBJ-{row}, Term {row}",
        rule_id="SHARED-RULE",
        message=message,
        impact="Medium",
        issue_type="Error",
    )


def _result(
    row: int,
    status: IssueStatus,
    source_rows: tuple[int, ...] = (),
    *,
    message: str = "Synthetic message",
) -> IssueMatchResult:
    return IssueMatchResult(
        issue=_issue(row, message),
        status=status,
        candidates=tuple(
            CandidateMatch(source_row, status, (), f"USUBJID = 'SUBJ-{row}'")
            for source_row in source_rows
        ),
        expected_where=f"USUBJID = 'SUBJ-{row}'",
        applied_where=f"USUBJID = 'SUBJ-{row}'" if source_rows else "",
    )


class UiModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_issue_table_roles_colors_long_tooltip_and_sorting(self) -> None:
        long_message = "Long message " * 80
        results = (
            _result(20, IssueStatus.MATCHED_EXACT, (1, 2), message=long_message),
            _result(3, IssueStatus.REVIEW, (3,)),
            _result(10, IssueStatus.NOT_FOUND),
            _result(4, IssueStatus.CANNOT_EVALUATE),
        )
        model = IssueTableModel(results)
        self.assertEqual(model.rowCount(), 4)
        self.assertEqual(model.columnCount(), 11)
        self.assertEqual(
            model.headerData(10, Qt.Orientation.Horizontal), "Current Match Count"
        )
        for row, result in enumerate(results):
            brush = model.data(model.index(row, 0), Qt.ItemDataRole.BackgroundRole)
            self.assertEqual(brush.color().name(), STATUS_COLORS[result.status].name())
        self.assertEqual(
            model.data(model.index(0, 5), Qt.ItemDataRole.ToolTipRole), long_message
        )

        model.sort(10, Qt.SortOrder.DescendingOrder)
        self.assertEqual(
            [item.current_match_count for item in model.results], [2, 1, 0, 0]
        )
        model.sort(2, Qt.SortOrder.AscendingOrder)
        self.assertEqual(
            [item.issue.record for item in model.results], ["10", "20", "3", "4"]
        )

    def test_rule_summary_numbers_and_materialized_request_are_deduplicated(
        self,
    ) -> None:
        exact = _result(1, IssueStatus.MATCHED_EXACT, (7, 8))
        review = _result(2, IssueStatus.REVIEW, (8, 9))
        not_found = _result(3, IssueStatus.NOT_FOUND)
        cannot_evaluate = _result(4, IssueStatus.CANNOT_EVALUATE)
        results = (exact, review, not_found, cannot_evaluate)
        summary = summarize_rule(results)
        widget = RuleSummaryWidget()
        try:
            widget.set_summary(summary)
            self.assertEqual(widget.value_labels["p21IssueCount"].text(), "4")
            self.assertEqual(widget.value_labels["currentMatchCount"].text(), "3")
            self.assertEqual(widget.value_labels["matchedIssueCount"].text(), "1")
            self.assertEqual(widget.value_labels["partialReviewIssueCount"].text(), "1")
            self.assertEqual(widget.value_labels["notFoundIssueCount"].text(), "1")
            self.assertEqual(
                widget.value_labels["cannotEvaluateIssueCount"].text(), "1"
            )
            self.assertTrue(widget.filter_all_button.isEnabled())

            request = RuleOpenRequest.from_results("AE", summary, results)
            self.assertEqual(request.source_rows, (7, 8, 9))
            status_by_row = {
                item.source_row: item.status for item in request.row_styles
            }
            self.assertIs(status_by_row[8], IssueStatus.MATCHED_EXACT)
        finally:
            widget.close()


if __name__ == "__main__":
    unittest.main()
