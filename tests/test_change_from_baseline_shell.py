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
        self.assertTrue(
            load_change_from_baseline_shell_preview().startswith(b"\x89PNG\r\n\x1a\n")
        )

    def test_packaged_docx_matches_the_maintained_source(self) -> None:
        root = Path(__file__).parents[1]
        packaged = (
            files("clinical_data_viewer.assets.shells")
            .joinpath("change_from_baseline_horizontal.docx")
            .read_bytes()
        )
        source = (
            root / "docs" / "change-from-baseline-horizontal-shell.docx"
        ).read_bytes()
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

    def test_builder_exposes_sas_code_generator_button(self) -> None:
        from PySide6.QtWidgets import QPushButton

        from clinical_data_viewer.ui.change_from_baseline_builder import (
            ChangeFromBaselineBuilder,
        )

        builder = ChangeFromBaselineBuilder()
        requests: list[object] = []
        builder.sas_code_requested.connect(lambda selection: requests.append(selection))
        button = next(
            button
            for button in builder.findChildren(QPushButton)
            if button.text() == "SAS Code Generator…"
        )
        self.assertFalse(button.isEnabled())
        builder.deleteLater()

    def test_builder_scrollbars_and_action_buttons(self) -> None:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QPushButton, QScrollArea

        from clinical_data_viewer.domain import DatasetMetadata, VariableMetadata
        from clinical_data_viewer.ui.change_from_baseline_builder import (
            ChangeFromBaselineBuilder,
        )

        builder = ChangeFromBaselineBuilder()
        metadata = DatasetMetadata(
            "ADLB",
            1,
            (
                VariableMetadata("USUBJID"),
                VariableMetadata("CHG", kind="numeric"),
                VariableMetadata("PARAMCD"),
            ),
        )
        builder.set_dataset(metadata, r"C:\data\adlb.sas7bdat")
        scroll = builder.findChild(QScrollArea)
        self.assertIsNotNone(scroll)
        assert scroll is not None
        self.assertEqual(scroll.verticalScrollBarPolicy(), Qt.ScrollBarAsNeeded)
        self.assertEqual(scroll.horizontalScrollBarPolicy(), Qt.ScrollBarAsNeeded)

        events: list[str] = []
        builder.shell_preview_requested.connect(lambda: events.append("preview"))
        builder.sas_code_requested.connect(lambda _selection: events.append("sas"))
        builder.run_requested.connect(lambda _selection: events.append("run"))
        builder.cleared.connect(lambda: events.append("clear"))
        buttons = {
            button.text(): button for button in builder.findChildren(QPushButton)
        }
        self.assertTrue(buttons["SAS Code Generator…"].isEnabled())
        buttons["Shell Preview…"].click()
        buttons["SAS Code Generator…"].click()
        buttons["Run Change from Baseline"].click()
        buttons["Clear"].click()
        self.assertEqual(events, ["preview", "sas", "run", "clear"])
        builder.deleteLater()

    def test_shell_preview_dialog_loads_packaged_png(self) -> None:
        from clinical_data_viewer.ui.shell_preview_dialog import ShellPreviewDialog

        dialog = ShellPreviewDialog()
        self.assertFalse(dialog._pixmap.isNull())
        self.assertEqual(dialog.windowTitle(), "Change from Baseline Shell Preview")
        dialog.deleteLater()

    def test_builder_exposes_global_statistic_decimal_settings(self) -> None:
        from clinical_data_viewer.ui.change_from_baseline_builder import (
            ChangeFromBaselineBuilder,
        )

        builder = ChangeFromBaselineBuilder()
        self.assertEqual(
            set(builder.statistic_decimals),
            {"mean", "std", "median", "q1", "q3", "min", "max"},
        )
        builder.statistic_decimals["mean"].setCurrentIndex(4)
        selected = dict(builder.selection().statistic_decimals)
        self.assertEqual(selected["mean"], 4)
        builder.clear()
        self.assertEqual(builder.statistic_decimals["mean"].currentData(), 1)
        builder.deleteLater()


if __name__ == "__main__":
    unittest.main()
