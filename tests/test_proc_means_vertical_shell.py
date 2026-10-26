from __future__ import annotations

import unittest
from importlib.resources import files
from pathlib import Path


class ProcMeansVerticalShellAssetTests(unittest.TestCase):
    def test_packaged_docx_matches_the_maintained_source(self) -> None:
        root = Path(__file__).parents[1]
        packaged = (
            files("clinical_data_viewer.assets.shells")
            .joinpath("proc_means_vertical.docx")
            .read_bytes()
        )
        source = (root / "docs" / "proc-means-vertical-shell.docx").read_bytes()

        self.assertEqual(packaged, source)


if __name__ == "__main__":
    unittest.main()
