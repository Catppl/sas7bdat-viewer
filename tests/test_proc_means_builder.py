from __future__ import annotations

import csv
import json
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
    VariableMetadata,
)
from clinical_data_viewer.filter_engine import FilterEngine
from clinical_data_viewer.proc_means import ProcMeansConfig, ProcMeansEngine
from clinical_data_viewer.temp_manager import TempManager

VARIABLES = (
    VariableMetadata("USUBJID"),
    VariableMetadata("PARAMCD"),
    VariableMetadata("AVISITN", kind="numeric"),
    VariableMetadata("TRT01AN", kind="numeric"),
    VariableMetadata("AVAL", "Analysis Value", "numeric"),
    VariableMetadata("CHG", "Change", "numeric"),
    VariableMetadata("ANL01FL"),
)


def make_source(root: Path) -> DatasetHandle:
    directory = root / "source"
    directory.mkdir()
    database = directory / "dataset.sqlite"
    rows = (
        ("S1", "ALB", 1, 1, 1.1, 0.1, "Y"),
        ("S2", "ALB", 1, 1, 2.0, 0.2, "Y"),
        ("S3", "ALB", 1, None, None, 0.3, "Y"),
        ("S4", "ALT", None, 2, 10.123, 1.0, "Y"),
        ("S5", "ALT", None, 2, 99.999, 9.0, "N"),
    )
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            'CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "USUBJID" TEXT, '
            '"PARAMCD" TEXT, "AVISITN" REAL, "TRT01AN" REAL, "AVAL" REAL, '
            '"CHG" REAL, "ANL01FL" TEXT)'
        )
        connection.executemany(
            'INSERT INTO dataset("USUBJID", "PARAMCD", "AVISITN", "TRT01AN", '
            '"AVAL", "CHG", "ANL01FL") VALUES (?, ?, ?, ?, ?, ?, ?)',
            rows,
        )
        connection.commit()
    source = root / "ADLB.sas7bdat"
    source.touch()
    return DatasetHandle(
        source,
        directory / source.name,
        database,
        DatasetMetadata("ADLB", len(rows), VARIABLES),
        len(rows),
        True,
    )


class ProcMeansBuilderTests(unittest.TestCase):
    def test_grouped_long_result_missing_groups_precision_csv_and_json(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_source(root)
            manager = TempManager(root / "temp")
            compiled = FilterEngine(VARIABLES).compile('ANL01FL = "Y"')
            config = ProcMeansConfig(
                ("AVAL", "CHG"),
                ("PARAMCD", "AVISITN"),
                ("TRT01AN",),
                ("subjects", "n", "nmiss", "mean", "std", "median", "min", "max"),
                compiled,
                'ANL01FL = "Y"',
                "PARAMCD",
                (("mean", 1), ("std", 2), ("median", 1), ("min", 0), ("max", 0)),
                0.95,
            )
            result = ProcMeansEngine(manager).run(source, config)
            self.assertEqual(result.kind, "proc_means")
            self.assertEqual(result.metadata.row_count, 6)
            self.assertIsNotNone(result.configuration_path)

            with closing(sqlite3.connect(result.database_path)) as connection:
                rows = connection.execute(
                    'SELECT "PARAMCD", "AVISITN", "TRT01AN", '
                    '"ANALYSIS_VARIABLE", "SUBJECT_N", "N", "NMISS", "MEAN", '
                    '"__CDE_BASE_DECIMALS" FROM dataset'
                ).fetchall()
            alb_aval = next(row for row in rows if row[:4] == ("ALB", 1.0, 1.0, "AVAL"))
            self.assertEqual(alb_aval[4:7], (2, 2, 0))
            self.assertAlmostEqual(alb_aval[7], 1.55)
            self.assertEqual(alb_aval[8], 1)
            self.assertTrue(
                any(
                    row[0] == "ALB"
                    and row[2] is None
                    and row[3] == "AVAL"
                    and row[5] == 0
                    and row[6] == 1
                    for row in rows
                )
            )
            self.assertNotIn(99.999, {row[7] for row in rows})

            destination = root / "proc-means.csv"
            empty = FilterEngine(result.metadata.variables).compile("")
            CsvExporter().export(
                result,
                destination,
                [variable.name for variable in result.metadata.variables],
                empty,
                None,
            )
            with destination.open(encoding="utf-8-sig", newline="") as stream:
                csv_rows = list(csv.DictReader(stream))
            alb_csv = next(
                row
                for row in csv_rows
                if row["PARAMCD"] == "ALB"
                and row["TRT01AN"] == "1.0"
                and row["ANALYSIS_VARIABLE"] == "AVAL"
            )
            self.assertEqual(alb_csv["MEAN"], "1.55")
            self.assertEqual(alb_csv["MIN"], "1.1")
            self.assertEqual(alb_csv["N"], "2")
            alt_csv = next(
                row
                for row in csv_rows
                if row["PARAMCD"] == "ALT" and row["ANALYSIS_VARIABLE"] == "AVAL"
            )
            self.assertEqual(alt_csv["MEAN"], "10.1230")

            configuration = json.loads(
                result.configuration_path.read_text(encoding="utf-8")
            )
            self.assertEqual(configuration["type"], "proc_means")
            self.assertEqual(configuration["version"], 1)
            self.assertEqual(configuration["dataset"], "ADLB")
            self.assertEqual(configuration["analysis_variables"], ["AVAL", "CHG"])
            self.assertEqual(configuration["statistics"][3:5], ["MEAN", "SD"])
            self.assertTrue(configuration["options"]["nway"])
            self.assertTrue(configuration["options"]["include_missing_class"])
            self.assertEqual(
                configuration["display"]["decimal_group_variable"], "PARAMCD"
            )
            self.assertEqual(configuration["display"]["maximum_decimals"], 4)
            manager.cleanup()

    def test_decimal_group_must_be_a_by_or_class_variable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = make_source(Path(directory))
            config = ProcMeansConfig(
                ("AVAL",),
                ("PARAMCD",),
                (),
                ("mean",),
                decimal_group_variable="AVISITN",
            )
            with self.assertRaisesRegex(ValueError, "Decimal Group Variable"):
                config.validate(source.metadata)

    def test_result_page_exposes_per_row_decimal_base(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_source(root)
            manager = TempManager(root / "temp")
            config = ProcMeansConfig(
                ("AVAL",),
                ("PARAMCD",),
                (),
                ("mean",),
                decimal_group_variable="PARAMCD",
                decimal_offsets=(("mean", 1),),
            )
            result = ProcMeansEngine(manager).run(source, config)
            page = DataStore().query_page(
                result.database_path,
                result.metadata,
                ["PARAMCD", "MEAN"],
                FilterEngine(result.metadata.variables).compile(""),
                None,
                0,
                100,
            )
            self.assertEqual(len(page.rows), len(page.row_decimal_bases))
            self.assertEqual(
                dict(zip((row[0] for row in page.rows), page.row_decimal_bases))["ALT"],
                3,
            )
            manager.cleanup()


if __name__ == "__main__":
    unittest.main()
