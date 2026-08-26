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
from clinical_data_viewer.proc_means import (
    ProcMeansConfig,
    ProcMeansEngine,
    ProcMeansQueryBuilder,
    build_drilldown_filter,
    build_drilldown_where_text,
)
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
                ("PARAMCD", "AVISITN"),
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
            self.assertEqual(configuration["version"], 3)
            self.assertEqual(configuration["input"]["dataset"], "ADLB")
            self.assertEqual(configuration["input"]["format"], "sas7bdat")
            self.assertEqual(
                configuration["input"]["source_path"], str(source.source_path)
            )
            self.assertNotEqual(
                configuration["input"]["source_path"], str(source.temporary_path)
            )
            self.assertEqual(configuration["analysis_variables"], ["AVAL", "CHG"])
            self.assertEqual(configuration["statistics"][3:5], ["MEAN", "SD"])
            self.assertEqual(
                configuration["calculation"]["reference_engine"],
                "python_proc_means_v1",
            )
            self.assertTrue(configuration["calculation"]["include_missing_class"])
            self.assertEqual(
                configuration["calculation"]["subject_count"]["variable"],
                "USUBJID",
            )
            self.assertEqual(configuration["filter"]["language"], "sas_like")
            self.assertEqual(configuration["filter"]["text"], 'ANL01FL = "Y"')
            self.assertEqual(configuration["filter"]["ast"]["type"], "comparison")
            self.assertEqual(configuration["variables"]["AVAL"]["type"], "numeric")
            self.assertEqual(
                configuration["display"]["decimal_group_variables"],
                ["PARAMCD", "AVISITN"],
            )
            self.assertEqual(
                configuration["display"]["decimal_inference"]["maximum_decimals"],
                4,
            )
            self.assertEqual(
                configuration["display"]["decimal_inference"]["mode"],
                "runtime_from_filtered_input",
            )
            self.assertEqual(configuration["output"]["layout"], "long")
            self.assertEqual(configuration["display"]["rounding"]["mode"], "half_up")
            self.assertNotIn("resolved_decimals", configuration["display"])
            self.assertEqual(
                configuration["targets"]["sas"]["source_library"], "analysis"
            )
            self.assertEqual(configuration["targets"]["sas"]["source_member"], "adlb")
            self.assertEqual(
                configuration["targets"]["r"]["output_object"],
                "proc_means_result",
            )
            manager.cleanup()

    def test_decimal_group_must_be_a_by_or_class_variable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = make_source(Path(directory))
            config = ProcMeansConfig(
                ("AVAL",),
                ("PARAMCD",),
                (),
                ("mean",),
                decimal_group_variables=("AVISITN",),
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
                decimal_group_variables=("PARAMCD",),
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

    def test_multiple_decimal_groups_use_the_complete_combination(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_source(root)
            manager = TempManager(root / "temp")
            with closing(sqlite3.connect(source.database_path)) as connection:
                connection.execute(
                    'INSERT INTO dataset("USUBJID", "PARAMCD", "AVISITN", '
                    '"TRT01AN", "AVAL", "CHG", "ANL01FL") '
                    'VALUES ("S6", "ALB", 2, 1, 1.234, 0.4, "Y")'
                )
                connection.commit()
            config = ProcMeansConfig(
                ("AVAL",),
                ("PARAMCD", "AVISITN"),
                (),
                ("mean",),
                decimal_group_variables=("PARAMCD", "AVISITN"),
            )
            result = ProcMeansEngine(manager).run(source, config)
            with closing(sqlite3.connect(result.database_path)) as connection:
                bases = {
                    (row[0], row[1]): row[2]
                    for row in connection.execute(
                        'SELECT "PARAMCD", "AVISITN", "__CDE_BASE_DECIMALS" '
                        'FROM dataset WHERE "PARAMCD" = ?',
                        ("ALB",),
                    )
                }
            self.assertEqual(bases[("ALB", 1.0)], 1)
            self.assertEqual(bases[("ALB", 2.0)], 3)
            manager.cleanup()

    def test_drilldown_builds_independent_query_rows_from_result_group(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = make_source(root)
            manager = TempManager(root / "temp")
            source_filter = FilterEngine(VARIABLES).compile('ANL01FL = "Y"')
            config = ProcMeansConfig(
                ("AVAL",),
                ("PARAMCD", "AVISITN"),
                ("TRT01AN",),
                ("mean", "nmiss"),
                source_filter,
                'ANL01FL = "Y"',
            )
            mean_filter = build_drilldown_filter(
                source.metadata,
                config,
                {"PARAMCD": "ALB", "AVISITN": 1.0, "TRT01AN": 1.0},
                "AVAL",
                "mean",
            )
            mean_where = build_drilldown_where_text(
                source.metadata,
                config,
                {"PARAMCD": "ALB", "AVISITN": 1.0, "TRT01AN": 1.0},
                "AVAL",
                "mean",
            )
            self.assertEqual(
                mean_where,
                '(ANL01FL = "Y") and PARAMCD = "ALB" and AVISITN = 1 '
                'and TRT01AN = 1 and not missing(AVAL)',
            )
            query = ProcMeansQueryBuilder(manager).run(
                source, mean_filter, "Query: Mean: 1.55"
            )
            self.assertEqual(query.kind, "query")
            self.assertEqual(query.metadata.row_count, 2)
            self.assertNotIn(":", query.source_path.name)
            with closing(sqlite3.connect(query.database_path)) as connection:
                subjects = connection.execute(
                    'SELECT "USUBJID" FROM dataset ORDER BY _source_row'
                ).fetchall()
            self.assertEqual(subjects, [("S1",), ("S2",)])

            missing_filter = build_drilldown_filter(
                source.metadata,
                config,
                {"PARAMCD": "ALB", "AVISITN": 1.0, "TRT01AN": None},
                "AVAL",
                "nmiss",
            )
            missing_where = build_drilldown_where_text(
                source.metadata,
                config,
                {"PARAMCD": "ALB", "AVISITN": 1.0, "TRT01AN": None},
                "AVAL",
                "nmiss",
            )
            self.assertIn("missing(TRT01AN)", missing_where)
            self.assertTrue(missing_where.endswith("missing(AVAL)"))
            missing_query = ProcMeansQueryBuilder(manager).run(
                source, missing_filter, "Query: NMISS: 1"
            )
            self.assertEqual(missing_query.metadata.row_count, 1)
            with closing(sqlite3.connect(missing_query.database_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        'SELECT "USUBJID", "AVAL" FROM dataset'
                    ).fetchone(),
                    ("S3", None),
                )
            manager.cleanup()


if __name__ == "__main__":
    unittest.main()
