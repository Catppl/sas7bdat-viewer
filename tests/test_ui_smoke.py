from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class UiSmokeTests(unittest.TestCase):
    def test_main_window_constructs_with_reference_layout(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.filter_history import FilterHistory
        from clinical_data_viewer.settings import AppSettings
        from clinical_data_viewer.temp_manager import TempManager
        from clinical_data_viewer.ui.main_window import MainWindow

        class TestSettings(AppSettings):
            def save(self, path=None):
                return None

        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = TempManager(root / "temp")
            window = MainWindow(
                TestSettings(), manager, FilterHistory(root / "history.sqlite")
            )
            self.assertEqual(window.windowTitle(), "SASDataViewer")
            self.assertEqual(
                window.variable_search.placeholderText(), "Search Variable"
            )
            self.assertEqual(
                window.variables_panel.search.placeholderText(), "Filter variables"
            )
            self.assertTrue(window.variables_dock.isVisible() or not window.isVisible())
            window.close()
            application.processEvents()
            self.assertFalse(manager.session_directory.exists())


if __name__ == "__main__":
    unittest.main()
