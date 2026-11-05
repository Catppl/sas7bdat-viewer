from __future__ import annotations

import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "p21_issue_explorer"


class ArchitectureTests(unittest.TestCase):
    def test_matching_and_snapshot_core_do_not_import_qt_or_viewer(self) -> None:
        for directory in ("matching", "integration", "models"):
            for path in (PACKAGE_ROOT / directory).glob("*.py"):
                source = path.read_text(encoding="utf-8")
                with self.subTest(path=path.name):
                    self.assertNotIn("PySide6", source)
                    self.assertNotIn("clinical_data_viewer", source)


if __name__ == "__main__":
    unittest.main()
