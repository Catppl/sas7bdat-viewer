from __future__ import annotations

import importlib.util
import os
import unittest

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class ClickOnlyComboBoxTests(unittest.TestCase):
    def test_wheel_event_does_not_change_selection(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.ui.widgets import ClickOnlyComboBox

        application = QApplication.instance() or QApplication([])
        combo = ClickOnlyComboBox()
        combo.addItems(["A", "B"])
        combo.setCurrentIndex(0)

        class Event:
            ignored = False

            def ignore(self):
                self.ignored = True

        event = Event()
        combo.wheelEvent(event)
        self.assertEqual(combo.currentIndex(), 0)
        self.assertTrue(event.ignored)
        combo.deleteLater()
        application.processEvents()


if __name__ == "__main__":
    unittest.main()
