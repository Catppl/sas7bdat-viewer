from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pyreadstat

from clinical_data_viewer.data_store import DataStore
from clinical_data_viewer.domain import VariableMetadata
from clinical_data_viewer.filter_engine import FilterEngine
from clinical_data_viewer.sas_reader import SasDatasetReader
from clinical_data_viewer.sas_value_formatter import format_sas_value
from clinical_data_viewer.temp_manager import TempManager
from clinical_data_viewer.xpt_reader import XptSequentialReader


class FakePyreadstat:
    def __init__(self, expected_source: Path) -> None:
        self.expected_source = expected_source.resolve()
        self.read_paths: list[Path] = []
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.chunk_calls: list[dict[str, object]] = []

    def read_sas7bdat(self, dataset, **kwargs):
        self.calls.append(("sas7bdat", kwargs))
        self.read_paths.append(Path(dataset).resolve())
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
            return {"USUBJID": ["101-001", "101-002"], "AGE": [45, 52]}, None
        raise AssertionError("Unexpected direct data read")

    def read_file_in_chunks(self, reader, dataset, **kwargs):
        self.chunk_calls.append(kwargs)
        self.read_paths.append(Path(dataset).resolve())
        subjects = ["101-001", "101-002", "101-003"]
        ages = [45, 52, 30]
        offset = kwargs.get("offset", 0)
        chunk_size = kwargs.get("chunksize", len(subjects))
        for start in range(offset, len(subjects), chunk_size):
            end = start + chunk_size
            yield {"USUBJID": subjects[start:end], "AGE": ages[start:end]}, None


class TrackingXptReader:
    """Small sequential-reader fake that records every requested chunk size."""

    def __init__(self, _path: Path, rows: list[tuple[str, float | None]]) -> None:
        self._frame = pd.DataFrame(rows, columns=["USUBJID", "AGE"])
        self._position = 0
        self.requests: list[int] = []
        self.variables = (
            VariableMetadata("USUBJID", "Subject", "character", 20),
            VariableMetadata("AGE", "Age", "numeric", 8),
        )
        self.total_rows: int | None = None

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(variable.name for variable in self.variables)

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def read_chunk(self, count: int):
        self.requests.append(count)
        if self._position >= len(self._frame):
            return None
        frame = self._frame.iloc[self._position : self._position + count].copy()
        self._position += len(frame)
        return frame


def write_xpt(path: Path, rows: int, *, version: int = 5) -> None:
    frame = pd.DataFrame(
        {
            "USUBJID": [f"S{index:04d}" for index in range(1, rows + 1)],
            "AGE": [float(index) for index in range(1, rows + 1)],
            "TERM": ["HEADACHE" if index % 2 else "NAUSEA" for index in range(rows)],
        }
    )
    if rows > 2:
        frame.loc[1, "AGE"] = float("nan")
        frame.loc[2, "TERM"] = None
    pyreadstat.write_xport(
        frame,
        path,
        file_format_version=version,
        column_labels={"USUBJID": "Subject", "AGE": "Age", "TERM": "Term"},
    )


