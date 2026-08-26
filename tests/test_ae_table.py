from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from clinical_data_viewer.ae_table import AeTableConfig, AeTableDenominator, AeTableEngine, AeTableLongResultBuilder, MissingTreatmentError
from clinical_data_viewer.ae_table.configuration import build_ae_table_configuration
from clinical_data_viewer.ae_table.drilldown import AeTableCell, build_cell_filter
from clinical_data_viewer.domain import DatasetHandle, DatasetMetadata, VariableMetadata
from clinical_data_viewer.filter_engine import FilterEngine
from clinical_data_viewer.temp_manager import TempManager


def handle(root, name, variables, rows):
    directory = root / (name + "-cache"); directory.mkdir()
    db = directory / "dataset.sqlite"
    defs = ", ".join(f'"{v.name}" {"REAL" if v.kind == "numeric" else "TEXT"}' for v in variables)
    with closing(sqlite3.connect(db)) as c:
        c.execute(f"CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, {defs})")
        c.executemany(f"INSERT INTO dataset ({','.join('"'+v.name+'"' for v in variables)}) VALUES ({','.join('?' for _ in variables)})", rows)
        c.execute("CREATE TABLE cache_info (cached_rows INTEGER,total_rows INTEGER,complete INTEGER)")
        c.execute("INSERT INTO cache_info VALUES (?,?,1)", (len(rows), len(rows))); c.commit()
    src = root / f"{name}.sas7bdat"; src.touch()
    return DatasetHandle(src, directory / src.name, db, DatasetMetadata(name.upper(), len(rows), tuple(variables)), len(rows), True)


