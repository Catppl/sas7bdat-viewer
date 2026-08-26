from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from clinical_data_viewer.csv_exporter import CsvExporter
from clinical_data_viewer.data_store import DataStore
from clinical_data_viewer.domain import (
    DatasetHandle,
    DatasetMetadata,
    SortSpec,
    VariableMetadata,
)
from clinical_data_viewer.filter_engine import FilterEngine


class DataStoreExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "dataset.sqlite"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                'CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "USUBJID" TEXT, '
                '"AESER" TEXT, "AGE" REAL, "AESTDTC" TEXT, "AEENDTC" TEXT, '
                '"ARMCD" TEXT)'
            )
            connection.executemany(
                'INSERT INTO dataset("USUBJID", "AESER", "AGE", "AESTDTC", '
                '"AEENDTC", "ARMCD") VALUES (?, ?, ?, ?, ?, ?)',
                [
                    ("101-001", "Y", 45, "2024-01-01", "2024-01-02", "PKO1"),
                    ("101-002", "N", 51, "2024-02-02", "2024-02-01", "pko"),
                    ("101-003", "Y", 30, "2024-03-01", "2024-03-01", "PK2"),
                    ("101-004", "Y", 60, "2024-04-01", "2024-04-03", "XX"),
                ],
            )
            connection.commit()
        self.metadata = DatasetMetadata(
            "adae",
            4,
            (
                VariableMetadata("USUBJID", kind="character"),
                VariableMetadata("AESER", kind="character"),
                VariableMetadata("AGE", kind="numeric"),
                VariableMetadata("AESTDTC", kind="character"),
                VariableMetadata("AEENDTC", kind="character"),
                VariableMetadata("ARMCD", kind="character"),
            ),
        )
        self.compiled = FilterEngine(self.metadata.variables).compile(
            'AESER = "Y" AND AGE >= 40'
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_page_is_filtered_sorted_and_column_bounded(self) -> None:
        result = DataStore().query_page(
            self.database,
            self.metadata,
            ["USUBJID", "AGE"],
            self.compiled,
            SortSpec("AGE", ascending=False),
            0,
            500,
        )
        self.assertEqual(result.filtered_count, 2)
        self.assertEqual(result.rows, (("101-004", 60.0), ("101-001", 45.0)))
        self.assertEqual(result.source_rows, (4, 1))

    def test_export_contains_only_current_filter_columns_and_sort(self) -> None:
        source = self.root / "adae.sas7bdat"
        source.write_bytes(b"fixture")
        handle = DatasetHandle(source, source, self.database, self.metadata)
        destination = self.root / "current.csv"
        exported = CsvExporter().export(
            handle,
            destination,
            ["USUBJID", "AGE"],
            self.compiled,
            SortSpec("AGE", ascending=False),
        )
        self.assertEqual(exported, 2)
        self.assertTrue(destination.read_bytes().startswith(b"\xef\xbb\xbf"))
        with destination.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(
            rows, [["USUBJID", "AGE"], ["101-004", "60.0"], ["101-001", "45.0"]]
        )

    def test_find_uses_current_filter_sort_and_visible_columns(self) -> None:
        result = DataStore().find_text(
            self.database,
            self.metadata,
            ["USUBJID", "AGE"],
            self.compiled,
            SortSpec("AGE", ascending=False),
            "101-001",
            -1,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.row_index, 1)
        self.assertEqual(result.column_name, "USUBJID")

    def test_column_comparison_and_contains_are_executed_case_sensitively(self) -> None:
        compiled = FilterEngine(self.metadata.variables).compile(
            'AESTDTC > AEENDTC & ARMCD ? "pko"'
        )
        result = DataStore().query_page(
            self.database,
            self.metadata,
            ["USUBJID"],
            compiled,
            None,
            0,
            500,
        )
        self.assertEqual(result.rows, (("101-002",),))

        upper_case = FilterEngine(self.metadata.variables).compile('ARMCD LIKE "PK%"')
        result = DataStore().query_page(
            self.database,
            self.metadata,
            ["USUBJID"],
            upper_case,
            None,
            0,
            500,
        )
        self.assertEqual(result.rows, (("101-001",), ("101-003",)))


if __name__ == "__main__":
    unittest.main()
