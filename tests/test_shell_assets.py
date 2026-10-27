from __future__ import annotations

import unittest
from importlib.resources import files
from pathlib import Path

SHELL_ASSETS = (
    (
        "change-from-baseline-horizontal-shell.docx",
        "change_from_baseline_horizontal.docx",
        "change_from_baseline_horizontal.png",
    ),
    (
        "proc-means-vertical-shell.docx",
        "proc_means_vertical.docx",
        "proc_means_vertical.png",
    ),
    (
        "proc-means-rows-shell.docx",
        "proc_means_rows.docx",
        "proc_means_rows.png",
    ),
    (
        "baseline-characteristics-shell.docx",
        "baseline_characteristics.docx",
        "baseline_characteristics.png",
    ),
    (
        "ae-soc-pt-shell.docx",
        "ae_soc_pt.docx",
        "ae_soc_pt.png",
    ),
    (
        "categorical-adsl-shell.docx",
        "categorical_adsl.docx",
        "categorical_adsl.png",
    ),
)


class ShellAssetInventoryTests(unittest.TestCase):
    def test_every_maintained_shell_has_packaged_docx_and_png(self) -> None:
        root = Path(__file__).parents[1]
        packaged = files("clinical_data_viewer.assets.shells")

        for source_name, packaged_docx_name, preview_name in SHELL_ASSETS:
            with self.subTest(shell=source_name):
                source = (root / "docs" / source_name).read_bytes()
                packaged_docx = packaged.joinpath(packaged_docx_name).read_bytes()
                preview = packaged.joinpath(preview_name).read_bytes()

                self.assertEqual(packaged_docx, source)
                self.assertTrue(preview.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_baseline_characteristics_preview_loader_reads_packaged_png(self) -> None:
        from clinical_data_viewer.shell_assets import (
            load_baseline_characteristics_shell_preview,
        )

        self.assertTrue(
            load_baseline_characteristics_shell_preview().startswith(
                b"\x89PNG\r\n\x1a\n"
            )
        )


if __name__ == "__main__":
    unittest.main()
