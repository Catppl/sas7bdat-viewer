from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from clinical_data_viewer.ae_table import AeTableConfig, AeTableDenominator, AeTableEngine, MissingTreatmentError
from clinical_data_viewer.ae_table.configuration import build_ae_table_configuration
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


if __name__ == "__main__":
    unittest.main()
