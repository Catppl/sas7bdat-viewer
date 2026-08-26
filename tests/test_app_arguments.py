from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clinical_data_viewer.app import dataset_paths_from_arguments


class AppArgumentTests(unittest.TestCase):
    def test_extracts_dataset_paths_with_spaces_unicode_and_mixed_case(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "clinical data" / "中文 AE.sas7bdat"
            second = root / "ADSL.XPT"
            result = dataset_paths_from_arguments(
                ["--ignored", f'"{first}"', str(second), "notes.txt"]
            )
        self.assertEqual(result, (first.resolve(), second.resolve()))

    def test_deduplicates_the_same_path(self) -> None:
        path = Path("same.xpt").resolve()
        self.assertEqual(dataset_paths_from_arguments([str(path), str(path)]), (path,))


if __name__ == "__main__":
    unittest.main()
