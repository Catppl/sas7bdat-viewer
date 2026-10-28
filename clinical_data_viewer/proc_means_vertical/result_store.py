from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..domain import DatasetHandle, DatasetMetadata, VariableMetadata
from ..filter_engine import quote_identifier
from ..temp_manager import TempManager


class ProcMeansVerticalResultWriter:
    def __init__(
        self,
        database_path: Path,
        variables: tuple[VariableMetadata, ...],
        long_variables: tuple[VariableMetadata, ...],
    ) -> None:
        self.database_path = database_path
        self.connection = sqlite3.connect(database_path)
        self.variables = variables
        self.long_variables = long_variables
        self.row_count = 0
        self.long_count = 0
        definitions = ", ".join(
            f"{quote_identifier(variable.name)} TEXT" for variable in variables
        )
        self.connection.execute(
            "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "
            + definitions
            + ")"
        )
        long_defs = ", ".join(
            f"{quote_identifier(variable.name)} "
            f"{'REAL' if variable.kind == 'numeric' else 'TEXT'}"
            for variable in long_variables
        )
        self.connection.execute(
            "CREATE TABLE proc_means_vertical_long (_source_row INTEGER PRIMARY KEY, "
            + long_defs
            + ")"
        )
        self.connection.execute(
            "CREATE TABLE proc_means_vertical_cell_map ("
            "result_row INTEGER NOT NULL, column_name TEXT NOT NULL, "
            "analysis_variable TEXT NOT NULL, statistic_key TEXT NOT NULL, "
            "statistic_label TEXT NOT NULL, group_values_json TEXT, "
            "PRIMARY KEY (result_row, column_name))"
        )

    def add_row(self, values: tuple[object, ...]) -> int:
        self.row_count += 1
        names = ", ".join(
            quote_identifier(variable.name) for variable in self.variables
        )
        placeholders = ", ".join("?" for _ in self.variables)
        self.connection.execute(
            f"INSERT INTO dataset (_source_row, {names}) VALUES (?, {placeholders})",
            (self.row_count, *values),
        )
        return self.row_count

    def add_long(self, values: tuple[object, ...]) -> None:
        self.long_count += 1
        names = ", ".join(
            quote_identifier(variable.name) for variable in self.long_variables
        )
        placeholders = ", ".join("?" for _ in self.long_variables)
        self.connection.execute(
            f"INSERT INTO proc_means_vertical_long (_source_row, {names}) VALUES (?, {placeholders})",
            (self.long_count, *values),
        )

    def add_cell(
        self,
        result_row: int,
        column_name: str,
        analysis_variable: str,
        statistic_key: str,
        statistic_label: str,
        group_values: tuple[object, ...] | None,
    ) -> None:
        self.connection.execute(
            "INSERT INTO proc_means_vertical_cell_map "
            "(result_row, column_name, analysis_variable, statistic_key, "
            "statistic_label, group_values_json) VALUES (?, ?, ?, ?, ?, ?)",
            (
                result_row,
                column_name,
                analysis_variable,
                statistic_key,
                statistic_label,
                json.dumps(group_values, ensure_ascii=False)
                if group_values is not None
                else None,
            ),
        )

    def finish(
        self,
        directory: Path,
        source: DatasetHandle,
        configuration_path: Path,
        display_names: tuple[tuple[str, str], ...],
    ) -> DatasetHandle:
        self.connection.execute(
            "CREATE TABLE cache_info (cached_rows INTEGER,total_rows INTEGER,complete INTEGER)"
        )
        self.connection.execute(
            "INSERT INTO cache_info VALUES (?, ?, 1)", (self.row_count, self.row_count)
        )
        self.connection.commit()
        self.connection.close()
        marker = directory / "proc-means-vertical-result.tmp"
        marker.touch()
        metadata = DatasetMetadata(
            f"PROC MEANS Vertical Result - {source.metadata.name}",
            self.row_count,
            self.variables,
            display_column_names=display_names,
            proc_means_vertical_drilldown_columns=tuple(
                variable.name
                for variable in self.variables
                if variable.name not in {"GROUP", "ITEM", "STATISTIC"}
            ),
        )
        return DatasetHandle(
            source.source_path.parent
            / f"PROC MEANS Vertical Result - {source.metadata.name}",
            marker,
            self.database_path,
            metadata,
            self.row_count,
            True,
            kind="proc_means_vertical",
            display_source=f"PROC MEANS Table Builder (Vertical) from {source.source_path}",
            configuration_path=configuration_path,
        )

    def abort(self, manager: TempManager, directory: Path) -> None:
        try:
            self.connection.close()
        except sqlite3.Error:
            pass
        manager.remove_dataset(directory)


class ProcMeansVerticalLongResultBuilder:
    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    def run(self, result: DatasetHandle, source: DatasetHandle) -> DatasetHandle:
        directory = self.temp_manager.create_dataset_directory()
        database = directory / "dataset.sqlite"
        with sqlite3.connect(
            result.database_path.resolve().as_uri() + "?mode=ro", uri=True
        ) as connection:
            columns = [
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(proc_means_vertical_long)"
                )
            ]
            rows = connection.execute(
                "SELECT "
                + ", ".join(quote_identifier(name) for name in columns[1:])
                + " FROM proc_means_vertical_long ORDER BY _source_row"
            ).fetchall()
        variables = (
            VariableMetadata("ITEM", "Item", "character", 200),
            VariableMetadata("STATISTIC", "Statistic", "character", 32),
            VariableMetadata("GROUP", "Group", "character", 200),
            VariableMetadata("VALUE", "Value", "numeric"),
            VariableMetadata("N", "n", "numeric"),
            VariableMetadata("SUBJECT_N", "N", "numeric"),
            VariableMetadata("DISPLAY_VALUE", "Display Value", "character", 200),
        )
        target: sqlite3.Connection | None = None
        try:
            target = sqlite3.connect(database)
            definitions = ", ".join(
                f"{quote_identifier(variable.name)} {'REAL' if variable.kind == 'numeric' else 'TEXT'}"
                for variable in variables
            )
            target.execute(
                "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "
                + definitions
                + ")"
            )
            target.executemany(
                "INSERT INTO dataset ("
                + ", ".join(quote_identifier(v.name) for v in variables)
                + ") VALUES ("
                + ", ".join("?" for _ in variables)
                + ")",
                rows,
            )
            target.execute(
                "CREATE TABLE cache_info (cached_rows INTEGER,total_rows INTEGER,complete INTEGER)"
            )
            target.execute(
                "INSERT INTO cache_info VALUES (?, ?, 1)", (len(rows), len(rows))
            )
            target.commit()
            target.close()
            target = None
            marker = directory / "proc-means-vertical-long-result.tmp"
            marker.touch()
            return DatasetHandle(
                source.source_path.parent
                / f"PROC MEANS Vertical Long - {source.metadata.name}",
                marker,
                database,
                DatasetMetadata(
                    f"PROC MEANS Vertical Long - {source.metadata.name}",
                    len(rows),
                    variables,
                ),
                len(rows),
                True,
                kind="proc_means_vertical_long",
                display_source=f"PROC MEANS Vertical long result from {source.source_path}",
            )
        except BaseException:
            if target is not None:
                target.close()
            self.temp_manager.remove_dataset(directory)
            raise


__all__ = ["ProcMeansVerticalLongResultBuilder", "ProcMeansVerticalResultWriter"]
