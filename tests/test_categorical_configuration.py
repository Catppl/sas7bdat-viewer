from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from clinical_data_viewer.categorical import (
    CategoricalConfig,
    CategoricalEngine,
    CategoricalItem,
    DenominatorConfig,
    build_categorical_configuration,
    categorical_configuration_json,
    write_categorical_configuration,
)
from clinical_data_viewer.domain import DatasetHandle, DatasetMetadata, VariableMetadata
from clinical_data_viewer.filter_engine import FilterEngine
from clinical_data_viewer.temp_manager import TempManager


def make_handle(
    root: Path,
    name: str,
    variables: tuple[VariableMetadata, ...],
    rows: tuple[tuple[object, ...], ...] = (),
    *,
    kind: str = "sas",
) -> DatasetHandle:
    directory = root / f"{name}_cache"
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
        if rows:
            connection.executemany(
                f"INSERT INTO dataset ({columns}) VALUES ({', '.join('?' for _ in variables)})",
                rows,
            )
        connection.commit()
    source = root / (f"{name}.sas7bdat" if kind == "sas" else f"{name}.tmp")
    source.touch()
    return DatasetHandle(
        source,
        directory / source.name,
        database,
        DatasetMetadata(name.upper(), len(rows), variables),
        len(rows),
        True,
        kind=kind,
    )


