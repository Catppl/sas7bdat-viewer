from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

from clinical_data_viewer.temp_manager import TempManager


class TempManagerTests(unittest.TestCase):
    def test_copy_is_independent_and_source_can_be_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "adae.sas7bdat"
            source.write_bytes(b"original SAS bytes")
            manager = TempManager(root / "cache")
            copied, dataset_directory = manager.copy_dataset(source)
            source.unlink()
            source.write_bytes(b"new generation")
            self.assertEqual(copied.read_bytes(), b"original SAS bytes")
            self.assertEqual(source.read_bytes(), b"new generation")
            manager.remove_dataset(dataset_directory)
            self.assertFalse(dataset_directory.exists())
            manager.cleanup()

    def test_startup_cleanup_removes_only_old_session_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = TempManager(root)
            old = root / "cde-old-crash"
            recent = root / "cde-recent-other-instance"
            unrelated = root / "keep-me"
            old.mkdir()
            recent.mkdir()
            unrelated.mkdir()
            now = time.time()
            os.utime(old, (now - 10_000, now - 10_000))
            removed = manager.cleanup_stale(3_600, now=now)
            self.assertEqual(removed, [old.resolve()])
            self.assertFalse(old.exists())
            self.assertTrue(recent.exists())
            self.assertTrue(unrelated.exists())
            manager.cleanup()

    def test_refuses_to_remove_outside_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = TempManager(root / "cache")
            with self.assertRaises(ValueError):
                manager.remove_dataset(root)
            manager.cleanup()


if __name__ == "__main__":
    unittest.main()
