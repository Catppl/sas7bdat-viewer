from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from clinical_data_viewer.filter_history import FilterHistory, format_history_timestamp


class FilterHistoryTests(unittest.TestCase):
    def test_timestamp_display_uses_local_timezone_and_handles_legacy_values(
        self,
    ) -> None:
        shanghai = ZoneInfo("Asia/Shanghai")
        self.assertEqual(
            format_history_timestamp("2026-08-21T00:00:00+00:00", shanghai),
            "2026-08-21 08:00:00",
        )
        tooltip = format_history_timestamp(
            "2026-08-21T00:00:00Z", shanghai, include_timezone=True
        )
        self.assertTrue(tooltip.startswith("2026-08-21 08:00:00"))
        self.assertIn("UTC+08:00", tooltip)
        self.assertEqual(
            format_history_timestamp("2026-08-21T00:00:00", shanghai),
            "2026-08-21 08:00:00",
        )
        self.assertEqual(format_history_timestamp("not-a-time", shanghai), "not-a-time")

    def test_timestamp_display_observes_daylight_saving_time(self) -> None:
        new_york = ZoneInfo("America/New_York")
        summer = format_history_timestamp(
            "2026-07-01T12:00:00+00:00", new_york, include_timezone=True
        )
        winter = format_history_timestamp(
            "2026-01-01T12:00:00+00:00", new_york, include_timezone=True
        )
        self.assertTrue(summer.startswith("2026-07-01 08:00:00"))
        self.assertIn("UTC-04:00", summer)
        self.assertTrue(winter.startswith("2026-01-01 07:00:00"))
        self.assertIn("UTC-05:00", winter)

    def test_history_persists_and_skips_consecutive_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "history.sqlite"
            dataset = root / "adae.sas7bdat"
            history = FilterHistory(database)
            first = history.add(
                dataset, 'AESER = "Y"', datetime(2026, 8, 21, tzinfo=UTC)
            )
            duplicate = history.add(dataset, '  AESER = "Y"  ')
            self.assertIsNotNone(first)
            self.assertIsNone(duplicate)

            restored = FilterHistory(database)
            entries = restored.list(dataset)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].where_text, 'AESER = "Y"')
            self.assertEqual(entries[0].dataset_name, "adae.sas7bdat")

    def test_current_global_delete_and_clear(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            history = FilterHistory(root / "history.sqlite")
            first_path = root / "adae.sas7bdat"
            second_path = root / "adsl.sas7bdat"
            first_id = history.add(first_path, "AGE >= 18")
            history.add(second_path, 'SEX = "F"')
            self.assertEqual(len(history.list()), 2)
            self.assertEqual(len(history.list(first_path)), 1)
            history.delete(first_id or -1)
            self.assertEqual(history.list(first_path), [])
            history.clear()
            self.assertEqual(history.list(), [])


if __name__ == "__main__":
    unittest.main()