class AeTableTests(unittest.TestCase):
    def test_missing_treatment_blocks_calculation(self):
        variables = tuple(VariableMetadata(n) for n in ("USUBJID", "TRT", "SOC", "PT"))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = handle(root, "adae", variables, (("S1", None, "GI", "Nausea"),))
            with self.assertRaises(MissingTreatmentError):
                AeTableEngine(TempManager(root / "temp")).run(source, AeTableConfig("SOC", "PT", "TRT"))

    def test_subject_variable_is_fixed_to_usubjid(self):
        variables = tuple(VariableMetadata(n) for n in ("USUBJID", "TRT", "SOC", "PT", "SUBJ"))
        metadata = DatasetMetadata("ADAE", 0, variables)
        with self.assertRaisesRegex(ValueError, "USUBJID"):
            AeTableConfig("SOC", "PT", "TRT", subject_id_variable="SUBJ").validate(metadata)

    def test_uncoded_missing_soc_and_pt_are_hierarchy_levels(self):
        variables = tuple(VariableMetadata(n) for n in ("USUBJID", "TRT", "SOC", "PT"))
        rows = (("S1", "A", None, "Headache"), ("S1", "A", None, "Headache"), ("S2", "A", "GI", None), ("S3", "A", "GI", "Nausea"))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = handle(root, "adae", variables, rows)
            config = AeTableConfig("SOC", "PT", "TRT", hierarchy_missing_policy="uncoded")
            result = AeTableEngine(TempManager(root / "temp")).run(source, config)
            with closing(sqlite3.connect(result.database_path)) as c:
                items = [r[0].replace("\u00a0", "") for r in c.execute('SELECT "ITEM" FROM dataset ORDER BY _source_row')]
                uncoded_soc = c.execute('SELECT "TRT_1" FROM dataset WHERE "ITEM" = ?', ("Uncoded",)).fetchone()[0]
            self.assertEqual(items, ["Any AE", "GI", "Nausea", "Uncoded", "Uncoded", "Headache"])
            self.assertEqual(uncoded_soc, "1 (33.3)")
            configuration = json.loads(result.configuration_path.read_text(encoding="utf-8"))
            self.assertEqual(configuration["hierarchy"]["missing"], {"policy": "uncoded", "label": "Uncoded"})
            self.assertEqual(configuration["resolved_hierarchy"][1]["soc"], "GI")

    def test_uncoded_numerator_drilldown_matches_missing_and_literal(self):
        variables = tuple(VariableMetadata(n) for n in ("USUBJID", "TRT", "SOC", "PT"))
        rows = (("S1", "A", None, "Headache"), ("S2", "A", "Uncoded", "Headache"),
                ("S3", "A", "GI", None), ("S4", "A", "GI", "Uncoded"))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = handle(root, "adae", variables, rows)
            config = AeTableConfig("SOC", "PT", "TRT", hierarchy_missing_policy="uncoded")
            for cell, expected_subjects in (
                (AeTableCell("pt", "Uncoded", "Headache", "A"), {"S1", "S2"}),
                (AeTableCell("pt", "GI", "Uncoded", "A"), {"S3", "S4"}),
            ):
                sql, params = build_cell_filter(source.metadata, config, cell)
                with closing(sqlite3.connect(source.database_path)) as connection:
                    found = {row[0] for row in connection.execute(
                        f'SELECT "USUBJID" FROM dataset WHERE {sql}', params
                    )}
                self.assertEqual(found, expected_subjects)

    def test_denominator_drilldown_never_contains_soc_pt(self):
        source_vars = tuple(VariableMetadata(n) for n in ("USUBJID", "TRT", "SOC", "PT"))
        pop_vars = tuple(VariableMetadata(n) for n in ("USUBJID", "TRT", "SAFFL"))
        source_meta = DatasetMetadata("ADAE", 0, source_vars); pop_meta = DatasetMetadata("ADSL", 0, pop_vars)
        source_filter = FilterEngine(source_vars).compile('TRT = "A"')
        pop_filter = FilterEngine(pop_vars).compile('SAFFL = "Y"')
        for denominator_type, metadata, denominator in (
            ("same_universe", source_meta, AeTableDenominator("same_universe")),
            ("population", pop_meta, AeTableDenominator("population", pop_filter, 'SAFFL = "Y"')),
        ):
            config = AeTableConfig("SOC", "PT", "TRT", dataset_filter=source_filter, dataset_filter_text='TRT = "A"', denominator=denominator)
            normal_cell = AeTableCell("pt", "GI", "Nausea", "A")
            sql, params = build_cell_filter(metadata, config, normal_cell, denominator=True)
            self.assertNotIn('"SOC"', sql)
            self.assertNotIn('"PT"', sql)
            self.assertIn('"TRT"', sql)
            total_cell = AeTableCell("pt", "GI", "Nausea", None)
            sql, _ = build_cell_filter(metadata, config, total_cell, denominator=True)
            # The source Dataset Filter may itself contain TRT; no additional
            # cell-level treatment predicate is added for Total.
            self.assertEqual(sql.count('"TRT" ='), 1 if denominator_type == "same_universe" else 0)

    def test_hierarchy_distinct_counts_and_sorting(self):
        variables = tuple(VariableMetadata(n) for n in ("USUBJID", "TRT", "SOC", "PT"))
        rows = (("S1", "A", "GI", "Nausea"), ("S1", "A", "GI", "Nausea"), ("S1", "A", "GI", "Diarrhoea"), ("S2", "A", "GI", "Nausea"), ("S3", "B", "Neuro", "Headache"), ("S4", "B", "Neuro", "Headache"), ("S4", "B", "Neuro", "Dizziness"), ("S5", "A", None, "Other"), ("", "A", "GI", "Ignored"), ("S6", "A", "GI", None))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = handle(root, "adae", variables, rows)
            source_filter = FilterEngine(variables).compile('TRT in ("A", "B")')
            config = AeTableConfig("SOC", "PT", "TRT", dataset_filter=source_filter, dataset_filter_text='TRT in ("A", "B")')
            result = AeTableEngine(TempManager(root / "temp")).run(source, config)
            with closing(sqlite3.connect(result.database_path)) as c:
                items = [r[0].replace("\u00a0", "") for r in c.execute('SELECT "ITEM" FROM dataset ORDER BY _source_row')]
                gi = c.execute('SELECT "TRT_1" FROM dataset WHERE "ITEM" = ?', ("GI",)).fetchone()[0]
                nausea = c.execute('SELECT "TRT_1" FROM dataset WHERE "ITEM" LIKE ?', ("%Nausea",)).fetchone()[0]
            self.assertEqual(items, ["Any AE", "GI", "Nausea", "Diarrhoea", "Neuro", "Headache", "Dizziness"])
            self.assertEqual(gi, "3 (75.0)")
            self.assertEqual(nausea, "2 (50.0)")
            self.assertEqual(result.configuration_path.name, "ae_table_config.json")
            configuration = json.loads(result.configuration_path.read_text(encoding="utf-8"))
            self.assertEqual(configuration["type"], "ae_soc_pt_table")
            self.assertEqual(configuration["dataset_filter"]["text"], 'TRT in ("A", "B")')
            self.assertEqual(configuration["dataset_filter"]["ast"]["type"], "in")
            self.assertEqual(configuration["resolved_hierarchy"][0]["row_type"], "any")
            self.assertEqual(configuration["hierarchy"]["missing"], {"policy": "exclude", "label": "Uncoded"})

    def test_population_filter_is_independent_and_merge_contract(self):
        vars_ = tuple(VariableMetadata(n) for n in ("USUBJID", "TRT", "SOC", "PT"))
        popvars = tuple(VariableMetadata(n) for n in ("USUBJID", "TRT", "SAFFL"))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = handle(root, "adae", vars_, (("S1", "A", "GI", "Nausea"), ("S2", "B", "GI", "Nausea")))
            pop = handle(root, "adsl", popvars, (("S1", "A", "Y"), ("S2", "B", "N")))
            pf = FilterEngine(popvars).compile('SAFFL = "Y"')
            config = AeTableConfig("SOC", "PT", "TRT", denominator=AeTableDenominator("population", pf, 'SAFFL = "Y"'))
            result = AeTableEngine(TempManager(root / "temp")).run(source, config, pop)
            with closing(sqlite3.connect(result.database_path)) as c:
                self.assertEqual(c.execute('SELECT "TRT_1" FROM dataset WHERE "ITEM" = ?', ("GI",)).fetchone()[0], "1 (100.0)")
            merge = DatasetHandle(source.source_path, source.temporary_path, source.database_path, source.metadata, source.cached_row_count, True, kind="merge")
            cfg = build_ae_table_configuration(merge, AeTableConfig("SOC", "PT", "TRT"), resolved_treatment_levels=[])
            self.assertEqual((cfg["input"]["kind"], cfg["input"]["format"]), ("merge", "merge"))

    def test_total_recomputes_distinct_subjects_not_arm_sum(self):
        variables = tuple(VariableMetadata(n) for n in ("USUBJID", "TRT", "SOC", "PT"))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = handle(root, "adae", variables, (("S1", "A", "GI", "Nausea"), ("S1", "B", "GI", "Nausea"), ("S2", "A", "GI", "Nausea")))
            result = AeTableEngine(TempManager(root / "temp")).run(source, AeTableConfig("SOC", "PT", "TRT"))
            with closing(sqlite3.connect(result.database_path)) as c:
                row = c.execute('SELECT "TRT_1", "TRT_2", "TOTAL" FROM dataset WHERE "ITEM" = ?', ("Any AE",)).fetchone()
            self.assertEqual(row, ("2 (100.0)", "1 (100.0)", "2 (100.0)"))

    def test_optional_any_total_and_decimal_display(self):
        variables = tuple(VariableMetadata(n) for n in ("USUBJID", "TRT", "SOC", "PT"))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = handle(root, "adae", variables, (("S1", "A", "GI", "Nausea"), ("S2", "A", "GI", "Nausea"), ("S3", "A", "GI", "Other")))
            result = AeTableEngine(TempManager(root / "temp")).run(source, AeTableConfig("SOC", "PT", "TRT", include_any_ae=False, include_total=False, percent_digits=2))
            with closing(sqlite3.connect(result.database_path)) as c:
                columns = [r[1] for r in c.execute("PRAGMA table_info(dataset)")]
                item = c.execute('SELECT "ITEM", "TRT_1" FROM dataset ORDER BY _source_row LIMIT 1').fetchone()
            self.assertEqual(columns, ["_source_row", "ITEM", "TRT_1"])
            self.assertEqual(item, ("GI", "3 (100.00)"))

    def test_numeric_treatment_and_long_result_order(self):
        variables = (VariableMetadata("USUBJID"), VariableMetadata("TRT", kind="numeric"), VariableMetadata("SOC"), VariableMetadata("PT"))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); source = handle(root, "adae", variables, (("S1", 2, "GI", "Nausea"), ("S2", 1, "GI", "Nausea")))
            manager = TempManager(root / "temp"); result = AeTableEngine(manager).run(source, AeTableConfig("SOC", "PT", "TRT"))
            config = json.loads(result.configuration_path.read_text(encoding="utf-8"))
            self.assertEqual([level["value"] for level in config["treatment"]["resolved_levels"]], [1, 2])
            long_result = AeTableLongResultBuilder(manager).run(result, source)
            with closing(sqlite3.connect(long_result.database_path)) as c:
                orders = c.execute("SELECT ROW_ORDER, TRT_ORDER, TRT FROM dataset ORDER BY ROW_ORDER, TRT_ORDER").fetchall()
            self.assertEqual(orders[:3], [(1, 1, "1"), (1, 2, "2"), (1, 3, "Total")])

    def test_merge_source_runs_ae_table_engine(self):
        variables = tuple(VariableMetadata(n) for n in ("USUBJID", "TRT", "SOC", "PT"))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); base = handle(root, "merged", variables, (("S1", "A", "GI", "Nausea"),))
            merge = DatasetHandle(base.source_path, base.temporary_path, base.database_path, base.metadata, base.cached_row_count, True, kind="merge")
            result = AeTableEngine(TempManager(root / "temp")).run(merge, AeTableConfig("SOC", "PT", "TRT"))
            self.assertEqual(result.kind, "ae_table")
            config = json.loads(result.configuration_path.read_text(encoding="utf-8"))
            self.assertEqual((config["input"]["kind"], config["input"]["format"]), ("merge", "merge"))


if __name__ == "__main__":
    unittest.main()
