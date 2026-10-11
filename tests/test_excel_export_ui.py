from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


class ExcelExportUiTests(unittest.TestCase):
    def test_large_export_requires_confirmation_and_preserves_applied_filter(self) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QMessageBox

        from clinical_data_viewer.domain import (
            DatasetHandle,
            DatasetMetadata,
            SortSpec,
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

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data.sas7bdat"
            source.write_bytes(b"fixture")
            handle = DatasetHandle(
                source,
                source,
                root / "data.sqlite",
                DatasetMetadata("data", 2, (VariableMetadata("AGE", kind="numeric"),)),
            )
            tab = SimpleNamespace(
                handle=handle,
                visible_columns=["AGE"],
                cache_complete=True,
                compiled_filter=CompiledFilter('"AGE" > ?', (40,)),
                model=SimpleNamespace(
                    sort_spec=SortSpec("AGE"),
                    highlighted_rows=set(),
                    highlighted_columns=set(),
                ),
                current_where_text=lambda: "AGE > 40",
                manual_highlights_for_export=dict,
            )
            window = MainWindow(
                TestSettings(), TempManager(root / "temp"), FilterHistory(root / "history.sqlite")
            )
            destination = root / "data.xlsx"
            destination.write_bytes(b"original")
            seen = []

            def submit(_owner, function, completed, _failed):
                completed(function(SimpleNamespace(report=lambda _message: None)))

            def estimate(view):
                seen.append(view)
                return 2, 101_000_000

            def export(view, path, _progress):
                seen.append(view)
                path.write_bytes(b"exported")
                return 2

            with (
                patch.object(window, "current_dataset_tab", return_value=tab),
                patch.object(window.tabs, "indexOf", return_value=0),
                patch("clinical_data_viewer.ui.main_window.QFileDialog.getSaveFileName", return_value=(str(destination), "")),
                patch("clinical_data_viewer.ui.main_window.QMessageBox.question", return_value=QMessageBox.No) as question,
                patch("clinical_data_viewer.ui.main_window.QMessageBox.information"),
                patch.object(window, "_submit", side_effect=submit),
                patch.object(window.excel_exporter, "estimate_bytes", side_effect=estimate),
                patch.object(window.excel_exporter, "export", side_effect=export),
            ):
                window.export_excel_current()
                self.assertEqual(destination.read_bytes(), b"original")
                self.assertEqual(len(seen), 1)
                self.assertEqual(seen[0].applied_filter_text, "AGE > 40")
                self.assertTrue(question.called)
                question.return_value = QMessageBox.Yes
                window.export_excel_current()
                self.assertEqual(destination.read_bytes(), b"exported")
                self.assertEqual(len(seen), 3)
            window.close()
            app.processEvents()
