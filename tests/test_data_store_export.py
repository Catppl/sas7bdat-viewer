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
                '"ARMCD" TEXT, "ADT" REAL, "ADTM" REAL, "ATM" REAL)'
            )
            connection.executemany(
                'INSERT INTO dataset("USUBJID", "AESER", "AGE", "AESTDTC", '
                '"AEENDTC", "ARMCD", "ADT", "ADTM", "ATM") '
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        "101-001",
                        "Y",
                        45,
                        "2024-01-01",
                        "2024-01-02",
                        "PKO1",
                        24_345,
                        24_345 * 86_400 + 45_296,
                        45_296,
                    ),
                    (
                        "101-002",
                        "N",
                        51,
                        "2024-02-02",
                        "2024-02-01",
                        "pko",
                        24_346,
                        24_346 * 86_400 + 45_297,
                        45_297,
                    ),
                    (
                        "101-003",
                        "Y",
                        30,
                        "2024-03-01",
                        "2024-03-01",
                        "PK2",
                        None,
                        None,
                        None,
                    ),
                    (
                        "101-004",
                        "Y",
                        60,
                        "2024-04-01",
                        "2024-04-03",
                        "XX",
                        24_349,
                        24_349 * 86_400,
                        0,
                    ),
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
                VariableMetadata("ADT", kind="numeric", format="YYMMDD10."),
                VariableMetadata("ADTM", kind="numeric", format="DATETIME20."),
                VariableMetadata("ATM", kind="numeric", format="TIME8."),
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

    def test_export_includes_manual_highlight_marker_when_present(self) -> None:
        source = self.root / "adae.sas7bdat"
        source.write_bytes(b"fixture")
        handle = DatasetHandle(source, source, self.database, self.metadata)
        destination = self.root / "highlighted.csv"
        exported = CsvExporter().export(
            handle,
            destination,
            ["USUBJID", "AGE"],
            self.compiled,
            SortSpec("AGE", ascending=False),
            highlight_rows={4: "Light Purple"},
        )
        self.assertEqual(exported, 2)
        with destination.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(
            rows,
            [
                ["USUBJID", "AGE", "HIGHLIGHT"],
                ["101-004", "60.0", "Light Purple"],
                ["101-001", "45.0", ""],
            ],
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

    def test_temporal_literals_and_functions_execute_against_raw_sqlite_values(
        self,
    ) -> None:
        date = FilterEngine(self.metadata.variables).compile("ADT = '2026-08-27'd")
        result = DataStore().query_page(
            self.database, self.metadata, ["USUBJID"], date, None, 0, 500
        )
        self.assertEqual(result.rows, (("101-001",),))

        date_list = FilterEngine(self.metadata.variables).compile(
            "ADT IN ('27AUG2026'd, '28AUG2026'd)"
        )
        result = DataStore().query_page(
            self.database, self.metadata, ["USUBJID"], date_list, None, 0, 500
        )
        self.assertEqual(result.rows, (("101-001",), ("101-002",)))

        nested = FilterEngine(self.metadata.variables).compile(
            'FIND(LOWCASE(ARMCD), "pko") AND UPCASE(AESER) = "Y"'
        )
        result = DataStore().query_page(
            self.database, self.metadata, ["USUBJID"], nested, None, 0, 500
        )
        self.assertEqual(result.rows, (("101-001",),))

        exact_position = FilterEngine(self.metadata.variables).compile(
            'INDEX(ARMCD, "PK") = 1'
        )
        result = DataStore().query_page(
            self.database,
            self.metadata,
            ["USUBJID"],
            exact_position,
            None,
            0,
            500,
        )
        self.assertEqual(result.rows, (("101-001",), ("101-003",)))

    def test_csv_export_keeps_raw_numeric_temporal_values(self) -> None:
        source = self.root / "adae.sas7bdat"
        source.write_bytes(b"fixture")
        handle = DatasetHandle(source, source, self.database, self.metadata)
        destination = self.root / "raw-temporal.csv"
        compiled = FilterEngine(self.metadata.variables).compile("ADT = '2026-08-27'd")
        CsvExporter().export(handle, destination, ["USUBJID", "ADT"], compiled, None)
        with destination.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(rows, [["USUBJID", "ADT"], ["101-001", "24345.0"]])


if __name__ == "__main__":
    unittest.main()
