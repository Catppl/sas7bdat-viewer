from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from clinical_data_viewer.domain import DatasetHandle, DatasetMetadata, VariableMetadata
from clinical_data_viewer.filter_engine import FilterEngine
from clinical_data_viewer.rule_based import (
    MissingTreatmentError,
    RuleBasedConfig,
    RuleBasedDenominator,
    RuleBasedEngine,
    RuleBasedLongResultBuilder,
    RuleBasedRow,
)
from clinical_data_viewer.rule_based.drilldown import (
    build_cell_filter,
    build_population_cell_filter,
    lookup_cell,
)
from clinical_data_viewer.temp_manager import TempManager


SOURCE_VARIABLES = (
    VariableMetadata("USUBJID"),
    VariableMetadata("TRT"),
    VariableMetadata("ITEM"),
    VariableMetadata("AVAL", kind="numeric"),
)
ADSL_VARIABLES = (
    VariableMetadata("USUBJID"),
    VariableMetadata("TRT"),
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
        connection.execute(
            "CREATE TABLE cache_info (cached_rows INTEGER, total_rows INTEGER, complete INTEGER)"
        )
        connection.execute("INSERT INTO cache_info VALUES (?, ?, 1)", (len(rows), len(rows)))
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


class RuleBasedEngineTests(unittest.TestCase):
    def _config(self, source: DatasetHandle, denominator: RuleBasedDenominator) -> RuleBasedConfig:
        dataset_filter = FilterEngine(SOURCE_VARIABLES).compile('ITEM = "X"')
        row_filter = FilterEngine(SOURCE_VARIABLES).compile('AVAL > 0')
        return RuleBasedConfig(
            (RuleBasedRow("row_001", "Item X", row_filter, 'AVAL > 0', 0),),
            "TRT",
            "USUBJID",
            dataset_filter,
            'ITEM = "X"',
            denominator,
            True,
            1,
        )

    def test_same_universe_counts_distinct_subjects_and_long_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                "adae",
                SOURCE_VARIABLES,
                (
                    ("S1", "A", "X", 1.0),
                    ("S1", "A", "X", 2.0),
                    ("S2", "A", "X", None),
                    ("S3", "B", "X", 3.0),
                    ("S4", "B", "Y", 4.0),
                ),
            )
            config = self._config(
                source, RuleBasedDenominator(type="same_universe")
            )
            result = RuleBasedEngine(TempManager(root / "temp")).run(source, config)
            with closing(sqlite3.connect(result.database_path)) as connection:
                row = connection.execute(
                    'SELECT "TRT_1", "TRT_2", "TOTAL" FROM dataset'
                ).fetchone()
                result_row = connection.execute(
                    'SELECT _source_row FROM dataset WHERE "ITEM" = ?',
                    ("Item X",),
                ).fetchone()[0]
                long_row = connection.execute(
                    "SELECT freq, denom, pct FROM rule_based_long "
                    "WHERE treatment_json = ?",
                    ('"A"',),
                ).fetchone()
            self.assertEqual(row, ("1 (50.0)", "1 (100.0)", "2 (66.7)"))
            self.assertEqual(long_row, (1, 2, 50.0))
            cell = lookup_cell(result, result_row, "TRT_1")
            self.assertIsNotNone(cell)
            assert cell is not None
            self.assertEqual((cell.row_id, cell.treatment), ("row_001", "A"))
            self.assertEqual(
                RuleBasedLongResultBuilder(TempManager(root / "long")).run(result).kind,
                "rule_based_long",
            )

    def test_population_and_nonmissing_filters_are_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                "adae",
                SOURCE_VARIABLES,
                (
                    ("S1", "A", "X", 1.0),
                    ("S1", "A", "X", 2.0),
                    ("S2", "A", "X", None),
                    ("S3", "B", "X", 3.0),
                ),
            )
            adsl = make_handle(
                root,
                "adsl",
                ADSL_VARIABLES,
                (
                    ("S1", "A", "Y"),
                    ("S2", "A", "Y"),
                    ("S3", "B", "Y"),
                ),
            )
            population_filter = FilterEngine(ADSL_VARIABLES).compile('SAFFL = "Y"')
            population_config = self._config(
                source,
                RuleBasedDenominator(
                    type="population",
                    population_filter=population_filter,
                    population_filter_text='SAFFL = "Y"',
                ),
            )
            population_result = RuleBasedEngine(TempManager(root / "population-temp")).run(
                source, population_config, adsl
            )
            with closing(sqlite3.connect(population_result.database_path)) as connection:
                self.assertEqual(
                    connection.execute('SELECT "TRT_1", "TRT_2" FROM dataset').fetchone(),
                    ("1 (50.0)", "1 (100.0)"),
                )
            nonmissing_config = self._config(
                source,
                RuleBasedDenominator(
                    type="nonmissing", analysis_value_variable="AVAL"
                ),
            )
            nonmissing_result = RuleBasedEngine(TempManager(root / "nonmissing-temp")).run(
                source, nonmissing_config
            )
            with closing(sqlite3.connect(nonmissing_result.database_path)) as connection:
                self.assertEqual(
                    connection.execute('SELECT "TRT_1", "TRT_2" FROM dataset').fetchone(),
                    ("1 (100.0)", "1 (100.0)"),
                )

    def test_population_denominator_can_use_a_different_treatment_variable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_variables = (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRTAN", kind="numeric"),
                VariableMetadata("ITEM"),
                VariableMetadata("AVAL", kind="numeric"),
            )
            population_variables = (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRT01AN", kind="numeric"),
                VariableMetadata("SAFFL"),
            )
            source = make_handle(
                root,
                "adae",
                source_variables,
                (("S1", 1, "X", 1.0), ("S2", 2, "X", 1.0)),
            )
            adsl = make_handle(
                root,
                "adsl",
                population_variables,
                (("S1", 1, "Y"), ("S2", 2, "Y")),
            )
            source_engine = FilterEngine(source_variables)
            population_filter = FilterEngine(population_variables).compile('SAFFL = "Y"')
            config = RuleBasedConfig(
                (RuleBasedRow("row_001", "Item X", source_engine.compile('ITEM = "X"'), 'ITEM = "X"'),),
                "TRTAN",
                "USUBJID",
                source_engine.compile(""),
                "",
                RuleBasedDenominator(
                    type="population",
                    population_filter=population_filter,
                    population_filter_text='SAFFL = "Y"',
                    population_treatment_variable="TRT01AN",
                ),
            )
            result = RuleBasedEngine(TempManager(root / "temp")).run(
                source, config, adsl
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                self.assertEqual(
                    connection.execute('SELECT "TRT_1", "TRT_2" FROM dataset').fetchone(),
                    ("1 (100.0)", "1 (100.0)"),
                )
            _sql, parameters = build_population_cell_filter(
                adsl.metadata, config, config.rows[0], 1
            )
            self.assertIn('"TRT01AN" = ?', _sql)
            self.assertEqual(parameters[-1], 1)

    def test_missing_treatment_blocks_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                "adae",
                SOURCE_VARIABLES,
                (("S1", None, "X", 1.0),),
            )
            config = self._config(source, RuleBasedDenominator())
            with self.assertRaisesRegex(MissingTreatmentError, "Item X"):
                RuleBasedEngine(TempManager(root / "temp")).run(source, config)

    def test_drilldown_filters_follow_rule_and_population_scopes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_handle(
                root,
                "adae",
                SOURCE_VARIABLES,
                (("S1", "A", "X", 1.0), ("S2", "A", "X", None)),
            )
            adsl = make_handle(
                root,
                "adsl",
                ADSL_VARIABLES,
                (("S1", "A", "Y"), ("S2", "A", "Y")),
            )
            population_filter = FilterEngine(ADSL_VARIABLES).compile('SAFFL = "Y"')
            config = self._config(
                source,
                RuleBasedDenominator(
                    "population", population_filter, 'SAFFL = "Y"'
                ),
            )
            row = config.rows[0]
            numerator_sql, numerator_params = build_cell_filter(
                source.metadata, config, row, "A"
            )
            denominator_sql, denominator_params = build_population_cell_filter(
                adsl.metadata, config, row, "A"
            )
            self.assertIn('"ITEM" = ?', numerator_sql)
            self.assertIn('"SAFFL" = ?', denominator_sql)
            self.assertNotIn("SAFFL", numerator_sql)
            self.assertNotIn("ITEM", denominator_sql)
            self.assertEqual(numerator_params[-1], "A")
            self.assertEqual(denominator_params[0], "Y")


if __name__ == "__main__":
    unittest.main()
