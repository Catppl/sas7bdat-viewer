from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from ..domain import DatasetHandle, DatasetMetadata, VariableMetadata
from ..filter_engine import quote_identifier
from ..temp_manager import TempManager
from .models import RuleBasedConfig, RuleBasedRow

TOTAL_KEY = "__rule_based_total__"


def _unique(base: str, used: set[str]) -> str:
    value = base
    suffix = 2
    while value.casefold() in used:
        value = f"{base}_{suffix}"
        suffix += 1
    used.add(value.casefold())
    return value


def _format_cell(freq: int, denom: int, digits: int) -> str:
    if denom == 0:
        return "0 (—)"
    percent = (Decimal(freq) * Decimal(100) / Decimal(denom)).quantize(
        Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP
    )
    return f"{freq} ({percent:.{digits}f})"


def _value_from_key(key: str | None) -> object | None:
    if key is None:
        return None
    return json.loads(key)


class RuleBasedResultWriter:
    def __init__(
        self,
        database_path: Path,
        treatment_levels: list[tuple[str, object, str]],
        config: RuleBasedConfig,
    ) -> None:
        self.database_path = database_path
        self.config = config
        used: set[str] = set()
        self.item_column = _unique("ITEM", used)
        self.treatment_columns: list[tuple[str | None, str, str]] = []
        for position, (key, _value, label) in enumerate(treatment_levels, start=1):
            self.treatment_columns.append((key, _unique(f"TRT_{position}", used), label))
        if config.include_total:
            self.treatment_columns.append((None, _unique("TOTAL", used), "Total"))
        self.variables = (
            VariableMetadata(self.item_column, "Rule-based item", "character"),
            *(
                VariableMetadata(column, f"{label} n (%)", "character")
                for _key, column, label in self.treatment_columns
            ),
        )
        self.connection = sqlite3.connect(database_path)
        definitions = ", ".join(
            f"{quote_identifier(variable.name)} TEXT" for variable in self.variables
        )
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(
            "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, " + definitions + ")"
        )
        self.connection.execute(
            "CREATE TABLE rule_based_cell_map ("
            "result_row INTEGER NOT NULL, column_name TEXT NOT NULL, "
            "row_id TEXT NOT NULL, treatment_json TEXT, "
            "PRIMARY KEY (result_row, column_name))"
        )
        self.connection.execute(
            "CREATE TABLE rule_based_long ("
            "row_id TEXT NOT NULL, item TEXT NOT NULL, filter_text TEXT NOT NULL, "
            "indent INTEGER NOT NULL, treatment_json TEXT, freq INTEGER NOT NULL, "
            "denom INTEGER NOT NULL, pct REAL, dataset_filter TEXT NOT NULL, "
            "denominator_type TEXT NOT NULL, count_type TEXT NOT NULL, "
            "count_variable TEXT NOT NULL)"
        )
        self.row_count = 0

    def add_row(
        self,
        row: RuleBasedRow,
        numerator: dict[str, int],
        denominator: dict[str, int],
        treatments: list[tuple[str, object, str]],
    ) -> None:
        all_treatments = [key for key, _value, _label in treatments]
        if self.config.include_total:
            all_treatments.append(None)
        cells: list[str] = []
        display_item = "\u00a0" * (row.indent * 4) + row.item
        for treatment_key in all_treatments:
            if treatment_key is None:
                freq = numerator.get(TOTAL_KEY, sum(numerator.values()))
                denom = denominator.get(TOTAL_KEY, sum(denominator.values()))
            else:
                freq = numerator.get(treatment_key, 0)
                denom = denominator.get(treatment_key, 0)
            cells.append(_format_cell(freq, denom, self.config.percent_digits))
        values = [display_item, *cells]
        columns = [variable.name for variable in self.variables]
        self.connection.execute(
            "INSERT INTO dataset ("
            + ", ".join(quote_identifier(column) for column in columns)
            + ") VALUES ("
            + ", ".join("?" for _column in columns)
            + ")",
            values,
        )
        self.row_count += 1
        result_row = int(self.connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        for treatment_key, column, _label in self.treatment_columns:
            if treatment_key is None:
                freq = numerator.get(TOTAL_KEY, sum(numerator.values()))
                denom = denominator.get(TOTAL_KEY, sum(denominator.values()))
            else:
                freq = numerator.get(treatment_key, 0)
                denom = denominator.get(treatment_key, 0)
            treatment_json = json.dumps(
                _value_from_key(treatment_key),
                ensure_ascii=False,
                separators=(",", ":"),
            )
            self.connection.execute(
                "INSERT INTO rule_based_cell_map VALUES (?, ?, ?, ?)",
                (result_row, column, row.row_id, treatment_json),
            )
            pct = (freq * 100.0 / denom) if denom else None
            self.connection.execute(
                "INSERT INTO rule_based_long VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    row.row_id,
                    row.item,
                    row.row_filter_text,
                    row.indent,
                    treatment_json,
                    freq,
                    denom,
                    pct,
                    self.config.dataset_filter_text,
                    self.config.denominator.type,
                    "distinct",
                    self.config.subject_id_variable,
                ),
            )
        if self.row_count % 500 == 0:
            self.connection.commit()

    def finish(self, directory: Path, source: DatasetHandle) -> DatasetHandle:
        self.connection.execute(
            "CREATE TABLE cache_info (cached_rows INTEGER NOT NULL, total_rows INTEGER, complete INTEGER NOT NULL)"
        )
        self.connection.execute(
            "INSERT INTO cache_info VALUES (?, ?, 1)", (self.row_count, self.row_count)
        )
        self.connection.commit()
        self.connection.close()
        marker = directory / "rule-based-result.tmp"
        marker.touch()
        return DatasetHandle(
            source.source_path.parent / f"Rule-based Table - {source.metadata.name}",
            marker,
            self.database_path,
            DatasetMetadata(
                f"Rule-based Table - {source.metadata.name}",
                self.row_count,
                self.variables,
                display_column_names=tuple(
                    [
                        (self.item_column, "Item"),
                        *(
                            (column, f"{label} n (%)")
                            for _key, column, label in self.treatment_columns
                        ),
                    ]
                ),
            ),
            self.row_count,
            True,
            kind="rule_based",
            display_source=f"Rule-based Table from {source.source_path}",
        )

    def abort(self, manager: TempManager, directory: Path) -> None:
        self.connection.close()
        manager.remove_dataset(directory)


