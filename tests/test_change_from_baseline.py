from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from clinical_data_viewer.change_from_baseline import (
    ChangeFromBaselineConfig,
    ChangeFromBaselineEngine,
)
from clinical_data_viewer.change_from_baseline.result_store import (
    ChangeFromBaselineLongResultBuilder,
)
from clinical_data_viewer.change_from_baseline.drilldown import (
    build_cell_filter,
    lookup_cell,
)
from clinical_data_viewer.domain import DatasetHandle, DatasetMetadata, VariableMetadata
from clinical_data_viewer.filter_engine import FilterEngine
from clinical_data_viewer.temp_manager import TempManager


def make_handle(root: Path, rows: tuple[tuple[object, ...], ...]) -> DatasetHandle:
    variables = (
        VariableMetadata("USUBJID"),
        VariableMetadata("PARAMCD"),
        VariableMetadata("AVISITN", kind="numeric"),
        VariableMetadata("TRT01A"),
        VariableMetadata("CHG", kind="numeric"),
    )
    directory = root / "adalb"
    directory.mkdir()
    database = directory / "dataset.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            'CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "USUBJID" TEXT, "PARAMCD" TEXT, "AVISITN" REAL, "TRT01A" TEXT, "CHG" REAL)'
        )
        connection.executemany(
            'INSERT INTO dataset ("USUBJID", "PARAMCD", "AVISITN", "TRT01A", "CHG") VALUES (?, ?, ?, ?, ?)',
            rows,
        )
        connection.commit()
    source_path = root / "adlb.sas7bdat"
    source_path.touch()
    return DatasetHandle(
        source_path,
        directory / source_path.name,
        database,
        DatasetMetadata("ADLB", len(rows), variables),
        len(rows),
        True,
    )


