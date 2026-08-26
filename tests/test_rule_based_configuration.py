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
from clinical_data_viewer.rule_based import (
    RuleBasedConfig,
    RuleBasedDenominator,
    RuleBasedEngine,
    RuleBasedRow,
    build_rule_based_configuration,
    rule_based_configuration_json,
    write_rule_based_configuration,
)
from clinical_data_viewer.temp_manager import TempManager

SOURCE_VARIABLES = (
    VariableMetadata("USUBJID", "Unique Subject Identifier", "character", 20),
    VariableMetadata("TRT01A", "Actual Treatment", "character", 40),
    VariableMetadata("TRTEMFL", "Treatment Emergent", "character", 1),
    VariableMetadata("AESER", "Serious Event", "character", 1),
    VariableMetadata("AVAL", "Analysis Value", "numeric"),
)


def _handle(
    root: Path,
    name: str,
    variables: tuple[VariableMetadata, ...] = SOURCE_VARIABLES,
    rows: tuple[tuple[object, ...], ...] = (),
    *,
    kind: str = "sas",
) -> DatasetHandle:
    directory = root / f"{name}-data"
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
        connection.execute(
            "INSERT INTO cache_info VALUES (?, ?, 1)", (len(rows), len(rows))
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
        kind=kind,
    )


