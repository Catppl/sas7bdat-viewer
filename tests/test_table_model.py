from __future__ import annotations

import importlib.util
import os
import unittest

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class TableModelTests(unittest.TestCase):
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