class ChangeFromBaselineTests(unittest.TestCase):
    def test_result_cells_map_back_to_source_group_and_statistic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                (
                    ("S1", "ALB", 1, "A", 1.0),
                    ("S2", "ALB", 1, "A", 2.0),
                    ("S3", "ALB", 1, "B", 3.0),
                ),
            )
            config = ChangeFromBaselineConfig(
                "CHG",
                "PARAMCD",
                row_variables=("AVISITN",),
                treatment_variable="TRT01A",
                data_filter=FilterEngine(source.metadata.variables).compile(
                    'PARAMCD = "ALB"'
                ),
                data_filter_text='PARAMCD = "ALB"',
            )
            result = ChangeFromBaselineEngine(TempManager(root / "temp")).run(
                source, config
            )
            cell = lookup_cell(result, 1, "TRT_1_MEAN")
            self.assertIsNotNone(cell)
            assert cell is not None
            self.assertEqual(cell.item, "ALB")
            self.assertEqual(cell.row_values, (("AVISITN", 1.0),))
            self.assertEqual(cell.treatment, "A")
            self.assertEqual(cell.statistic, "mean")
            where, parameters = build_cell_filter(source.metadata, config, cell)
            self.assertIn('"PARAMCD" = ?', where)
            self.assertIn('"AVISITN" = ?', where)
            self.assertIn('"TRT01A" = ?', where)
            self.assertIn('"CHG"', where)
            self.assertEqual(parameters, ("ALB", "ALB", 1.0, "A"))

    def test_total_cell_filter_does_not_add_treatment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(root, (("S1", "ALB", 1, "A", 1.0),))
            config = ChangeFromBaselineConfig(
                "CHG", "PARAMCD", treatment_variable="TRT01A"
            )
            result = ChangeFromBaselineEngine(TempManager(root / "temp")).run(
                source, config
            )
            cell = lookup_cell(result, 1, "TOTAL_MEAN")
            self.assertIsNotNone(cell)
            assert cell is not None
            where, _parameters = build_cell_filter(source.metadata, config, cell)
            self.assertNotIn('"TRT01A"', where)

    def test_change_table_has_small_n_large_n_total_and_where_item_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                (
                    ("S1", "ALB", 1, "A", 1.0),
                    ("S1", "ALB", 1, "A", 2.0),
                    ("S2", "ALB", 1, "A", 3.0),
                    ("S1", "ALT", 1, "A", 4.0),
                    ("S3", "ALT", 1, "B", None),
                    ("S4", "ALT", 2, "B", 5.0),
                ),
            )
            variables = source.metadata.variables
            config = ChangeFromBaselineConfig(
                "CHG",
                "PARAMCD",
                row_variables=("AVISITN",),
                treatment_variable="TRT01A",
                row_sort=(("AVISITN", True),),
                data_filter=FilterEngine(variables).compile(
                    'PARAMCD IN ("ALT", "ALB")'
                ),
                data_filter_text='PARAMCD IN ("ALT", "ALB")',
                postbaseline_filter=FilterEngine(variables).compile("AVISITN > 0"),
                postbaseline_filter_text="AVISITN > 0",
                item_labels=(("ALT", "Alanine"), ("ALB", "Albumin")),
            )
            engine = ChangeFromBaselineEngine(TempManager(root / "temp"))
            warnings = engine.duplicate_warnings(source, config)
            self.assertEqual(len(warnings), 1)
            result = engine.run(source, config)
            with closing(sqlite3.connect(result.database_path)) as connection:
                rows = connection.execute(
                    'SELECT "ITEM", "AVISITN", "TRT_1_N", "TRT_1_SUBJECT_N", "TRT_2_N", "TOTAL_N", "TOTAL_SUBJECT_N" FROM dataset ORDER BY _source_row'
                ).fetchall()
                long_count = connection.execute(
                    "SELECT count(*) FROM change_from_baseline_long"
                ).fetchone()[0]
            self.assertEqual(rows[0][0], "Alanine")
            self.assertEqual(rows[1][0], "Alanine")
            self.assertEqual(rows[2][0], "Albumin")
            self.assertEqual(rows[0][2:], (1, 1, 0, 1, 1))
            self.assertEqual(rows[2][2:], (3, 2, 0, 3, 2))
            self.assertEqual(long_count, 81)
            assert result.configuration_path is not None
            configuration = json.loads(
                result.configuration_path.read_text(encoding="utf-8")
            )
            self.assertEqual(configuration["item"]["resolved_order"], ["ALT", "ALB"])
            self.assertEqual(
                configuration["variable_metadata"]["CHG"]["type"], "numeric"
            )
            self.assertEqual(
                configuration["calculation"]["small_n"], "nonmissing_change_records"
            )
            self.assertEqual(
                configuration["calculation"]["large_n"], "distinct_subjects"
            )
            self.assertEqual(configuration["display"]["maximum_decimals"], 4)
            self.assertEqual(
                configuration["display"]["statistic_decimals"],
                {
                    "MEAN": 1,
                    "STD": 2,
                    "MEDIAN": 1,
                    "Q1": 1,
                    "Q3": 1,
                    "MIN": 0,
                    "MAX": 0,
                },
            )
            self.assertIsNotNone(result.metadata.decimal_base_column)
            decimal_offsets = dict(result.metadata.statistic_decimal_offsets)
            self.assertEqual(decimal_offsets["TRT_1_MEAN"], 1)
            self.assertEqual(decimal_offsets["TRT_1_STD"], 2)
            self.assertEqual(decimal_offsets["TOTAL_MEAN"], 1)
            labels = {
                variable.name: variable.label for variable in result.metadata.variables
            }
            self.assertEqual(labels["TRT_1_N"], "A n")
            self.assertEqual(labels["TRT_1_SUBJECT_N"], "A N")
            self.assertEqual(labels["TOTAL_N"], "Total n")
            self.assertEqual(labels["TOTAL_SUBJECT_N"], "Total N")

    def test_statistic_decimal_places_are_serialized_and_capped_at_four(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(root, (("S1", "ALB", 1, "A", 1.0),))
            config = ChangeFromBaselineConfig(
                "CHG",
                "PARAMCD",
                statistic_decimals=(
                    ("mean", 4),
                    ("std", 3),
                    ("median", 2),
                    ("q1", 1),
                    ("q3", 0),
                    ("min", 4),
                    ("max", 4),
                ),
            )
            result = ChangeFromBaselineEngine(TempManager(root / "temp")).run(
                source, config
            )
            assert result.configuration_path is not None
            configuration = json.loads(
                result.configuration_path.read_text(encoding="utf-8")
            )
            self.assertEqual(configuration["display"]["statistic_decimals"]["MEAN"], 4)
            self.assertEqual(configuration["display"]["statistic_decimals"]["Q3"], 0)

    def test_statistic_decimal_places_above_four_are_rejected(self) -> None:
        source = DatasetMetadata(
            "ADLB",
            1,
            (
                VariableMetadata("USUBJID"),
                VariableMetadata("PARAMCD"),
                VariableMetadata("CHG", kind="numeric"),
            ),
        )
        config = ChangeFromBaselineConfig(
            "CHG",
            "PARAMCD",
            statistic_decimals=(("mean", 5),),
        )
        with self.assertRaisesRegex(ValueError, "between 0 and 4"):
            config.validate(source)

    def test_missing_treatment_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(root, (("S1", "ALB", 1, None, 1.0),))
            variables = source.metadata.variables
            config = ChangeFromBaselineConfig(
                "CHG",
                "PARAMCD",
                treatment_variable="TRT01A",
                data_filter=FilterEngine(variables).compile(""),
            )
            with self.assertRaisesRegex(ValueError, "Missing treatment"):
                ChangeFromBaselineEngine(TempManager(root / "temp")).run(source, config)

    def test_complex_item_filter_reports_default_order_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(root, (("S1", "ALB", 1, "A", 1.0),))
            variables = source.metadata.variables
            config = ChangeFromBaselineConfig(
                "CHG",
                "PARAMCD",
                data_filter=FilterEngine(variables).compile('PARAMCD LIKE "A%"'),
                data_filter_text='PARAMCD LIKE "A%"',
            )
            warning = ChangeFromBaselineEngine(
                TempManager(root / "temp")
            ).item_order_warnings(source, config)
            self.assertEqual(len(warning), 1)

    def test_item_or_equality_order_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                (
                    ("S1", "ALB", 1, "A", 1.0),
                    ("S2", "ALT", 1, "A", 2.0),
                ),
            )
            variables = source.metadata.variables
            text = 'PARAMCD = "ALT" OR PARAMCD = "ALB"'
            config = ChangeFromBaselineConfig(
                "CHG",
                "PARAMCD",
                treatment_variable="TRT01A",
                data_filter=FilterEngine(variables).compile(text),
                data_filter_text=text,
            )
            result = ChangeFromBaselineEngine(TempManager(root / "temp")).run(
                source, config
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                items = [
                    row[0]
                    for row in connection.execute(
                        'SELECT "ITEM" FROM dataset ORDER BY _source_row'
                    )
                ]
            self.assertEqual(items, ["ALT", "ALB"])

    def test_treatment_can_be_selected_as_a_row_variable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                (
                    ("S1", "ALB", 1, "B", 1.0),
                    ("S2", "ALB", 1, "A", 2.0),
                ),
            )
            config = ChangeFromBaselineConfig(
                "CHG",
                "PARAMCD",
                row_variables=("TRT01A",),
                treatment_variable="TRT01A",
            )
            result = ChangeFromBaselineEngine(TempManager(root / "temp")).run(
                source, config
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                columns = [
                    row[1] for row in connection.execute("PRAGMA table_info(dataset)")
                ]
                rows = connection.execute(
                    'SELECT "TRT01A", "MEAN" FROM dataset ORDER BY _source_row'
                ).fetchall()
            self.assertEqual(rows, [("A", 2.0), ("B", 1.0)])
            self.assertNotIn("TRT_1_MEAN", columns)

    def test_item_labels_are_serialized_in_one_block(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(root, (("S1", "ALB", 1, "A", 1.0),))
            config = ChangeFromBaselineConfig(
                "CHG",
                "PARAMCD",
                treatment_variable="TRT01A",
                item_labels=(("ALB", "Albumin"),),
            )
            result = ChangeFromBaselineEngine(TempManager(root / "temp")).run(
                source, config
            )
            assert result.configuration_path is not None
            configuration = json.loads(
                result.configuration_path.read_text(encoding="utf-8")
            )
            self.assertEqual(configuration["item"]["labels"], {"ALB": "Albumin"})

    def test_row_descending_sort_keeps_missing_values_last(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                (
                    ("S1", "ALB", 1, "A", 1.0),
                    ("S2", "ALB", 3, "A", 2.0),
                    ("S3", "ALB", 2, "A", 3.0),
                    ("S4", "ALB", None, "A", 4.0),
                ),
            )
            config = ChangeFromBaselineConfig(
                "CHG",
                "PARAMCD",
                row_variables=("AVISITN",),
                treatment_variable="TRT01A",
                row_sort=(("AVISITN", False),),
            )
            result = ChangeFromBaselineEngine(TempManager(root / "temp")).run(
                source, config
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                values = [
                    row[0]
                    for row in connection.execute(
                        'SELECT "AVISITN" FROM dataset ORDER BY _source_row'
                    )
                ]
            self.assertEqual(values, [3.0, 2.0, 1.0, None])

    def test_no_treatment_uses_one_all_group_without_total_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(root, (("S1", "ALB", 1, "A", 1.0),))
            config = ChangeFromBaselineConfig(
                "CHG", "PARAMCD", treatment_variable="", include_total=True
            )
            result = ChangeFromBaselineEngine(TempManager(root / "temp")).run(
                source, config
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                columns = [
                    row[1] for row in connection.execute("PRAGMA table_info(dataset)")
                ]
            self.assertIn("MEAN", columns)
            self.assertNotIn("TOTAL_MEAN", columns)
            labels = {
                variable.name: variable.label for variable in result.metadata.variables
            }
            self.assertEqual(labels["N"], "n")
            self.assertEqual(labels["MEAN"], "MEAN")
            self.assertEqual(labels["SUBJECT_N"], "N")

    def test_long_result_is_materialized_in_row_order(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(root, (("S1", "ALB", 1, "A", 1.0),))
            result = ChangeFromBaselineEngine(TempManager(root / "temp")).run(
                source,
                ChangeFromBaselineConfig("CHG", "PARAMCD", treatment_variable="TRT01A"),
            )
            long_result = ChangeFromBaselineLongResultBuilder(
                TempManager(root / "long-temp")
            ).run(result, source)
            with closing(sqlite3.connect(long_result.database_path)) as connection:
                first = connection.execute(
                    'SELECT "ITEM", "TREATMENT", "STATISTIC" FROM dataset ORDER BY _source_row LIMIT 1'
                ).fetchone()
            self.assertEqual(first, ("ALB", "A", "N"))


if __name__ == "__main__":
    unittest.main()
