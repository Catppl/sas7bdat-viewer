from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class ProcMeansBuilderFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    @staticmethod
    def _metadata():
        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata

        return DatasetMetadata(
            "ADLB",
            2,
            (
                VariableMetadata("USUBJID"),
                VariableMetadata("AVAL", kind="numeric"),
                VariableMetadata("ANL01FL"),
                VariableMetadata("PARAMCD"),
            ),
        )

    def test_filter_editor_is_independent_and_reinitialized_for_new_source(self):
        from clinical_data_viewer.ui.proc_means_builder import ProcMeansBuilder

        builder = ProcMeansBuilder()
        metadata = self._metadata()
        builder.set_dataset(metadata, r"C:\data\adlb.sas7bdat", 'ANL01FL = "Y"')
        self.assertEqual(builder.filter_editor.text(), 'ANL01FL = "Y"')

        builder.filter_editor.setText('PARAMCD = "ALB"')
        self.assertEqual(builder.current_filter_text(), 'PARAMCD = "ALB"')

        # A later source-tab WHERE change must not overwrite the Builder filter.
        builder.set_dataset(
            metadata, r"C:\data\adlb.sas7bdat", 'ANL01FL = "N"'
        )
        self.assertEqual(builder.current_filter_text(), 'PARAMCD = "ALB"')

        # A different source gets its own current WHERE.
        builder.set_dataset(
            metadata, r"C:\data\adae.sas7bdat", 'TRTEMFL = "Y"'
        )
        self.assertEqual(builder.current_filter_text(), 'TRTEMFL = "Y"')

        builder.set_dataset(None, "")
        self.assertEqual(builder.current_filter_text(), "")
        builder.clear()
        self.assertEqual(builder.current_filter_text(), "")
        builder.deleteLater()

    def test_builder_filter_compiles_and_is_carried_into_proc_means_config(self):
        from clinical_data_viewer.controllers.analysis.proc_means import (
            ProcMeansController,
        )
        from clinical_data_viewer.domain import DatasetHandle
        from clinical_data_viewer.settings import AppSettings
        from clinical_data_viewer.temp_manager import TempManager
        from clinical_data_viewer.ui.proc_means_builder import (
            ProcMeansBuilder,
            ProcMeansBuilderSelection,
        )

        metadata = self._metadata()
        builder = ProcMeansBuilder()
        builder.set_dataset(metadata, r"C:\data\adlb.sas7bdat")
        builder.filter_editor.setText('ANL01FL = "Y" and PARAMCD = "ALB"')
        filter_text = builder.current_filter_text()
        class Host:
            def is_open_dataset_tab(self, _tab):
                return True

        handle = DatasetHandle(
            Path("C:/data/adlb.sas7bdat"),
            Path("C:/temp/adlb.sas7bdat"),
            Path("C:/temp/adlb.sqlite"),
            metadata,
            2,
            True,
        )
        source_tab = type("SourceTab", (), {})()
        source_tab.handle = handle
        source_tab.cache_complete = True
        owner = SimpleNamespace(
            _host=Host(), _panel=SimpleNamespace(builder=builder), _parent_widget=lambda: None
        )
        with tempfile.TemporaryDirectory() as path:
            controller = ProcMeansController(
                owner, TempManager(Path(path) / "temp"), AppSettings()
            )
            controller._proc_means_source = source_tab
            selection = ProcMeansBuilderSelection(
                ("AVAL",), (), (), ("mean",), ()
            )
            context = controller._proc_means_context(selection, "Test")
        self.assertIsNotNone(context)
        config = context[1]
        self.assertEqual(config.filter_text, filter_text)
        self.assertTrue(config.compiled_filter.sql)
        builder.deleteLater()

    def test_invalid_builder_filter_still_uses_existing_validation_warning(self):
        from clinical_data_viewer.controllers.analysis.proc_means import (
            ProcMeansController,
        )
        from clinical_data_viewer.domain import DatasetHandle
        from clinical_data_viewer.settings import AppSettings
        from clinical_data_viewer.temp_manager import TempManager
        from clinical_data_viewer.ui.proc_means_builder import (
            ProcMeansBuilder,
            ProcMeansBuilderSelection,
        )

        class Host:
            def is_open_dataset_tab(self, _tab):
                return True

        metadata = self._metadata()
        handle = DatasetHandle(
            Path("C:/data/adlb.sas7bdat"),
            Path("C:/temp/adlb.sas7bdat"),
            Path("C:/temp/adlb.sqlite"),
            metadata,
            2,
            True,
        )
        class SourceTab:
            def __init__(self, handle):
                self.handle = handle
                self.cache_complete = True

        source_tab = SourceTab(handle)
        builder = ProcMeansBuilder()
        builder.set_dataset(metadata, str(handle.source_path))
        builder.filter_editor.setText("ANL01FL =")
        owner = SimpleNamespace(
            _host=Host(), _panel=SimpleNamespace(builder=builder), _parent_widget=lambda: None
        )
        with tempfile.TemporaryDirectory() as path:
            controller = ProcMeansController(
                owner, TempManager(Path(path) / "temp"), AppSettings()
            )
            controller._proc_means_source = source_tab
            selection = ProcMeansBuilderSelection(
                ("AVAL",), (), (), ("mean",), ()
            )
            with patch(
                "clinical_data_viewer.controllers.analysis.proc_means.QMessageBox.warning"
            ) as warning:
                self.assertIsNone(controller._proc_means_context(selection, "Test"))
            warning.assert_called_once()
            self.assertIn("Expected", warning.call_args.args[2])
        builder.deleteLater()

    def test_run_does_not_ask_to_apply_source_dataset_filter(self):
        from clinical_data_viewer.controllers.analysis.proc_means import (
            ProcMeansController,
        )
        from clinical_data_viewer.domain import DatasetHandle
        from clinical_data_viewer.proc_means import ProcMeansConfig
        from clinical_data_viewer.settings import AppSettings
        from clinical_data_viewer.temp_manager import TempManager
        from clinical_data_viewer.ui.proc_means_builder import (
            ProcMeansBuilder,
            ProcMeansBuilderSelection,
        )

        class Host:
            def is_open_dataset_tab(self, _tab):
                return True

            def submit_analysis_task(self, *_args):
                return None

        metadata = self._metadata()
        handle = DatasetHandle(
            Path("C:/data/adlb.sas7bdat"),
            Path("C:/temp/adlb.sas7bdat"),
            Path("C:/temp/adlb.sqlite"),
            metadata,
            2,
            True,
        )
        class SourceTab:
            def __init__(self, handle):
                self.handle = handle
                self.cache_complete = True

        source_tab = SourceTab(handle)
        builder = ProcMeansBuilder()
        builder.set_dataset(metadata, str(handle.source_path), 'ANL01FL = "N"')
        builder.filter_editor.setText('ANL01FL = "Y"')
        owner = SimpleNamespace(
            _host=Host(), _panel=SimpleNamespace(builder=builder), _parent_widget=lambda: None
        )
        with tempfile.TemporaryDirectory() as path:
            controller = ProcMeansController(
                owner, TempManager(Path(path) / "temp"), AppSettings()
            )
            controller._proc_means_source = source_tab
            selection = ProcMeansBuilderSelection(
                ("AVAL",), (), (), ("mean",), ()
            )
            config = ProcMeansConfig(
                ("AVAL",), statistics=("mean",), filter_text='ANL01FL = "Y"'
            )
            with (
                patch.object(
                    controller,
                    "_proc_means_context",
                    return_value=(source_tab, config),
                ),
                patch(
                    "clinical_data_viewer.controllers.analysis.proc_means.QMessageBox.question"
                ) as question,
            ):
                controller.run_proc_means_builder(selection)
            question.assert_not_called()
            self.assertTrue(builder._busy)
            builder.set_busy(False)
        builder.deleteLater()


if __name__ == "__main__":
    unittest.main()
