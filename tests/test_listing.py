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
from clinical_data_viewer.listing import (
    ListingColumn,
    ListingConfig,
    ListingEngine,
    ListingMergeAdsl,
)
from clinical_data_viewer.temp_manager import TempManager


def handle(root: Path, name: str, variables, rows) -> DatasetHandle:
    directory = root / f"{name}-cache"
    directory.mkdir()
    database = directory / "dataset.sqlite"
    definitions = ", ".join(
        f'"{variable.name}" {"REAL" if variable.kind == "numeric" else "TEXT"}'
        for variable in variables
    )
    names = ", ".join(f'"{variable.name}"' for variable in variables)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            f"CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, {definitions})"
        )
        connection.executemany(
            f"INSERT INTO dataset ({names}) VALUES ({','.join('?' for _ in variables)})",
            rows,
        )
        connection.execute(
            "CREATE TABLE cache_info (cached_rows INTEGER,total_rows INTEGER,complete INTEGER)"
        )
        connection.execute(
            "INSERT INTO cache_info VALUES (?,?,1)", (len(rows), len(rows))
        )
        connection.commit()
    metadata = DatasetMetadata(name.upper(), len(rows), tuple(variables))
    marker = directory / "source.tmp"
    marker.touch()
    return DatasetHandle(
        directory / f"{name}.sas7bdat", marker, database, metadata, len(rows), True
    )


