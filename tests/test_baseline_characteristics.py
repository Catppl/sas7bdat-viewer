from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from clinical_data_viewer.baseline_characteristics import (
    BaselineCategoricalItem,
    BaselineCharacteristicsConfig,
    BaselineCharacteristicsEngine,
    BaselineCharacteristicsLongResultBuilder,
    BaselineContinuousItem,
    build_baseline_characteristics_configuration,
)
from clinical_data_viewer.baseline_characteristics.drilldown import (
    build_cell_filter,
    build_cell_where_text,
    lookup_cell,
)
from clinical_data_viewer.domain import DatasetHandle, DatasetMetadata, VariableMetadata
from clinical_data_viewer.filter_engine import FilterEngine
from clinical_data_viewer.temp_manager import TempManager

VARIABLES = (
    VariableMetadata("USUBJID", "Subject identifier"),
    VariableMetadata("SAFFL", "Safety population flag"),
    VariableMetadata("SEX", "Sex"),
    VariableMetadata("AGEGR1N", "Age group", "numeric"),
    VariableMetadata("AGE", "Age", "numeric"),
    VariableMetadata("WEIGHTBL", "Baseline weight", "numeric"),
)


def make_handle(root: Path, rows) -> DatasetHandle:
    directory = root / "source"
    directory.mkdir()
    database = directory / "dataset.sqlite"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "CREATE TABLE dataset ("
            "_source_row INTEGER PRIMARY KEY, USUBJID TEXT, SAFFL TEXT, SEX TEXT, "
            "AGEGR1N REAL, AGE REAL, WEIGHTBL REAL)"
        )
        connection.executemany(
            "INSERT INTO dataset (USUBJID, SAFFL, SEX, AGEGR1N, AGE, WEIGHTBL) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
        connection.commit()
    source = root / "adsl.sas7bdat"
    source.touch()
    return DatasetHandle(
        source,
        directory / source.name,
        database,
        DatasetMetadata("ADSL", len(rows), VARIABLES),
        len(rows),
        True,
    )


def make_config(source: DatasetHandle) -> BaselineCharacteristicsConfig:
    filter_text = 'SAFFL = "Y"'
    return BaselineCharacteristicsConfig(
        (
            BaselineCategoricalItem("SEX", "Sex"),
            BaselineContinuousItem(
                "AGE",
                "Age",
                "years",
                (
                    ("mean", 1),
                    ("std", 2),
                    ("median", 1),
                    ("q1", 0),
                    ("q3", 0),
                    ("min", 0),
                    ("max", 0),
                ),
            ),
            BaselineCategoricalItem(
                "AGEGR1N",
                "Age group",
                "custom",
                (2, 1),
            ),
        ),
        FilterEngine(source.metadata.variables).compile(filter_text),
        filter_text,
        "Overall",
    )


class BaselineCharacteristicsTests(unittest.TestCase):
    def test_custom_level_labels_include_explicit_missing_level(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                (
                    ("S1", "Y", "F", 1, 34, 60),
                    ("S2", "Y", "M", 2, 36, 70),
                    ("S3", "Y", "", 1, 38, 65),
                ),
            )
            config = BaselineCharacteristicsConfig(
                (
                    BaselineCategoricalItem(
                        "SEX",
                        "Sex",
                        "custom",
                        ("F", "M", None),
                        1,
                        (("F", "Female"), ("M", "Male"), (None, "Missing")),
                    ),
                )
            )
            result = BaselineCharacteristicsEngine(TempManager(root / "temp")).run(
                source, config
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                labels = connection.execute(
                    'SELECT "ROW_LABEL" FROM dataset ORDER BY _source_row'
                ).fetchall()
            self.assertEqual(
                [row[0] for row in labels[1:]],
                ["\u00a0" * 4 + "Female", "\u00a0" * 4 + "Male", "\u00a0" * 4 + "Missing"],
            )

    def test_selected_statistics_are_rendered_without_auto_decimals(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                (("S1", "Y", "F", 1, 34.56, 60), ("S2", "Y", "M", 2, 36.78, 70)),
            )
            config = BaselineCharacteristicsConfig(
                (
                    BaselineContinuousItem(
                        "AGE",
                        "Age",
                        statistic_decimals=(("mean", 0),),
                        statistics=("n", "mean"),
                    ),
                )
            )
            result = BaselineCharacteristicsEngine(TempManager(root / "temp")).run(
                source, config
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                rows = connection.execute(
                    "SELECT statistic_key, display_value "
                    "FROM baseline_characteristics_long WHERE statistic_key IS NOT NULL "
                    "ORDER BY row_order"
                ).fetchall()
            self.assertEqual(rows, [("n", "2"), ("mean", "36")])

    def test_treatment_variable_creates_one_result_column_per_level(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "source" / "dataset.sqlite"
            database.parent.mkdir()
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "CREATE TABLE dataset ("
                    "_source_row INTEGER PRIMARY KEY, USUBJID TEXT, TRT TEXT, SEX TEXT)"
                )
                connection.executemany(
                    "INSERT INTO dataset (USUBJID, TRT, SEX) VALUES (?, ?, ?)",
                    (("S1", "A", "F"), ("S2", "A", "M"), ("S3", "B", "F")),
                )
                connection.commit()
            source_path = root / "adsl.sas7bdat"
            source_path.touch()
            source = DatasetHandle(
                source_path,
                root / "source" / source_path.name,
                database,
                DatasetMetadata(
                    "ADSL",
                    3,
                    (
                        VariableMetadata("USUBJID"),
                        VariableMetadata("TRT"),
                        VariableMetadata("SEX"),
                    ),
                ),
                3,
                True,
            )
            config = BaselineCharacteristicsConfig(
                (BaselineCategoricalItem("SEX", "Sex"),),
                treatment_variable="TRT",
            )
            result = BaselineCharacteristicsEngine(TempManager(root / "temp")).run(
                source, config
            )
            self.assertEqual(
                [variable.name for variable in result.metadata.variables],
                ["ROW_LABEL", "TRT_1", "TRT_2"],
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                female_row = connection.execute(
                    'SELECT _source_row FROM dataset WHERE "ROW_LABEL" = ?',
                    ("\u00a0" * 4 + "F",),
                ).fetchone()[0]
            cell = lookup_cell(result, female_row, "TRT_1")
            assert cell is not None
            self.assertEqual(
                build_cell_where_text(source.metadata, config, cell),
                'TRT = "A" and SEX = "F"',
            )

    def test_mixed_items_keep_configured_order_and_statistics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                (
                    ("S1", "Y", "F", 1, 34, 60),
                    ("S2", "Y", "M", 2, 36, 70),
                    ("S3", "Y", "F", 1, None, 65),
                    ("S4", "N", "M", 2, 40, 80),
                ),
            )
            config = make_config(source)
            result = BaselineCharacteristicsEngine(TempManager(root / "temp")).run(
                source, config
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                rows = connection.execute(
                    'SELECT "ROW_LABEL", "VALUE" FROM dataset ORDER BY _source_row'
                ).fetchall()
                long_items = connection.execute(
                    "SELECT DISTINCT item_order, item_kind, variable "
                    "FROM baseline_characteristics_long ORDER BY item_order"
                ).fetchall()
            self.assertEqual(result.kind, "baseline_characteristics")
            self.assertEqual(result.metadata.variables[1].label, "Overall\n(N=3)")
            self.assertEqual(
                long_items,
                [
                    (1, "categorical", "SEX"),
                    (2, "continuous", "AGE"),
                    (3, "categorical", "AGEGR1N"),
                ],
            )
            self.assertEqual(rows[0], ("Sex - n (%)", ""))
            self.assertEqual(rows[1], ("\u00a0" * 4 + "F", "2 (66.7)"))
            self.assertEqual(rows[2], ("\u00a0" * 4 + "M", "1 (33.3)"))
            age_start = rows.index(("Age (years)", ""))
            self.assertEqual(rows[age_start + 1], ("\u00a0" * 4 + "n", "2"))
            self.assertEqual(rows[age_start + 2], ("\u00a0" * 4 + "Mean", "35.0"))
            self.assertEqual(rows[age_start + 3], ("\u00a0" * 4 + "SD", "1.41"))
            group_start = rows.index(("Age group - n (%)", ""))
            self.assertEqual(rows[group_start + 1][0], "\u00a0" * 4 + "2")
            self.assertEqual(rows[group_start + 2][0], "\u00a0" * 4 + "1")
            assert result.configuration_path is not None
            configuration = json.loads(
                result.configuration_path.read_text(encoding="utf-8")
            )
            self.assertEqual(configuration["type"], "baseline_characteristics")
            self.assertEqual(configuration["analysis_universe"]["runtime_header_n"], 3)
            self.assertFalse(configuration["display"]["footnote"])

    def test_duplicate_and_missing_subject_rows_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            duplicate = make_handle(
                root,
                (
                    ("S1", "Y", "F", 1, 34, 60),
                    ("S1", "Y", "F", 1, 35, 61),
                ),
            )
            with self.assertRaisesRegex(ValueError, "one row per USUBJID"):
                BaselineCharacteristicsEngine(TempManager(root / "temp")).run(
                    duplicate, make_config(duplicate)
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            missing = make_handle(root, ((None, "Y", "F", 1, 34, 60),))
            with self.assertRaisesRegex(ValueError, "missing USUBJID"):
                BaselineCharacteristicsEngine(TempManager(root / "temp")).run(
                    missing, make_config(missing)
                )

    def test_storage_type_is_metadata_driven_and_role_is_validated(self) -> None:
        source = DatasetHandle(
            Path("adsl.sas7bdat"),
            Path("copy.sas7bdat"),
            Path("dataset.sqlite"),
            DatasetMetadata("ADSL", 0, VARIABLES),
        )
        BaselineCharacteristicsConfig((BaselineCategoricalItem("AGEGR1N"),)).validate(
            source.metadata
        )
        with self.assertRaisesRegex(ValueError, "must use a numeric variable"):
            BaselineCharacteristicsConfig((BaselineContinuousItem("SEX"),)).validate(
                source.metadata
            )

    def test_configuration_preserves_role_storage_and_filter_ast(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(root, (("S1", "Y", "F", 1, 34, 60),))
            configuration = build_baseline_characteristics_configuration(
                source, make_config(source)
            )
            self.assertEqual(configuration["version"], 1)
            self.assertEqual(configuration["items"][0]["role"], "categorical")
            self.assertEqual(configuration["items"][0]["storage_type"], "character")
            self.assertEqual(configuration["items"][1]["role"], "continuous")
            self.assertEqual(configuration["items"][1]["storage_type"], "numeric")
            self.assertEqual(configuration["dataset_filter"]["text"], 'SAFFL = "Y"')
            self.assertTrue(configuration["dataset_filter"]["ast"])

    def test_categorical_and_continuous_drilldown_use_raw_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                (
                    ("S1", "Y", "F", 1, 34, 60),
                    ("S2", "Y", "M", 2, 36, 70),
                    ("S3", "Y", "F", 1, None, 65),
                ),
            )
            config = make_config(source)
            result = BaselineCharacteristicsEngine(TempManager(root / "temp")).run(
                source, config
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                female_row = connection.execute(
                    'SELECT _source_row FROM dataset WHERE "ROW_LABEL" = ?',
                    ("\u00a0" * 4 + "F",),
                ).fetchone()[0]
                mean_row = connection.execute(
                    'SELECT _source_row FROM dataset WHERE "ROW_LABEL" = ?',
                    ("\u00a0" * 4 + "Mean",),
                ).fetchone()[0]
            female = lookup_cell(result, female_row, "VALUE")
            mean = lookup_cell(result, mean_row, "VALUE")
            assert female is not None and mean is not None
            numerator = build_cell_filter(source.metadata, config, female)
            denominator = build_cell_filter(
                source.metadata, config, female, denominator=True
            )
            continuous = build_cell_filter(source.metadata, config, mean)
            with closing(sqlite3.connect(source.database_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        f"SELECT count(*) FROM dataset WHERE {numerator.sql}",
                        numerator.parameters,
                    ).fetchone()[0],
                    2,
                )
                self.assertEqual(
                    connection.execute(
                        f"SELECT count(*) FROM dataset WHERE {denominator.sql}",
                        denominator.parameters,
                    ).fetchone()[0],
                    3,
                )
                self.assertEqual(
                    connection.execute(
                        f"SELECT count(*) FROM dataset WHERE {continuous.sql}",
                        continuous.parameters,
                    ).fetchone()[0],
                    2,
                )
            self.assertEqual(
                build_cell_where_text(source.metadata, config, female),
                '(SAFFL = "Y") and SEX = "F"',
            )
            self.assertEqual(
                build_cell_where_text(source.metadata, config, mean),
                '(SAFFL = "Y") and not missing(AGE)',
            )

    def test_long_result_is_materialized_as_an_independent_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(root, (("S1", "Y", "F", 1, 34, 60),))
            manager = TempManager(root / "temp")
            result = BaselineCharacteristicsEngine(manager).run(
                source, make_config(source)
            )
            long_result = BaselineCharacteristicsLongResultBuilder(manager).run(
                result, source
            )
            self.assertEqual(long_result.kind, "baseline_characteristics_long")
            self.assertEqual(long_result.metadata.row_count, result.metadata.row_count)
            with closing(sqlite3.connect(long_result.database_path)) as connection:
                first = connection.execute(
                    'SELECT "ITEM_KIND", "VARIABLE" FROM dataset ORDER BY _source_row LIMIT 1'
                ).fetchone()
            self.assertEqual(first, ("categorical", "SEX"))


if __name__ == "__main__":
    unittest.main()
