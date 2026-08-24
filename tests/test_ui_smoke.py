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
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QPalette
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication, QStyleOptionViewItem

        from clinical_data_viewer.column_filters import ColumnFilterSpec
        from clinical_data_viewer.domain import (
            DatasetHandle,
            DatasetMetadata,
            DistinctValuesResult,
            VariableMetadata,
        )
        from clinical_data_viewer.filter_engine import FilterEngine
        from clinical_data_viewer.filter_history import FilterHistory
        from clinical_data_viewer.resources import resource_path
        from clinical_data_viewer.settings import AppSettings
        from clinical_data_viewer.statistics import StatisticsResult
        from clinical_data_viewer.temp_manager import TempManager
        from clinical_data_viewer.ui.column_filter_dialog import ColumnFilterDialog
        from clinical_data_viewer.ui.dataset_tab import DatasetTab
        from clinical_data_viewer.ui.main_window import MainWindow
        from clinical_data_viewer.ui.settings_dialog import SettingsDialog

        class TestSettings(AppSettings):
            def save(self, path=None):
                return None

        application = QApplication.instance() or QApplication([])
        self.assertTrue(resource_path("assets/SASDataViewer.ico").is_file())
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
            tools_actions = [
                action.text()
                for action in window.menuBar().actions()[3].menu().actions()
            ]
            self.assertIn("Analysis", tools_actions)
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

            numeric_metadata = DatasetMetadata(
                "adlb",
                3,
                (
                    VariableMetadata("USUBJID"),
                    VariableMetadata("PARAMCD"),
                    VariableMetadata("AVAL", kind="numeric"),
                ),
            )
            numeric_handle = DatasetHandle(
                root / "adlb.sas7bdat",
                root / "temp-adlb.sas7bdat",
                root / "adlb.sqlite",
                numeric_metadata,
                3,
                True,
            )
            numeric_tab = DatasetTab(numeric_handle, 500)
            numeric_tab.set_column_filter(
                "PARAMCD",
                ColumnFilterSpec("PARAMCD", "include", ("ALT",), False),
            )
            self.assertTrue(
                numeric_tab.filter_frame.isVisible() or not numeric_tab.isVisible()
            )
            self.assertIn('"PARAMCD" IN (?)', numeric_tab.compiled_filter.sql)
            self.assertEqual(
                numeric_tab.where_editor.toPlainText(), 'PARAMCD in ("ALT")'
            )
            self.assertEqual(numeric_tab.pending_history_text, 'PARAMCD in ("ALT")')
            filter_dialog = ColumnFilterDialog(
                VariableMetadata("PARAMCD"),
                DistinctValuesResult(("ALB",), False, 1, False),
                None,
            )
            filter_dialog._initial_all = False
            filter_dialog.value_list.item(0).setCheckState(Qt.Checked)
            filter_dialog._accept_filter()
            self.assertFalse(filter_dialog.result_spec.include_missing)
            self.assertEqual(filter_dialog.result_spec.values, ("ALB",))
            numeric_tab.resize(650, 420)
            numeric_tab.show()
            application.processEvents()
            requested_filters: list[str] = []
            numeric_tab.column_filter_requested.connect(requested_filters.append)
            filter_x = (
                numeric_tab.filter_header.sectionViewportPosition(2)
                + numeric_tab.filter_header.sectionSize(2)
                - 10
            )
            QTest.mouseClick(
                numeric_tab.filter_header.viewport(),
                Qt.LeftButton,
                Qt.NoModifier,
                QPoint(filter_x, 5),
            )
            self.assertEqual(requested_filters, ["AVAL"])
            self.assertIsNone(numeric_tab.model.sort_spec)
            vertical_header = numeric_tab.table.verticalHeader()
            first_y = (
                vertical_header.sectionViewportPosition(0)
                + vertical_header.sectionSize(0) // 2
            )
            third_y = (
                vertical_header.sectionViewportPosition(2)
                + vertical_header.sectionSize(2) // 2
            )
            QTest.mouseClick(
                vertical_header.viewport(),
                Qt.LeftButton,
                Qt.NoModifier,
                QPoint(5, first_y),
            )
            QTest.mouseClick(
                vertical_header.viewport(),
                Qt.LeftButton,
                Qt.ControlModifier,
                QPoint(5, third_y),
            )
            self.assertEqual(numeric_tab.table.selected_row_numbers(), [0, 2])
            numeric_tab.table._context_index = numeric_tab.model.index(0, 2)
            self.assertTrue(numeric_tab.table._context_variable_is_numeric())
            numeric_tab.model.set_page(
                0,
                (
                    ("101", "ALT", 1.0),
                    ("102", "ALT", 2.0),
                    ("103", "ALT", 3.0),
                ),
                3,
            )
            numeric_tab.show_comparison_highlights(("AVAL",), (0, 2))
            self.assertEqual(numeric_tab.model.highlighted_columns, {"AVAL"})
            self.assertEqual(numeric_tab.model.highlighted_rows, {0, 2})
            self.assertIsNotNone(
                numeric_tab.model.data(numeric_tab.model.index(0, 2), Qt.BackgroundRole)
            )
            self.assertIsNone(
                numeric_tab.model.data(numeric_tab.model.index(1, 2), Qt.BackgroundRole)
            )
            self.assertIsNotNone(
                numeric_tab.model.data(numeric_tab.model.index(2, 2), Qt.BackgroundRole)
            )
            selected_option = QStyleOptionViewItem()
            numeric_tab.table.itemDelegate().initStyleOption(
                selected_option, numeric_tab.model.index(0, 2)
            )
            self.assertEqual(
                selected_option.palette.color(QPalette.Highlight).name(), "#fff2b2"
            )
            ordinary_option = QStyleOptionViewItem()
            numeric_tab.table.itemDelegate().initStyleOption(
                ordinary_option, numeric_tab.model.index(1, 2)
            )
            self.assertNotEqual(
                ordinary_option.palette.color(QPalette.Highlight).name(), "#fff2b2"
            )
            window.analysis_panel.show_statistics(
                StatisticsResult(
                    "AVAL",
                    "Analysis Value",
                    2,
                    {"subjects": 2, "mean": 1.235, "std": 0.12345},
                    3,
                    0.95,
                ),
                ["subjects", "mean", "std"],
                {"mean": 2, "std": 3},
                "All rows",
            )
            self.assertEqual(
                window.analysis_panel.statistics_table.item(0, 1).text(), "2"
            )
            self.assertEqual(
                window.analysis_panel.statistics_table.item(1, 1).text(), "1.24"
            )
            self.assertEqual(
                window.analysis_panel.statistics_table.item(2, 1).text(), "0.123"
            )
            settings_dialog = SettingsDialog(TestSettings())
            settings_dialog.statistic_decimals["mean"].setValue(4)
            settings_dialog._save()
            self.assertEqual(
                settings_dialog.settings.proc_means_decimal_places["mean"], 4
            )
            numeric_tab.where_editor.setPlainText("AVAL > 1")
            self.assertTrue(numeric_tab.where_editor_is_dirty())
            numeric_tab.apply_filter(
                FilterEngine(numeric_metadata.variables).compile("AVAL > 1"),
                "AVAL > 1",
                add_history=True,
            )
            self.assertEqual(numeric_tab.column_filters, {})
            self.assertEqual(numeric_tab.where_editor.toPlainText(), "AVAL > 1")
            numeric_tab.close()
            window.close()
            application.processEvents()
            self.assertFalse(manager.session_directory.exists())


if __name__ == "__main__":
    unittest.main()
