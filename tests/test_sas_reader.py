from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from clinical_data_viewer.data_store import DataStore
from clinical_data_viewer.filter_engine import FilterEngine
from clinical_data_viewer.sas_reader import SasDatasetReader
from clinical_data_viewer.temp_manager import TempManager


class FakePyreadstat:
    def __init__(self, expected_source: Path) -> None:
        self.expected_source = expected_source.resolve()
        self.read_paths: list[Path] = []

    def read_sas7bdat(self, dataset, **kwargs):
        path = Path(dataset).resolve()
        self.read_paths.append(path)
        if kwargs.get("metadataonly"):
            return {}, SimpleNamespace(
                column_names=["USUBJID", "AGE"],
                column_labels=["Subject", "Age"],
                readstat_variable_types={"USUBJID": "string", "AGE": "double"},
                variable_storage_width={"USUBJID": 20, "AGE": 8},
                original_variable_types={"USUBJID": "$20.", "AGE": "8."},
            )
        raise AssertionError("Unexpected direct data read")

    def read_file_in_chunks(self, reader, dataset, **kwargs):
        path = Path(dataset).resolve()
        self.read_paths.append(path)
        yield {"USUBJID": ["101-001", "101-002"], "AGE": [45, 52]}, None
        yield {"USUBJID": ["101-003"], "AGE": [30]}, None


class SasReaderTests(unittest.TestCase):
    def test_reader_uses_only_temp_copy_after_copy_and_builds_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "adsl.sas7bdat"
            source.write_bytes(b"sas fixture placeholder")
            manager = TempManager(root / "temp")
            fake = FakePyreadstat(source)
            with patch(
                "clinical_data_viewer.sas_reader._import_pyreadstat", return_value=fake
            ):
                handle = SasDatasetReader(manager, chunk_size=2).load(source)
            self.assertTrue(handle.temporary_path.exists())
            self.assertTrue(
                all(path == handle.temporary_path.resolve() for path in fake.read_paths)
            )
            self.assertNotIn(source.resolve(), fake.read_paths)
            self.assertEqual(handle.metadata.row_count, 3)
            self.assertEqual(handle.metadata.variables[0].label, "Subject")

            source.unlink()
            result = DataStore().query_page(
                handle.database_path,
                handle.metadata,
                ["USUBJID"],
                FilterEngine(handle.metadata.variables).compile("AGE >= 40"),
                None,
                0,
                100,
            )
            self.assertEqual(result.filtered_count, 2)
            self.assertEqual(result.rows, (("101-001",), ("101-002",)))
            manager.cleanup()


if __name__ == "__main__":
    unittest.main()
