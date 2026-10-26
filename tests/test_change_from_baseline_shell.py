from __future__ import annotations

import importlib.util
import os
import unittest
from importlib.resources import files
from pathlib import Path

from clinical_data_viewer.shell_assets import load_change_from_baseline_shell_preview


class ChangeFromBaselineShellAssetTests(unittest.TestCase):
    def test_shell_source_and_preview_assets_exist(self) -> None:
        root = Path(__file__).parents[1]
        self.assertTrue(
            (root / "docs" / "change-from-baseline-horizontal-shell.docx").is_file()
        )
        self.assertTrue(
            (
                root
                / "clinical_data_viewer"
                / "assets"
                / "shells"
                / "change_from_baseline_horizontal.png"
            ).is_file()
        )

    def test_packaged_preview_is_a_png(self) -> None:
        self.assertTrue(load_change_from_baseline_shell_preview().startswith(b"\x89PNG\r\n\x1a\n"))

    def test_packaged_docx_matches_the_maintained_source(self) -> None:
        root = Path(__file__).parents[1]
        packaged = files("clinical_data_viewer.assets.shells").joinpath(
            "change_from_baseline_horizontal.docx"
        ).read_bytes()
        source = (root / "docs" / "change-from-baseline-horizontal-shell.docx").read_bytes()
        self.assertEqual(packaged, source)


PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 is not installed")
class ChangeFromBaselineShellUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication

        cls.application = QApplication.instance() or QApplication([])

    def test_builder_exposes_shell_preview_button(self) -> None:
        from PySide6.QtWidgets import QPushButton

        from clinical_data_viewer.ui.change_from_baseline_builder import (
            ChangeFromBaselineBuilder,
        )

        builder = ChangeFromBaselineBuilder()
        requests: list[bool] = []
        builder.shell_preview_requested.connect(lambda: requests.append(True))
        button = next(
            button
            for button in builder.findChildren(QPushButton)
            if button.text() == "Shell Preview…"
        )
        button.click()
        self.assertEqual(requests, [True])
        builder.deleteLater()

    def test_shell_preview_dialog_loads_packaged_png(self) -> None:
        from clinical_data_viewer.ui.shell_preview_dialog import ShellPreviewDialog

        dialog = ShellPreviewDialog()
        self.assertFalse(dialog._pixmap.isNull())
        self.assertEqual(dialog.windowTitle(), "Change from Baseline Shell Preview")
        dialog.deleteLater()


if __name__ == "__main__":
    unittest.main()
