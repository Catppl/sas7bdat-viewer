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
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.domain import (
            DatasetHandle,
            DatasetMetadata,
            VariableMetadata,
        )
        from clinical_data_viewer.filter_history import FilterHistory
        from clinical_data_viewer.settings import AppSettings
        from clinical_data_viewer.temp_manager import TempManager
        from clinical_data_viewer.ui.dataset_tab import DatasetTab
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
            metadata = DatasetMetadata(
                "adae",
                2,
                (
                    VariableMetadata("USUBJID"),
                    VariableMetadata("AESER"),
                ),
            )
            window.variables_panel.set_dataset(metadata)
            window.variables_panel.select_all.click()
            application.processEvents()
            self.assertEqual(window.variables_panel.visible_variables(), [])
            window.variables_panel.select_all.click()
            application.processEvents()
            self.assertEqual(
                window.variables_panel.visible_variables(), ["USUBJID", "AESER"]
            )
            empty_metadata = DatasetMetadata("adae", 0, metadata.variables)
            handle = DatasetHandle(
                root / "adae.sas7bdat",
                root / "temp.sas7bdat",
                root / "dataset.sqlite",
                empty_metadata,
                0,
                True,
            )
            dataset_tab = DatasetTab(handle, 500)
            window.tabs.addTab(dataset_tab, "adae.sas7bdat")
            window.tabs.setCurrentWidget(dataset_tab)
            window._sync_active_tab()
            self.assertEqual(dataset_tab.model.rowCount(), 0)

            displayed_item = window.variables_panel.displayed_tree.topLevelItem(0)
            displayed_item.setCheckState(0, Qt.Unchecked)
            application.processEvents()
            self.assertEqual(window.variables_panel.visible_variables(), ["AESER"])
            self.assertEqual(dataset_tab.visible_columns, ["AESER"])

            window.variables_panel.all_toggle.click()
            hidden_item = window.variables_panel.all_tree.topLevelItem(0)
            hidden_item.setCheckState(0, Qt.Checked)
            application.processEvents()
            self.assertEqual(
                window.variables_panel.visible_variables(), ["AESER", "USUBJID"]
            )
            self.assertEqual(dataset_tab.visible_columns, ["AESER", "USUBJID"])

            dataset_tab.show_find()
            self.assertFalse(dataset_tab.find_frame.isHidden())
            window.close()
            application.processEvents()
            self.assertFalse(manager.session_directory.exists())


if __name__ == "__main__":
    unittest.main()
