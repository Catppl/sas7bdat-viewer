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

    def test_analysis_controller_exposes_separate_module_boundaries(self) -> None:
        from clinical_data_viewer.controllers.analysis import (
            AeTableController,
            CategoricalController,
            ListingController,
            ProcMeansController,
            RuleBasedController,
        )
        self.assertTrue(all(cls.name for cls in (
            ListingController,
            RuleBasedController,
            AeTableController,
            ProcMeansController,
            CategoricalController,
        )))

    def test_listing_binding_close_blocker_and_result_release_paths(self) -> None:
        """Listing lifecycle state stays in AnalysisController, not MainWindow."""
        from clinical_data_viewer.controllers.analysis import ListingResultContext
        from clinical_data_viewer.controllers.analysis_controller import (
            AnalysisController,
        )
        from clinical_data_viewer.domain import (
            DatasetHandle,
            DatasetMetadata,
            VariableMetadata,
        )
        from clinical_data_viewer.listing import (
            ListingColumn,
            ListingConfig,
            ListingMergeAdsl,
        )
        from clinical_data_viewer.settings import AppSettings
        from clinical_data_viewer.temp_manager import TempManager
        from clinical_data_viewer.ui.analysis_panel import AnalysisPanel
        from clinical_data_viewer.ui.dataset_tab import DatasetTab
        from clinical_data_viewer.ui.listing_builder import ListingBuilderSelection

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

            def browse_ae_table_adsl_dataset(self):
                return None

            def browse_categorical_adsl_dataset(self):
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
            controller = AnalysisController(
                host, panel, TempManager(root / "temp"), AppSettings()
            )

            controller.show_listing_builder()
            host.active = other
            controller.show_listing_builder()
            self.assertIs(controller.listing_source, source)
            self.assertEqual(
                panel.listing_builder.source_label.text(),
                "Source: " + str(source.handle.source_path),
            )
            context = controller.listing._listing_context(
                ListingBuilderSelection(
                    "",
                    (ListingColumn("USUBJID", "USUBJID"),),
                    ListingMergeAdsl(),
                    None,
                )
            )
            self.assertIsNotNone(context)
            self.assertIs(context[0], source)

            controller.listing._listing_input_tabs.add(source)
            blocker = controller.tab_close_blocker(source)
            self.assertIsNotNone(blocker)
            self.assertEqual(blocker.title, "Listing Running")
            controller.listing._listing_input_tabs.clear()

            controller.listing._listing_results[result] = ListingResultContext(
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
        from clinical_data_viewer.controllers.analysis import RuleBasedResultContext
        from clinical_data_viewer.controllers.analysis_controller import (
            AnalysisController,
        )
        from clinical_data_viewer.domain import (
            DatasetHandle,
            DatasetMetadata,
            VariableMetadata,
        )
        from clinical_data_viewer.rule_based import RuleBasedConfig, RuleBasedRow
        from clinical_data_viewer.settings import AppSettings
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

            def browse_ae_table_adsl_dataset(self):
                return None

            def browse_categorical_adsl_dataset(self):
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
            controller = AnalysisController(
                host, panel, TempManager(root / "temp"), AppSettings()
            )

            controller.show_rule_based_builder()
            host.active = other
            controller.show_rule_based_builder()
            self.assertIs(controller.rule_based_source, source)
            self.assertEqual(
                panel.rule_based_builder.source_label.text(),
                "Source: " + str(source.handle.source_path),
            )

            controller.rule_based._rule_based_input_tabs.add(source)
            blocker = controller.tab_close_blocker(source)
            self.assertIsNotNone(blocker)
            self.assertEqual(blocker.title, "Rule-based Table Running")
            controller.rule_based._rule_based_input_tabs.clear()

            controller.rule_based._rule_based_results[result] = RuleBasedResultContext(
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

    def test_categorical_binding_close_blocker_and_result_release_paths(self) -> None:
        """Categorical lifecycle state is owned by its module controller."""
        from clinical_data_viewer.categorical import CategoricalConfig, CategoricalItem
        from clinical_data_viewer.controllers.analysis import CategoricalResultContext
        from clinical_data_viewer.controllers.analysis_controller import (
            AnalysisController,
        )
        from clinical_data_viewer.domain import (
            DatasetHandle,
            DatasetMetadata,
            VariableMetadata,
        )
        from clinical_data_viewer.settings import AppSettings
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

            def browse_ae_table_adsl_dataset(self):
                return None

            def browse_categorical_adsl_dataset(self):
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
            return DatasetTab(
                DatasetHandle(
                    root / f"{name}.sas7bdat",
                    temporary / f"{name}.sas7bdat",
                    temporary / "dataset.sqlite",
                    DatasetMetadata(
                        name.upper(),
                        1,
                        (
                            VariableMetadata("USUBJID"),
                            VariableMetadata("TRT01A"),
                            VariableMetadata("RACE"),
                        ),
                    ),
                    1,
                    True,
                ),
                100,
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, other, adsl, result = (
                make_tab(root, "adae"),
                make_tab(root, "adlb"),
                make_tab(root, "adsl"),
                make_tab(root, "categorical-result"),
            )
            host = Host(source, {source, other, adsl, result})
            panel = AnalysisPanel()
            controller = AnalysisController(
                host, panel, TempManager(root / "temp"), AppSettings()
            )

            controller.show_categorical_builder()
            host.active = other
            controller.show_categorical_builder()
            self.assertIs(controller.categorical_source, source)
            self.assertEqual(
                panel.categorical_builder.source_label.text(),
                "Source: " + str(source.handle.source_path),
            )

            controller.categorical._categorical_input_tabs.add(source)
            blocker = controller.tab_close_blocker(source)
            self.assertIsNotNone(blocker)
            self.assertEqual(blocker.title, "Categorical Table Running")
            controller.categorical._categorical_input_tabs.clear()

            controller.categorical._categorical_results[result] = CategoricalResultContext(
                source.handle,
                adsl.handle,
                CategoricalConfig((CategoricalItem("RACE"),), "TRT01A", "USUBJID"),
            )
            self.assertEqual(
                set(controller.take_result_release_paths(result)),
                {
                    source.handle.temporary_path.parent,
                    adsl.handle.temporary_path.parent,
                },
            )
            self.assertEqual(controller.take_result_release_paths(result), ())

            panel.categorical_builder.clear()
            self.assertIsNone(controller.categorical_source)
            panel.deleteLater()

    def test_ae_table_binding_close_blocker_and_result_release_paths(self) -> None:
        """AE Table lifecycle state is owned by AnalysisController."""
        from clinical_data_viewer.ae_table import AeTableConfig
        from clinical_data_viewer.controllers.analysis import AeTableResultContext
        from clinical_data_viewer.controllers.analysis_controller import (
            AnalysisController,
        )
        from clinical_data_viewer.domain import (
            DatasetHandle,
            DatasetMetadata,
            VariableMetadata,
        )
        from clinical_data_viewer.settings import AppSettings
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

            def browse_ae_table_adsl_dataset(self):
                return None

            def browse_categorical_adsl_dataset(self):
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
                        VariableMetadata("AEBODSYS"),
                        VariableMetadata("AEDECOD"),
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
                make_tab(root, "ae-result"),
            )
            host = Host(source, {source, other, adsl, result})
            panel = AnalysisPanel()
            controller = AnalysisController(
                host, panel, TempManager(root / "temp"), AppSettings()
            )

            controller.show_ae_table_builder()
            host.active = other
            controller.show_ae_table_builder()
            self.assertIs(controller.ae_table_source, source)
            self.assertEqual(
                panel.ae_table_builder.source_label.text(),
                "Source: " + str(source.handle.source_path),
            )

            controller.ae_table._ae_table_input_tabs.add(source)
            blocker = controller.tab_close_blocker(source)
            self.assertIsNotNone(blocker)
            self.assertEqual(blocker.title, "AE Table Running")
            controller.ae_table._ae_table_input_tabs.clear()

            controller.ae_table._ae_table_results[result] = AeTableResultContext(
                source.handle,
                adsl.handle,
                AeTableConfig("AEBODSYS", "AEDECOD", "TRT01A"),
            )
            self.assertEqual(
                set(controller.take_result_release_paths(result)),
                {
                    source.handle.temporary_path.parent,
                    adsl.handle.temporary_path.parent,
                },
            )
            self.assertEqual(controller.take_result_release_paths(result), ())

            panel.ae_table_builder.clear()
            self.assertIsNone(controller.ae_table_source)
            panel.deleteLater()

    def test_proc_means_binding_close_blocker_and_result_release_paths(self) -> None:
        """PROC MEANS Builder lifecycle state is owned by its module controller."""
        from clinical_data_viewer.controllers.analysis import ProcMeansResultContext
        from clinical_data_viewer.controllers.analysis_controller import (
            AnalysisController,
        )
        from clinical_data_viewer.domain import (
            DatasetHandle,
            DatasetMetadata,
            VariableMetadata,
        )
        from clinical_data_viewer.proc_means import ProcMeansConfig
        from clinical_data_viewer.settings import AppSettings
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

            def browse_ae_table_adsl_dataset(self):
                return None

            def browse_categorical_adsl_dataset(self):
                return None

            def unique_analysis_tab_title(self, base):
                return base

            def discard_analysis_result(self, handle):
                return None

            def set_analysis_task_status(self, text):
                return None

            def show_proc_means_query_result(self, *args):
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
                        VariableMetadata("AVAL", kind="numeric"),
                    ),
                ),
                1,
                True,
            )
            return DatasetTab(handle, 100)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, other, result = (
                make_tab(root, "adlb"),
                make_tab(root, "adae"),
                make_tab(root, "proc-result"),
            )
            host = Host(source, {source, other, result})
            panel = AnalysisPanel()
            controller = AnalysisController(
                host, panel, TempManager(root / "temp"), AppSettings()
            )

            controller.show_proc_means_builder()
            host.active = other
            controller.show_proc_means_builder()
            self.assertIs(controller.proc_means_source, source)
            self.assertEqual(
                panel.builder.source_label.text(), "Source: " + str(source.handle.source_path)
            )

            controller.proc_means._proc_means_input_tabs.add(source)
            blocker = controller.tab_close_blocker(source)
            self.assertIsNotNone(blocker)
            self.assertEqual(blocker.title, "PROC MEANS Running")
            controller.proc_means._proc_means_input_tabs.clear()

            controller.proc_means._proc_means_results[result] = ProcMeansResultContext(
                source.handle, ProcMeansConfig(("AVAL",))
            )
            self.assertEqual(
                controller.take_result_release_paths(result),
                (source.handle.temporary_path.parent,),
            )
            self.assertEqual(controller.take_result_release_paths(result), ())

            panel.builder.clear()
            self.assertIsNone(controller.proc_means_source)
            panel.deleteLater()


if __name__ == "__main__":
    unittest.main()
