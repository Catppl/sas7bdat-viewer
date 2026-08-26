from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..domain import DatasetHandle, DatasetMetadata, VariableMetadata
from ..filter_engine import quote_identifier
from .models import MergeSummary


def _unique_name(base: str, used: set[str]) -> str:
    candidate = base
    suffix = 2
    while candidate.casefold() in used:
        candidate = f"{base}_{suffix}"
        suffix += 1
    used.add(candidate.casefold())
    return candidate


@dataclass(frozen=True, slots=True)
class MergeResultSchema:
    variables: tuple[VariableMetadata, ...]
    status_column: str
    left_source_row_column: str
    right_source_row_column: str

    @property
    def all_variables(self) -> tuple[VariableMetadata, ...]:
        return self.variables


def build_result_schema(
    left: DatasetMetadata,
    right: DatasetMetadata,
    by_variables: tuple[str, ...],
) -> tuple[MergeResultSchema, tuple[tuple[str, str, str], ...]]:
    """Return result metadata and right-column mapping (source, output, kind)."""

    left_by_name = {variable.name.casefold(): variable for variable in left.variables}
    by_names = tuple(left_by_name[name.casefold()].name for name in by_variables)
    by_set = {name.casefold() for name in by_names}
    used: set[str] = set()
    result: list[VariableMetadata] = []
    for name in by_names:
        variable = left_by_name[name.casefold()]
        output_name = _unique_name(variable.name, used)
        result.append(
            VariableMetadata(
                output_name,
                variable.label,
                variable.kind,
                variable.length,
                variable.format,
            )
        )
    left_non_by: list[tuple[str, str]] = []
    for variable in left.variables:
        if variable.name.casefold() in by_set:
            continue
        output_name = _unique_name(variable.name, used)
        left_non_by.append((variable.name, output_name))
        result.append(
            VariableMetadata(
                output_name,
                variable.label,
                variable.kind,
                variable.length,
                variable.format,
            )
        )
    right_mapping: list[tuple[str, str, str]] = []
    for variable in right.variables:
        if variable.name.casefold() in by_set:
            continue
        preferred_name = (
            f"{variable.name}_RIGHT"
            if variable.name.casefold() in used
            else variable.name
        )
        output_name = _unique_name(preferred_name, used)
        right_mapping.append((variable.name, output_name, variable.kind))
        result.append(
            VariableMetadata(
                output_name,
                variable.label,
                variable.kind,
                variable.length,
                variable.format,
            )
        )
    status = _unique_name("_MERGE_STATUS", used)
    left_source = _unique_name("_LEFT_SOURCE_ROW", used)
    right_source = _unique_name("_RIGHT_SOURCE_ROW", used)
    result.extend(
        (
            VariableMetadata(status, "Merge status", "character", 16),
            VariableMetadata(left_source, "Left source row", "numeric"),
            VariableMetadata(right_source, "Right source row", "numeric"),
        )
    )
    return MergeResultSchema(tuple(result), status, left_source, right_source), tuple(
        right_mapping
    )


class MergeResultWriter:
    """Create the session-only SQLite dataset used by the normal DatasetTab."""

    def __init__(self, database_path: Path, schema: MergeResultSchema) -> None:
        self.database_path = database_path
        self.schema = schema
        self.connection = sqlite3.connect(database_path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        definitions = [
            f"{quote_identifier(variable.name)} "
            f"{'REAL' if variable.kind == 'numeric' else 'TEXT'}"
            for variable in schema.all_variables
        ]
        self.connection.execute(
            "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "
            + ", ".join(definitions)
            + ")"
        )

    def finish(
        self,
        result_directory: Path,
        left: DatasetHandle,
        right: DatasetHandle,
        summary: MergeSummary,
        by_variables: tuple[str, ...],
        join_type: str,
    ) -> DatasetHandle:
        row_count = int(
            self.connection.execute("SELECT count(*) FROM dataset").fetchone()[0]
        )
        self.connection.execute(
            "CREATE TABLE cache_info (cached_rows INTEGER NOT NULL, "
            "total_rows INTEGER, complete INTEGER NOT NULL)"
        )
        self.connection.execute(
            "INSERT INTO cache_info VALUES (?, ?, 1)", (row_count, row_count)
        )
        self.connection.commit()
        self.connection.close()
        marker = result_directory / "merge-result.tmp"
        marker.touch()
        metadata = DatasetMetadata(
            f"Merge Result - {left.metadata.name} + {right.metadata.name}",
            row_count,
            self.schema.all_variables,
        )
        display_source = (
            f"Merge {join_type.title()} Join | Left: {left.source_path} | "
            f"Right: {right.source_path} | BY: {', '.join(by_variables)} | "
            "Current Viewer WHERE conditions were not applied"
        )
        return DatasetHandle(
            left.source_path.parent
            / f"Merge Result - {left.metadata.name} + {right.metadata.name}",
            marker,
            self.database_path,
            metadata,
            row_count,
            True,
            kind="merge",
            display_source=display_source,
        )

    def abort(self) -> None:
        try:
            self.connection.close()
        except sqlite3.Error:
            pass
