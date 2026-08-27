from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class UiSmokeTests(unittest.TestCase):
    def test_sas_temporal_display_setting_updates_open_new_and_reloaded_tabs(
        self,
    ) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.domain import (
            DatasetHandle,
            DatasetMetadata,
            VariableMetadata,
        )
        from clinical_data_viewer.filter_engine import CompiledFilter
        from clinical_data_viewer.filter_history import FilterHistory
        from clinical_data_viewer.settings import AppSettings
        from clinical_data_viewer.temp_manager import TempManager
        from clinical_data_viewer.ui.main_window import MainWindow

        class TestSettings(AppSettings):
            def save(self, path=None):
                return None

        def make_handle(root: Path, name: str) -> DatasetHandle:
            metadata = DatasetMetadata(
                name.upper(),
                1,
                (VariableMetadata("ADT", kind="numeric", format="YYMMDD10."),),
            )
            return DatasetHandle(
                root / f"{name}.sas7bdat",
                root / f"{name}.tmp",
                root / f"{name}.sqlite",
                metadata,
                1,
                True,
            )

        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = MainWindow(
                TestSettings(),
                TempManager(root / "temp"),
                FilterHistory(root / "history.sqlite"),
            )
            first = window._make_dataset_tab(make_handle(root, "adsl"))
            second = window._make_dataset_tab(make_handle(root, "adae"))
            window.tabs.addTab(first, "ADSL")
            window.tabs.addTab(second, "ADAE")
            self.assertFalse(first.model.apply_sas_date_time_formats)
            self.assertFalse(second.model.apply_sas_date_time_formats)

            window.sas_date_time_formats_action.trigger()
            self.assertTrue(first.model.apply_sas_date_time_formats)
            self.assertTrue(second.model.apply_sas_date_time_formats)

            third = window._make_dataset_tab(make_handle(root, "adlb"))
            self.assertTrue(third.model.apply_sas_date_time_formats)

            first.replace_handle(
                make_handle(root, "adsl_reload"),
                ["ADT"],
                CompiledFilter("", ()),
            )
            self.assertTrue(first.model.apply_sas_date_time_formats)
            third.deleteLater()
            window.close()
            application.processEvents()

    def test_large_xpt_submission_warning_is_english_and_persists_on_reload(
        self,
    ) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.domain import (
            DatasetHandle,
            DatasetMetadata,
            VariableMetadata,
        )
        from clinical_data_viewer.filter_engine import CompiledFilter
        from clinical_data_viewer.ui.dataset_tab import DatasetTab

        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            metadata = DatasetMetadata("ADAE", 1, (VariableMetadata("USUBJID"),))
            large = DatasetHandle(
                root / "adae.xpt",
                root / "temp.xpt",
                root / "dataset.sqlite",
                metadata,
                1,
                True,
                source_size_bytes=5_000_000_001,
            )
            tab = DatasetTab(large, 500)
            self.assertFalse(tab.xpt_submission_warning.isHidden())
            self.assertIn("Submission warning:", tab.xpt_submission_warning.text())
            self.assertIn("FDA recommends", tab.xpt_submission_warning.text())

            compliant = DatasetHandle(
                root / "adae.xpt",
                root / "temp.xpt",
                root / "dataset.sqlite",
                metadata,
                1,
                True,
                source_size_bytes=5_000_000_000,
            )
            tab.replace_handle(compliant, ["USUBJID"], CompiledFilter("", ()))
            self.assertTrue(tab.xpt_submission_warning.isHidden())
            tab.deleteLater()
            application.processEvents()

    def test_rule_based_codegen_stays_disabled_for_merge(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.rule_based_builder import RuleBasedBuilder

        application = QApplication.instance() or QApplication([])
        metadata = DatasetMetadata(
            "Merge Result",
            1,
            (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRT01A"),
            ),
        )
        builder = RuleBasedBuilder()
        builder.set_dataset(metadata, "Merge Result", source_kind="merge")
        self.assertFalse(builder.sas_code_button.isEnabled())
        self.assertEqual(
            builder.sas_code_button.toolTip(),
            "SAS code generation for merged Rule-based sources is not available yet.",
        )
        builder.set_busy(True)
        builder.set_busy(False)
        self.assertFalse(builder.sas_code_button.isEnabled())

        builder.set_dataset(metadata, "adae.sas7bdat", source_kind="sas")
        builder.set_busy(True)
        builder.set_busy(False)
        self.assertTrue(builder.sas_code_button.isEnabled())
        builder.deleteLater()
        application.processEvents()

    def test_proc_means_codegen_stays_disabled_for_merge_when_busy_state_changes(
        self,
    ) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.proc_means_builder import ProcMeansBuilder

        application = QApplication.instance() or QApplication([])
        metadata = DatasetMetadata(
            "Merge Result",
            1,
            (VariableMetadata("AVAL", kind="numeric"),),
        )
        builder = ProcMeansBuilder()
        builder.set_dataset(metadata, "Merge Result", source_kind="merge")
        self.assertFalse(builder.sas_code_button.isEnabled())
        self.assertFalse(builder.r_code_button.isEnabled())
        builder.set_busy(True)
        builder.set_busy(False)
        self.assertFalse(builder.sas_code_button.isEnabled())
        self.assertFalse(builder.r_code_button.isEnabled())

        builder.set_dataset(metadata, "adlb.sas7bdat", source_kind="sas")
        builder.set_busy(True)
        builder.set_busy(False)
        self.assertTrue(builder.sas_code_button.isEnabled())
        self.assertTrue(builder.r_code_button.isEnabled())
        builder.deleteLater()
        application.processEvents()

    def test_merge_sort_variables_accept_enter_and_default_ascending(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.dataset_merge_panel import DatasetMergePanel

        class Tab:
            def __init__(self, name: str) -> None:
                self.handle = type("Handle", (), {})()
                self.handle.metadata = DatasetMetadata(
                    name,
                    2,
                    (
                        VariableMetadata("USUBJID"),
                        VariableMetadata("AESEQ", kind="numeric"),
                    ),
                )
                self.handle.source_path = Path(f"{name}.sas7bdat")
                self.cache_complete = True

        application = QApplication.instance() or QApplication([])
        left, right = Tab("left"), Tab("right")
        panel = DatasetMergePanel()
        panel.set_datasets([(left, "left", True), (right, "right", True)])
        panel.left_dataset.setCurrentIndex(1)
        panel.right_dataset.setCurrentIndex(2)
        panel.by_variables.item(0).setCheckState(Qt.Checked)
        panel.sort_editor.setText("USUBJID")
        panel.sort_editor.returnPressed.emit()
        self.assertEqual(panel._read_sort_items()[0].variable, "USUBJID")
        self.assertEqual(panel._read_sort_items()[0].direction, "ASC")
        self.assertEqual(panel.sort_editor.text(), "")
        panel.sort_editor.setText("AESEQ")
        panel.sort_direction.setCurrentIndex(1)
        panel.sort_editor.returnPressed.emit()
        self.assertEqual(
            [(item.variable, item.direction) for item in panel._read_sort_items()],
            [("USUBJID", "ASC"), ("AESEQ", "DESC")],
        )
        panel.deleteLater()
        application.processEvents()

    def test_merge_by_change_prunes_invalid_sort_items(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.dataset_merge_panel import DatasetMergePanel

        class Tab:
            def __init__(self, name: str) -> None:
                self.handle = type("Handle", (), {})()
                self.handle.metadata = DatasetMetadata(
                    name,
                    2,
                    (
                        VariableMetadata("USUBJID"),
                        VariableMetadata("AGE", kind="numeric"),
                    ),
                )
                self.handle.source_path = Path(f"{name}.sas7bdat")
                self.cache_complete = True

        application = QApplication.instance() or QApplication([])
        left, right = Tab("left"), Tab("right")
        panel = DatasetMergePanel()
        panel.set_datasets([(left, "left", True), (right, "right", True)])
        panel.left_dataset.setCurrentIndex(1)
        panel.right_dataset.setCurrentIndex(2)
        panel.by_variables.item(0).setCheckState(Qt.Checked)  # USUBJID
        panel.sort_editor.setText("AGE_RIGHT")
        panel.sort_editor.returnPressed.emit()
        self.assertEqual(panel._read_sort_items()[0].variable, "AGE_RIGHT")
        panel.by_variables.item(1).setCheckState(Qt.Checked)  # AGE changes schema
        self.assertEqual(panel._read_sort_items(), ())
        panel.deleteLater()
        application.processEvents()

    def test_merge_result_is_forwarded_to_analysis_builders(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
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
            metadata = DatasetMetadata(
                "Merge Result - ADAE + ADSL",
                2,
                (
                    VariableMetadata("USUBJID"),
                    VariableMetadata("TRT01A"),
                    VariableMetadata("AVAL", kind="numeric"),
                ),
            )
            handle = DatasetHandle(
                root / "Merge Result - ADAE + ADSL",
                root / "merge-result.tmp",
                root / "merge.sqlite",
                metadata,
                2,
                True,
                kind="merge",
            )
            tab = DatasetTab(handle, 500)
            tab.applied_where = 'TRT01A = "A"'
            tab.where_editor.setPlainText(tab.applied_where)
            window.tabs.addTab(tab, "Merge Result")
            window.tabs.setCurrentWidget(tab)
            window._sync_active_tab()
            self.assertTrue(window.merge_panel.left_dataset.count() >= 2)
            window.show_proc_means_builder()
            self.assertIs(window.analysis_panel.builder._metadata, metadata)
            window.show_categorical_builder()
            self.assertEqual(
                window.analysis_panel.categorical_builder.current_filter_text(),
                'TRT01A = "A"',
            )
            window.show_rule_based_builder()
            self.assertEqual(
                window.analysis_panel.rule_based_builder.current_filter_text(),
                'TRT01A = "A"',
            )
            window.close()
            application.processEvents()

    def test_analysis_builders_keep_their_fixed_source_and_inputs_until_clear(
        self,
    ) -> None:
        """Changing tabs must not silently rebind or reset an open Builder."""
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
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

        def make_tab(root: Path, name: str) -> DatasetTab:
            metadata = DatasetMetadata(
                name.upper(),
                2,
                (
                    VariableMetadata("USUBJID"),
                    VariableMetadata("TRT01A"),
                    VariableMetadata("AVAL", kind="numeric"),
                    VariableMetadata("AEBODSYS"),
                    VariableMetadata("AEDECOD"),
                ),
            )
            handle = DatasetHandle(
                root / f"{name}.sas7bdat",
                root / f"{name}.tmp",
                root / f"{name}.sqlite",
                metadata,
                2,
                True,
            )
            tab = DatasetTab(handle, 500)
            tab.applied_where = 'TRT01A = "A"'
            tab.where_editor.setPlainText(tab.applied_where)
            return tab

        application = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            window = MainWindow(
                TestSettings(),
                TempManager(root / "temp"),
                FilterHistory(root / "history.sqlite"),
            )
            source_a, source_b = make_tab(root, "adae"), make_tab(root, "adlb")
            window.tabs.addTab(source_a, "ADAE")
            window.tabs.addTab(source_b, "ADLB")
            window.tabs.setCurrentWidget(source_a)
            window.show_proc_means_builder()
            window.show_categorical_builder()
            window.show_rule_based_builder()
            window.show_ae_table_builder()

            window.analysis_panel.builder.analysis_variables.set_variables(("AVAL",))
            window.analysis_panel.categorical_builder.numerator_where.setPlainText(
                'TRT01A = "A" and AVAL > 0'
            )
            window.analysis_panel.rule_based_builder.dataset_filter.setPlainText(
                'TRT01A = "A" and AVAL > 0'
            )
            window.analysis_panel.ae_table_builder.dataset_filter.setPlainText(
                'TRT01A = "A" and AVAL > 0'
            )

            window.tabs.setCurrentWidget(source_b)
            application.processEvents()

            self.assertTrue(
                all(source is source_a for source in window._builder_sources.values())
            )
            self.assertEqual(
                window.analysis_panel.builder.analysis_variables.selected_variables(),
                ("AVAL",),
            )
            self.assertEqual(
                window.analysis_panel.categorical_builder.current_filter_text(),
                'TRT01A = "A" and AVAL > 0',
            )
            self.assertEqual(
                window.analysis_panel.rule_based_builder.current_filter_text(),
                'TRT01A = "A" and AVAL > 0',
            )
            self.assertEqual(
                window.analysis_panel.ae_table_builder.current_filter_text(),
                'TRT01A = "A" and AVAL > 0',
            )

            with patch(
                "clinical_data_viewer.ui.main_window.QMessageBox.warning"
            ) as warning:
                window.close_tab(window.tabs.indexOf(source_a))
            self.assertGreaterEqual(window.tabs.indexOf(source_a), 0)
            self.assertIn("fixed source", warning.call_args.args[2])

            for builder in (
                window.analysis_panel.builder,
                window.analysis_panel.categorical_builder,
                window.analysis_panel.rule_based_builder,
                window.analysis_panel.ae_table_builder,
            ):
                builder.clear()
            self.assertTrue(
                all(source is None for source in window._builder_sources.values())
            )
            window.close()
            application.processEvents()

    def test_rule_based_builder_allows_a_different_population_treatment_variable(
        self,
    ) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.rule_based_builder import RuleBasedBuilder

        class Tab:
            def __init__(self) -> None:
                self.handle = type("Handle", (), {})()
                self.handle.metadata = DatasetMetadata(
                    "ADSL",
                    1,
                    (
                        VariableMetadata("USUBJID"),
                        VariableMetadata("TRT01AN", kind="numeric"),
                    ),
                )

        application = QApplication.instance() or QApplication([])
        builder = RuleBasedBuilder()
        builder.set_dataset(
            DatasetMetadata(
                "ADAE",
                1,
                (
                    VariableMetadata("USUBJID"),
                    VariableMetadata("TRTAN", kind="numeric"),
                ),
            ),
            "adae.sas7bdat",
        )
        adsl = Tab()
        builder.set_adsl_sources([(adsl, "ADSL — adsl.sas7bdat")])
        self.assertEqual(builder.population_treatment.currentText(), "TRT01AN")
        builder.deleteLater()
        application.processEvents()

    def test_ae_table_builder_scrolls_when_the_panel_is_short(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QScrollArea

        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.ae_table_builder import AeTableBuilder

        application = QApplication.instance() or QApplication([])
        builder = AeTableBuilder()
        builder.set_dataset(
            DatasetMetadata(
                "ADAE",
                1,
                (
                    VariableMetadata("USUBJID"),
                    VariableMetadata("TRT01A"),
                    VariableMetadata("AEBODSYS"),
                    VariableMetadata("AEDECOD"),
                ),
            ),
            "adae.sas7bdat",
        )
        builder.resize(360, 220)
        builder.show()
        application.processEvents()
        scroll = builder.findChild(QScrollArea)
        self.assertIsNotNone(scroll)
        assert scroll is not None
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        builder.deleteLater()
        application.processEvents()

    def test_rule_based_builder_scrollbars_reach_both_content_edges(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QScrollArea

        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.rule_based_builder import RuleBasedBuilder

        application = QApplication.instance() or QApplication([])
        builder = RuleBasedBuilder()
        builder.set_dataset(
            DatasetMetadata(
                "ADAE",
                1,
                (
                    VariableMetadata("USUBJID"),
                    VariableMetadata("TRT01A"),
                    VariableMetadata("AVAL", kind="numeric"),
                ),
            ),
            "adae.sas7bdat",
        )
        for _ in range(4):
            builder.add_row()
        builder.resize(360, 220)
        builder.show()
        application.processEvents()
        scroll = builder.findChild(QScrollArea)
        self.assertIsNotNone(scroll)
        assert scroll is not None
        self.assertGreater(scroll.horizontalScrollBar().maximum(), 0)
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)

        scroll.horizontalScrollBar().setValue(scroll.horizontalScrollBar().maximum())
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        application.processEvents()
        content_rect = scroll.widget().geometry()
        viewport_rect = scroll.viewport().rect()
        self.assertLessEqual(content_rect.right(), viewport_rect.right())
        self.assertLessEqual(content_rect.bottom(), viewport_rect.bottom())
        builder.deleteLater()
        application.processEvents()

    def test_main_window_constructs_with_reference_layout(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtGui import QColor, QPalette
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
        from clinical_data_viewer.ui.history_dialog import HistoryDialog
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
            history = FilterHistory(root / "history.sqlite")
            window = MainWindow(TestSettings(), manager, history)
            retained = manager.create_dataset_directory()
            (retained / "sentinel").touch()
            window._retain_directory(retained)
            window._remove_dataset_directory(retained)
            self.assertTrue(retained.exists())
            window._release_directory(retained)
            self.assertFalse(retained.exists())
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
            self.assertIn("PROC MEANS Builder", tools_actions)
            self.assertIn("Categorical Table Builder", tools_actions)
            view_actions = [
                action.text()
                for action in window.menuBar().actions()[2].menu().actions()
            ]
            self.assertIn("Apply SAS Date/Time Formats", view_actions)
            self.assertFalse(window.sas_date_time_formats_action.isChecked())
            window.sas_date_time_formats_action.trigger()
            self.assertTrue(window.settings.apply_sas_date_time_formats)
            self.assertIn("Open Categorical Long Result", view_actions)
            self.assertFalse(window.open_categorical_long_action.isEnabled())
            self.assertEqual(
                window.analysis_panel.builder.sas_code_button.text(),
                "SAS Code Generator…",
            )
            self.assertEqual(
                window.analysis_panel.builder.r_code_button.text(),
                "R Code Generator…",
            )
            self.assertIn("Compare Datasets", tools_actions)
            self.assertIn("Merge Datasets", tools_actions)
            self.assertFalse(window.compare_dock.isVisible())
            self.assertFalse(window.merge_dock.isVisible())
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
            self.assertEqual(window.compare_panel.main_dataset.count(), 2)
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

            proc_means_metadata = DatasetMetadata(
                "PROC MEANS Result - adlb",
                2,
                (
                    VariableMetadata("PARAMCD"),
                    VariableMetadata("ANALYSIS_VARIABLE"),
                    VariableMetadata("MEAN", kind="numeric"),
                ),
                proc_means_analysis_column="ANALYSIS_VARIABLE",
                proc_means_statistic_keys=(("MEAN", "mean"),),
            )
            proc_means_handle = DatasetHandle(
                root / "PROC MEANS Result - adlb",
                root / "proc-means-result.tmp",
                root / "proc-means-result.sqlite",
                proc_means_metadata,
                2,
                True,
                kind="proc_means",
            )
            proc_means_tab = DatasetTab(proc_means_handle, 500)
            window.tabs.addTab(proc_means_tab, "PROC MEANS Result")
            window.tabs.setCurrentWidget(proc_means_tab)
            application.processEvents()
            self.assertEqual(
                window.variables_panel.visible_variables(),
                ["PARAMCD", "ANALYSIS_VARIABLE", "MEAN"],
            )
            proc_means_item = window.variables_panel.displayed_tree.topLevelItem(1)
            proc_means_item.setCheckState(0, Qt.Unchecked)
            application.processEvents()
            self.assertEqual(proc_means_tab.visible_columns, ["PARAMCD", "MEAN"])
            window.variables_panel.select_all.click()
            application.processEvents()
            self.assertEqual(
                proc_means_tab.visible_columns,
                ["PARAMCD", "ANALYSIS_VARIABLE", "MEAN"],
            )
            window.tabs.setCurrentWidget(dataset_tab)
            application.processEvents()

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
            builder = window.analysis_panel.builder
            self.assertEqual(window.analysis_panel.tabs.count(), 0)
            window.analysis_panel.show_builder_tab()
            window.analysis_panel.show_builder_tab()
            self.assertEqual(window.analysis_panel.tabs.count(), 1)
            builder.set_dataset(numeric_metadata, "adlb.sas7bdat", "All rows")
            self.assertEqual(builder.current_filter_text(), "")
            builder.set_dataset(numeric_metadata, "adlb.sas7bdat", "AVAL > 1")
            self.assertEqual(builder.current_filter_text(), "")
            builder.apply_current_filter("AVAL > 1")
            self.assertEqual(builder.current_filter_text(), "AVAL > 1")
            builder.set_dataset(numeric_metadata, "Merge Result", "", "merge")
            self.assertFalse(builder.sas_code_button.isEnabled())
            self.assertFalse(builder.r_code_button.isEnabled())
            self.assertEqual(
                builder.sas_code_button.toolTip(),
                "Code generation for merged results is not available yet.",
            )
            builder.set_dataset(numeric_metadata, "adlb.sas7bdat", "")
            self.assertTrue(builder.sas_code_button.isEnabled())
            self.assertTrue(builder.r_code_button.isEnabled())
            builder.analysis_variables.editor.setText("aval")
            builder.analysis_variables._add_from_editor()
            builder.by_variables.editor.setText("PARAMCD")
            builder.by_variables._add_from_editor()
            self.assertEqual(builder.analysis_variables.selected_variables(), ("AVAL",))
            self.assertEqual(builder.by_variables.selected_variables(), ("PARAMCD",))
            self.assertEqual(builder.decimal_groups.count(), 1)
            builder.decimal_groups.item(0).setCheckState(Qt.Checked)
            self.assertEqual(builder.selected_decimal_groups(), ("PARAMCD",))
            window.analysis_panel.show_statistics_tab()
            self.assertEqual(window.analysis_panel.tabs.count(), 2)
            builder_index = window.analysis_panel.tabs.indexOf(builder)
            window.analysis_panel._close_tab(builder_index)
            self.assertEqual(window.analysis_panel.tabs.count(), 1)
            statistics_index = window.analysis_panel.tabs.indexOf(
                window.analysis_panel.statistics_page
            )
            window.analysis_panel._close_tab(statistics_index)
            self.assertEqual(window.analysis_panel.tabs.count(), 0)
            categorical = window.analysis_panel.categorical_builder
            window.analysis_panel.show_categorical_tab()
            self.assertEqual(window.analysis_panel.tabs.count(), 1)
            categorical.set_dataset(numeric_metadata, "adlb.sas7bdat", "AVAL > 1")
            self.assertEqual(categorical.current_filter_text(), "AVAL > 1")
            self.assertEqual(categorical.numerator_where.toPlainText(), "AVAL > 1")
            categorical.numerator_where.setPlainText("AVAL > 2")
            self.assertEqual(categorical.current_filter_text(), "AVAL > 2")
            categorical.inherit_current_filter("AVAL > 3")
            self.assertEqual(categorical.current_filter_text(), "AVAL > 2")
            categorical.items.resize(180, 100)
            categorical.items.show()
            application.processEvents()
            self.assertLessEqual(
                categorical.items.remove_button.geometry().right(),
                categorical.items.width() - 1,
            )
            drilldown_dialog, records_button, subjects_button, denominator_button = (
                window._categorical_drilldown_dialog()
            )
            self.assertEqual(
                [
                    records_button.text(),
                    subjects_button.text(),
                    denominator_button.text(),
                ],
                [
                    "Show Numerator Records",
                    "Show Numerator Subjects",
                    "Show Denominator Subjects",
                ],
            )
            self.assertEqual(drilldown_dialog.minimumWidth(), 420)
            drilldown_dialog.deleteLater()
            categorical.items.hide()
            categorical.items.editor.setText("PARAMCD")
            categorical.items._add()
            categorical.items.contexts.editor.setText("USUBJID")
            categorical.items.contexts._add_from_editor()
            other_source = object()
            adsl_source = object()
            categorical.set_adsl_sources(
                [
                    (other_source, "ADLB — adlb.sas7bdat"),
                    (adsl_source, "ADSL — adsl.sas7bdat"),
                ]
            )
            self.assertEqual(
                categorical.items.selected_items()[0].context_variables, ("USUBJID",)
            )
            self.assertEqual(categorical.adsl.count(), 2)
            self.assertIs(categorical.adsl.currentData(), adsl_source)
            categorical_index = window.analysis_panel.tabs.indexOf(categorical)
            window.analysis_panel._close_tab(categorical_index)
            self.assertEqual(window.analysis_panel.tabs.count(), 0)
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
                DistinctValuesResult(("ALB", "ALBU", "AST"), True, 3, False),
                None,
            )
            filter_dialog.value_search.setText("ALB")
            self.assertEqual(
                [item.text() for item in filter_dialog._visible_items()], ["ALB"]
            )
            filter_dialog._accept_filter()
            self.assertFalse(filter_dialog.result_spec.include_missing)
            self.assertEqual(filter_dialog.result_spec.values, ("ALB",))
            matching_dialog = ColumnFilterDialog(
                VariableMetadata("PARAMCD"),
                DistinctValuesResult(("ALB", "ALBU", "AST"), True, 3, False),
                None,
            )
            matching_dialog.value_search.setText("AL")
            matching_dialog._accept_filter()
            self.assertFalse(matching_dialog.result_spec.include_missing)
            self.assertEqual(matching_dialog.result_spec.values, ("ALB", "ALBU"))
            no_match_dialog = ColumnFilterDialog(
                VariableMetadata("PARAMCD"),
                DistinctValuesResult(("ALB", "ALBU", "AST"), False, 3, True),
                None,
            )
            self.assertEqual(no_match_dialog._matching_item_indexes("xxxx"), set())
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
            numeric_tab.table.clearSelection()
            numeric_tab.table._context_index = numeric_tab.model.index(1, 2)
            self.assertEqual(numeric_tab.table.manual_highlight_row_numbers(), [1])
            numeric_tab.table.selectRow(0)
            self.assertEqual(numeric_tab.table.manual_highlight_row_numbers(), [0])
            numeric_tab.model.set_manual_row_highlight({0}, QColor("#e8ddff"))
            manual_option = QStyleOptionViewItem()
            numeric_tab.table.itemDelegate().initStyleOption(
                manual_option, numeric_tab.model.index(0, 2)
            )
            self.assertEqual(
                manual_option.palette.color(QPalette.Highlight).name(), "#e8ddff"
            )
            self.assertEqual(
                manual_option.palette.color(QPalette.Text).name(), "#1f2937"
            )
            self.assertIsNone(
                numeric_tab.model.data(numeric_tab.model.index(0, 2), Qt.CheckStateRole)
            )
            self.assertFalse(
                manual_option.features & QStyleOptionViewItem.HasCheckIndicator
            )
            window.analysis_panel.show_statistics(
                StatisticsResult(
                    "AVAL",
                    "Analysis Value",
                    2,
                    {"subjects": 2, "mean": 1.235, "std": 0.12345},
                    3,
                    0.95,
                    1,
                ),
                ["subjects", "mean", "std"],
                {"mean": 1, "std": 2},
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
            self.assertGreaterEqual(
                settings_dialog.statistic_decimals["mean"].minimumWidth(), 72
            )
            settings_dialog.statistic_decimals["mean"].setValue(4)
            settings_dialog._save()
            self.assertEqual(
                settings_dialog.settings.proc_means_decimal_offsets["mean"], 4
            )
            history.add(root / "adlb.sas7bdat", 'PARAMCD = "ALB"')
            history_dialog = HistoryDialog(history, root / "adlb.sas7bdat")
            self.assertEqual(history_dialog.table.columnCount(), 3)
            self.assertEqual(history_dialog.table.topLevelItemCount(), 1)
            self.assertEqual(
                history_dialog.table.topLevelItem(0).text(2), 'PARAMCD = "ALB"'
            )
            history_dialog.close()
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
