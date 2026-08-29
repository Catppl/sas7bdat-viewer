from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from clinical_data_viewer.categorical import (
    CategoricalConfig,
    CategoricalEngine,
    CategoricalItem,
    CategoricalLongResultBuilder,
    DenominatorConfig,
    MissingTreatmentError,
)
from clinical_data_viewer.categorical.drilldown import (
    CategoricalCell,
    CategoricalQueryBuilder,
    build_cell_filter,
    build_n1_cell_filter,
    lookup_cell,
)
from clinical_data_viewer.domain import DatasetHandle, DatasetMetadata, VariableMetadata
from clinical_data_viewer.filter_engine import FilterEngine
from clinical_data_viewer.temp_manager import TempManager

SOURCE_VARIABLES = (
    VariableMetadata("USUBJID"),
    VariableMetadata("TRT"),
    VariableMetadata("RACE"),
    VariableMetadata("PARAMCD"),
    VariableMetadata("VISIT"),
    VariableMetadata("AVAL", kind="numeric"),
    VariableMetadata("ABLFL"),
)
ADSL_VARIABLES = (
    VariableMetadata("USUBJID"),
    VariableMetadata("TRT"),
    VariableMetadata("RACE"),
    VariableMetadata("SAFFL"),
)


def make_handle(
    root: Path,
    name: str,
    variables: tuple[VariableMetadata, ...],
    rows: tuple[tuple[object, ...], ...],
) -> DatasetHandle:
    directory = root / name
    directory.mkdir()
    database = directory / "dataset.sqlite"
    definitions = ", ".join(
        f'"{variable.name}" {"REAL" if variable.kind == "numeric" else "TEXT"}'
        for variable in variables
    )
    columns = ", ".join(f'"{variable.name}"' for variable in variables)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            f"CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, {definitions})"
        )
        connection.executemany(
            f"INSERT INTO dataset ({columns}) VALUES ({', '.join('?' for _ in variables)})",
            rows,
        )
        connection.commit()
    source_path = root / f"{name}.sas7bdat"
    source_path.touch()
    return DatasetHandle(
        source_path,
        directory / source_path.name,
        database,
        DatasetMetadata(name.upper(), len(rows), variables),
        len(rows),
        True,
    )


