from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class AnalysisControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_listing_binding_close_blocker_and_result_release_paths(self) -> None:
        """Listing lifecycle state stays in AnalysisController, not MainWindow."""
        from clinical_data_viewer.controllers.analysis_controller import (
            AnalysisController,
            ListingResultContext,
        )
        from clinical_data_viewer.domain import (
            DatasetHandle,
            DatasetMetadata,
            VariableMetadata,
        )
        from clinical_data_viewer.listing import ListingConfig
        from clinical_data_viewer.temp_manager import TempManager
        from clinical_data_viewer.ui.analysis_panel import AnalysisPanel
        from clinical_data_viewer.ui.dataset_tab import DatasetTab

        class Host:
            def __init__(self, active: DatasetTab, open_tabs: set[DatasetTab]) -> None:
                self.active = active
                self.open_tabs = open_tabs

            def current_dataset_tab(self):
                return self.active

            def is_open_dataset_tab(self, tab):
                return tab in self.open_tabs

            def available_sas_dataset_tabs(self):
                return [(tab, tab.handle.metadata.name) for tab in self.open_tabs]

            def create_analysis_result_tab(self, handle):
                return DatasetTab(handle, 100)

            def show_analysis_result_tab(self, tab, title):
                return None

            def submit_analysis_task(self, owner, function, completed, failed):
                return None

            def retain_analysis_directory(self, path):
                return None

            def show_analysis_error(self, title, message, details=""):
                return None

            def browse_listing_adsl_dataset(self):
                return None

            def browse_rule_based_adsl_dataset(self):
                return None

            def unique_analysis_tab_title(self, base):
                return base

            def discard_analysis_result(self, handle):
                return None

            def set_analysis_task_status(self, text):
                return None

        def make_tab(root: Path, name: str) -> DatasetTab:
            temporary = root / name
            temporary.mkdir()
            handle = DatasetHandle(
                root / f"{name}.sas7bdat",
                temporary / f"{name}.sas7bdat",
                temporary / "dataset.sqlite",
                DatasetMetadata(
                    name.upper(),
                    1,
                    (
                        VariableMetadata("USUBJID"),
                        VariableMetadata("TRT01A"),
                        VariableMetadata("AETERM"),
                    ),
                ),
                1,
                True,
            )
            return DatasetTab(handle, 100)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, other, adsl, result = (
                make_tab(root, "adae"),
                make_tab(root, "adlb"),
                make_tab(root, "adsl"),
                make_tab(root, "listing-result"),
            )
            host = Host(source, {source, other, adsl, result})
            panel = AnalysisPanel()
            controller = AnalysisController(host, panel, TempManager(root / "temp"))

            controller.show_listing_builder()
            host.active = other
            controller.show_listing_builder()
            self.assertIs(controller.listing_source, source)
            self.assertEqual(
                panel.listing_builder.source_label.text(),
                "Source: " + str(source.handle.source_path),
            )

            controller._listing_input_tabs.add(source)
            blocker = controller.tab_close_blocker(source)
            self.assertIsNotNone(blocker)
            self.assertEqual(blocker.title, "Listing Running")
            controller._listing_input_tabs.clear()

            controller._listing_results[result] = ListingResultContext(
                source.handle, adsl.handle, ListingConfig(())
            )
            self.assertEqual(
                set(controller.take_result_release_paths(result)),
                {
                    source.handle.temporary_path.parent,
                    adsl.handle.temporary_path.parent,
                },
            )
            self.assertEqual(controller.take_result_release_paths(result), ())

            panel.listing_builder.clear()
            self.assertIsNone(controller.listing_source)
            panel.deleteLater()

    def test_rule_based_binding_close_blocker_and_result_release_paths(self) -> None:
        """Rule-based lifecycle state is owned by AnalysisController."""
        from clinical_data_viewer.controllers.analysis_controller import (
            AnalysisController,
            RuleBasedResultContext,
        )
        from clinical_data_viewer.domain import (
            DatasetHandle,
            DatasetMetadata,
            VariableMetadata,
        )
        from clinical_data_viewer.rule_based import RuleBasedConfig, RuleBasedRow
        from clinical_data_viewer.temp_manager import TempManager
        from clinical_data_viewer.ui.analysis_panel import AnalysisPanel
        from clinical_data_viewer.ui.dataset_tab import DatasetTab

        class Host:
            def __init__(self, active, open_tabs):
                self.active = active
                self.open_tabs = open_tabs

            def current_dataset_tab(self):
                return self.active

            def is_open_dataset_tab(self, tab):
                return tab in self.open_tabs

            def available_sas_dataset_tabs(self):
                return [(tab, tab.handle.metadata.name) for tab in self.open_tabs]

            def create_analysis_result_tab(self, handle):
                return DatasetTab(handle, 100)

            def show_analysis_result_tab(self, tab, title):
                return None

            def submit_analysis_task(self, owner, function, completed, failed):
                return None

            def retain_analysis_directory(self, path):
                return None

            def show_analysis_error(self, title, message, details=""):
                return None

            def browse_listing_adsl_dataset(self):
                return None

            def browse_rule_based_adsl_dataset(self):
                return None

            def unique_analysis_tab_title(self, base):
                return base

            def discard_analysis_result(self, handle):
                return None

            def set_analysis_task_status(self, text):
                return None

        def make_tab(root: Path, name: str) -> DatasetTab:
            temporary = root / name
            temporary.mkdir()
            handle = DatasetHandle(
                root / f"{name}.sas7bdat",
                temporary / f"{name}.sas7bdat",
                temporary / "dataset.sqlite",
                DatasetMetadata(
                    name.upper(),
                    1,
                    (
                        VariableMetadata("USUBJID"),
                        VariableMetadata("TRT01A"),
                    ),
                ),
                1,
                True,
            )
            return DatasetTab(handle, 100)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, other, adsl, result = (
                make_tab(root, "adae"),
                make_tab(root, "adlb"),
                make_tab(root, "adsl"),
                make_tab(root, "rule-result"),
            )
            host = Host(source, {source, other, adsl, result})
            panel = AnalysisPanel()
            controller = AnalysisController(host, panel, TempManager(root / "temp"))

            controller.show_rule_based_builder()
            host.active = other
            controller.show_rule_based_builder()
            self.assertIs(controller.rule_based_source, source)
            self.assertEqual(
                panel.rule_based_builder.source_label.text(),
                "Source: " + str(source.handle.source_path),
            )

            controller._rule_based_input_tabs.add(source)
            blocker = controller.tab_close_blocker(source)
            self.assertIsNotNone(blocker)
            self.assertEqual(blocker.title, "Rule-based Table Running")
            controller._rule_based_input_tabs.clear()

            controller._rule_based_results[result] = RuleBasedResultContext(
                source.handle,
                adsl.handle,
                RuleBasedConfig((RuleBasedRow("row_001", "Any AE"),), "TRT01A"),
            )
            self.assertEqual(
                set(controller.take_result_release_paths(result)),
                {
                    source.handle.temporary_path.parent,
                    adsl.handle.temporary_path.parent,
                },
            )
            self.assertEqual(controller.take_result_release_paths(result), ())

            panel.rule_based_builder.clear()
            self.assertIsNone(controller.rule_based_source)
            panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
