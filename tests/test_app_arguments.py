from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from clinical_data_viewer.app import (
    StartupRequest,
    apply_startup_navigation_mode,
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

    def test_startup_navigation_mode_uses_workspace_precedence(self) -> None:
        window = Mock()
        request = StartupRequest(Path("workspace"), (Path("adsl.sas7bdat"),))
        apply_startup_navigation_mode(window, request)
        window.set_startup_navigation_panels.assert_called_once_with(
            folder_visible=True,
            variables_visible=True,
        )

    def test_startup_navigation_mode_hides_folder_for_dataset_launch(self) -> None:
        window = Mock()
        request = StartupRequest(None, (Path("adsl.sas7bdat"),))
        apply_startup_navigation_mode(window, request)
        window.set_startup_navigation_panels.assert_called_once_with(
            folder_visible=False,
            variables_visible=True,
        )

    def test_startup_navigation_mode_preserves_settings_without_arguments(self) -> None:
        window = Mock()
        apply_startup_navigation_mode(window, StartupRequest(None, ()))
        window.set_startup_navigation_panels.assert_not_called()


if __name__ == "__main__":
    unittest.main()
