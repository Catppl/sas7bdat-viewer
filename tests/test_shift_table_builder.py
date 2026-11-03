from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class ShiftTableBuilderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    @staticmethod
    def _analysis_metadata():
        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata

        return DatasetMetadata(
            "ADLB",
            12,
            tuple(
                VariableMetadata(name, kind=kind)
                for name, kind in (
                    ("USUBJID", "character"),
                    ("SUBJID2", "character"),
                    ("PARAMCD", "character"),
                    ("AVISIT", "character"),
                    ("TRTA", "character"),
                    ("BTOXGR", "numeric"),
                    ("ATOXGR", "numeric"),
                    ("ANL01FL", "character"),
                )
            ),
        )

    @staticmethod
    def _population_metadata():
        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata

        return DatasetMetadata(
            "ADSL",
            8,
            (
                VariableMetadata("USUBJID"),
                VariableMetadata("SUBJID2"),
                VariableMetadata("TRT01A"),
                VariableMetadata("TRTA"),
            ),
        )

    def test_source_refresh_preserves_user_selected_variables_and_filters(self) -> None:
        from clinical_data_viewer.ui.shift_table_builder import ShiftTableBuilder

        builder = ShiftTableBuilder()
        metadata = self._analysis_metadata()
        population = self._population_metadata()
        builder.set_dataset(metadata, "adlb.sas7bdat", 'ANL01FL = "Y"')
        builder.subject_variable.setCurrentIndex(
            builder.subject_variable.findData("SUBJID2")
        )
        builder.treatment_variable.setCurrentIndex(
            builder.treatment_variable.findData("TRTA")
        )
        builder.filter_editor.setText('PARAMCD = "ALT"')
        builder.set_population_dataset(population, "adsl.sas7bdat", 'SAFFL = "Y"')
        builder.population_subject_variable.setCurrentIndex(
            builder.population_subject_variable.findData("SUBJID2")
        )
        builder.population_treatment_variable.setCurrentIndex(
            builder.population_treatment_variable.findData("TRTA")
        )
        builder.population_filter_editor.setText('SAFFL = "Y" and TRT01A ne ""')

        builder.set_dataset(None, "")
        builder.set_population_dataset(None, "")
        builder.set_dataset(metadata, "adlb-reloaded.sas7bdat", 'ANL01FL = "N"')
        builder.set_population_dataset(population, "adsl-reloaded.sas7bdat")

        self.assertEqual(builder.subject_variable.currentData(), "SUBJID2")
        self.assertEqual(builder.treatment_variable.currentData(), "TRTA")
        self.assertEqual(builder.filter_editor.toPlainText(), 'PARAMCD = "ALT"')
        self.assertEqual(builder.population_subject_variable.currentData(), "SUBJID2")
        self.assertEqual(builder.population_treatment_variable.currentData(), "TRTA")
        self.assertEqual(
            builder.population_filter_editor.toPlainText(),
            'SAFFL = "Y" and TRT01A ne ""',
        )

    def test_order_buttons_and_actions_emit_complete_selection(self) -> None:
        from clinical_data_viewer.ui.shift_table_builder import ShiftTableBuilder

        builder = ShiftTableBuilder()
        builder.set_dataset(self._analysis_metadata(), "adlb.sas7bdat")
        builder.set_population_dataset(self._population_metadata(), "adsl.sas7bdat")
        builder.set_level_values("parameters", ("ALT", "AST"))
        builder.set_level_values("visits", ("Week 4", "Week 8"))
        builder.set_level_values("treatments", ("Drug A", "Placebo"))
        builder.set_level_values("grades", (0, 1, 2))

        builder.parameter_levels.table.selectRow(1)
        builder.parameter_levels.up_button.click()
        self.assertEqual(
            tuple(level.raw_value for level in builder.parameter_levels.levels()),
            ("AST", "ALT"),
        )

        run_requests = []
        code_requests = []
        preview_requests = []
        builder.run_requested.connect(run_requests.append)
        builder.sas_code_requested.connect(code_requests.append)
        builder.shell_preview_requested.connect(lambda: preview_requests.append(True))
        builder.run_button.click()
        builder.sas_code_button.click()
        builder.preview_button.click()

        self.assertEqual(len(run_requests), 1)
        self.assertEqual(len(code_requests), 1)
        self.assertEqual(len(preview_requests), 1)
        self.assertEqual(run_requests[0].parameters[0].raw_value, "AST")
        self.assertEqual(run_requests[0].grade_levels[-1].raw_value, 2)

    def test_compact_scroll_area_and_click_only_controls_remain_usable(self) -> None:
        from PySide6.QtWidgets import QPushButton, QScrollArea

        from clinical_data_viewer.ui.shift_table_builder import ShiftTableBuilder
        from clinical_data_viewer.ui.widgets import ClickOnlyComboBox, MouseOnlySpinBox

        builder = ShiftTableBuilder()
        builder.set_dataset(self._analysis_metadata(), "adlb.sas7bdat")
        builder.resize(260, 420)
        builder.show()
        self.application.processEvents()

        scroll = builder.findChild(QScrollArea)
        self.assertIsNotNone(scroll)
        assert scroll is not None
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        self.assertIsInstance(builder.treatment_variable, ClickOnlyComboBox)
        self.assertIsInstance(builder.percent_digits, MouseOnlySpinBox)

        wheel = Mock()
        builder.treatment_variable.wheelEvent(wheel)
        wheel.ignore.assert_called_once_with()
        spin_wheel = Mock()
        builder.percent_digits.wheelEvent(spin_wheel)
        spin_wheel.ignore.assert_called_once_with()

        buttons = {
            button.text(): button for button in builder.findChildren(QPushButton)
        }
        for text in ("Shell Preview…", "Clear", "SAS Code Generator…", "Run"):
            self.assertIn(text, buttons)
            self.assertTrue(buttons[text].isEnabled())
        builder.set_busy(True, "Running")
        self.assertFalse(builder.source_selector.isEnabled())
        self.assertFalse(builder.population_selector.isEnabled())
        self.assertFalse(builder.run_button.isEnabled())
        builder.set_busy(False)
        self.assertTrue(builder.source_selector.isEnabled())
        builder.close()
        self.application.processEvents()

    def test_clear_removes_configuration_but_keeps_builder_available(self) -> None:
        from clinical_data_viewer.ui.shift_table_builder import ShiftTableBuilder

        builder = ShiftTableBuilder()
        builder.set_dataset(self._analysis_metadata(), "adlb.sas7bdat")
        builder.set_level_values("parameters", ("ALT",))
        builder.filter_editor.setText('PARAMCD = "ALT"')
        cleared = []
        builder.cleared.connect(lambda: cleared.append(True))

        builder.clear_button.click()

        self.assertEqual(cleared, [True])
        self.assertEqual(builder.parameter_levels.levels(), ())
        self.assertEqual(builder.filter_editor.toPlainText(), "")
        self.assertEqual(builder.analysis_flag_value.text(), "Y")

    def test_shift_result_columns_enable_context_menu_drilldown(self) -> None:
        from clinical_data_viewer.domain import DatasetMetadata
        from clinical_data_viewer.ui.copy_table import CopyTableView

        metadata = DatasetMetadata(
            "Shift Table",
            1,
            (),
            shift_table_drilldown_columns=("T1_G1", "T1_TOTAL"),
        )
        table = SimpleNamespace(
            _context_variable=lambda: "T1_G1",
            model=lambda: SimpleNamespace(metadata=metadata),
        )
        self.assertTrue(CopyTableView._context_variable_is_shift_table_statistic(table))
        table._context_variable = lambda: "PARAMETER"
        self.assertFalse(
            CopyTableView._context_variable_is_shift_table_statistic(table)
        )

    def test_result_cell_and_header_emit_their_drilldown_requests(self) -> None:
        from clinical_data_viewer.data_store import DataStore
        from clinical_data_viewer.filter_engine import CompiledFilter
        from clinical_data_viewer.shift_table import ShiftTableEngine
        from clinical_data_viewer.temp_manager import TempManager
        from clinical_data_viewer.ui.dataset_tab import DatasetTab
        from tests.test_shift_table import make_analysis, make_config, make_population

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_analysis(
                root,
                (
                    ("S1", "Y", "AST", "Week 4", "A", 0, 1, "Y"),
                    ("S2", "Y", "AST", "Week 4", "B", 0, 1, "Y"),
                ),
            )
            population = make_population(root, (("S1", "Y", "A"), ("S2", "Y", "B")))
            result = ShiftTableEngine(TempManager(root / "temp")).run(
                source,
                make_config(source, population),
                population=population,
            )
            tab = DatasetTab(result, 500)
            page = DataStore().query_page(
                result.database_path,
                result.metadata,
                tab.visible_columns,
                CompiledFilter("", ()),
                None,
                0,
                100,
            )
            tab.model.set_page(
                0,
                page.rows,
                page.filtered_count,
                source_rows=page.source_rows,
            )
            cells: list[tuple[int, str, str]] = []
            headers: list[str] = []
            tab.shift_table_drilldown_requested.connect(
                lambda row, column, display: cells.append((row, column, display))
            )
            tab.shift_table_header_drilldown_requested.connect(headers.append)

            column = tab.visible_columns.index("T1_G2")
            index = tab.model.index(0, column)
            tab.table.doubleClicked.emit(index)
            tab.filter_header.sectionDoubleClicked.emit(column)

            self.assertEqual(cells, [(0, "T1_G2", "1 (100.0)")])
            self.assertEqual(headers, ["T1_G2"])
            tab.close()
            self.application.processEvents()


if __name__ == "__main__":
    unittest.main()