class ListingEngineTests(unittest.TestCase):
    def test_visible_character_hidden_numeric_sort_and_json(self):
        variables = (
            VariableMetadata("USUBJID", "Subject", "character", 20),
            VariableMetadata("ADY", "Study Day", "numeric"),
            VariableMetadata("AESTDTC", "Start", "character", 10),
        )
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            source = handle(
                root,
                "adae",
                variables,
                (("02", 2, "2022-01-02"), ("01", 10, "2022-01-01")),
            )
            config = ListingConfig(
                (
                    ListingColumn(
                        "USUBJID",
                        "USUBJID",
                        "Subject",
                        sort_order=1,
                        report_type="ORDER",
                    ),
                    ListingColumn(
                        "AESTDTC || ' / (' || ADY || ')'", "AESTDY", "Start / Day"
                    ),
                    ListingColumn(
                        "ADY", "ADY", "", sort_order=2, include_in_report=False
                    ),
                )
            )
            result = ListingEngine(TempManager(root / "temp")).run(source, config)
            self.assertEqual(
                [
                    (item.name, item.kind, item.length)
                    for item in result.metadata.variables
                ],
                [
                    ("USUBJID", "character", 20),
                    ("AESTDY", "character", 47),
                    ("ADY", "numeric", None),
                ],
            )
            with closing(sqlite3.connect(result.database_path)) as connection:
                rows = connection.execute(
                    'SELECT "USUBJID","AESTDY","ADY" FROM dataset ORDER BY _source_row'
                ).fetchall()
            self.assertEqual(
                rows, [("01", "2022-01-01 / (10)", 10), ("02", "2022-01-02 / (2)", 2)]
            )
            configuration = json.loads(
                result.configuration_path.read_text(encoding="utf-8")
            )
            self.assertEqual(configuration["type"], "listing")
            self.assertEqual(
                configuration["columns"][1]["expression"]["ast"]["type"], "concat"
            )
            self.assertEqual(
                configuration["calculation"]["output_type"], "expression_inferred"
            )
            self.assertNotIn(
                "visible_output_type", configuration["calculation"]
            )

    def test_adsl_left_merge_filter_duplicate_rename_and_missing_keys(self):
        source_vars = (
            VariableMetadata("USUBJID"),
            VariableMetadata("AGE", kind="numeric"),
            VariableMetadata("TERM"),
        )
        adsl_vars = (
            VariableMetadata("USUBJID"),
            VariableMetadata("AGE", kind="numeric"),
            VariableMetadata("SAFFL"),
        )
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            source = handle(
                root,
                "adae",
                source_vars,
                (("01", 9, "A"), ("", 8, "B"), ("02", 7, "C")),
            )
            adsl = handle(
                root,
                "adsl",
                adsl_vars,
                (("01", 40, "Y"), (None, 50, "Y"), ("02", 60, "N")),
            )
            merge = ListingMergeAdsl(
                True, "USUBJID", ("AGE", "SAFFL"), (), "rename", (("AGE", "AGE_ADSL"),)
            )
            config = ListingConfig(
                (
                    ListingColumn("TERM", "ITEM", "Event"),
                    ListingColumn("AGE_ADSL", "AGE_ADSL", include_in_report=False),
                ),
                FilterEngine(
                    (
                        VariableMetadata("USUBJID"),
                        VariableMetadata("AGE", kind="numeric"),
                        VariableMetadata("TERM"),
                        VariableMetadata("AGE_ADSL", kind="numeric"),
                        VariableMetadata("SAFFL"),
                    )
                ).compile('SAFFL = "Y"'),
                'SAFFL = "Y"',
                merge,
            )
            engine = ListingEngine(TempManager(root / "temp"))
            warnings = engine.warnings(source, config, adsl)
            self.assertTrue(any("missing USUBJID" in message for message in warnings))
            result = engine.run(source, config, adsl)
            with closing(sqlite3.connect(result.database_path)) as connection:
                rows = connection.execute(
                    'SELECT "ITEM","AGE_ADSL" FROM dataset'
                ).fetchall()
            self.assertEqual(rows, [("A", 40)])

    def test_duplicate_adsl_key_blocks_and_division_post_process_is_missing(self):
        variables = (
            VariableMetadata("USUBJID"),
            VariableMetadata("NUM", kind="numeric"),
            VariableMetadata("DEN", kind="numeric"),
        )
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            source = handle(root, "adae", variables, (("01", 1, 0),))
            config = ListingConfig(
                (ListingColumn("NUM / DEN", "RATIO", division_by_zero_missing=True),)
            )
            result = ListingEngine(TempManager(root / "temp")).run(source, config)
            with closing(sqlite3.connect(result.database_path)) as connection:
                self.assertEqual(
                    connection.execute('SELECT "RATIO" FROM dataset').fetchone()[0], None
                )

    def test_duplicate_adsl_key_blocks_a_left_merge(self):
        source_vars = (VariableMetadata("USUBJID"), VariableMetadata("TERM"))
        adsl_vars = (VariableMetadata("USUBJID"), VariableMetadata("SAFFL"))
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            source = handle(root, "adae", source_vars, (("01", "A"),))
            adsl = handle(root, "adsl", adsl_vars, (("01", "Y"), ("01", "N")))
            config = ListingConfig(
                (ListingColumn("TERM", "ITEM", "Event"),),
                merge_adsl=ListingMergeAdsl(True, "USUBJID", ("SAFFL",)),
            )
            with self.assertRaisesRegex(ValueError, "not unique"):
                ListingEngine(TempManager(root / "temp")).run(source, config, adsl)

    def test_expression_precedence_and_metadata_driven_numeric_concat(self):
        variables = (
            VariableMetadata("USUBJID"),
            VariableMetadata("ADT", kind="numeric", format="DATE9."),
            VariableMetadata("NUM", kind="numeric"),
        )
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            source = handle(root, "adae", variables, (("01", 0, 2),))
            config = ListingConfig(
                (
                    ListingColumn("ADT || ' / ' || NUM", "ITEM", "Event"),
                    ListingColumn("NUM + 2 * NUM", "CALC", "Calculation"),
                )
            )
            result = ListingEngine(TempManager(root / "temp")).run(source, config)
            with closing(sqlite3.connect(result.database_path)) as connection:
                row = connection.execute(
                    'SELECT "ITEM", "CALC" FROM dataset'
                ).fetchone()
            self.assertEqual(row, ("01JAN1960 / 2", 6.0))

    def test_input_date_and_output_format_keep_a_numeric_internal_value(self):
        variables = (VariableMetadata("USUBJID"), VariableMetadata("AESTDTC"))
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            source = handle(root, "adae", variables, (("01", "2022-02-03"),))
            config = ListingConfig(
                (
                    ListingColumn(
                        "INPUT(AESTDTC, E8601DA.)",
                        "AESTDT",
                        "Start Date",
                        "DATE9.",
                    ),
                )
            )
            result = ListingEngine(TempManager(root / "temp")).run(source, config)
            with closing(sqlite3.connect(result.database_path)) as connection:
                raw_value = connection.execute(
                    'SELECT "AESTDT" FROM dataset'
                ).fetchone()[0]
            self.assertEqual(raw_value, 22_679.0)
            self.assertEqual(result.metadata.variables[0].kind, "numeric")
            self.assertEqual(result.metadata.variables[0].format, "DATE9.")

    def test_put_numeric_format_infers_character_length_without_coercing_source(self):
        variables = (
            VariableMetadata("USUBJID"),
            VariableMetadata("AVAL", kind="numeric"),
        )
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            source = handle(root, "adlb", variables, (("01", 12.5),))
            config = ListingConfig(
                (ListingColumn("PUT(AVAL, 8.2)", "AVAL_TEXT"),)
            )
            result = ListingEngine(TempManager(root / "temp")).run(source, config)
            with closing(sqlite3.connect(result.database_path)) as connection:
                value = connection.execute('SELECT "AVAL_TEXT" FROM dataset').fetchone()[0]
            self.assertEqual(value, "12.50")
            self.assertEqual(result.metadata.variables[0].kind, "character")
            self.assertEqual(result.metadata.variables[0].length, 8)

    def test_no_sort_warning_and_source_order_are_stable(self):
        variables = (VariableMetadata("USUBJID"),)
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            source = handle(root, "adae", variables, (("02",), ("01",)))
            config = ListingConfig((ListingColumn("USUBJID", "SUBJECT"),))
            engine = ListingEngine(TempManager(root / "temp"))
            self.assertTrue(
                any(
                    "No Sort Order" in warning
                    for warning in engine.warnings(source, config)
                )
            )
            result = engine.run(source, config)
            with closing(sqlite3.connect(result.database_path)) as connection:
                rows = connection.execute('SELECT "SUBJECT" FROM dataset').fetchall()
            self.assertEqual(rows, [("02",), ("01",)])

    def test_merge_result_is_allowed_for_python_listing_and_marked_in_json(self):
        variables = (VariableMetadata("USUBJID"), VariableMetadata("TERM"))
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            source = replace(
                handle(root, "merge-result", variables, (("01", "Headache"),)),
                kind="merge",
            )
            result = ListingEngine(TempManager(root / "temp")).run(
                source, ListingConfig((ListingColumn("TERM", "ITEM", "Event"),))
            )
            configuration = json.loads(
                result.configuration_path.read_text(encoding="utf-8")
            )
            self.assertEqual(configuration["input"]["kind"], "merge")
            self.assertEqual(configuration["input"]["format"], "merge")

    def test_keep_drop_conflict_is_rejected(self):
        config = ListingConfig(
            (ListingColumn("USUBJID", "USUBJID"),),
            merge_adsl=ListingMergeAdsl(True, keep=("AGE",), drop=("SEX",)),
        )
        with self.assertRaisesRegex(ValueError, "either Keep"):
            config.validate_basic()

    def test_reserved_internal_output_prefix_is_rejected(self):
        config = ListingConfig((ListingColumn("USUBJID", "_lst_out1"),))
        with self.assertRaisesRegex(ValueError, "reserved prefix"):
            config.validate_basic()

    def test_reserved_internal_output_names_are_rejected(self):
        for name in ("_source_row", "_listing_row", "_SOURCE_ROW"):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "reserved"):
                ListingConfig((ListingColumn("USUBJID", name),)).validate_basic()

    def test_result_writer_rejects_reserved_output_names(self):
        from clinical_data_viewer.listing.result_store import ListingResultWriter

        with tempfile.TemporaryDirectory() as path:
            database = Path(path) / "dataset.sqlite"
            with self.assertRaisesRegex(ValueError, "reserved"):
                ListingResultWriter(
                    database, (VariableMetadata("_source_row"),)
                )
            self.assertFalse(database.exists())

    def test_adsl_reserved_rename_target_is_rejected(self):
        variables = (VariableMetadata("USUBJID"), VariableMetadata("AGE", kind="numeric"))
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            source = handle(root, "adae", variables, (("01", 30),))
            adsl = handle(root, "adsl", variables, (("01", 40),))
            config = ListingConfig(
                (ListingColumn("AGE_ADSL", "AGE_ADSL"),),
                merge_adsl=ListingMergeAdsl(
                    True,
                    "USUBJID",
                    ("AGE",),
                    (),
                    "rename",
                    (("AGE", "_listing_row"),),
                ),
            )
            with self.assertRaisesRegex(ValueError, "reserved"):
                ListingEngine(TempManager(root / "temp")).resolved_metadata(
                    source, config, adsl
                )

    def test_character_sort_uses_raw_case_sensitive_values(self):
        variables = (VariableMetadata("VALUE", length=10),)
        with tempfile.TemporaryDirectory() as path:
            root = Path(path)
            source = handle(root, "adae", variables, (("a",), ("B",), ("b",), ("A",)))
            config = ListingConfig(
                (ListingColumn("VALUE", "VALUE", sort_order=1),)
            )
            result = ListingEngine(TempManager(root / "temp")).run(source, config)
            with closing(sqlite3.connect(result.database_path)) as connection:
                values = connection.execute('SELECT "VALUE" FROM dataset').fetchall()
            self.assertEqual(values, [("A",), ("B",), ("a",), ("b",)])
