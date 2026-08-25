from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..domain import DatasetHandle, DatasetMetadata, VariableMetadata
from ..filter_engine import quote_identifier
from .models import ProcMeansConfig

STATISTIC_COLUMN_NAMES = {
    "subjects": "SUBJECT_N",
    "n": "N",
    "nmiss": "NMISS",
    "mean": "MEAN",
    "std": "SD",
    "stderr": "SE",
    "median": "MEDIAN",
    "q1": "Q1",
    "q3": "Q3",
    "min": "MIN",
    "max": "MAX",
    "lclm": "LCLM",
    "uclm": "UCLM",
}
COUNT_STATISTICS = {"subjects", "n", "nmiss"}


def _unique_name(base: str, used: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate.casefold() in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate.casefold())
    return candidate


@dataclass(frozen=True, slots=True)
class ProcMeansResultSchema:
    group_variables: tuple[VariableMetadata, ...]
    analysis_variable: str
    analysis_label: str
    statistic_columns: tuple[tuple[str, str], ...]
    decimal_base: str

    @property
    def visible_variables(self) -> tuple[VariableMetadata, ...]:
        statistics = tuple(
            VariableMetadata(
                column,
                STATISTIC_COLUMN_NAMES[key],
                "numeric",
            )
            for key, column in self.statistic_columns
        )
        return (
            *self.group_variables,
            VariableMetadata(
                self.analysis_variable, "Analysis variable", "character", 32
            ),
            VariableMetadata(
                self.analysis_label, "Analysis variable label", "character"
            ),
            *statistics,
        )


def build_result_schema(
    groups: tuple[VariableMetadata, ...], statistics: tuple[str, ...]
) -> ProcMeansResultSchema:
    used = {variable.name.casefold() for variable in groups}
    analysis_variable = _unique_name("ANALYSIS_VARIABLE", used)
    analysis_label = _unique_name("ANALYSIS_LABEL", used)
    statistic_columns = tuple(
        (key, _unique_name(STATISTIC_COLUMN_NAMES[key], used)) for key in statistics
    )
    decimal_base = _unique_name("__CDE_BASE_DECIMALS", used)
    return ProcMeansResultSchema(
        groups,
        analysis_variable,
        analysis_label,
        statistic_columns,
        decimal_base,
    )


class ProcMeansResultWriter:
    def __init__(
        self,
        database_path: Path,
        schema: ProcMeansResultSchema,
        config: ProcMeansConfig,
    ) -> None:
        self.database_path = database_path
        self.schema = schema
        self.config = config
        self.connection = sqlite3.connect(database_path)
        self.row_count = 0
        count_columns = {
            column
            for key, column in schema.statistic_columns
            if key in COUNT_STATISTICS
        }
        definitions = [
            f"{quote_identifier(variable.name)} "
            f"{'INTEGER' if variable.name in count_columns else 'REAL' if variable.kind == 'numeric' else 'TEXT'}"
            for variable in schema.visible_variables
        ]
        definitions.append(f"{quote_identifier(schema.decimal_base)} INTEGER NOT NULL")
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(
            "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "
            + ", ".join(definitions)
            + ")"
        )

    def add_row(
        self,
        group_values: dict[str, object],
        analysis: VariableMetadata,
        statistics: dict[str, float | int | None],
        base_decimals: int,
    ) -> None:
        columns = [variable.name for variable in self.schema.visible_variables]
        columns.append(self.schema.decimal_base)
        row = [
            group_values.get(variable.name) for variable in self.schema.group_variables
        ]
        row.extend((analysis.name, analysis.label))
        row.extend(statistics[key] for key, _column in self.schema.statistic_columns)
        row.append(base_decimals)
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
        source: DatasetHandle,
    ) -> DatasetHandle:
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
        offsets = dict(self.config.decimal_offsets)
        statistic_offsets = tuple(
            (column, offsets.get(key, 0))
            for key, column in self.schema.statistic_columns
            if key not in COUNT_STATISTICS
        )
        metadata = DatasetMetadata(
            f"PROC MEANS Result - {source.metadata.name}",
            self.row_count,
            self.schema.visible_variables,
            decimal_base_column=self.schema.decimal_base,
            statistic_decimal_offsets=statistic_offsets,
        )
        marker = result_directory / "proc-means-result.tmp"
        marker.touch()
        configuration_path = result_directory / "proc_means_config.json"
        configuration = {
            "type": "proc_means",
            "version": 1,
            "dataset": source.metadata.name,
            "filter": self.config.filter_text,
            "analysis_variables": list(self.config.analysis_variables),
            "by_variables": list(self.config.by_variables),
            "class_variables": list(self.config.class_variables),
            "statistics": [
                STATISTIC_COLUMN_NAMES[key] for key in self.config.statistics
            ],
            "options": {
                "nway": True,
                "include_missing_class": True,
                "include_missing_by": True,
                "confidence": self.config.confidence,
            },
            "display": {
                "result_layout": "long",
                "decimal_group_variable": self.config.decimal_group_variable,
                "decimal_offsets": {
                    STATISTIC_COLUMN_NAMES.get(key, key.upper()): value
                    for key, value in self.config.decimal_offsets
                },
                "maximum_decimals": 4,
            },
        }
        configuration_path.write_text(
            json.dumps(configuration, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        filter_scope = self.config.filter_text or "All rows"
        return DatasetHandle(
            source.source_path.parent / f"PROC MEANS Result - {source.metadata.name}",
            marker,
            self.database_path,
            metadata,
            self.row_count,
            True,
            kind="proc_means",
            display_source=f"PROC MEANS from {source.source_path} | {filter_scope}",
            configuration_path=configuration_path,
        )

    def abort(self) -> None:
        self.connection.close()
