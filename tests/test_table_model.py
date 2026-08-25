from __future__ import annotations

import importlib.util
import os
import unittest

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class TableModelTests(unittest.TestCase):
    def test_generated_compare_cells_use_page_level_highlights(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.table_model import DatasetTableModel

        _application = QApplication.instance() or QApplication([])
        metadata = DatasetMetadata(
            "compare",
            2,
            (VariableMetadata("SIDE"), VariableMetadata("AVAL", kind="numeric")),
            pair_id_column="COMPARE_PAIR",
            side_order_column="__SIDE_ORDER",
            diff_columns_column="__DIFF",
        )
        model = DatasetTableModel(metadata, ["SIDE", "AVAL"], page_size=10)
        model.set_page(
            0,
            (("Main", 1.0), ("QC", 2.0)),
            2,
            (frozenset({"AVAL"}), frozenset({"AVAL"})),
        )
        self.assertIsNone(model.data(model.index(0, 0), Qt.BackgroundRole))
        self.assertIsNotNone(model.data(model.index(0, 1), Qt.BackgroundRole))
        self.assertIsNotNone(model.data(model.index(1, 1), Qt.BackgroundRole))

    def test_compare_warning_colors_and_advanced_columns(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from pathlib import Path

        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.domain import (
            DatasetHandle,
            DatasetMetadata,
            VariableMetadata,
        )
        from clinical_data_viewer.ui.dataset_tab import DatasetTab

        _application = QApplication.instance() or QApplication([])
        variables = (
            VariableMetadata("COMPARE_PAIR", kind="numeric"),
            VariableMetadata("SIDE"),
            VariableMetadata("MATCH_COST", kind="numeric"),
            VariableMetadata("MATCH_MARGIN", kind="numeric"),
            VariableMetadata("MAIN_ONLY"),
        )
        metadata = DatasetMetadata(
            "compare",
            1,
            variables,
            pair_id_column="COMPARE_PAIR",
            row_warning_column="__WARNING",
            advanced_columns=("COMPARE_PAIR", "MATCH_COST", "MATCH_MARGIN"),
            export_excluded_columns=("COMPARE_PAIR", "MATCH_COST", "MATCH_MARGIN"),
            warning_columns=("MAIN_ONLY",),
            warning_column_messages=(("MAIN_ONLY", "Variable exists only in Main."),),
        )
        handle = DatasetHandle(
            Path("compare"),
            Path("compare.tmp"),
            Path("compare.sqlite"),
            metadata,
            1,
            True,
            kind="compare",
        )
        tab = DatasetTab(handle, 10)
        self.assertEqual(tab.visible_columns, ["SIDE", "MAIN_ONLY"])
        self.assertEqual(tab.available_columns(), ["SIDE", "MAIN_ONLY"])
        tab.set_advanced_visible(True)
        self.assertEqual(tab.available_columns(), [item.name for item in variables])
        tab.model.set_page(
            0,
            ((1.0, "Main", None, None, "value"),),
            1,
            (frozenset(),),
            (True,),
        )
        warning = tab.model.data(tab.model.index(0, 4), Qt.BackgroundRole)
        self.assertEqual(warning.name(), "#ffd9d9")
        self.assertEqual(
            tab.model.headerData(4, Qt.Horizontal, Qt.BackgroundRole).name(),
            "#ffd9d9",
        )

    def test_virtual_model_requests_an_offscreen_page_without_loading_prior_rows(
        self,
    ) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.table_model import DatasetTableModel

        application = QApplication.instance() or QApplication([])
        metadata = DatasetMetadata("large", 100_000, (VariableMetadata("USUBJID"),))
        model = DatasetTableModel(metadata, ["USUBJID"], page_size=500)
        requested: list[tuple[int, int]] = []
        model.page_requested.connect(
            lambda offset, limit: requested.append((offset, limit))
        )

        self.assertEqual(model.rowCount(), 100_000)
        self.assertIsNone(model.data(model.index(75_123, 0), Qt.DisplayRole))
        application.processEvents()
        self.assertEqual(requested, [(75_000, 500)])
        model.set_page(75_000, (("101-001",),), 100_000)
        self.assertEqual(model.data(model.index(75_000, 0)), "101-001")

    def test_zero_visible_columns_does_not_request_data(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.table_model import DatasetTableModel

        application = QApplication.instance() or QApplication([])
        metadata = DatasetMetadata("empty-view", 10, (VariableMetadata("A"),))
        model = DatasetTableModel(metadata, [], page_size=5)
        requested: list[tuple[int, int]] = []
        model.page_requested.connect(
            lambda offset, limit: requested.append((offset, limit))
        )
        model.reset_query(filtered_count=10)
        application.processEvents()
        self.assertEqual(model.columnCount(), 0)
        self.assertEqual(requested, [])


if __name__ == "__main__":
    unittest.main()
