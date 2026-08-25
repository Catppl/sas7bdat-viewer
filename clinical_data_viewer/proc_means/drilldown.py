from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing

from ..domain import DatasetHandle, DatasetMetadata
from ..filter_engine import CompiledFilter, quote_identifier
from ..temp_manager import TempManager
from .models import ProcMeansConfig

ProgressCallback = Callable[[str], None]


def _missing_sql(name: str, kind: str, *, missing: bool) -> str:
    column = quote_identifier(name)
    if kind == "character":
        expression = f"({column} IS NULL OR {column} = '')"
    else:
        expression = f"{column} IS NULL"
    return expression if missing else f"NOT ({expression})"


def build_drilldown_filter(
    metadata: DatasetMetadata,
    config: ProcMeansConfig,
    group_values: dict[str, object],
    analysis_variable: str,
    statistic_key: str,
) -> CompiledFilter:
    by_fold = {variable.name.casefold(): variable for variable in metadata.variables}
    analysis = by_fold[analysis_variable.casefold()]
    clauses: list[str] = []
    parameters: list[object] = []
    if config.compiled_filter.sql:
        clauses.append(f"({config.compiled_filter.sql})")
        parameters.extend(config.compiled_filter.parameters)
    for requested in config.group_variables:
        variable = by_fold[requested.casefold()]
        value = group_values[variable.name]
        if value is None or (variable.kind == "character" and value == ""):
            clauses.append(_missing_sql(variable.name, variable.kind, missing=True))
        else:
            clauses.append(f"{quote_identifier(variable.name)} = ?")
            parameters.append(value)
    clauses.append(
        _missing_sql(
            analysis.name,
            analysis.kind,
            missing=statistic_key == "nmiss",
        )
    )
    if statistic_key == "subjects":
        subject = by_fold.get("usubjid")
        if subject is not None:
            clauses.append(_missing_sql(subject.name, subject.kind, missing=False))
    return CompiledFilter(" AND ".join(clauses), tuple(parameters))


class ProcMeansQueryBuilder:
    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    def run(
        self,
        source: DatasetHandle,
        compiled_filter: CompiledFilter,
        title: str,
        progress: ProgressCallback | None = None,
    ) -> DatasetHandle:
        notify = progress or (lambda _message: None)
        result_directory = self.temp_manager.create_dataset_directory()
        database_path = result_directory / "dataset.sqlite"
        marker = result_directory / "proc-means-query.tmp"
        variables = source.metadata.variables
        definitions = [
            f"{quote_identifier(variable.name)} "
            f"{'REAL' if variable.kind == 'numeric' else 'TEXT'}"
            for variable in variables
        ]
        columns = [variable.name for variable in variables]
        select = ", ".join(quote_identifier(column) for column in columns)
        placeholders = ", ".join("?" for _column in columns)
        quoted_columns = ", ".join(quote_identifier(column) for column in columns)
        source_uri = source.database_path.resolve().as_uri() + "?mode=ro"
        row_count = 0
        target: sqlite3.Connection | None = None
        try:
            target = sqlite3.connect(database_path)
            target.execute("PRAGMA journal_mode=WAL")
            target.execute("PRAGMA synchronous=NORMAL")
            target.execute(
                "CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, "
                + ", ".join(definitions)
                + ")"
            )
            insert = (
                f"INSERT INTO dataset (_source_row, {quoted_columns}) "
                f"VALUES (?, {placeholders})"
            )
            with closing(sqlite3.connect(source_uri, uri=True)) as connection:
                connection.execute("PRAGMA case_sensitive_like=ON")
                cursor = connection.execute(
                    f"SELECT _source_row, {select} FROM dataset "
                    f"WHERE {compiled_filter.sql} ORDER BY _source_row",
                    compiled_filter.parameters,
                )
                while True:
                    rows = cursor.fetchmany(2_000)
                    if not rows:
                        break
                    target.executemany(insert, rows)
                    row_count += len(rows)
                    if row_count % 2_000 == 0:
                        target.commit()
                    notify(f"Building Query Tab… {row_count:,} source rows")
            target.execute(
                "CREATE TABLE cache_info (cached_rows INTEGER NOT NULL, "
                "total_rows INTEGER, complete INTEGER NOT NULL)"
            )
            target.execute(
                "INSERT INTO cache_info VALUES (?, ?, 1)",
                (row_count, row_count),
            )
            target.commit()
            target.close()
            target = None
            marker.touch()
            metadata = DatasetMetadata(title, row_count, variables)
            safe_dataset_name = "".join(
                character
                if character.isalnum() or character in {"-", "_"}
                else "_"
                for character in source.metadata.name
            ).strip("_") or "Dataset"
            return DatasetHandle(
                source.source_path.parent / f"Query-{safe_dataset_name}.query",
                marker,
                database_path,
                metadata,
                row_count,
                True,
                kind="query",
                display_source=f"PROC MEANS drill-down from {source.source_path}",
            )
        except BaseException:
            if target is not None:
                target.close()
            self.temp_manager.remove_dataset(result_directory)
            raise
