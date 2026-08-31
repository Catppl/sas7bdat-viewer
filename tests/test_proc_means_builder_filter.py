from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from dataclasses import replace
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

    def test_variable_controls_fit_the_default_analysis_width(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtTest import QTest

        from clinical_data_viewer.ui.proc_means_builder import ProcMeansBuilder

        builder = ProcMeansBuilder()
        builder.set_dataset(self._metadata(), r"C:\data\adlb.sas7bdat")
        builder.resize(310, 520)
        builder.show()
        self.application.processEvents()

        self.assertLessEqual(builder.minimumSizeHint().width(), 310)
        self.assertEqual(builder.scroll_area.horizontalScrollBar().maximum(), 0)
        for editor in (
            builder.analysis_variables,
            builder.by_variables,
            builder.class_variables,
        ):
            self.assertTrue(editor.remove_button.isVisible())
            self.assertLess(editor.remove_button.geometry().right(), editor.width())
            self.assertGreaterEqual(editor.editor.width(), 80)
            self.assertLessEqual(editor.values.geometry().right(), editor.width())

        self.assertLessEqual(
            builder.decimal_groups.geometry().right(), builder.content.width()
        )
        self.assertLessEqual(builder.filter_editor.width(), builder.content.width())
        self.assertGreaterEqual(builder.filter_editor.height(), 56)
        self.assertEqual(builder.filter_editor.horizontalScrollBar().maximum(), 0)
        long_filter = (
            'ANL01FL = "Y" and PARAMCD = "ALB" and '
            'AVISIT = "Week 12" and TRT01AN in (1, 2, 3)'
        )
        builder.filter_editor.setText(long_filter)
        self.application.processEvents()
        self.assertEqual(builder.current_filter_text(), long_filter)
        self.assertEqual(builder.statistics_layout.horizontalSpacing(), 4)
        self.assertLessEqual(
            builder.statistics_box.minimumSizeHint().width(),
            builder.scroll_area.viewport().width(),
        )
        for button in (
            builder.settings_button,
            builder.clear_button,
            builder.run_button,
            builder.sas_code_button,
            builder.r_code_button,
        ):
            self.assertTrue(button.isVisible())
            self.assertLess(button.geometry().right(), builder.width())

        builder.analysis_variables.set_variables(("AVAL",))
        builder.analysis_variables.values.setCurrentRow(0)
        QTest.mouseClick(builder.analysis_variables.remove_button, Qt.LeftButton)
        self.assertEqual(builder.analysis_variables.selected_variables(), ())
        self.assertGreater(builder.scroll_area.verticalScrollBar().maximum(), 0)
        builder.scroll_area.verticalScrollBar().setValue(
            builder.scroll_area.verticalScrollBar().maximum()
        )
        self.application.processEvents()
        self.assertEqual(
            builder.scroll_area.verticalScrollBar().value(),
            builder.scroll_area.verticalScrollBar().maximum(),
        )
        builder.close()
        builder.deleteLater()

    def test_metadata_refresh_preserves_filter_and_prunes_stale_selections(self):
        from PySide6.QtCore import Qt

        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.proc_means_builder import ProcMeansBuilder

        builder = ProcMeansBuilder()
        old_metadata = self._metadata()
        builder.set_dataset(old_metadata, r"C:\data\adlb.sas7bdat", 'ANL01FL = "Y"')
        builder.analysis_variables.set_variables(("AVAL",))
        builder.by_variables.set_variables(("PARAMCD",))
        decimal_item = builder.decimal_groups.item(0)
        decimal_item.setCheckState(Qt.Checked)
        builder.filter_editor.setText('PARAMCD = "ALB"')

        new_metadata = DatasetMetadata(
            "ADLB",
            3,
            (
                VariableMetadata("USUBJID"),
                VariableMetadata("AVAL"),  # no longer valid for Analysis Variables
                VariableMetadata("PARAMCD"),
                VariableMetadata("NEWNUM", kind="numeric"),
            ),
        )
        builder.set_dataset(
            new_metadata, r"C:\data\adlb.sas7bdat", 'ANL01FL = "N"'
        )

        self.assertEqual(builder.current_filter_text(), 'PARAMCD = "ALB"')
        self.assertEqual(builder.analysis_variables.selected_variables(), ())
        self.assertEqual(builder.by_variables.selected_variables(), ("PARAMCD",))
        self.assertEqual(builder.selected_decimal_groups(), ("PARAMCD",))
        self.assertEqual(
            builder.analysis_variables.editor.completer().model().stringList(),
            ["NEWNUM"],
        )
        builder.deleteLater()

    def test_metadata_refresh_reports_removed_selections(self):
        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.proc_means_builder import ProcMeansBuilder

        builder = ProcMeansBuilder()
        old_metadata = self._metadata()
        builder.set_dataset(old_metadata, r"C:\data\adlb.sas7bdat")
        builder.analysis_variables.set_variables(("AVAL",))
        builder.by_variables.set_variables(("PARAMCD",))

        new_metadata = DatasetMetadata(
            "ADLB",
            1,
            (
                VariableMetadata("USUBJID"),
                VariableMetadata("NEWNUM", kind="numeric"),
            ),
        )
        removed = builder.set_dataset(
            new_metadata, r"C:\data\adlb.sas7bdat"
        )

        self.assertEqual(removed, ("AVAL", "PARAMCD"))
        builder.deleteLater()

    def test_source_reloading_state_disables_and_restores_actions(self):
        from clinical_data_viewer.ui.proc_means_builder import ProcMeansBuilder

        builder = ProcMeansBuilder()
        builder.set_dataset(self._metadata(), r"C:\data\adlb.sas7bdat")
        self.assertTrue(builder.run_button.isEnabled())
        self.assertTrue(builder.sas_code_button.isEnabled())
        self.assertTrue(builder.filter_editor.isEnabled())

        builder.set_source_reloading(True)
        self.assertFalse(builder.run_button.isEnabled())
        self.assertFalse(builder.sas_code_button.isEnabled())
        self.assertFalse(builder.r_code_button.isEnabled())
        self.assertFalse(builder.filter_editor.isEnabled())
        self.assertFalse(builder.analysis_variables.isEnabled())

        builder.set_source_reloading(False)
        self.assertTrue(builder.run_button.isEnabled())
        self.assertTrue(builder.sas_code_button.isEnabled())
        self.assertTrue(builder.r_code_button.isEnabled())
        self.assertTrue(builder.filter_editor.isEnabled())
        builder.deleteLater()

    def test_controller_reload_refresh_keeps_builder_filter_and_reloads_metadata(self):
        from clinical_data_viewer.controllers.analysis.proc_means import (
            ProcMeansController,
        )
        from clinical_data_viewer.domain import (
            DatasetHandle,
            DatasetMetadata,
            VariableMetadata,
        )
        from clinical_data_viewer.settings import AppSettings
        from clinical_data_viewer.temp_manager import TempManager
        from clinical_data_viewer.ui.proc_means_builder import ProcMeansBuilder

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
        source_tab = SimpleNamespace(
            handle=handle,
            cache_complete=True,
            reload_in_progress=False,
            current_where_text=lambda: 'ANL01FL = "Y"',
        )
        builder = ProcMeansBuilder()
        builder.set_dataset(metadata, str(handle.source_path), 'ANL01FL = "Y"')
        builder.filter_editor.setText('PARAMCD = "ALB"')
        builder.analysis_variables.set_variables(("AVAL",))
        owner = SimpleNamespace(
            _host=Host(),
            _panel=SimpleNamespace(builder=builder),
            _parent_widget=lambda: None,
            _safe_source_name=lambda _source: "adlb",
        )
        with tempfile.TemporaryDirectory() as path:
            controller = ProcMeansController(
                owner, TempManager(Path(path) / "temp"), AppSettings()
            )
            controller._proc_means_source = source_tab
            controller.source_reload_started(source_tab)
            self.assertFalse(builder.run_button.isEnabled())
            controller.source_reload_failed(source_tab)
            self.assertTrue(builder.run_button.isEnabled())
            self.assertEqual(builder.current_filter_text(), 'PARAMCD = "ALB"')
            source_tab.cache_complete = False
            controller.source_reload_started(source_tab)
            controller.source_reload_failed(source_tab)
            self.assertFalse(builder.run_button.isEnabled())
            source_tab.cache_complete = True
            controller.source_reload_started(source_tab)
            new_metadata = DatasetMetadata(
                "ADLB",
                3,
                (
                    VariableMetadata("USUBJID"),
                    VariableMetadata("AVAL"),
                    VariableMetadata("ANL01FL"),
                    VariableMetadata("PARAMCD"),
                    VariableMetadata("NEWNUM", kind="numeric"),
                ),
            )
            source_tab.handle = replace(handle, metadata=new_metadata)
            controller.source_reload_completed(source_tab)

        self.assertEqual(builder.current_filter_text(), 'PARAMCD = "ALB"')
        self.assertTrue(builder.run_button.isEnabled())
        self.assertIn("Removed unavailable selections: AVAL", builder.status.text())
        self.assertEqual(
            builder.analysis_variables.editor.completer().model().stringList(),
            ["NEWNUM"],
        )
        builder.deleteLater()

    def test_context_rejects_source_while_reload_is_in_progress(self):
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
        source_tab = SimpleNamespace(
            handle=handle, cache_complete=True, reload_in_progress=True
        )
        builder = ProcMeansBuilder()
        builder.set_dataset(metadata, str(handle.source_path))
        owner = SimpleNamespace(
            _host=Host(), _panel=SimpleNamespace(builder=builder), _parent_widget=lambda: None
        )
        with tempfile.TemporaryDirectory() as path:
            controller = ProcMeansController(
                owner, TempManager(Path(path) / "temp"), AppSettings()
            )
            controller._proc_means_source = source_tab
            selection = ProcMeansBuilderSelection(("AVAL",), (), (), ("mean",), ())
            with patch(
                "clinical_data_viewer.controllers.analysis.proc_means.QMessageBox.warning"
            ) as warning:
                self.assertIsNone(controller._proc_means_context(selection, "Test"))
            warning.assert_called_once()
            self.assertIn("reloading", warning.call_args.args[2])
        builder.deleteLater()

    def test_code_generators_use_builder_owned_filter_snapshot(self):
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
        source_tab = SimpleNamespace(
            handle=handle,
            cache_complete=True,
            reload_in_progress=False,
            current_where_text=lambda: 'ANL01FL = "N"',
        )
        builder = ProcMeansBuilder()
        builder.set_dataset(metadata, str(handle.source_path), 'ANL01FL = "N"')
        builder.filter_editor.setText('ANL01FL = "Y"')
        owner = SimpleNamespace(
            _host=Host(),
            _panel=SimpleNamespace(builder=builder),
            _parent_widget=lambda: None,
            _safe_source_name=lambda _source: "adlb",
        )
        selection = ProcMeansBuilderSelection(("AVAL",), (), (), ("mean",), ())
        cases = (
            (
                "generate_proc_means_sas_code",
                "_sas_proc_means_generator",
                "SasCodeDialog",
            ),
            (
                "generate_proc_means_r_code",
                "_r_proc_means_generator",
                "RCodeDialog",
            ),
        )
        with tempfile.TemporaryDirectory() as path:
            controller = ProcMeansController(
                owner, TempManager(Path(path) / "temp"), AppSettings()
            )
            controller._proc_means_source = source_tab
            for method_name, generator_name, dialog_name in cases:
                with (
                    patch.object(
                        getattr(controller, generator_name),
                        "generate",
                        return_value="generated code",
                    ) as generate,
                    patch(
                        "clinical_data_viewer.controllers.analysis.proc_means."
                        + dialog_name
                    ) as dialog,
                ):
                    getattr(controller, method_name)(selection)
                configuration = generate.call_args.args[0]
                self.assertEqual(
                    configuration["filter"]["text"], 'ANL01FL = "Y"'
                )
                self.assertTrue(configuration["filter"]["ast"])
                dialog.assert_called_once()
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
