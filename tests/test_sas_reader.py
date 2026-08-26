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
        self.calls: list[tuple[str, dict[str, object]]] = []

    def read_sas7bdat(self, dataset, **kwargs):
        return self._read("sas7bdat", dataset, **kwargs)

    def read_xport(self, dataset, **kwargs):
        return self._read("xpt", dataset, **kwargs)

    def _read(self, kind, dataset, **kwargs):
        self.calls.append((kind, kwargs))
        path = Path(dataset).resolve()
        self.read_paths.append(path)
        if kwargs.get("metadataonly"):
            return {}, SimpleNamespace(
                column_names=["USUBJID", "AGE"],
                column_labels=["Subject", "Age"],
                readstat_variable_types={"USUBJID": "string", "AGE": "double"},
                variable_storage_width={"USUBJID": 20, "AGE": 8},
                original_variable_types={"USUBJID": "$20.", "AGE": "8."},
                number_rows=3,
            )
        if kwargs.get("row_limit"):
            return {
                "USUBJID": ["101-001", "101-002"],
                "AGE": [45, 52],
            }, None
        raise AssertionError("Unexpected direct data read")

    def read_file_in_chunks(self, reader, dataset, **kwargs):
        path = Path(dataset).resolve()
        self.read_paths.append(path)
        subjects = ["101-001", "101-002", "101-003"]
        ages = [45, 52, 30]
        offset = kwargs.get("offset", 0)
        chunk_size = kwargs.get("chunksize", len(subjects))
        for start in range(offset, len(subjects), chunk_size):
            end = start + chunk_size
            yield {"USUBJID": subjects[start:end], "AGE": ages[start:end]}, None


class SasReaderTests(unittest.TestCase):
    def test_initial_load_exposes_first_chunk_before_background_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "adsl.sas7bdat"
            source.write_bytes(b"sas fixture placeholder")
            manager = TempManager(root / "temp")
            fake = FakePyreadstat(source)
            with patch(
                "clinical_data_viewer.sas_reader._import_pyreadstat", return_value=fake
            ):
                reader = SasDatasetReader(manager, chunk_size=2)
                initial = reader.load_initial(source)
                self.assertFalse(initial.cache_complete)
                self.assertEqual(initial.cached_row_count, 2)
                complete = reader.continue_cache(initial)
            self.assertTrue(complete.cache_complete)
            self.assertEqual(complete.cached_row_count, 3)
            self.assertEqual(complete.metadata.row_count, 3)
            manager.cleanup()

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

    def test_xpt_uses_xport_reader_without_sas_only_options(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "adsl.xpt"
            source.write_bytes(b"xpt fixture placeholder")
            manager = TempManager(root / "temp")
            fake = FakePyreadstat(source)
            with patch(
                "clinical_data_viewer.sas_reader._import_pyreadstat", return_value=fake
            ):
                handle = SasDatasetReader(manager, chunk_size=2).load(source)
            self.assertTrue(handle.cache_complete)
            self.assertTrue(all(kind == "xpt" for kind, _kwargs in fake.calls))
            self.assertTrue(
                all("user_missing" not in kwargs for _kind, kwargs in fake.calls)
            )
            self.assertTrue(
                all(path == handle.temporary_path.resolve() for path in fake.read_paths)
            )
            manager.cleanup()


if __name__ == "__main__":
    unittest.main()
