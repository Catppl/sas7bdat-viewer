from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from clinical_data_viewer.settings import AppSettings


class SettingsTests(unittest.TestCase):
    def test_settings_round_trip_and_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            value = AppSettings(
                page_size=700,
                last_open_directory="C:/clinical",
                proc_means_decimals=4,
                proc_means_confidence=0.9,
                proc_means_statistics=["subjects", "mean", "q1"],
            )
            value.save(path)
            restored = AppSettings.load(path)
            self.assertEqual(restored.page_size, 700)
            self.assertEqual(restored.last_open_directory, "C:/clinical")
            self.assertEqual(restored.proc_means_decimals, 4)
            self.assertEqual(restored.proc_means_confidence, 0.9)
            self.assertEqual(restored.proc_means_statistics, ["subjects", "mean", "q1"])


if __name__ == "__main__":
    unittest.main()
