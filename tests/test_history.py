from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from clinical_data_viewer.filter_history import FilterHistory


class FilterHistoryTests(unittest.TestCase):
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
