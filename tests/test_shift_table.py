from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from clinical_data_viewer.domain import DatasetHandle, DatasetMetadata, VariableMetadata
from clinical_data_viewer.filter_engine import FilterEngine
from clinical_data_viewer.shift_table import (
    DuplicateShiftRecordsError,
    OrderedLevel,
    ShiftTableConfig,
    ShiftTableEngine,
    ShiftTableLongResultBuilder,
    build_shift_table_configuration,
)
from clinical_data_viewer.shift_table.drilldown import (
    build_cell_filter,
    build_cell_where_text,
    build_header_filter,
    lookup_cell,
    lookup_header,
)
from clinical_data_viewer.temp_manager import TempManager

ANALYSIS_VARIABLES = (
    VariableMetadata("USUBJID"),
    VariableMetadata("SAFFL"),
    VariableMetadata("PARAMCD"),
    VariableMetadata("AVISIT"),
    VariableMetadata("TRTA"),
    VariableMetadata("BGRD", kind="numeric"),
    VariableMetadata("PGRD", kind="numeric"),
    VariableMetadata("ANL01FL"),
)

POPULATION_VARIABLES = (
    VariableMetadata("USUBJID"),
    VariableMetadata("SAFFL"),
    VariableMetadata("TRT01A"),
)


def make_handle(
    root: Path, name: str, rows, variables, definition: str
) -> DatasetHandle:
    directory = root / name.casefold()
    directory.mkdir()
    database = directory / "dataset.sqlite"
    columns = [variable.name for variable in variables]
    placeholders = ", ".join("?" for _ in columns)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            f"CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, {definition})"
        )
        connection.executemany(
            "INSERT INTO dataset ("
            + ", ".join(columns)
            + ") VALUES ("
            + placeholders
            + ")",
            rows,
        )
        connection.commit()
    source = root / f"{name.casefold()}.sas7bdat"
    source.touch()
    return DatasetHandle(
        source,
        directory / source.name,
        database,
        DatasetMetadata(name, len(rows), variables),
        len(rows),
        True,
    )


def make_analysis(root: Path, rows) -> DatasetHandle:
    return make_handle(
        root,
        "ADLB",
        rows,
        ANALYSIS_VARIABLES,
        "USUBJID TEXT, SAFFL TEXT, PARAMCD TEXT, AVISIT TEXT, TRTA TEXT, "
        "BGRD REAL, PGRD REAL, ANL01FL TEXT",
    )


def make_population(root: Path, rows) -> DatasetHandle:
    return make_handle(
        root,
        "ADSL",
        rows,
        POPULATION_VARIABLES,
        "USUBJID TEXT, SAFFL TEXT, TRT01A TEXT",
    )


def make_config(source: DatasetHandle, population: DatasetHandle) -> ShiftTableConfig:
    analysis_filter = 'SAFFL = "Y"'
    population_filter = 'SAFFL = "Y"'
    return ShiftTableConfig(
        "USUBJID",
        "PARAMCD",
        "AVISIT",
        "TRTA",
        "BGRD",
        "PGRD",
        "ANL01FL",
        "Y",
        (
            OrderedLevel("ALT", "ALT", 2),
            OrderedLevel("AST", "AST", 1),
        ),
        (
            OrderedLevel("Week 8", "Week 8", 2),
            OrderedLevel("Week 4", "Week 4", 1),
        ),
        (
            OrderedLevel("A", "Drug A", 1),
            OrderedLevel("B", "Drug B", 2),
        ),
        (
            OrderedLevel(0, "0", 1),
            OrderedLevel(1, "1", 2),
            OrderedLevel(2, "2", 3),
        ),
        FilterEngine(source.metadata.variables).compile(analysis_filter),
        analysis_filter,
        True,
        1,
        "USUBJID",
        "TRT01A",
        FilterEngine(population.metadata.variables).compile(population_filter),
        population_filter,
    )


