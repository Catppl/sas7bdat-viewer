from __future__ import annotations

import unittest
from pathlib import Path


class PackagingConfigurationTests(unittest.TestCase):
    def test_pyinstaller_uses_onedir_collection(self) -> None:
        root = Path(__file__).resolve().parents[1]
        spec = (root / "SASDataViewer.spec").read_text(encoding="utf-8")
        self.assertIn("exclude_binaries=True", spec)
        self.assertIn("bundle = COLLECT(", spec)
        self.assertIn('name="SASDataViewer"', spec)
        self.assertIn("clinical_data_viewer/codegen/sas/templates", spec)
        self.assertIn("clinical_data_viewer/codegen/r/templates", spec)

    def test_windows_build_script_creates_release_zip(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "scripts" / "build_windows.ps1").read_text(encoding="utf-8")
        self.assertIn("dist\\SASDataViewer\\SASDataViewer.exe", script)
        self.assertIn("SASDataViewer-Windows-x64.zip", script)
        self.assertIn("Compress-Archive", script)
        self.assertIn("ZIP SHA256", script)


if __name__ == "__main__":
    unittest.main()
