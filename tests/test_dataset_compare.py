from __future__ import annotations

import csv
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from clinical_data_viewer.compare_engine import (
    CompareConfig,
    DatasetComparer,
    MatchVariable,
)
from clinical_data_viewer.compare_engine.comparator import differing_variables
from clinical_data_viewer.compare_engine.matcher import match_group
from clinical_data_viewer.compare_engine.models import SourceRecord
from clinical_data_viewer.csv_exporter import CsvExporter
from clinical_data_viewer.data_store import DataStore
from clinical_data_viewer.domain import (
    DatasetHandle,
    DatasetMetadata,
    SortSpec,
    VariableMetadata,
)
from clinical_data_viewer.filter_engine import FilterEngine
from clinical_data_viewer.temp_manager import TempManager

VARIABLES = (
    VariableMetadata("USUBJID"),
    VariableMetadata("PARAMCD"),
    VariableMetadata("AVISITN", kind="numeric"),
    VariableMetadata("AVAL", kind="numeric"),
    VariableMetadata("ASEQ", kind="numeric"),
)


def make_handle(root: Path, name: str, rows: list[tuple[object, ...]]) -> DatasetHandle:
    directory = root / name
    directory.mkdir()
    database = directory / "dataset.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            'CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "USUBJID" TEXT, '
            '"PARAMCD" TEXT, "AVISITN" REAL, "AVAL" REAL, "ASEQ" REAL)'
        )
        connection.executemany(
            'INSERT INTO dataset("USUBJID", "PARAMCD", "AVISITN", "AVAL", "ASEQ") '
            "VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    source = root / f"{name}.sas7bdat"
    source.touch()
    return DatasetHandle(
        source,
        directory / source.name,
        database,
        DatasetMetadata(name, len(rows), VARIABLES),
        len(rows),
        True,
    )


class DatasetCompareTests(unittest.TestCase):
    def test_hungarian_matching_is_global_and_one_to_one(self) -> None:
        main = [
            SourceRecord(1, {"X": "A", "Y": "1"}),
            SourceRecord(2, {"X": "B", "Y": "2"}),
        ]
        qc = [
            SourceRecord(1, {"X": "B", "Y": "2"}),
            SourceRecord(2, {"X": "A", "Y": "1"}),
        ]
        variables = (
            MatchVariable("X", "character", 2),
            MatchVariable("Y", "character", 1),
        )
        result = match_group(main, qc, variables, 0.5, 0.01)
        self.assertEqual(
            [(item.main_index, item.qc_index) for item in result.decisions],
            [(0, 1), (1, 0)],
        )
        self.assertFalse(result.unmatched_main)
        self.assertFalse(result.unmatched_qc)

    def test_threshold_and_ambiguity_do_not_force_a_match(self) -> None:
        variables = (MatchVariable("X", "character"),)
        unmatched = match_group(
            [SourceRecord(1, {"X": "A"})],
            [SourceRecord(1, {"X": "B"})],
            variables,
            0.25,
            0.05,
        )
        self.assertFalse(unmatched.decisions)
        self.assertEqual(unmatched.unmatched_main, (0,))
        self.assertEqual(unmatched.unmatched_qc, (0,))

        ambiguous = match_group(
            [SourceRecord(1, {"X": "A"})],
            [SourceRecord(1, {"X": "A"}), SourceRecord(2, {"X": "A"})],
            variables,
            0.5,
            0.05,
        )
        self.assertTrue(ambiguous.decisions[0].ambiguous)

    def test_key_variables_gate_formal_differences(self) -> None:
        main = SourceRecord(1, {"KEY": "A", "VALUE": 1.0})
        qc = SourceRecord(2, {"KEY": "B", "VALUE": 2.0})
        differences = differing_variables(
            main,
            qc,
            {"KEY": "character", "VALUE": "numeric"},
            ("KEY",),
            (),
        )
        self.assertEqual(differences, ("KEY",))

    def test_compare_result_uses_adjacent_main_qc_rows_and_pair_filters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manager = TempManager(root / "temp")
            main = make_handle(
                root,
                "main",
                [
                    ("01", "ALT", 1, 10, 1),
                    ("01", "ALT", 2, 20, 2),
                    ("02", "AST", 1, 30, 1),
                ],
            )
            qc = make_handle(
                root,
                "qc",
                [
                    ("01", "ALT", 2, 21, 99),
                    ("01", "ALT", 1, 10, 1),
                    ("03", "AST", 1, 40, 1),
                ],
            )
            config = CompareConfig(
                ("USUBJID", "PARAMCD"),
                (
                    MatchVariable("AVISITN", "numeric", 3),
                    MatchVariable("AVAL", "numeric", 1, 2),
                ),
                threshold=0.5,
                ambiguity_margin=0.01,
            )
            result = DatasetComparer(manager).compare(main, qc, config)
            self.assertEqual(result.kind, "compare")
            store = DataStore()
            empty = FilterEngine(result.metadata.variables).compile("")
            page = store.query_page(
                result.database_path,
                result.metadata,
                ["COMPARE_PAIR", "SIDE", "MATCH_STATUS", "USUBJID", "AVAL"],
                empty,
                None,
                0,
                100,
            )
            self.assertEqual([row[1] for row in page.rows[:2]], ["Main", "QC"])
            self.assertEqual(page.rows[0][2], "Different")
            self.assertIn("Main only", {row[2] for row in page.rows})
            self.assertIn("QC only", {row[2] for row in page.rows})
            self.assertIn("ASEQ", page.cell_highlights[0])
            self.assertIn("ASEQ", page.cell_highlights[1])

            filtered = FilterEngine(result.metadata.variables).compile('SIDE = "QC"')
            pair_page = store.query_page(
                result.database_path,
                result.metadata,
                ["COMPARE_PAIR", "SIDE", "AVAL"],
                filtered,
                SortSpec("AVAL", ascending=False),
                0,
                100,
            )
            sides_by_pair: dict[float, list[str]] = {}
            for pair, side, _value in pair_page.rows:
                sides_by_pair.setdefault(pair, []).append(side)
            self.assertIn(["Main", "QC"], sides_by_pair.values())

            destination = root / "compare.csv"
            exported = CsvExporter().export(
                result,
                destination,
                ["COMPARE_PAIR", "SIDE", "AVAL"],
                filtered,
                SortSpec("AVAL", ascending=False),
            )
            with destination.open(encoding="utf-8-sig", newline="") as stream:
                csv_rows = list(csv.reader(stream))
            self.assertEqual(exported, len(pair_page.rows))
            self.assertEqual(
                csv_rows[1:],
                [[str(value) for value in row] for row in pair_page.rows],
            )
            manager.cleanup()


if __name__ == "__main__":
    unittest.main()