class RuleBasedLongResultBuilder:
    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    def run(self, result: DatasetHandle) -> DatasetHandle:
        directory = self.temp_manager.create_dataset_directory()
        database = directory / "dataset.sqlite"
        variables = (
            VariableMetadata("ROW_ID", "Rule row ID", "character"),
            VariableMetadata("ITEM", "Item", "character"),
            VariableMetadata("FILTER", "Row filter", "character"),
            VariableMetadata("INDENT", "Indent", "numeric"),
            VariableMetadata("TRT", "Treatment", "character"),
            VariableMetadata("FREQ", "Frequency", "numeric"),
            VariableMetadata("DENOM", "Denominator", "numeric"),
            VariableMetadata("PCT", "Percent", "numeric"),
            VariableMetadata("DATASET_FILTER", "Dataset filter", "character"),
            VariableMetadata("DENOMINATOR", "Denominator type", "character"),
            VariableMetadata("COUNT_TYPE", "Count type", "character"),
            VariableMetadata("COUNT_VARIABLE", "Count variable", "character"),
        )
        target: sqlite3.Connection | None = None
        try:
            target = sqlite3.connect(database)
            definitions = ", ".join(
                f"{quote_identifier(variable.name)} {'REAL' if variable.kind == 'numeric' else 'TEXT'}"
                for variable in variables
            )
            target.execute(
                "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, " + definitions + ")"
            )
            with closing(sqlite3.connect(result.database_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
                rows = connection.execute(
                    "SELECT row_id, item, filter_text, indent, treatment_json, freq, denom, pct, "
                    "dataset_filter, denominator_type, count_type, count_variable "
                    "FROM rule_based_long ORDER BY rowid"
                ).fetchall()
            target.executemany(
                "INSERT INTO dataset ("
                + ", ".join(quote_identifier(variable.name) for variable in variables)
                + ") VALUES ("
                + ", ".join("?" for _variable in variables)
                + ")",
                [
                    (
                        row[0],
                        row[1],
                        row[2],
                        row[3],
                        "(Missing)" if json.loads(row[4]) is None else str(json.loads(row[4])),
                        row[5], row[6], row[7], row[8], row[9], row[10], row[11],
                    )
                    for row in rows
                ],
            )
            target.execute(
                "CREATE TABLE cache_info (cached_rows INTEGER NOT NULL, total_rows INTEGER, complete INTEGER NOT NULL)"
            )
            target.execute("INSERT INTO cache_info VALUES (?, ?, 1)", (len(rows), len(rows)))
            target.commit()
            target.close()
            target = None
            marker = directory / "rule-based-long-result.tmp"
            marker.touch()
            return DatasetHandle(
                result.source_path.parent / f"Rule-based Long - {result.metadata.name}",
                marker,
                database,
                DatasetMetadata(f"Rule-based Long - {result.metadata.name}", len(rows), variables),
                len(rows), True, kind="rule_based_long",
                display_source=f"Rule-based long result from {result.source_path}",
            )
        except BaseException:
            if target is not None:
                target.close()
            self.temp_manager.remove_dataset(directory)
            raise
