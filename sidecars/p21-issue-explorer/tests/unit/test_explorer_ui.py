from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from p21_issue_explorer.integration import MappingSnapshotProvider
from p21_issue_explorer.models import IssueOpenRequest, IssueStatus, RuleOpenRequest
from p21_issue_explorer.report import P21ReportReader
from p21_issue_explorer.ui import ExplorerPage
from p21_issue_explorer.ui.issue_table import STATUS_COLORS
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from snapshot_factory import create_fixture_snapshot

FIXTURES = Path(__file__).parents[1] / "fixtures"


class _DelayedProvider:
    def __init__(self, delegate: MappingSnapshotProvider, delayed_domain: str) -> None:
        self.delegate = delegate
        self.delayed_domain = delayed_domain
        self.gate = threading.Event()

    def open_snapshot(self, domain: str):
        if domain.casefold() == self.delayed_domain.casefold():
            self.gate.wait(timeout=3)
        return self.delegate.open_snapshot(domain)


class ExplorerUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        cls.report = P21ReportReader().read(FIXTURES / "p21_synthetic_report.xlsx")

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.snapshot_paths = {
            domain: create_fixture_snapshot(root / domain, domain, FIXTURES)
            for domain in ("AE", "LB")
        }
        self.provider = MappingSnapshotProvider(self.snapshot_paths)

    def tearDown(self) -> None:
        self.application.processEvents()
        self.temporary.cleanup()

    def test_report_domain_and_rule_drive_summary_and_colored_issue_row(self) -> None:
        page = ExplorerPage(snapshot_provider=self.provider)
        try:
            page.apply_report(self.report)
            self._select_rule(page, "SYNTH-MULTIPLE")
            summary = page.current_summary
            self.assertIsNotNone(summary)
            self.assertEqual(summary.p21_issue_count, 1)
            self.assertEqual(summary.matched_issue_count, 1)
            self.assertEqual(summary.current_exact_record_count, 2)
            self.assertEqual(summary.current_candidate_record_count, 2)
            self.assertEqual(
                page.rule_summary.value_labels["currentMatchCount"].text(), "2"
            )
            self.assertEqual(page.issue_table.issue_model.rowCount(), 1)
            index = page.issue_table.issue_model.index(0, 0)
            brush = page.issue_table.issue_model.data(
                index, Qt.ItemDataRole.BackgroundRole
            )
            self.assertEqual(
                brush.color().name(),
                STATUS_COLORS[IssueStatus.MATCHED_EXACT].name(),
            )
            self.assertIn("STUDYID", page.issue_detail.expected_where.toPlainText())
            self.assertNotIn("AESEQ", page.issue_detail.expected_where.toPlainText())
        finally:
            page.close()

    def test_each_status_and_count_is_traceable_from_frozen_fixture(self) -> None:
        cases = (
            ("SYNTH-LENIENT", IssueStatus.REVIEW, 0, 1),
            ("SYNTH-NOT-FOUND", IssueStatus.NOT_FOUND, 0, 0),
            ("SYNTH-MISSING-VAR", IssueStatus.CANNOT_EVALUATE, 0, 0),
            ("SYNTH-COMMA", IssueStatus.MATCHED_EXACT, 1, 0),
        )
        page = ExplorerPage(snapshot_provider=self.provider)
        try:
            page.apply_report(self.report)
            for rule_id, status, exact_count, review_count in cases:
                with self.subTest(rule_id=rule_id):
                    self._select_rule(page, rule_id)
                    result = page.current_results[0]
                    self.assertIs(result.status, status)
                    self.assertEqual(result.exact_count, exact_count)
                    self.assertEqual(result.review_count, review_count)
                    index = page.issue_table.issue_model.index(0, 0)
                    brush = page.issue_table.issue_model.data(
                        index, Qt.ItemDataRole.BackgroundRole
                    )
                    self.assertEqual(brush.color().name(), STATUS_COLORS[status].name())
        finally:
            page.close()

    def test_click_issue_and_filter_all_emit_p4_boundary_requests(self) -> None:
        page = ExplorerPage(snapshot_provider=self.provider)
        issue_requests: list[IssueOpenRequest] = []
        rule_requests: list[RuleOpenRequest] = []
        page.open_issue_requested.connect(issue_requests.append)
        page.filter_all_requested.connect(rule_requests.append)
        try:
            page.apply_report(self.report)
            self._select_rule(page, "SYNTH-MULTIPLE")
            index = page.issue_table.issue_model.index(0, 0)
            page.issue_table.clicked.emit(index)
            self.application.processEvents()
            self.assertEqual(len(issue_requests), 1)
            self.assertEqual(issue_requests[0].source_rows, (7, 8))
            self.assertEqual(
                issue_requests[0].where_text,
                page.current_results[0].applied_where,
            )

            page.rule_summary.filter_all_button.click()
            self.application.processEvents()
            self.assertEqual(len(rule_requests), 1)
            self.assertEqual(rule_requests[0].source_rows, (7, 8))
            self.assertEqual(
                rule_requests[0].selection_description,
                "Materialized P21 candidate selection",
            )
        finally:
            page.close()

    def test_review_detail_shows_expected_actual_reason_and_applied_where(self) -> None:
        page = ExplorerPage(snapshot_provider=self.provider)
        try:
            page.apply_report(self.report)
            self._select_rule(page, "SYNTH-LENIENT")
            detail = page.issue_detail
            self.assertGreater(detail.candidate_table.rowCount(), 0)
            results = {
                detail.candidate_table.item(row, 3).text()
                for row in range(detail.candidate_table.rowCount())
            }
            self.assertIn("lenient", results)
            reasons = " ".join(
                detail.candidate_table.item(row, 6).text()
                for row in range(detail.candidate_table.rowCount())
            )
            self.assertIn("case", reasons)
            self.assertIn("SUBJ-003", detail.applied_where.toPlainText())
            self.assertIn("subj-003", detail.expected_where.toPlainText())
        finally:
            page.close()

    def test_missing_snapshot_is_cannot_evaluate_not_not_found(self) -> None:
        page = ExplorerPage(snapshot_provider=None)
        try:
            page.apply_report(self.report)
            self.assertTrue(page.current_results)
            self.assertTrue(
                all(
                    result.status is IssueStatus.CANNOT_EVALUATE
                    for result in page.current_results
                )
            )
            self.assertEqual(
                page.current_summary.cannot_evaluate_issue_count,
                page.current_summary.p21_issue_count,
            )
            self.assertIn("snapshot is unavailable", page.match_status.text())
        finally:
            page.close()

    def test_domain_switch_discards_late_result_from_previous_generation(self) -> None:
        provider = _DelayedProvider(self.provider, "AE")
        page = ExplorerPage(snapshot_provider=provider)
        try:
            page.apply_report(self.report)
            self._wait_until(lambda: page.cancel_match_button.isEnabled())
            page.domain_combo.setCurrentText("LB")
            self._wait_until(
                lambda: (
                    page.current_summary is not None
                    and page.domain_combo.currentText() == "LB"
                    and page.current_summary.rule_id == "SYNTH-LB-NULL"
                )
            )
            provider.gate.set()
            self._wait_until(lambda: not page._active_match_tasks)
            self.assertEqual(page.domain_combo.currentText(), "LB")
            self.assertEqual(page.current_summary.rule_id, "SYNTH-LB-NULL")
            self.assertEqual(page.current_results[0].issue.dataset, "LB")
        finally:
            provider.gate.set()
            page.close()

    def test_cancel_stops_visible_match_and_run_button_can_restart(self) -> None:
        provider = _DelayedProvider(self.provider, "AE")
        page = ExplorerPage(snapshot_provider=provider)
        try:
            page.apply_report(self.report)
            self._wait_until(lambda: page.cancel_match_button.isEnabled())
            page.cancel_match_button.click()
            self.assertIn("cancelled", page.match_status.text())
            self.assertFalse(page.cancel_match_button.isEnabled())
            self.assertTrue(page.run_match_button.isEnabled())
            provider.gate.set()
            self._wait_until(lambda: not page._active_match_tasks)
            page.run_match_button.click()
            self._wait_until(lambda: page.current_summary is not None)
        finally:
            provider.gate.set()
            page.close()

    def _select_rule(self, page: ExplorerPage, rule_id: str) -> None:
        index = page.rule_combo.findData(rule_id)
        self.assertGreaterEqual(index, 0)
        page.rule_combo.setCurrentIndex(index)
        self._wait_until(
            lambda: (
                page.current_summary is not None
                and page.current_summary.rule_id == rule_id
                and not page.cancel_match_button.isEnabled()
            )
        )

    def _wait_until(self, predicate, timeout: float = 4.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("Timed out waiting for Explorer UI operation")
            self.application.processEvents()
            time.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