class CategoricalEngineTests(unittest.TestCase):
    def test_distinct_subject_count_excludes_blank_and_null_subjects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                "adae",
                SOURCE_VARIABLES,
                (
                    ("S1", "A", "WHITE", "ALB", 1, 1.0, "Y"),
                    ("", "A", "WHITE", "ALB", 1, 2.0, "Y"),
                    (None, "A", "WHITE", "ALB", 1, 3.0, "Y"),
                ),
            )
            config = CategoricalConfig(
                (CategoricalItem("RACE"),),
                "TRT",
                "USUBJID",
                denominator=DenominatorConfig("nonmissing", analysis_value_variable="AVAL"),
            )
            result = CategoricalEngine(TempManager(root / "temp")).run(source, config)
            with closing(sqlite3.connect(result.database_path)) as connection:
                row = connection.execute(
                    "SELECT freq, denom FROM categorical_long "
                    "WHERE level_json = ? AND treatment_json = ?",
                    ('"WHITE"', '"A"'),
                ).fetchone()
            self.assertEqual(row, (1, 1))

    def test_missing_character_item_levels_are_displayed_as_one_level(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                "adae",
                SOURCE_VARIABLES,
                (
                    ("S1", "A", "", "ALB", 1, 1.0, "Y"),
                    ("S2", "A", None, "ALB", 1, 2.0, "Y"),
                ),
            )
            config = CategoricalConfig(
                (CategoricalItem("RACE", include_missing_level=True),),
                "TRT",
                "USUBJID",
                denominator=DenominatorConfig("nonmissing", analysis_value_variable="AVAL"),
            )
            result = CategoricalEngine(TempManager(root / "temp")).run(source, config)
            with closing(sqlite3.connect(result.database_path)) as connection:
                rows = connection.execute(
                    'SELECT "ITEM_LEVEL", "TRT_1" FROM dataset'
                ).fetchall()
                long_rows = connection.execute(
                    "SELECT level_json, treatment_json, freq FROM categorical_long"
                ).fetchall()
            self.assertIn(("\u00a0\u00a0\u00a0\u00a0(Missing)", "2 (100.0)"), rows)
            self.assertEqual(
                long_rows,
                [("null", '"A"', 2), ("null", None, 2)],
            )

    def test_zero_frequency_cells_are_materialized_and_drilldownable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                "adae",
                SOURCE_VARIABLES,
                (("S1", "A", "WHITE", "ALB", 1, 1.0, "Y"),),
            )
            adsl = make_handle(
                root,
                "adsl",
                ADSL_VARIABLES,
                (("S1", "A", "WHITE", "Y"), ("S2", "B", "BLACK", "Y")),
            )
            config = CategoricalConfig(
                (CategoricalItem("RACE"),),
                "TRT",
                "USUBJID",
                denominator=DenominatorConfig("population"),
            )
            result = CategoricalEngine(TempManager(root / "temp")).run(source, config, adsl)
            with closing(sqlite3.connect(result.database_path)) as connection:
                row = connection.execute(
                    'SELECT _source_row, "TRT_2" FROM dataset WHERE "ITEM_LEVEL" = ?',
                    ("\u00a0\u00a0\u00a0\u00a0WHITE",),
                ).fetchone()
            assert row is not None
            self.assertEqual(row[1], "0 (0.0)")
            cell = lookup_cell(result, row[0], "TRT_2")
            self.assertIsNotNone(cell)
            assert cell is not None
            where, parameters = build_cell_filter(source.metadata, config, cell)
            query = CategoricalQueryBuilder(TempManager(root / "query")).run(
                source, where, parameters, "zero"
            )
            with closing(sqlite3.connect(query.database_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM dataset").fetchone()[0],
                    0,
                )

    def test_population_can_use_a_different_treatment_variable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_variables = (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRTA"),
                VariableMetadata("RACE"),
            )
            population_variables = (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRT01A"),
                VariableMetadata("SAFFL"),
            )
            source = make_handle(
                root, "adae", source_variables, (("S1", "A", "WHITE"),)
            )
            adsl = make_handle(
                root,
                "adsl",
                population_variables,
                (("S1", "A", "Y"), ("S2", "B", "Y")),
            )
            population_filter = FilterEngine(population_variables).compile('SAFFL = "Y"')
            config = CategoricalConfig(
                (CategoricalItem("RACE"),),
                "TRTA",
                "USUBJID",
                denominator=DenominatorConfig(
                    "population",
                    population_filter=population_filter,
                    population_treatment_variable="TRT01A",
                ),
            )
            result = CategoricalEngine(TempManager(root / "temp")).run(source, config, adsl)
            with closing(sqlite3.connect(result.database_path)) as connection:
                rows = connection.execute(
                    'SELECT "TRT_1", "TRT_2" FROM dataset WHERE "ITEM_LEVEL" = ?',
                    ("\u00a0\u00a0\u00a0\u00a0WHITE",),
                ).fetchone()
            self.assertEqual(rows, ("1 (100.0)", "0 (0.0)"))

            cell = CategoricalCell("RACE", {}, "WHITE", "B")
            denominator_where, denominator_params = build_cell_filter(
                adsl.metadata, config, cell, denominator=True
            )
            denominator_query = CategoricalQueryBuilder(
                TempManager(root / "denominator-query")
            ).run(adsl, denominator_where, denominator_params, "denominator")
            with closing(sqlite3.connect(denominator_query.database_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM dataset").fetchone()[0],
                    1,
                )

    def test_item_label_is_used_in_wide_header(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                "adae",
                SOURCE_VARIABLES,
                (("S1", "A", "WHITE", "ALB", 1, 1.0, "Y"),),
            )
            config = CategoricalConfig(
                (CategoricalItem("RACE", label="Race category"),),
                "TRT",
                "USUBJID",
                denominator=DenominatorConfig("nonmissing", analysis_value_variable="AVAL"),
            )
            result = CategoricalEngine(TempManager(root / "temp")).run(source, config)
            with closing(sqlite3.connect(result.database_path)) as connection:
                header = connection.execute(
                    'SELECT "ITEM_LEVEL" FROM dataset WHERE "ITEM_LEVEL" NOT LIKE ? LIMIT 1',
                    ("%WHITE%",),
                ).fetchone()
            self.assertEqual(header, ("Race category",))

    def test_nonmissing_denominator_zero_is_displayed_explicitly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                "adae",
                SOURCE_VARIABLES,
                (
                    ("S1", "A", "WHITE", "ALB", 1, 1.0, "Y"),
                    ("S2", "B", "BLACK", "ALB", 1, None, "Y"),
                ),
            )
            config = CategoricalConfig(
                (CategoricalItem("RACE"),),
                "TRT",
                "USUBJID",
                denominator=DenominatorConfig("nonmissing", analysis_value_variable="AVAL"),
            )
            result = CategoricalEngine(TempManager(root / "temp")).run(source, config)
            with closing(sqlite3.connect(result.database_path)) as connection:
                row = connection.execute(
                    'SELECT "TRT_2" FROM dataset WHERE "ITEM_LEVEL" = ?',
                    ("\u00a0\u00a0\u00a0\u00a0WHITE",),
                ).fetchone()
            self.assertEqual(row, ("0 (—)",))

    def test_population_missing_treatment_also_blocks_calculation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                "adae",
                SOURCE_VARIABLES,
                (("S1", "A", "WHITE", "ALB", 1, 1.0, "Y"),),
            )
            adsl = make_handle(
                root,
                "adsl",
                ADSL_VARIABLES,
                (("S1", None, "WHITE", "Y"),),
            )
            config = CategoricalConfig(
                (CategoricalItem("RACE"),),
                "TRT",
                "USUBJID",
                denominator=DenominatorConfig("population"),
            )
            with self.assertRaises(MissingTreatmentError):
                CategoricalEngine(TempManager(root / "temp")).run(source, config, adsl)

    def test_population_context_matching_is_case_insensitive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_variables = (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRT"),
                VariableMetadata("PARAMCD"),
                VariableMetadata("RACE"),
            )
            population_variables = (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRT01A"),
                VariableMetadata("paramcd"),
            )
            source = make_handle(
                root,
                "adae",
                source_variables,
                (("S1", "A", "ALB", "WHITE"),),
            )
            adsl = make_handle(
                root,
                "adsl",
                population_variables,
                (("S1", "A", "ALB"),),
            )
            config = CategoricalConfig(
                (CategoricalItem("RACE", context_variables=("PARAMCD",)),),
                "TRT",
                "USUBJID",
                denominator=DenominatorConfig(
                    "population", population_treatment_variable="TRT01A"
                ),
            )
            result = CategoricalEngine(TempManager(root / "temp")).run(source, config, adsl)
            with closing(sqlite3.connect(result.database_path)) as connection:
                row = connection.execute(
                    'SELECT "TRT_1" FROM dataset WHERE "ITEM_LEVEL" = ?',
                    ("\u00a0\u00a0\u00a0\u00a0WHITE",),
                ).fetchone()
            self.assertEqual(row, ("1 (100.0)",))

    def test_population_treatment_type_mismatch_is_rejected(self) -> None:
        source = DatasetMetadata(
            "ADAE",
            0,
            (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRTA", kind="character"),
                VariableMetadata("RACE"),
            ),
        )
        population = DatasetMetadata(
            "ADSL",
            0,
            (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRT01AN", kind="numeric"),
            ),
        )
        config = CategoricalConfig(
            (CategoricalItem("RACE"),),
            "TRTA",
            "USUBJID",
            denominator=DenominatorConfig(
                "population", population_treatment_variable="TRT01AN"
            ),
        )
        with self.assertRaisesRegex(ValueError, "same type"):
            config.validate(source, population)

    def test_missing_treatment_blocks_source_and_population_calculation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                "adae",
                SOURCE_VARIABLES,
                (("S1", None, "WHITE", "ALB", 1, 1.0, "Y"),),
            )
            config = CategoricalConfig(
                (CategoricalItem("RACE"),),
                "TRT",
                "USUBJID",
                denominator=DenominatorConfig("nonmissing", analysis_value_variable="AVAL"),
            )
            with self.assertRaises(MissingTreatmentError):
                CategoricalEngine(TempManager(root / "temp")).run(source, config)

    def test_numerator_and_population_filters_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                "adae",
                SOURCE_VARIABLES,
                (
                    ("S1", "A", "WHITE", "ALB", 1, 1.0, "Y"),
                    ("S2", "A", "BLACK", "ALB", 1, 2.0, "Y"),
                    ("S3", "B", "WHITE", "ALB", 1, 3.0, "Y"),
                    ("S4", "B", "WHITE", "ALT", 1, 4.0, "Y"),
                ),
            )
            adsl = make_handle(
                root,
                "adsl",
                ADSL_VARIABLES,
                (
                    ("S1", "A", "WHITE", "Y"),
                    ("S2", "A", "BLACK", "Y"),
                    ("S3", "B", "WHITE", "Y"),
                    ("S4", "B", "WHITE", "Y"),
                ),
            )
            numerator = FilterEngine(SOURCE_VARIABLES).compile('PARAMCD = "ALB"')
            population = FilterEngine(ADSL_VARIABLES).compile('SAFFL = "Y"')
            config = CategoricalConfig(
                (CategoricalItem("RACE"),),
                "TRT",
                "USUBJID",
                source_filter=numerator,
                source_filter_text='PARAMCD = "ALB"',
                denominator=DenominatorConfig(
                    "population",
                    population_filter=population,
                    population_filter_text='SAFFL = "Y"',
                ),
            )
            result = CategoricalEngine(TempManager(root / "temp")).run(
                source, config, adsl
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                numerator_cell = connection.execute(
                    "SELECT freq, denom FROM categorical_long "
                    "WHERE level_json = ? AND treatment_json = ?",
                    ('"WHITE"', '"B"'),
                ).fetchone()
            self.assertEqual(numerator_cell, (1, 2))
            # Drilldown uses the same independent source/ADSL filters as the
            # calculation.  Build the query directly from a known cell shape.
            cell = CategoricalCell("RACE", {}, "WHITE", "B")
            numerator_where, numerator_params = build_cell_filter(
                source.metadata, config, cell
            )
            denominator_where, denominator_params = build_cell_filter(
                adsl.metadata, config, cell, denominator=True
            )
            numerator_query = CategoricalQueryBuilder(
                TempManager(root / "numerator-query")
            ).run(source, numerator_where, numerator_params, "Numerator")
            denominator_query = CategoricalQueryBuilder(
                TempManager(root / "denominator-query")
            ).run(adsl, denominator_where, denominator_params, "Denominator")
            with closing(sqlite3.connect(numerator_query.database_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM dataset").fetchone()[0], 1
                )
            with closing(sqlite3.connect(denominator_query.database_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM dataset").fetchone()[0], 2
                )

    def test_population_n_uses_adsl_subjects_total_and_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                "adlb",
                SOURCE_VARIABLES,
                (
                    ("S1", "A", "WHITE", "ALB", 1, 1.0, "Y"),
                    ("S2", "A", "BLACK", "ALB", 1, 2.0, "Y"),
                    ("S3", "B", "WHITE", "ALB", 1, 3.0, "Y"),
                    ("S4", "B", None, "ALT", 1, 4.0, "Y"),
                ),
            )
            adsl = make_handle(
                root,
                "adsl",
                ADSL_VARIABLES,
                (
                    ("S1", "A", "WHITE", "Y"),
                    ("S2", "A", "BLACK", "Y"),
                    ("S3", "B", "WHITE", "Y"),
                    ("S4", "B", "ASIAN", "N"),
                ),
            )
            population_filter = FilterEngine(ADSL_VARIABLES).compile('SAFFL = "Y"')
            config = CategoricalConfig(
                (CategoricalItem("RACE"),),
                "TRT",
                "USUBJID",
                denominator=DenominatorConfig(
                    "population", population_filter=population_filter, population_filter_text='SAFFL = "Y"'
                ),
            )
            result = CategoricalEngine(TempManager(root / "temp")).run(
                source, config, adsl
            )
            self.assertEqual(result.kind, "categorical")
            self.assertEqual(
                dict(result.metadata.display_column_names)["TRT_1"], "A n (%)"
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                columns = [row[1] for row in connection.execute("PRAGMA table_info(dataset)")]
                rows = connection.execute('SELECT "ITEM_LEVEL", "TRT_1", "TRT_2", "TOTAL" FROM dataset').fetchall()
                long_row = connection.execute(
                    "SELECT freq, denom, pct FROM categorical_long "
                    "WHERE level_json = ? AND treatment_json = ?",
                    ('"WHITE"', '"A"'),
                ).fetchone()
            self.assertIn("TRT_1", columns)
            self.assertEqual(long_row, (1, 2, 50.0))
            self.assertIn(("RACE", "", "", ""), rows)
            self.assertIn(("\u00a0\u00a0\u00a0\u00a0WHITE", "1 (50.0)", "1 (100.0)", "2 (66.7)"), rows)
            self.assertIn(("\u00a0\u00a0\u00a0\u00a0BLACK", "1 (50.0)", "0 (0.0)", "1 (33.3)"), rows)
            with closing(sqlite3.connect(result.database_path)) as connection:
                result_row = connection.execute(
                    'SELECT _source_row FROM dataset WHERE "ITEM_LEVEL" = ?',
                    ("\u00a0\u00a0\u00a0\u00a0WHITE",),
                ).fetchone()[0]
            cell = lookup_cell(result, result_row, "TRT_1")
            self.assertIsNotNone(cell)
            assert cell is not None
            numerator_where, numerator_params = build_cell_filter(
                source.metadata, config, cell
            )
            query = CategoricalQueryBuilder(TempManager(root / "queries")).run(
                source, numerator_where, numerator_params, "Numerator"
            )
            with closing(sqlite3.connect(query.database_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM dataset").fetchone()[0], 1
                )
            subject_query = CategoricalQueryBuilder(
                TempManager(root / "subject-queries")
            ).run(
                source,
                numerator_where,
                numerator_params,
                "Numerator subjects",
                subject_id_variable="USUBJID",
            )
            self.assertEqual(
                [variable.name for variable in subject_query.metadata.variables],
                ["USUBJID"],
            )
            with closing(sqlite3.connect(subject_query.database_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM dataset").fetchone()[0], 1
                )
            denominator_where, denominator_params = build_cell_filter(
                adsl.metadata, config, cell, denominator=True
            )
            denominator_query = CategoricalQueryBuilder(
                TempManager(root / "denominator-queries")
            ).run(adsl, denominator_where, denominator_params, "Denominator")
            with closing(sqlite3.connect(denominator_query.database_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM dataset").fetchone()[0], 2
                )
            long_result = CategoricalLongResultBuilder(
                TempManager(root / "long-temp")
            ).run(result, source, ())
            self.assertEqual(long_result.kind, "categorical_long")
            with closing(sqlite3.connect(long_result.database_path)) as connection:
                long_columns = [
                    row[1] for row in connection.execute("PRAGMA table_info(dataset)")
                ]
                long_values = connection.execute(
                    'SELECT "ITEM", "LEVEL", "TRT", "FREQ", "DENOM", "PCT" '
                    'FROM dataset WHERE "LEVEL" = "WHITE" AND "TRT" = "A"'
                ).fetchone()
            self.assertEqual(
                long_columns,
                ["_source_row", "ITEM", "ITEM_LABEL", "LEVEL", "TRT", "FREQ", "DENOM", "PCT"],
            )
            self.assertEqual(long_values, ("RACE", "WHITE", "A", 1.0, 2.0, 50.0))

    def test_nonmissing_n_honours_source_filter_and_missing_level_option(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                "adlb",
                SOURCE_VARIABLES,
                (
                    ("S1", "A", "WHITE", "ALB", 1, 1.0, "Y"),
                    ("S2", "A", None, "ALB", 1, 2.0, "Y"),
                    ("S3", "A", "WHITE", "ALB", 1, None, "Y"),
                    ("S4", "B", "BLACK", "ALT", 1, 4.0, "N"),
                ),
            )
            source_filter = FilterEngine(SOURCE_VARIABLES).compile('ABLFL = "Y"')
            config = CategoricalConfig(
                (CategoricalItem("RACE", include_missing_level=True),),
                "TRT",
                "USUBJID",
                source_filter=source_filter,
                source_filter_text='ABLFL = "Y"',
                denominator=DenominatorConfig("nonmissing", analysis_value_variable="AVAL"),
            )
            result = CategoricalEngine(TempManager(root / "temp")).run(source, config)
            with closing(sqlite3.connect(result.database_path)) as connection:
                rows = connection.execute('SELECT "ITEM_LEVEL", "TRT_1" FROM dataset').fetchall()
            self.assertIn(("\u00a0\u00a0\u00a0\u00a0WHITE", "2 (100.0)"), rows)
            self.assertIn(("\u00a0\u00a0\u00a0\u00a0(Missing)", "1 (50.0)"), rows)

    def test_n1_record_count_uses_only_eligible_postbaseline_records(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                "adlb",
                SOURCE_VARIABLES,
                (
                    ("S1", "A", "LOW", "ALB", 0, 1.0, "Y"),
                    ("S1", "A", "HIGH", "ALB", 1, 2.0, "N"),
                    ("S1", "A", "HIGH", "ALB", 2, 3.0, "N"),
                    ("S2", "A", "LOW", "ALB", 0, 2.0, "Y"),
                    ("S3", "B", "HIGH", "ALB", 1, 1.0, "N"),
                    # A complete baseline/post pair in another PARAMCD must
                    # be excluded by Numerator WHERE.
                    ("S4", "A", "LOW", "ALT", 0, 2.0, "Y"),
                    ("S4", "A", "HIGH", "ALT", 1, 3.0, "N"),
                ),
            )
            numerator = FilterEngine(SOURCE_VARIABLES).compile('PARAMCD = "ALB"')
            baseline = FilterEngine(SOURCE_VARIABLES).compile('ABLFL = "Y"')
            postbaseline = FilterEngine(SOURCE_VARIABLES).compile('ABLFL != "Y"')
            config = CategoricalConfig(
                (CategoricalItem("RACE", context_variables=("PARAMCD",)),),
                "TRT",
                "USUBJID",
                count_type="record",
                source_filter=numerator,
                source_filter_text='PARAMCD = "ALB"',
                denominator=DenominatorConfig(
                    "baseline_postbaseline",
                    analysis_value_variable="AVAL",
                    baseline_filter=baseline,
                    baseline_filter_text='ABLFL = "Y"',
                    postbaseline_filter=postbaseline,
                    postbaseline_filter_text='ABLFL != "Y"',
                ),
            )
            result = CategoricalEngine(TempManager(root / "temp")).run(source, config)
            with closing(sqlite3.connect(result.database_path)) as connection:
                row = connection.execute(
                    'SELECT "ITEM_LEVEL", "TRT_1" FROM dataset WHERE "ITEM_LEVEL" = ?',
                    ("\u00a0\u00a0\u00a0\u00a0HIGH",),
                ).fetchone()
            self.assertEqual(row, ("\u00a0\u00a0\u00a0\u00a0HIGH", "2 (100.0)"))
            with closing(sqlite3.connect(result.database_path)) as connection:
                result_row = connection.execute(
                    'SELECT _source_row FROM dataset WHERE "ITEM_LEVEL" = ?',
                    ("\u00a0\u00a0\u00a0\u00a0HIGH",),
                ).fetchone()[0]
            cell = lookup_cell(result, result_row, "TRT_1")
            assert cell is not None
            where, parameters = build_n1_cell_filter(source.metadata, config, cell)
            query = CategoricalQueryBuilder(TempManager(root / "query-temp")).run(
                source, where, parameters, "n1"
            )
            with closing(sqlite3.connect(query.database_path)) as connection:
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM dataset").fetchone()[0], 2
                )

    def test_n1_requires_record_count(self) -> None:
        config = CategoricalConfig(
            (CategoricalItem("RACE"),),
            "TRT",
            "USUBJID",
            denominator=DenominatorConfig("baseline_postbaseline", "AVAL"),
        )
        with self.assertRaisesRegex(ValueError, "record count"):
            config.validate(DatasetMetadata("ADLB", 0, SOURCE_VARIABLES))

    def test_custom_level_order_is_explicitly_reserved(self) -> None:
        config = CategoricalConfig(
            (CategoricalItem("RACE", level_order=("WHITE", "BLACK")),),
            "TRT",
            "USUBJID",
            denominator=DenominatorConfig("nonmissing", analysis_value_variable="AVAL"),
        )
        with self.assertRaisesRegex(ValueError, "level order"):
            config.validate(DatasetMetadata("ADLB", 0, SOURCE_VARIABLES))