class SasReaderTests(unittest.TestCase):
    def test_sas7bdat_metadata_preserves_original_variable_format(self) -> None:
        class MetadataPyreadstat:
            @staticmethod
            def read_sas7bdat(_dataset, **kwargs):
                self.assertTrue(kwargs.get("metadataonly"))
                return {}, SimpleNamespace(
                    column_names=["ADT"],
                    column_labels=["Analysis Date"],
                    readstat_variable_types={"ADT": "double"},
                    variable_storage_width={"ADT": 8},
                    original_variable_types={"ADT": "YYMMDD10."},
                    number_rows=1,
                )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "adsl.sas7bdat"
            source.write_bytes(b"metadata fixture")
            reader = SasDatasetReader(TempManager(root / "temp"))
            with patch(
                "clinical_data_viewer.sas_reader._import_pyreadstat",
                return_value=MetadataPyreadstat(),
            ):
                variables, rows = reader._read_sas_metadata(source)
        self.assertEqual(rows, 1)
        self.assertEqual(variables[0].format, "YYMMDD10.")
        self.assertEqual(format_sas_value(24_345, variables[0]), "2026-08-27")

    def test_sas7bdat_keeps_pyreadstat_and_uses_smaller_initial_chunk(self) -> None:
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
                complete = reader.continue_cache(initial)
            self.assertFalse(initial.cache_complete)
            self.assertEqual(initial.cached_row_count, 2)
            self.assertTrue(complete.cache_complete)
            self.assertEqual(complete.cached_row_count, 3)
            self.assertEqual(fake.calls[1][1]["row_limit"], 2)
            self.assertEqual(fake.chunk_calls[0]["chunksize"], 2)
            self.assertTrue(
                all(
                    path == initial.temporary_path.resolve() for path in fake.read_paths
                )
            )
            manager.cleanup()

    def test_default_chunk_sizes_are_format_specific(self) -> None:
        reader = SasDatasetReader(
            TempManager(Path(tempfile.gettempdir()) / "reader-test")
        )
        self.assertEqual(reader.sas_initial_chunk_size, 5_000)
        self.assertEqual(reader.xpt_initial_chunk_size, 2_500)
        self.assertEqual(reader.cache_chunk_size, 20_000)

    def test_xpt_is_sequential_and_preserves_values_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "adlb.xpt"
            write_xpt(source, 5)
            manager = TempManager(root / "temp")
            with patch(
                "clinical_data_viewer.sas_reader._import_pyreadstat",
                side_effect=AssertionError("XPT must not use pyreadstat chunk reads"),
            ):
                reader = SasDatasetReader(
                    manager, xpt_initial_chunk_size=2, cache_chunk_size=3
                )
                initial = reader.load_initial(source)
                self.assertFalse(initial.cache_complete)
                self.assertEqual(initial.cached_row_count, 2)
                self.assertEqual(initial.metadata.row_count, 5)
                self.assertEqual(initial.metadata.variables[0].label, "Subject")
                self.assertEqual(initial.metadata.variables[1].kind, "numeric")
                source.unlink()
                complete = reader.continue_cache(initial)
            self.assertTrue(complete.cache_complete)
            self.assertEqual(complete.metadata.row_count, 5)
            with sqlite3.connect(complete.database_path) as connection:
                rows = connection.execute(
                    'SELECT "USUBJID", "AGE", "TERM" FROM dataset ORDER BY _source_row'
                ).fetchall()
            self.assertEqual(rows[0], ("S0001", 1.0, "NAUSEA"))
            self.assertIsNone(rows[1][1])
            self.assertEqual(rows[2][2], "")
            self.assertEqual(rows[-1], ("S0005", 5.0, "NAUSEA"))
            manager.cleanup()

    def test_xpt_v8_uses_the_same_sequential_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "adae.xpt"
            write_xpt(source, 3, version=8)
            with XptSequentialReader(source) as reader:
                self.assertEqual(reader.total_rows, 3)
                self.assertEqual(reader.variables[0].name, "USUBJID")
                self.assertEqual(len(reader.read_chunk(2)), 2)
                self.assertEqual(len(reader.read_chunk(2)), 1)
                self.assertIsNone(reader.read_chunk(2))

    def test_xpt_metadata_format_drives_temporal_display(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "adsl.xpt"
            pyreadstat.write_xport(
                pd.DataFrame({"ADT": [24_345.0]}),
                source,
                file_format_version=5,
                variable_format={"ADT": "DATE9."},
            )
            with XptSequentialReader(source) as reader:
                variable = reader.variables[0]
                row = reader.read_chunk(1)
        self.assertEqual(variable.format, "DATE9.")
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(format_sas_value(row.iloc[0]["ADT"], variable), "27AUG2026")

    def test_xpt_preserves_common_format_width_and_decimals(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "formats.xpt"
            frame = pd.DataFrame(
                {
                    "ADT": [24_345.0],
                    "ADTM": [2_102_533_522.0],
                    "ATM": [45_296.0],
                    "AVAL": [8.25],
                }
            )
            pyreadstat.write_xport(
                frame,
                source,
                file_format_version=5,
                variable_format={
                    "ADT": "DATE9.",
                    "ADTM": "DATETIME20.",
                    "ATM": "TIME10.",
                    "AVAL": "8.2",
                },
            )
            with XptSequentialReader(source) as reader:
                formats = {variable.name: variable.format for variable in reader.variables}

        self.assertEqual(
            formats,
            {
                "ADT": "DATE9.",
                "ADTM": "DATETIME20.",
                "ATM": "TIME10.",
                "AVAL": "8.2",
            },
        )

    def test_xpt_unknown_total_uses_bounded_sequential_skip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "unknown.xpt"
            source.write_bytes(b"placeholder")
            readers: list[TrackingXptReader] = []

            def factory(path: Path):
                item = TrackingXptReader(
                    path,
                    [(f"S{number}", float(number)) for number in range(1, 6)],
                )
                readers.append(item)
                return item

            manager = TempManager(root / "temp")
            reader = SasDatasetReader(
                manager,
                xpt_initial_chunk_size=2,
                cache_chunk_size=3,
                xpt_reader_factory=factory,
            )
            initial = reader.load_initial(source)
            self.assertFalse(initial.total_rows_known)
            self.assertFalse(initial.cache_complete)
            complete = reader.continue_cache(initial)
            self.assertTrue(complete.total_rows_known)
            self.assertEqual(complete.cached_row_count, 5)
            self.assertEqual(readers[1].requests, [2, 3, 3])
            manager.cleanup()

    def test_small_xpt_finishes_during_initial_load(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "small.xpt"
            write_xpt(source, 1)
            manager = TempManager(root / "temp")
            handle = SasDatasetReader(manager, xpt_initial_chunk_size=2).load_initial(
                source
            )
            self.assertTrue(handle.cache_complete)
            self.assertEqual(handle.cached_row_count, 1)
            manager.cleanup()

    def test_xpt_exact_initial_boundary_finishes_when_total_is_known(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "boundary.xpt"
            write_xpt(source, 2)
            manager = TempManager(root / "temp")
            handle = SasDatasetReader(manager, xpt_initial_chunk_size=2).load_initial(
                source
            )
            self.assertTrue(handle.total_rows_known)
            self.assertTrue(handle.cache_complete)
            self.assertEqual(handle.cached_row_count, 2)
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
