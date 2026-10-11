from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clinical_data_viewer.app import (
    dataset_paths_from_arguments,
    startup_request_from_arguments,
)


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

    def test_workspace_and_dataset_paths_are_parsed_independently(self) -> None:
        workspace = Path("folder with 空格 #").resolve()
        dataset = Path("explicit.sas7bdat").resolve()
        result = startup_request_from_arguments(
            ["--workspace", str(workspace), str(dataset)]
        )
        self.assertEqual(result.workspace_path, workspace)
        self.assertEqual(result.dataset_paths, (dataset,))

    def test_workspace_equals_syntax_and_quoted_path(self) -> None:
        workspace = Path("quoted workspace").resolve()
        result = startup_request_from_arguments([f'--workspace="{workspace}"'])
        self.assertEqual(result.workspace_path, workspace)

    def test_workspace_requires_one_path(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires a folder path"):
            startup_request_from_arguments(["--workspace"])
        with self.assertRaisesRegex(ValueError, "Only one"):
            startup_request_from_arguments(
                ["--workspace", "one", "--workspace", "two"]
            )


if __name__ == "__main__":
    unittest.main()