class CategoricalConfigurationTests(unittest.TestCase):
    def test_population_contract_preserves_filters_items_and_treatment_types(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_variables = (
                VariableMetadata("USUBJID", "Subject", "character", 20),
                VariableMetadata("TRTA", "Treatment", "character", 40),
                VariableMetadata("RACE", "Race", "character", 40),
                VariableMetadata("PARAMCD", "Parameter", "character", 8),
                VariableMetadata("TRTEMFL", "TE flag", "character", 1),
            )
            population_variables = (
                VariableMetadata("USUBJID", "Subject", "character", 20),
                VariableMetadata("TRT01A", "Treatment", "character", 40),
                VariableMetadata("PARAMCD", "Parameter", "character", 8),
                VariableMetadata("SAFFL", "Safety flag", "character", 1),
            )
            source = make_handle(root, "adae", source_variables)
            population = make_handle(root, "adsl", population_variables)
            numerator_text = 'TRTEMFL = "Y"'
            population_text = 'SAFFL = "Y"'
            config = CategoricalConfig(
                (
                    CategoricalItem(
                        "RACE",
                        "Race category",
                        ("PARAMCD",),
                        include_missing_level=True,
                    ),
                ),
                "TRTA",
                "USUBJID",
                source_filter=FilterEngine(source_variables).compile(numerator_text),
                source_filter_text=numerator_text,
                denominator=DenominatorConfig(
                    "population",
                    population_filter=FilterEngine(population_variables).compile(
                        population_text
                    ),
                    population_filter_text=population_text,
                    population_treatment_variable="TRT01A",
                ),
                percent_digits=2,
            )
            value = build_categorical_configuration(
                source,
                config,
                population,
                [('"Drug A"', "Drug A", "Drug A"), ('"Placebo"', "Placebo", "Placebo")],
            )

            self.assertEqual(value["type"], "categorical_table")
            self.assertEqual(value["version"], 1)
            self.assertEqual(
                set(value),
                {
                    "type",
                    "version",
                    "input",
                    "variables",
                    "numerator",
                    "items",
                    "count",
                    "treatment",
                    "denominator",
                    "total",
                    "sort",
                    "calculation",
                    "display",
                    "output",
                    "targets",
                },
            )
            self.assertEqual(value["numerator"]["filter"]["text"], numerator_text)
            self.assertIsNotNone(value["numerator"]["filter"]["ast"])
            self.assertEqual(value["items"][0]["context_variables"], ["PARAMCD"])
            self.assertTrue(value["items"][0]["missing_level"]["include"])
            self.assertEqual(value["count"]["type"], "distinct_subjects")
            self.assertEqual(value["treatment"]["source_variable"], "TRTA")
            self.assertEqual(
                value["treatment"]["resolved_levels"],
                [
                    {"value": "Drug A", "label": "Drug A"},
                    {"value": "Placebo", "label": "Placebo"},
                ],
            )
            population_block = value["denominator"]["population"]
            self.assertEqual(population_block["treatment_variable"], "TRT01A")
            self.assertEqual(population_block["filter"]["text"], population_text)
            self.assertNotEqual(
                population_block["filter"]["ast"],
                value["numerator"]["filter"]["ast"],
            )
            self.assertEqual(value["display"]["percent_digits"], 2)
            self.assertEqual(value["targets"]["sas"]["population_library"], "pop")

    def test_nonmissing_record_and_n1_contracts_are_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            variables = (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRT"),
                VariableMetadata("RACE"),
                VariableMetadata("PARAMCD"),
                VariableMetadata("AVAL", kind="numeric"),
                VariableMetadata("ABLFL"),
                VariableMetadata("AVISITN", kind="numeric"),
            )
            source = make_handle(root, "adlb", variables)
            empty = FilterEngine(variables).compile("")
            nonmissing = CategoricalConfig(
                (CategoricalItem("RACE"),),
                "TRT",
                "USUBJID",
                count_type="record",
                denominator=DenominatorConfig("nonmissing", "AVAL"),
            )
            nonmissing_json = build_categorical_configuration(source, nonmissing)
            self.assertEqual(nonmissing_json["count"]["type"], "records")
            self.assertEqual(
                nonmissing_json["denominator"],
                {
                    "type": "nonmissing",
                    "analysis_value_variable": "AVAL",
                    "base_filter": "numerator.filter",
                },
            )

            base_text = 'ABLFL = "Y"'
            post_text = 'ABLFL != "Y" and AVISITN > 0'
            n1 = CategoricalConfig(
                (CategoricalItem("RACE", context_variables=("PARAMCD",)),),
                "TRT",
                "USUBJID",
                count_type="record",
                source_filter=empty,
                denominator=DenominatorConfig(
                    "baseline_postbaseline",
                    "AVAL",
                    baseline_filter=FilterEngine(variables).compile(base_text),
                    baseline_filter_text=base_text,
                    postbaseline_filter=FilterEngine(variables).compile(post_text),
                    postbaseline_filter_text=post_text,
                ),
            )
            n1_json = build_categorical_configuration(source, n1)
            self.assertEqual(
                n1_json["denominator"]["eligibility"]["match_variables"],
                "treatment_subject_and_item_context",
            )
            self.assertEqual(
                n1_json["denominator"]["baseline_filter"]["text"], base_text
            )
            self.assertEqual(
                n1_json["denominator"]["postbaseline_filter"]["text"], post_text
            )
            self.assertEqual(
                n1_json["count"]["subject_missing"], "exclude_for_eligibility"
            )

    def test_merge_source_is_labeled_without_a_fake_sas_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            variables = (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRT"),
                VariableMetadata("RACE"),
                VariableMetadata("AVAL", kind="numeric"),
            )
            source = make_handle(root, "merge_result", variables, kind="merge")
            config = CategoricalConfig(
                (CategoricalItem("RACE"),),
                "TRT",
                "USUBJID",
                denominator=DenominatorConfig("nonmissing", "AVAL"),
            )
            value = build_categorical_configuration(source, config)
            self.assertEqual(value["input"]["kind"], "merge")
            self.assertEqual(value["input"]["format"], "merge")
            self.assertIsNone(value["input"]["source_path"])
            self.assertIsNone(value["targets"]["sas"]["source_member"])

    def test_writer_uses_utf8_indentation_and_final_newline(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            variables = (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRT"),
                VariableMetadata("RACE"),
                VariableMetadata("AVAL", kind="numeric"),
            )
            source = make_handle(root, "adae", variables)
            config = CategoricalConfig(
                (CategoricalItem("RACE", label="种族"),),
                "TRT",
                "USUBJID",
                denominator=DenominatorConfig("nonmissing", "AVAL"),
            )
            path = root / "categorical_config.json"
            written = write_categorical_configuration(path, source, config)
            raw = path.read_text(encoding="utf-8")
            self.assertTrue(raw.endswith("\n"))
            self.assertIn("种族", raw)
            self.assertEqual(json.loads(raw), written)
            self.assertEqual(categorical_configuration_json(written), raw)

    def test_engine_writes_json_and_orders_numeric_contexts_and_levels_numerically(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            variables = (
                VariableMetadata("USUBJID"),
                VariableMetadata("TRT"),
                VariableMetadata("LEVEL", kind="numeric"),
                VariableMetadata("VISIT", kind="numeric"),
                VariableMetadata("AVAL", kind="numeric"),
            )
            source = make_handle(
                root,
                "adlb",
                variables,
                (
                    ("S1", "A", 10, 2, 1),
                    ("S2", "A", 2, 2, 1),
                    ("S3", "A", 1, 10, 1),
                ),
            )
            config = CategoricalConfig(
                (CategoricalItem("LEVEL", context_variables=("VISIT",)),),
                "TRT",
                "USUBJID",
                denominator=DenominatorConfig("nonmissing", "AVAL"),
            )
            manager = TempManager(root / "temp")
            result = CategoricalEngine(manager).run(source, config)
            self.assertIsNotNone(result.configuration_path)
            assert result.configuration_path is not None
            self.assertEqual(result.configuration_path.name, "categorical_config.json")
            self.assertTrue(result.configuration_path.exists())
            with closing(sqlite3.connect(result.database_path)) as connection:
                displayed = [
                    row[0]
                    for row in connection.execute(
                        'SELECT "ITEM_LEVEL" FROM dataset ORDER BY _source_row'
                    )
                ]
            self.assertEqual(
                displayed,
                [
                    "LEVEL — VISIT=2",
                    "\u00a0\u00a0\u00a0\u00a02",
                    "\u00a0\u00a0\u00a0\u00a010",
                    "LEVEL — VISIT=10",
                    "\u00a0\u00a0\u00a0\u00a01",
                ],
            )
            result_directory = result.temporary_path.parent
            manager.remove_dataset(result_directory)
            self.assertFalse(result_directory.exists())


if __name__ == "__main__":
    unittest.main()
