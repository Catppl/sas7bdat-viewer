from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from ..domain import DatasetHandle, DatasetMetadata, VariableMetadata
from ..filter_engine import quote_identifier
from ..temp_manager import TempManager

LONG_VARIABLES = (
    VariableMetadata("ROW_ORDER", "Row order", "numeric"),
    VariableMetadata("ITEM_ORDER", "Item order", "numeric"),
    VariableMetadata("ITEM_KIND", "Item type"),
    VariableMetadata("VARIABLE", "Source variable"),
    VariableMetadata("ITEM_LABEL", "Item label"),
    VariableMetadata("UNIT", "Unit"),
    VariableMetadata("LEVEL_JSON", "Raw level JSON"),
    VariableMetadata("STATISTIC_KEY", "Statistic key"),
    VariableMetadata("RAW_VALUE_1", "Raw value 1", "numeric"),
    VariableMetadata("RAW_VALUE_2", "Raw value 2", "numeric"),
    VariableMetadata("NUMERATOR", "Numerator", "numeric"),
    VariableMetadata("DENOMINATOR", "Denominator", "numeric"),
    VariableMetadata("PERCENT", "Percent", "numeric"),
    VariableMetadata("DISPLAY_VALUE", "Display value"),
)


class BaselineCharacteristicsResultWriter:
    def __init__(
        self,
        database_path: Path,
        header_label: str,
        header_n: int,
        columns: tuple[tuple[str, str, int], ...] | None = None,
    ) -> None:
        self.database_path = database_path
        self.connection = sqlite3.connect(database_path)
        self.row_count = 0
        self.column_specs = columns or (("VALUE", f"{header_label}\n(N={header_n:,})", header_n),)
        self.variables = (VariableMetadata("ROW_LABEL", "Characteristic"),) + tuple(
            VariableMetadata(name, label) for name, label, _n in self.column_specs
        )
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(
            "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "
            + quote_identifier("ROW_LABEL") + " TEXT, "
            + ', '.join(f'{quote_identifier(name)} TEXT' for name, _label, _n in self.column_specs)
            + ")"
        )
        self.connection.execute(
            "CREATE TABLE baseline_characteristics_long ("
            "row_order INTEGER PRIMARY KEY, item_order INTEGER NOT NULL, "
            "item_kind TEXT NOT NULL, variable TEXT NOT NULL, item_label TEXT NOT NULL, "
            "unit TEXT NOT NULL, level_json TEXT, statistic_key TEXT, "
            "raw_value_1 REAL, raw_value_2 REAL, numerator INTEGER, denominator INTEGER, "
            "percent REAL, display_value TEXT NOT NULL)"
        )
        self.connection.execute(
            "CREATE TABLE baseline_characteristics_cell_map ("
            "result_row INTEGER NOT NULL, column_name TEXT NOT NULL, "
            "item_kind TEXT NOT NULL, variable TEXT NOT NULL, "
            "level_json TEXT, statistic_key TEXT, treatment_level_json TEXT, "
            "PRIMARY KEY (result_row, column_name))"
        )

    def add_row(
        self,
        *,
        item_order: int,
        item_kind: str,
        variable: str,
        item_label: str,
        row_label: str,
        display_value: str = "",
        display_values: dict[str, str] | None = None,
        unit: str = "",
        level_json: str | None = None,
        statistic_key: str | None = None,
        treatment_level_json: str | None = None,
        treatment_levels: dict[str, str | None] | None = None,
        raw_value_1: float | None = None,
        raw_value_2: float | None = None,
        numerator: int | None = None,
        denominator: int | None = None,
        percent: float | None = None,
        drilldown: bool = False,
    ) -> int:
        self.row_count += 1
        values = display_values or {self.column_specs[0][0]: display_value}
        columns = ", ".join([quote_identifier("ROW_LABEL")] + [quote_identifier(name) for name, _label, _n in self.column_specs])
        placeholders = ", ".join("?" for _ in range(len(self.column_specs) + 2))
        self.connection.execute(
            f"INSERT INTO dataset (_source_row, {columns}) VALUES ({placeholders})",
            (self.row_count, row_label)
            + tuple(values.get(name, "") for name, _label, _n in self.column_specs),
        )
        self.connection.execute(
            "INSERT INTO baseline_characteristics_long VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                self.row_count,
                item_order,
                item_kind,
                variable,
                item_label,
                unit,
                level_json,
                statistic_key,
                raw_value_1,
                raw_value_2,
                numerator,
                denominator,
                percent,
                display_value,
            ),
        )
        if drilldown:
            for column_name, _label, _n in self.column_specs:
                self.connection.execute(
                    "INSERT INTO baseline_characteristics_cell_map VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        self.row_count,
                        column_name,
                        item_kind,
                        variable,
                        level_json,
                        statistic_key,
                        (treatment_levels or {}).get(column_name, treatment_level_json),
                    ),
                )
        return self.row_count

    def finish(
        self,
        directory: Path,
        source: DatasetHandle,
        configuration_path: Path,
    ) -> DatasetHandle:
        self.connection.execute(
            "CREATE TABLE cache_info (cached_rows INTEGER NOT NULL, total_rows INTEGER, complete INTEGER NOT NULL)"
        )
        self.connection.execute(
            "INSERT INTO cache_info VALUES (?, ?, 1)",
            (self.row_count, self.row_count),
        )
        self.connection.commit()
        self.connection.close()
        marker = directory / "baseline-characteristics-result.tmp"
        marker.touch()
        metadata = DatasetMetadata(
            f"Baseline Characteristics - {source.metadata.name}",
            self.row_count,
            self.variables,
            display_column_names=(
                ("ROW_LABEL", "Characteristic"),
                *((name, label) for name, label, _n in self.column_specs),
            ),
            categorical_item_level_column="ROW_LABEL",
            baseline_characteristics_drilldown_columns=tuple(
                name for name, _label, _n in self.column_specs
            ),
        )
        return DatasetHandle(
            source.source_path.parent
            / f"Baseline Characteristics - {source.metadata.name}",
            marker,
            self.database_path,
            metadata,
            self.row_count,
            True,
            kind="baseline_characteristics",
            display_source=f"Baseline Characteristics from {source.source_path}",
            configuration_path=configuration_path,
        )

    def abort(self, temp_manager: TempManager, directory: Path) -> None:
        try:
            self.connection.close()
        except sqlite3.Error:
            pass
        temp_manager.remove_dataset(directory)


