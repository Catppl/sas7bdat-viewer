from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from ..domain import DatasetHandle, DatasetMetadata, VariableMetadata
from ..filter_engine import quote_identifier
from ..temp_manager import TempManager


def _format(freq: int, denom: int, digits: int) -> str:
    if not denom: return "0 (—)"
    pct = (Decimal(freq) * 100 / Decimal(denom)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP)
    return f"{freq} ({pct:.{digits}f})"


class AeTableResultWriter:
    def __init__(self, database_path: Path, levels, config):
        self.database_path, self.config, self.levels = database_path, config, levels
        self.treatment_columns = [(key, f"TRT_{i}", label) for i, (key, _v, label) in enumerate(levels, 1)]
        if config.include_total: self.treatment_columns.append((None, "TOTAL", "Total"))
        self.variables = (VariableMetadata("ITEM", "Item / Level", "character"),) + tuple(VariableMetadata(c, f"{l} n (%)", "character") for _k, c, l in self.treatment_columns)
        self.connection = sqlite3.connect(database_path)
        defs = ", ".join(f"{quote_identifier(v.name)} TEXT" for v in self.variables)
        self.connection.execute(f"CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, {defs})")
        self.connection.execute("CREATE TABLE ae_table_cell_map (result_row INTEGER, column_name TEXT, row_type TEXT, soc_json TEXT, pt_json TEXT, treatment_json TEXT, PRIMARY KEY(result_row,column_name))")
        self.connection.execute("CREATE TABLE ae_table_long (row_order INTEGER, row_type TEXT, soc_json TEXT, pt_json TEXT, item TEXT, indent INTEGER, trt_json TEXT, freq INTEGER, denom INTEGER, pct REAL, dataset_filter TEXT, denominator_type TEXT, count_type TEXT, count_variable TEXT)")
        self.row_count = 0

    def add_row(self, row, counts, denom, levels, filter_text, denominator_type):
        values = ["\u00a0" * (row["indent"] * 4) + row["item"]]
        for key, _col, _label in self.treatment_columns:
            freq = len(counts.get(key, ())) if key is not None else len(counts.get("__ae_total__", ()))
            dn = denom.get(key if key is not None else "__ae_total__", 0)
            values.append(_format(freq, dn, self.config.percent_digits))
        cols = [v.name for v in self.variables]
        self.connection.execute(f"INSERT INTO dataset ({','.join(quote_identifier(c) for c in cols)}) VALUES ({','.join('?' for _ in cols)})", values)
        self.row_count += 1; result_row = self.row_count
        for key, col, _label in self.treatment_columns:
            freq = len(counts.get(key, ())) if key is not None else len(counts.get("__ae_total__", ()))
            dn = denom.get(key if key is not None else "__ae_total__", 0)
            tjson = None if key is None else key
            self.connection.execute("INSERT INTO ae_table_cell_map VALUES (?,?,?,?,?,?)", (result_row, col, row["row_type"], json.dumps(row["soc"], ensure_ascii=False), json.dumps(row["pt"], ensure_ascii=False), tjson))
            self.connection.execute("INSERT INTO ae_table_long VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (result_row, row["row_type"], json.dumps(row["soc"], ensure_ascii=False), json.dumps(row["pt"], ensure_ascii=False), row["item"], row["indent"], tjson, freq, dn, freq * 100.0 / dn if dn else None, filter_text, denominator_type, "distinct", "USUBJID"))

    def finish(self, directory, source):
        self.connection.execute("CREATE TABLE cache_info (cached_rows INTEGER,total_rows INTEGER,complete INTEGER)")
        self.connection.execute("INSERT INTO cache_info VALUES (?,?,1)", (self.row_count, self.row_count)); self.connection.commit(); self.connection.close()
        marker = directory / "ae-table-result.tmp"; marker.touch()
        return DatasetHandle(source.source_path.parent / f"AE Table - {source.metadata.name}", marker, self.database_path,
            DatasetMetadata(f"AE Table - {source.metadata.name}", self.row_count, self.variables,
                            display_column_names=tuple((v.name, "Item / Level" if v.name == "ITEM" else v.label) for v in self.variables), categorical_item_level_column="ITEM"), self.row_count, True, kind="ae_table", display_source=f"AE Table from {source.source_path}")

    def abort(self, manager, directory):
        self.connection.close(); manager.remove_dataset(directory)


class AeTableLongResultBuilder:
    def __init__(self, temp_manager): self.temp_manager = temp_manager

    def run(self, result, source):
        directory = self.temp_manager.create_dataset_directory(); database = directory / "dataset.sqlite"
        variables = tuple(VariableMetadata(name, name.replace("_", " ").title(), "numeric" if name in {"ROW_ORDER","INDENT","FREQ","DENOM","PCT"} else "character") for name in ("ROW_ORDER","ROW_TYPE","SOC","PT","ITEM","INDENT","TRT","FREQ","DENOM","PCT","DATASET_FILTER","DENOMINATOR_TYPE","COUNT_TYPE","COUNT_VARIABLE"))
        try:
            with closing(sqlite3.connect(database)) as target, closing(sqlite3.connect(result.database_path.resolve().as_uri()+"?mode=ro", uri=True)) as src:
                target.execute("CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, " + ",".join(f'{quote_identifier(v.name)} {"REAL" if v.kind == "numeric" else "TEXT"}' for v in variables) + ")")
                rows = src.execute("SELECT row_order,row_type,soc_json,pt_json,item,indent,trt_json,freq,denom,pct,dataset_filter,denominator_type,count_type,count_variable FROM ae_table_long ORDER BY row_order").fetchall()
                for row in rows:
                    values = list(row); values[2] = None if values[2] == "null" else str(json.loads(values[2])); values[3] = None if values[3] == "null" else str(json.loads(values[3])); values[6] = "Total" if values[6] is None else str(json.loads(values[6]))
                    target.execute("INSERT INTO dataset VALUES (" + ",".join("?" for _ in range(len(values)+1)) + ")", [None, *values])
                target.execute("CREATE TABLE cache_info (cached_rows INTEGER,total_rows INTEGER,complete INTEGER)"); target.execute("INSERT INTO cache_info VALUES (?,?,1)",(len(rows),len(rows))); target.commit()
            marker = directory / "ae-table-long-result.tmp"; marker.touch()
            return DatasetHandle(source.source_path.parent / f"AE Table Long - {source.metadata.name}", marker, database, DatasetMetadata(f"AE Table Long - {source.metadata.name}", len(rows), variables), len(rows), True, kind="ae_table_long", display_source=f"AE Table long result from {source.source_path}")
        except BaseException:
            self.temp_manager.remove_dataset(directory); raise
