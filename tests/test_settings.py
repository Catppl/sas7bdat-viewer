from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from clinical_data_viewer.settings import (
    DEFAULT_FOLDER_PANEL_WIDTH,
    DEFAULT_VARIABLES_PANEL_WIDTH,
    AppSettings,
)


class SettingsTests(unittest.TestCase):
    def test_navigation_panel_defaults_are_compact(self) -> None:
        settings = AppSettings()
        self.assertEqual(settings.folder_panel_width, DEFAULT_FOLDER_PANEL_WIDTH)
        self.assertEqual(settings.variables_panel_width, DEFAULT_VARIABLES_PANEL_WIDTH)
        self.assertEqual(settings.folder_panel_width, 175)
        self.assertEqual(settings.variables_panel_width, 175)

    def test_settings_round_trip_and_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            value = AppSettings(
                page_size=700,
                last_open_directory="C:/clinical",
                last_workspace_directory="C:/clinical/adam",
                folder_panel_visible=True,
                folder_panel_width=320,
                variables_panel_width=340,
                apply_sas_date_time_formats=True,
                proc_means_decimals=4,
                proc_means_decimal_places={"mean": 2, "q1": 1, "max": 12},
                proc_means_decimal_offsets={"mean": 1, "q1": 3, "max": 9},
                proc_means_confidence=0.9,
                proc_means_statistics=["subjects", "mean", "q1"],
            )
            value.save(path)
            restored = AppSettings.load(path)
            self.assertEqual(restored.page_size, 700)
            self.assertEqual(restored.last_open_directory, "C:/clinical")
            self.assertEqual(restored.last_workspace_directory, "C:/clinical/adam")
            self.assertTrue(restored.folder_panel_visible)
            self.assertEqual(restored.folder_panel_width, 320)
            self.assertEqual(restored.variables_panel_width, 340)
            self.assertTrue(restored.apply_sas_date_time_formats)
            self.assertEqual(restored.proc_means_decimals, 4)
            self.assertEqual(restored.proc_means_decimal_places["mean"], 2)
            self.assertEqual(restored.proc_means_decimal_places["q1"], 1)
            self.assertEqual(restored.proc_means_decimal_places["max"], 10)
            self.assertEqual(restored.proc_means_decimal_places["std"], 4)
            self.assertEqual(restored.proc_means_decimal_offsets["mean"], 1)
            self.assertEqual(restored.proc_means_decimal_offsets["q1"], 3)
            self.assertEqual(restored.proc_means_decimal_offsets["max"], 4)
            self.assertEqual(restored.proc_means_confidence, 0.9)
            self.assertEqual(restored.proc_means_statistics, ["subjects", "mean", "q1"])

    def test_old_global_decimal_setting_migrates_to_each_statistic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"proc_means_decimals": 3}), encoding="utf-8")
            restored = AppSettings.load(path)
            self.assertTrue(restored.proc_means_decimal_places)
            self.assertEqual(set(restored.proc_means_decimal_places.values()), {3})

    def test_workspace_favorites_are_not_stored_in_settings_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            AppSettings(last_workspace_directory="C:/clinical/adam").save(path)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["last_workspace_directory"], "C:/clinical/adam")
            self.assertNotIn("favorite_workspaces", saved)


if __name__ == "__main__":
    unittest.main()
