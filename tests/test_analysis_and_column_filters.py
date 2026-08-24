from __future__ import annotations

import csv
import math
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from clinical_data_viewer.column_filters import (
    ColumnFilterSpec,
    combine_filters,
    compose_where_text,
    render_column_filter,
)
from clinical_data_viewer.csv_exporter import CsvExporter
from clinical_data_viewer.data_store import DataStore
from clinical_data_viewer.domain import (
    DatasetHandle,
    DatasetMetadata,
    SortSpec,
    VariableMetadata,
)
from clinical_data_viewer.filter_engine import FilterEngine
from clinical_data_viewer.statistics import calculate_statistics, qntldef5


class AnalysisAndColumnFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "dataset.sqlite"
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute(
                'CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "USUBJID" TEXT, '
                '"ARMCD" TEXT, "AVAL" REAL, "FLAG" TEXT)'
            )
            connection.executemany(
                'INSERT INTO dataset("USUBJID", "ARMCD", "AVAL", "FLAG") '
                "VALUES (?, ?, ?, ?)",
                [
                    ("101", "A", 1, "Y"),
                    ("101", "B", 2, "Y"),
                    ("102", "A", 3, "N"),
                    (None, "C", 4, "Y"),
                    ("103", None, None, "Y"),
                ],
            )
            connection.commit()
        self.metadata = DatasetMetadata(
            "adsl",
            5,
            (
                VariableMetadata("USUBJID", kind="character"),
                VariableMetadata("ARMCD", kind="character"),
                VariableMetadata("AVAL", kind="numeric"),
                VariableMetadata("FLAG", kind="character"),
            ),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_column_filter_combines_with_where_and_handles_missing(self) -> None:
        where = FilterEngine(self.metadata.variables).compile('FLAG = "Y"')
        combined = combine_filters(
            where,
            {
                "ARMCD": ColumnFilterSpec(
                    "ARMCD", "include", ("A",), include_missing=True
                )
            },
            self.metadata.variables,
        )
        result = DataStore().query_page(
            self.database,
            self.metadata,
            ["USUBJID", "ARMCD"],
            combined,
            None,
            0,
            50,
        )
        self.assertEqual(result.rows, (("101", "A"), ("103", None)))

    def test_exclusion_filter_keeps_unlisted_and_missing_values(self) -> None:
        combined = combine_filters(
            FilterEngine(self.metadata.variables).compile(""),
            {
                "ARMCD": ColumnFilterSpec(
                    "ARMCD", "exclude", ("B",), include_missing=True
                )
            },
            self.metadata.variables,
        )
        result = DataStore().query_page(
            self.database,
            self.metadata,
            ["ARMCD"],
            combined,
            None,
            0,
            50,
        )
        self.assertEqual(result.rows, (("A",), ("A",), ("C",), (None,)))

    def test_column_filters_render_as_equivalent_sas_like_where(self) -> None:
        manual_text = 'FLAG = "Y"'
        filters = {
            "AVAL": ColumnFilterSpec(
                "AVAL", "include", (1.0, 4.0), include_missing=False
            ),
            "ARMCD": ColumnFilterSpec("ARMCD", "exclude", ("B",), include_missing=True),
        }
        text = compose_where_text(manual_text, filters, self.metadata.variables)
        self.assertEqual(
            text,
            '(FLAG = "Y") AND (AVAL IN (1, 4)) AND '
            '((ARMCD NOT IN ("B") OR MISSING(ARMCD)))',
        )
        rendered = FilterEngine(self.metadata.variables).compile(text)
        combined = combine_filters(
            FilterEngine(self.metadata.variables).compile(manual_text),
            filters,
            self.metadata.variables,
        )
        rendered_rows = (
            DataStore()
            .query_page(
                self.database,
                self.metadata,
                ["USUBJID", "ARMCD", "AVAL"],
                rendered,
                None,
                0,
                50,
            )
            .rows
        )
        combined_rows = (
            DataStore()
            .query_page(
                self.database,
                self.metadata,
                ["USUBJID", "ARMCD", "AVAL"],
                combined,
                None,
                0,
                50,
            )
            .rows
        )
        self.assertEqual(rendered_rows, combined_rows)

    def test_renderer_quotes_character_values_and_supports_conditions(self) -> None:
        variable = VariableMetadata("ARMCD", kind="character")
        self.assertEqual(
            render_column_filter(
                ColumnFilterSpec("ARMCD", "include", ('A"B',), False), variable
            ),
            'ARMCD IN ("A""B")',
        )
        self.assertEqual(
            render_column_filter(
                ColumnFilterSpec("ARMCD", "contains", lower="PKO"), variable
            ),
            'ARMCD CONTAINS "PKO"',
        )

    def test_export_uses_combined_filter_visible_columns_and_sort(self) -> None:
        combined = combine_filters(
            FilterEngine(self.metadata.variables).compile('FLAG = "Y"'),
            {
                "ARMCD": ColumnFilterSpec(
                    "ARMCD", "include", ("A", "C"), include_missing=False
                )
            },
            self.metadata.variables,
        )
        source = Path(self.temporary.name) / "adsl.sas7bdat"
        source.touch()
        destination = Path(self.temporary.name) / "filtered.csv"
        handle = DatasetHandle(source, source, self.database, self.metadata)
        count = CsvExporter().export(
            handle,
            destination,
            ["USUBJID", "AVAL"],
            combined,
            SortSpec("AVAL", ascending=False),
        )
        with destination.open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.reader(stream))
        self.assertEqual(count, 2)
        self.assertEqual(rows, [["USUBJID", "AVAL"], ["", "4.0"], ["101", "1.0"]])

    def test_distinct_values_respect_other_filters(self) -> None:
        where = FilterEngine(self.metadata.variables).compile('FLAG = "Y"')
        result = DataStore().distinct_values(
            self.database, self.metadata, "ARMCD", where, limit=2
        )
        self.assertEqual(result.values, ("A", "B"))
        self.assertTrue(result.has_missing)
        self.assertEqual(result.total_distinct, 3)
        self.assertTrue(result.truncated)

    def test_proc_means_uses_filtered_rows_and_fixed_usubjid(self) -> None:
        where = FilterEngine(self.metadata.variables).compile('FLAG = "Y"')
        result = calculate_statistics(
            self.database, self.metadata, "AVAL", where, confidence=0.95
        )
        self.assertEqual(result.filtered_rows, 4)
        self.assertEqual(result.values["subjects"], 1)
        self.assertEqual(result.values["n"], 3)
        self.assertEqual(result.values["nmiss"], 1)
        self.assertEqual(result.values["mean"], 7 / 3)
        self.assertTrue(math.isclose(result.values["median"], 2.0))
        self.assertIsNotNone(result.values["lclm"])
        self.assertIsNotNone(result.values["uclm"])

    def test_qntldef5_averages_at_empirical_boundary(self) -> None:
        values = [1.0, 2.0, 3.0, 4.0]
        self.assertEqual(qntldef5(values, 0.25), 1.5)
        self.assertEqual(qntldef5(values, 0.5), 2.5)
        self.assertEqual(qntldef5(values, 0.75), 3.5)

    def test_compare_nonadjacent_rows_uses_current_sort_and_all_variables(self) -> None:
        where = FilterEngine(self.metadata.variables).compile('FLAG = "Y"')
        result = DataStore().compare_view_rows(
            self.database,
            self.metadata,
            where,
            SortSpec("AVAL", ascending=False),
            [0, 2],
        )
        self.assertEqual([row.view_row for row in result.rows], [0, 2])
        self.assertIn("AVAL", result.differing_variables)
        self.assertIn("ARMCD", result.differing_variables)
        self.assertNotIn("FLAG", result.differing_variables)


if __name__ == "__main__":
    unittest.main()