class RuleBasedConfigurationTests(unittest.TestCase):
    def _config(self, source: DatasetHandle, denominator=None) -> RuleBasedConfig:
        fields = source.metadata.variables
        engine = FilterEngine(fields)
        return RuleBasedConfig(
            (
                RuleBasedRow(
                    "row_001",
                    "Any TEAE",
                    engine.compile('TRTEMFL = "Y"'),
                    'TRTEMFL = "Y"',
                    0,
                ),
                RuleBasedRow(
                    "row_002",
                    "Serious TEAE",
                    engine.compile('TRTEMFL = "Y" and AESER = "Y"'),
                    'TRTEMFL = "Y" and AESER = "Y"',
                    1,
                ),
            ),
            "TRT01A",
            "USUBJID",
            engine.compile('TRTEMFL = "Y"'),
            'TRTEMFL = "Y"',
            denominator or RuleBasedDenominator(),
            True,
            1,
        )

    def test_v1_schema_filters_rows_and_contract_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _handle(root, "adae")
            config = self._config(source)
            configuration = build_rule_based_configuration(
                source,
                config,
                resolved_treatment_levels=[
                    ('"Placebo"', "Placebo", "Placebo"),
                    ('"Drug A"', "Drug A", "Drug A"),
                ],
            )
            self.assertEqual(
                set(configuration),
                {
                    "type",
                    "version",
                    "input",
                    "variables",
                    "dataset_filter",
                    "rows",
                    "count",
                    "treatment",
                    "denominator",
                    "total",
                    "calculation",
                    "display",
                    "targets",
                },
            )
            self.assertEqual(configuration["type"], "rule_based_table")
            self.assertEqual(configuration["version"], 1)
            self.assertEqual(configuration["input"]["kind"], "sas")
            self.assertEqual(configuration["input"]["format"], "sas7bdat")
            self.assertEqual(configuration["variables"]["USUBJID"]["type"], "character")
            self.assertEqual(configuration["variables"]["USUBJID"]["length"], 20)
            self.assertEqual(configuration["dataset_filter"]["text"], 'TRTEMFL = "Y"')
            self.assertEqual(
                configuration["dataset_filter"]["ast"]["type"], "comparison"
            )
            self.assertEqual(
                [
                    (row["id"], row["item"], row["indent"])
                    for row in configuration["rows"]
                ],
                [("row_001", "Any TEAE", 0), ("row_002", "Serious TEAE", 1)],
            )
            self.assertEqual(
                configuration["rows"][1]["filter"]["ast"]["type"], "boolean"
            )
            self.assertEqual(
                configuration["count"], {"type": "distinct", "variable": "USUBJID"}
            )
            self.assertEqual(configuration["treatment"]["missing_policy"], "error")
            self.assertEqual(configuration["treatment"]["level_order"], "resolved")
            self.assertEqual(
                [
                    level["value"]
                    for level in configuration["treatment"]["resolved_levels"]
                ],
                ["Placebo", "Drug A"],
            )
            self.assertEqual(configuration["denominator"], {"type": "same_universe"})
            self.assertEqual(
                configuration["total"],
                {"enabled": True, "method": "recompute_distinct_subjects"},
            )
            self.assertEqual(configuration["calculation"]["subject_missing"], "exclude")
            self.assertEqual(
                configuration["calculation"]["percent_method"],
                "freq_divided_by_denom_times_100",
            )
            self.assertEqual(
                configuration["display"],
                {
                    "percent_digits": 1,
                    "rounding": "half_up",
                    "zero_denominator_display": "0 (—)",
                },
            )

    def test_empty_filter_ast_is_null_and_population_is_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _handle(root, "adae")
            population_variables = (
                SOURCE_VARIABLES[0],
                SOURCE_VARIABLES[1],
                VariableMetadata("SAFFL", "Safety", "character", 1),
            )
            population = _handle(root, "adsl", population_variables)
            config = replace(
                self._config(
                    source,
                    RuleBasedDenominator(
                        type="population",
                        population_filter=FilterEngine(population_variables).compile(
                            'SAFFL = "Y"'
                        ),
                        population_filter_text='SAFFL = "Y"',
                    ),
                ),
                dataset_filter=FilterEngine(SOURCE_VARIABLES).compile(""),
                dataset_filter_text="",
            )
            configuration = build_rule_based_configuration(
                source, config, population, [("1", "Placebo", "Placebo")]
            )
            self.assertEqual(configuration["dataset_filter"]["text"], "")
            self.assertIsNone(configuration["dataset_filter"]["ast"])
            population_block = configuration["denominator"]["population"]
            self.assertEqual(population_block["filter"]["text"], 'SAFFL = "Y"')
            self.assertEqual(population_block["filter"]["ast"]["type"], "comparison")
            self.assertNotIn("population", configuration["input"])

    def test_nonmissing_and_merge_input_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _handle(root, "adae")
            config = self._config(
                source,
                RuleBasedDenominator(type="nonmissing", analysis_value_variable="AVAL"),
            )
            nonmissing = build_rule_based_configuration(
                source, config, resolved_treatment_levels=[("1", 1, "1")]
            )
            self.assertEqual(
                nonmissing["denominator"],
                {"type": "nonmissing", "analysis_value_variable": "AVAL"},
            )
            merged = replace(source, kind="merge")
            merge_configuration = build_rule_based_configuration(
                merged, self._config(merged), resolved_treatment_levels=[("1", 1, "1")]
            )
            self.assertEqual(merge_configuration["input"]["kind"], "merge")
            self.assertEqual(merge_configuration["input"]["format"], "merge")
            self.assertIsNone(merge_configuration["input"]["source_path"])
            self.assertIsNone(merge_configuration["targets"]["sas"]["source_member"])

    def test_numeric_treatment_values_keep_json_number_type(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            variables = (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRT01AN", kind="numeric"),
            )
            source = _handle(root, "adsl", variables)
            config = RuleBasedConfig(
                (
                    RuleBasedRow(
                        "row_001", "All", FilterEngine(variables).compile(""), "", 0
                    ),
                ),
                "TRT01AN",
            )
            configuration = build_rule_based_configuration(
                source, config, resolved_treatment_levels=[("1", 1, "1"), ("2", 2, "2")]
            )
            values = [
                level["value"]
                for level in configuration["treatment"]["resolved_levels"]
            ]
            self.assertEqual(values, [1, 2])
            self.assertTrue(all(isinstance(value, int) for value in values))

    def test_engine_writes_json_with_result_and_excludes_blank_subjects(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _handle(
                root,
                "adae",
                rows=(
                    ("S1", "A", "Y", "N", 1.0),
                    ("", "A", "Y", "N", 2.0),
                    (None, "A", "Y", "N", 3.0),
                ),
            )
            config = RuleBasedConfig(
                (
                    RuleBasedRow(
                        "row_001",
                        "Any",
                        FilterEngine(SOURCE_VARIABLES).compile(""),
                        "",
                        0,
                    ),
                ),
                "TRT01A",
            )
            manager = TempManager(root / "temp")
            result = RuleBasedEngine(manager).run(source, config)
            self.assertIsNotNone(result.configuration_path)
            assert result.configuration_path is not None
            self.assertTrue(result.configuration_path.is_file())
            self.assertEqual(
                result.configuration_path.parent, result.database_path.parent
            )
            configuration = json.loads(
                result.configuration_path.read_text(encoding="utf-8")
            )
            self.assertEqual(
                configuration["treatment"]["resolved_levels"],
                [{"value": "A", "label": "A"}],
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                self.assertEqual(
                    connection.execute(
                        'SELECT "TRT_1", "TOTAL" FROM dataset'
                    ).fetchone(),
                    ("1 (100.0)", "1 (100.0)"),
                )
            manager.cleanup()
            self.assertFalse(result.configuration_path.exists())

    def test_write_serialization_is_utf8_json_with_newline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _handle(root, "adae")
            config = self._config(source)
            path = root / "rule_based_config.json"
            write_rule_based_configuration(
                path, source, config, resolved_treatment_levels=[]
            )
            raw = path.read_bytes()
            self.assertTrue(raw.endswith(b"\n"))
            self.assertIn(b"Any TEAE", raw)
            self.assertEqual(
                json.loads(raw), build_rule_based_configuration(source, config)
            )
            self.assertEqual(
                rule_based_configuration_json(
                    build_rule_based_configuration(source, config)
                ),
                raw.decode(),
            )

    def test_invalid_filter_does_not_leave_partial_result_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = _handle(root, "adae", rows=(("S1", "A", "Y", "N", 1.0),))
            engine = FilterEngine(SOURCE_VARIABLES)
            config = RuleBasedConfig(
                (
                    RuleBasedRow(
                        "row_001",
                        "Invalid",
                        engine.compile(""),
                        "NOT_A_VALID_FILTER",
                        0,
                    ),
                ),
                "TRT01A",
            )
            manager = TempManager(root / "temp")
            with self.assertRaises(ValueError):
                RuleBasedEngine(manager).run(source, config)
            self.assertEqual(list(manager.session_directory.iterdir()), [])
            manager.cleanup()


if __name__ == "__main__":
    unittest.main()
