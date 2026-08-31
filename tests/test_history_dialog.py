from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class HistoryDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_dialog_displays_local_time_without_rewriting_stored_utc(self) -> None:
        from clinical_data_viewer.filter_history import (
            FilterHistory,
            format_history_timestamp,
        )
        from clinical_data_viewer.ui.history_dialog import HistoryDialog

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "adlb.sas7bdat"
            history = FilterHistory(root / "history.sqlite")
            history.add(
                dataset,
                'PARAMCD = "ALB"',
                datetime(2026, 8, 21, 0, 0, tzinfo=UTC),
            )
            stored = history.list(dataset)[0]
            self.assertEqual(stored.executed_at, "2026-08-21T00:00:00+00:00")

            dialog = HistoryDialog(history, dataset)
            item = dialog.table.topLevelItem(0)
            self.assertEqual(item.text(0), format_history_timestamp(stored.executed_at))
            self.assertEqual(
                item.toolTip(0),
                format_history_timestamp(stored.executed_at, include_timezone=True),
            )
            self.assertEqual(item.text(2), 'PARAMCD = "ALB"')
            dialog.close()
            dialog.deleteLater()
            self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
