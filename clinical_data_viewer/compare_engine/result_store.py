from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..domain import DatasetHandle, DatasetMetadata, VariableMetadata
from ..filter_engine import quote_identifier


@dataclass(frozen=True, slots=True)
class ResultSchema:
    pair: str
    side: str
    status: str
    source_obs: str
    match_cost: str
    match_margin: str
    diff_variables: str
    side_order: str
    diff_columns: str
    row_warning: str
    result_variables: tuple[VariableMetadata, ...]
    warning_columns: tuple[str, ...] = ()
    warning_column_messages: tuple[tuple[str, str], ...] = ()

    @property
    def visible_variables(self) -> tuple[VariableMetadata, ...]:
        return (
            VariableMetadata(self.pair, "Comparison pair", "numeric"),
            VariableMetadata(self.side, "Main or QC side", "character", 8),
            VariableMetadata(self.status, "Comparison result", "character", 20),
            VariableMetadata(self.source_obs, "Source observation", "numeric"),
            VariableMetadata(self.match_cost, "Normalized matching cost", "numeric"),
            VariableMetadata(
                self.match_margin, "Best versus second-best candidate margin", "numeric"
            ),
            VariableMetadata(
                self.diff_variables, "Variables formally identified as different"
            ),
            *self.result_variables,
        )

    @property
    def advanced_columns(self) -> tuple[str, ...]:
        return (self.pair, self.match_cost, self.match_margin)


def _unique_name(base: str, used: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate.casefold() in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate.casefold())
    return candidate


def build_result_schema(
    result_variables: tuple[VariableMetadata, ...],
    warning_columns: tuple[str, ...] = (),
    warning_column_messages: tuple[tuple[str, str], ...] = (),
) -> ResultSchema:
    used = {variable.name.casefold() for variable in result_variables}
    return ResultSchema(
        _unique_name("COMPARE_PAIR", used),
        _unique_name("SIDE", used),
        _unique_name("MATCH_STATUS", used),
        _unique_name("SOURCE_OBS", used),
        _unique_name("MATCH_COST", used),
        _unique_name("MATCH_MARGIN", used),
        _unique_name("DIFF_VARIABLES", used),
        _unique_name("__CDE_SIDE_ORDER", used),
        _unique_name("__CDE_DIFF_COLUMNS", used),
        _unique_name("__CDE_ROW_WARNING", used),
        result_variables,
        warning_columns,
        warning_column_messages,
    )


class CompareResultWriter:
    def __init__(self, database_path: Path, schema: ResultSchema) -> None:
        self.database_path = database_path
        self.schema = schema
        self.connection = sqlite3.connect(database_path)
        self.row_count = 0
        self._create()

    def _create(self) -> None:
        variables = self.schema.visible_variables
        definitions = [
            f"{quote_identifier(variable.name)} "
            f"{'REAL' if variable.kind == 'numeric' else 'TEXT'}"
            for variable in variables
        ]
        definitions.extend(
            (
                f"{quote_identifier(self.schema.side_order)} INTEGER NOT NULL",
                f"{quote_identifier(self.schema.diff_columns)} TEXT NOT NULL",
                f"{quote_identifier(self.schema.row_warning)} INTEGER NOT NULL",
            )
        )
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(
            "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "
            + ", ".join(definitions)
            + ")"
        )

    def add_row(
        self,
        pair_id: int,
        side: str,
        status: str,
        source_obs: int,
        match_cost: float | None,
        match_margin: float | None,
        differences: tuple[str, ...],
        values: dict[str, object],
    ) -> None:
        columns = [variable.name for variable in self.schema.visible_variables]
        columns.extend((self.schema.side_order, self.schema.diff_columns))
        columns.append(self.schema.row_warning)
        row = [
            pair_id,
            side,
            status,
            source_obs,
            match_cost,
            match_margin,
            ", ".join(differences),
        ]
        row.extend(
            values.get(variable.name) for variable in self.schema.result_variables
        )
        row.extend(
            (
                0 if side == "Main" else 1,
                json.dumps(differences),
                int(status in {"Main only", "QC only", "Unmatched"}),
            )
        )
        placeholders = ", ".join("?" for _column in columns)
        self.connection.execute(
            "INSERT INTO dataset ("
            + ", ".join(quote_identifier(column) for column in columns)
            + f") VALUES ({placeholders})",
            row,
        )
        self.row_count += 1
        if self.row_count % 2_000 == 0:
            self.connection.commit()

    def finish(
        self,
        result_directory: Path,
        main: DatasetHandle,
        qc: DatasetHandle,
    ) -> DatasetHandle:
        self.connection.execute(
            f"CREATE INDEX compare_pair_index ON dataset "
            f"({quote_identifier(self.schema.pair)}, "
            f"{quote_identifier(self.schema.side_order)})"
        )
        self.connection.execute(
            "CREATE TABLE cache_info (cached_rows INTEGER NOT NULL, "
            "total_rows INTEGER, complete INTEGER NOT NULL)"
        )
        self.connection.execute(
            "INSERT INTO cache_info VALUES (?, ?, 1)",
            (self.row_count, self.row_count),
        )
        self.connection.commit()
        self.connection.close()
        metadata = DatasetMetadata(
            f"Compare {main.metadata.name} vs {qc.metadata.name}",
            self.row_count,
            self.schema.visible_variables,
            pair_id_column=self.schema.pair,
            side_order_column=self.schema.side_order,
            diff_columns_column=self.schema.diff_columns,
            row_warning_column=self.schema.row_warning,
            advanced_columns=self.schema.advanced_columns,
            export_excluded_columns=self.schema.advanced_columns,
            warning_columns=self.schema.warning_columns,
            warning_column_messages=self.schema.warning_column_messages,
            compare_side_column=self.schema.side,
            source_obs_column=self.schema.source_obs,
        )
        marker = result_directory / "compare-result.tmp"
        marker.touch()
        return DatasetHandle(
            main.source_path.parent
            / f"Compare Result - {main.metadata.name} vs {qc.metadata.name}",
            marker,
            self.database_path,
            metadata,
            self.row_count,
            True,
            kind="compare",
            display_source=f"Main: {main.source_path}  |  QC: {qc.source_path}",
        )

    def abort(self) -> None:
        self.connection.close()
