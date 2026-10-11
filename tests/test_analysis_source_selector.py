from __future__ import annotations

import importlib.util
import os
import unittest

PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class AnalysisSourceSelectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_empty_state_prompts_user_to_open_a_dataset(self) -> None:
        from clinical_data_viewer.ui.analysis_source_selector import (
            AnalysisSourceSelector,
        )

        selector = AnalysisSourceSelector()
        self.assertEqual(selector.combo.count(), 1)
        self.assertIsNone(selector.selected_source())
        self.assertIn("Open a dataset", selector.hint.text())
        selector.deleteLater()

    def test_user_explicitly_selects_and_refresh_preserves_source(self) -> None:
        from clinical_data_viewer.ui.analysis_source_selector import (
            AnalysisSourceSelector,
        )

        source_a = object()
        source_b = object()
        selector = AnalysisSourceSelector()
        selected = []
        selector.source_selected.connect(selected.append)
        datasets = [(source_a, "ADAE"), (source_b, "ADLB")]
        selector.set_sources(datasets, None)
        self.assertIsNone(selector.selected_source())

        selector.combo.setCurrentIndex(selector.combo.findData(source_b))
        self.assertIs(selector.selected_source(), source_b)
        self.assertIs(selected[-1], source_b)

        selector.set_sources(datasets, source_b)
        self.assertIs(selector.selected_source(), source_b)
        selector.deleteLater()


if __name__ == "__main__":
    unittest.main()