class BaselineCharacteristicsLongResultBuilder:
    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    def run(self, result: DatasetHandle, source: DatasetHandle) -> DatasetHandle:
        directory = self.temp_manager.create_dataset_directory()
        database = directory / "dataset.sqlite"
        definitions = ", ".join(
            f"{quote_identifier(variable.name)} "
            f"{'REAL' if variable.kind == 'numeric' else 'TEXT'}"
            for variable in LONG_VARIABLES
        )
        columns = ", ".join(
            quote_identifier(variable.name) for variable in LONG_VARIABLES
        )
        target: sqlite3.Connection | None = None
        try:
            target = sqlite3.connect(database)
            target.execute(
                "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "
                + definitions
                + ")"
            )
            with closing(
                sqlite3.connect(
                    result.database_path.resolve().as_uri() + "?mode=ro", uri=True
                )
            ) as connection:
                rows = connection.execute(
                    "SELECT row_order, "
                    + columns
                    + " FROM baseline_characteristics_long ORDER BY row_order"
                ).fetchall()
            placeholders = ", ".join("?" for _ in range(len(LONG_VARIABLES) + 1))
            target.executemany(
                "INSERT INTO dataset VALUES (" + placeholders + ")",
                rows,
            )
            row_count = len(rows)
            target.execute(
                "CREATE TABLE cache_info (cached_rows INTEGER NOT NULL, total_rows INTEGER, complete INTEGER NOT NULL)"
            )
            target.execute(
                "INSERT INTO cache_info VALUES (?, ?, 1)", (row_count, row_count)
            )
            target.commit()
            target.close()
            target = None
            marker = directory / "baseline-characteristics-long.tmp"
            marker.touch()
            return DatasetHandle(
                source.source_path.parent / "Baseline Characteristics Long Result",
                marker,
                database,
                DatasetMetadata(
                    "Baseline Characteristics Long Result",
                    row_count,
                    LONG_VARIABLES,
                ),
                row_count,
                True,
                kind="baseline_characteristics_long",
                display_source=f"Baseline Characteristics long result from {source.source_path}",
            )
        except BaseException:
            if target is not None:
                target.close()
            self.temp_manager.remove_dataset(directory)
            raise


__all__ = [
    "LONG_VARIABLES",
    "BaselineCharacteristicsLongResultBuilder",
    "BaselineCharacteristicsResultWriter",
]
