from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from p21_issue_explorer.ui import ImportPage, P21IssueExplorerWindow

FIXTURES = Path(__file__).parents[1] / "fixtures"


class P21UiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_standalone_window_starts_without_sas_viewer(self) -> None:
        window = P21IssueExplorerWindow()
        try:
            window.show()
            self.application.processEvents()
            self.assertEqual(window.windowTitle(), "P21 Issue Explorer")
            self.assertIsInstance(window.centralWidget(), ImportPage)
        finally:
            window.close()
            self.application.processEvents()

    def test_background_import_populates_domain_selector(self) -> None:
        page = ImportPage()
        try:
            page.load_report(FIXTURES / "p21_synthetic_report.xlsx")
            self._wait_until(lambda: page.report is not None)
            self.assertEqual(page.domain_combo.count(), 2)
            self.assertEqual(page.domain_combo.itemText(0), "AE")
            self.assertEqual(page.domain_combo.itemText(1), "LB")
            self.assertIn("Imported 9 eligible issues", page.status_label.text())
            self.assertTrue(page.import_button.isEnabled())
        finally:
            page.close()
            self.application.processEvents()

    def test_bad_report_shows_error_without_crashing(self) -> None:
        page = ImportPage()
        try:
            page.load_report(FIXTURES / "p21_missing_issue_details.xlsx")
            self._wait_until(lambda: page.import_button.isEnabled())
            self.assertIsNone(page.report)
            self.assertFalse(page.domain_combo.isEnabled())
            self.assertIn("Issue Details", page.status_label.text())
        finally:
            page.close()
            self.application.processEvents()

    def _wait_until(self, predicate, timeout: float = 3.0) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= deadline:
                self.fail("Timed out waiting for background UI operation")
            self.application.processEvents()
            time.sleep(0.01)


if __name__ == "__main__":
    unittest.main()