class ShiftTableTests(unittest.TestCase):
    def test_engine_builds_ordered_matrices_independent_totals_and_population_headers(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_analysis(
                root,
                (
                    ("S1", "Y", "AST", "Week 4", "A", 0, 1, "Y"),
                    ("S2", "Y", "AST", "Week 4", "A", 0, 0, "Y"),
                    ("S3", "Y", "AST", "Week 4", "B", 1, 2, "Y"),
                    ("S4", "Y", "AST", "Week 4", "B", 1, None, "Y"),
                    ("S1", "Y", "ALT", "Week 8", "A", 1, 2, "Y"),
                    ("S2", "N", "ALT", "Week 8", "A", 1, 1, "Y"),
                    ("S5", "Y", "ALT", "Week 8", "B", 2, 2, "N"),
                ),
            )
            population = make_population(
                root,
                (
                    ("S1", "Y", "A"),
                    ("S2", "Y", "A"),
                    ("S3", "Y", "B"),
                    ("S4", "Y", "B"),
                    ("S5", "N", "B"),
                ),
            )
            config = make_config(source, population)
            result = ShiftTableEngine(TempManager(root / "temp")).run(
                source, config, population=population
            )
            self.assertEqual(result.kind, "shift_table")
            self.assertIn(
                "Excluded 1 records", result.metadata.warning_column_messages[0][1]
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                rows = connection.execute(
                    "SELECT PARAMETER, VISIT, BASELINE_GRADE, T1_G1, T1_G2, "
                    "T1_TOTAL, T2_G3, TOTAL_ALL FROM dataset ORDER BY _source_row"
                ).fetchall()
                long_rows = connection.execute(
                    "SELECT parameter_label, visit_label, cell_role, subject_n, denominator_n "
                    "FROM shift_table_long ORDER BY row_order"
                ).fetchall()
            self.assertEqual(rows[0][:3], ("AST", "Week 4", "0"))
            self.assertEqual(rows[0][3:6], ("1 (50.0)", "1 (50.0)", "2 (100.0)"))
            self.assertEqual(rows[1][6], "1 (100.0)")
            self.assertEqual(rows[3][7], "3 (100.0)")
            self.assertEqual(rows[4][:3], ("ALT", "Week 8", "0"))
            self.assertTrue(any(row[2] == "grand_total" for row in long_rows))
            labels = {
                variable.name: variable.label for variable in result.metadata.variables
            }
            self.assertIn("Drug A\n(N=2)", labels["T1_G1"])
            self.assertIn("Total\n(N=4)", labels["TOTAL_ALL"])
            assert result.configuration_path is not None
            saved = json.loads(result.configuration_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["type"], "shift_table")
            self.assertEqual(saved["ordering"]["parameters"][0]["raw"], "AST")

    def test_duplicate_requires_confirmation_and_continue_retains_source_records(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_analysis(
                root,
                (
                    ("S1", "Y", "AST", "Week 4", "A", 0, 1, "Y"),
                    ("S1", "Y", "AST", "Week 4", "A", 0, 2, "Y"),
                ),
            )
            population = make_population(root, (("S1", "Y", "A"),))
            config = make_config(source, population)
            with self.assertRaises(DuplicateShiftRecordsError):
                ShiftTableEngine(TempManager(root / "temp1")).run(
                    source, config, population=population
                )
            result = ShiftTableEngine(TempManager(root / "temp2")).run(
                source,
                replace(config, allow_duplicate_records=True),
                population=population,
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                values = connection.execute(
                    'SELECT T1_G2, T1_G3, T1_TOTAL FROM dataset WHERE BASELINE_GRADE = "0"'
                ).fetchone()
            self.assertEqual(values, ("1 (100.0)", "1 (100.0)", "1 (100.0)"))

    def test_unconfigured_nonmissing_grade_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_analysis(
                root, (("S1", "Y", "AST", "Week 4", "A", 0, 9, "Y"),)
            )
            population = make_population(root, (("S1", "Y", "A"),))
            with self.assertRaisesRegex(ValueError, "unconfigured"):
                ShiftTableEngine(TempManager(root / "temp")).run(
                    source, make_config(source, population), population=population
                )

    def test_missing_subjects_are_warned_and_excluded_from_counts_and_drilldown(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_analysis(
                root,
                (
                    ("S1", "Y", "AST", "Week 4", "A", 0, 1, "Y"),
                    ("", "Y", "AST", "Week 4", "A", 0, 1, "Y"),
                ),
            )
            population = make_population(root, (("S1", "Y", "A"), ("", "Y", "A")))
            config = make_config(source, population)
            engine = ShiftTableEngine(TempManager(root / "temp"))
            self.assertEqual(
                engine.missing_subject_count(source, config, population), 1
            )
            result = engine.run(source, config, population=population)
            with closing(sqlite3.connect(result.database_path)) as connection:
                value = connection.execute(
                    'SELECT T1_G2 FROM dataset WHERE PARAMETER = "AST" '
                    'AND VISIT = "Week 4" AND BASELINE_GRADE = "0"'
                ).fetchone()[0]
            self.assertEqual(value, "1 (100.0)")
            self.assertIn(
                "missing USUBJID",
                result.metadata.warning_column_messages[0][1],
            )
            header = lookup_header(result, "T1_G2")
            assert header is not None
            population_filter = build_header_filter(population.metadata, config, header)
            with closing(sqlite3.connect(population.database_path)) as connection:
                count = connection.execute(
                    f"SELECT count(*) FROM dataset WHERE {population_filter.sql}",
                    population_filter.parameters,
                ).fetchone()[0]
            self.assertEqual(count, 1)

    def test_drilldown_uses_raw_cell_and_overall_total_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_analysis(
                root,
                (
                    ("S1", "Y", "AST", "Week 4", "A", 0, 1, "Y"),
                    ("S2", "Y", "AST", "Week 4", "B", 0, 1, "Y"),
                ),
            )
            population = make_population(root, (("S1", "Y", "A"), ("S2", "Y", "B")))
            config = make_config(source, population)
            result = ShiftTableEngine(TempManager(root / "temp")).run(
                source, config, population=population
            )
            cell = lookup_cell(result, 1, "T1_G2")
            self.assertIsNotNone(cell)
            assert cell is not None
            compiled = build_cell_filter(source.metadata, config, cell)
            with closing(sqlite3.connect(source.database_path)) as connection:
                count = connection.execute(
                    f"SELECT count(*) FROM dataset WHERE {compiled.sql}",
                    compiled.parameters,
                ).fetchone()[0]
            self.assertEqual(count, 1)
            self.assertIn(
                'PARAMCD = "AST"', build_cell_where_text(source.metadata, config, cell)
            )
            overall = lookup_cell(result, 1, "TOTAL_G2")
            assert overall is not None
            self.assertTrue(overall.is_overall)
            overall_filter = build_cell_filter(source.metadata, config, overall)
            with closing(sqlite3.connect(source.database_path)) as connection:
                count = connection.execute(
                    f"SELECT count(*) FROM dataset WHERE {overall_filter.sql}",
                    overall_filter.parameters,
                ).fetchone()[0]
            self.assertEqual(count, 2)
            header = lookup_header(result, "TOTAL_G2")
            assert header is not None
            population_filter = build_header_filter(population.metadata, config, header)
            with closing(sqlite3.connect(population.database_path)) as connection:
                count = connection.execute(
                    f"SELECT count(*) FROM dataset WHERE {population_filter.sql}",
                    population_filter.parameters,
                ).fetchone()[0]
            self.assertEqual(count, 2)

    def test_configuration_and_long_result_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = make_analysis(
                root, (("S1", "Y", "AST", "Week 4", "A", 0, 1, "Y"),)
            )
            population = make_population(root, (("S1", "Y", "A"),))
            config = make_config(source, population)
            configuration = build_shift_table_configuration(
                source, config, population=population
            )
            self.assertEqual(configuration["calculation"]["count"], "distinct_subjects")
            self.assertEqual(
                configuration["display"]["treatment_layout"], "across_columns"
            )
            result = ShiftTableEngine(TempManager(root / "temp")).run(
                source, config, population=population
            )
            long_result = ShiftTableLongResultBuilder(
                TempManager(root / "long-temp")
            ).run(result, source)
            self.assertEqual(long_result.kind, "shift_table_long")
            self.assertGreater(long_result.metadata.row_count, 0)


if __name__ == "__main__":
    unittest.main()
