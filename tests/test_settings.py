from __future__ import annotations

import json
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
                proc_means_decimal_places={"mean": 2, "q1": 1, "max": 12},
                proc_means_confidence=0.9,
                proc_means_statistics=["subjects", "mean", "q1"],
            )
            value.save(path)
            restored = AppSettings.load(path)
            self.assertEqual(restored.page_size, 700)
            self.assertEqual(restored.last_open_directory, "C:/clinical")
            self.assertEqual(restored.proc_means_decimals, 4)
            self.assertEqual(restored.proc_means_decimal_places["mean"], 2)
            self.assertEqual(restored.proc_means_decimal_places["q1"], 1)
            self.assertEqual(restored.proc_means_decimal_places["max"], 10)
            self.assertEqual(restored.proc_means_decimal_places["std"], 4)
            self.assertEqual(restored.proc_means_confidence, 0.9)
            self.assertEqual(restored.proc_means_statistics, ["subjects", "mean", "q1"])

    def test_old_global_decimal_setting_migrates_to_each_statistic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"proc_means_decimals": 3}), encoding="utf-8")
            restored = AppSettings.load(path)
            self.assertTrue(restored.proc_means_decimal_places)
            self.assertEqual(set(restored.proc_means_decimal_places.values()), {3})


if __name__ == "__main__":
    unittest.main()
