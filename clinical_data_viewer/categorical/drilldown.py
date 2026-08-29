from __future__ import annotations

import json
import sqlite3
from contextlib import closing

from ..domain import DatasetHandle, DatasetMetadata
from ..filter_engine import quote_identifier
from ..temp_manager import TempManager
from .models import CategoricalConfig


def _missing_sql(name: str, kind: str, *, missing: bool) -> str:
    column = quote_identifier(name)
    expression = f"({column} IS NULL OR {column} = '')" if kind == "character" else f"{column} IS NULL"
    return expression if missing else f"NOT {expression}"


def _field(metadata: DatasetMetadata, name: str):
    return next(variable for variable in metadata.variables if variable.name.casefold() == name.casefold())


def _exact_clause(variable, value: object) -> tuple[str, tuple[object, ...]]:
    if value is None:
        return _missing_sql(variable.name, variable.kind, missing=True), ()
    return f"{quote_identifier(variable.name)} = ?", (value,)


def _and(*clauses: tuple[str, tuple[object, ...]]) -> tuple[str, tuple[object, ...]]:
    active = [(sql, params) for sql, params in clauses if sql]
    return (
        " AND ".join(f"({sql})" for sql, _params in active),
        tuple(value for _sql, params in active for value in params),
    ) if active else ("", ())


class CategoricalCell:
    def __init__(
        self,
        item_variable: str,
        context: dict[str, object],
        level: object,
        treatment: object | None,
    ) -> None:
        self.item_variable = item_variable
        self.context = context
        self.level = level
        self.treatment = treatment


def lookup_cell(
    result: DatasetHandle, result_source_row: int, column_name: str
) -> CategoricalCell | None:
    with closing(sqlite3.connect(result.database_path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        row = connection.execute(
            "SELECT item_variable, context_json, level_json, treatment_json "
            "FROM categorical_cell_map WHERE result_row = ? AND column_name = ?",
            (result_source_row, column_name),
        ).fetchone()
    if row is None:
        return None
    return CategoricalCell(row[0], json.loads(row[1]), json.loads(row[2]), json.loads(row[3]) if row[3] is not None else None)


class CategoricalQueryBuilder:
    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    def run(
        self,
        source: DatasetHandle,
        where_sql: str,
        parameters: tuple[object, ...],
        title: str,
        *,
        subject_id_variable: str | None = None,
    ) -> DatasetHandle:
        directory = self.temp_manager.create_dataset_directory()
        database = directory / "dataset.sqlite"
        variables = source.metadata.variables
        subject = (
            _field(source.metadata, subject_id_variable)
            if subject_id_variable
            else None
        )
        if subject is not None:
            variables = (subject,)
        definitions = ", ".join(
            f"{quote_identifier(variable.name)} {'REAL' if variable.kind == 'numeric' else 'TEXT'}"
            for variable in variables
        )
        columns = ", ".join(quote_identifier(variable.name) for variable in variables)
        target: sqlite3.Connection | None = None
        try:
            target = sqlite3.connect(database)
            target.execute("CREATE TABLE dataset (_source_row INTEGER PRIMARY KEY, " + definitions + ")")
            source_uri = source.database_path.resolve().as_uri() + "?mode=ro"
            row_count = 0
            insert = "INSERT INTO dataset VALUES (" + ", ".join(
                "?" for _ in range(len(variables) + 1)
            ) + ")"
            with closing(sqlite3.connect(source_uri, uri=True)) as input_connection:
                where = f" WHERE {where_sql}" if where_sql else ""
                if subject is not None:
                    subject_condition = _missing_sql(
                        subject.name, subject.kind, missing=False
                    )
                    where += (
                        " AND " if where else " WHERE "
                    ) + subject_condition
                    select_sql = (
                        f"SELECT MIN(_source_row), {columns} FROM dataset{where} "
                        f"GROUP BY {columns} ORDER BY {columns}"
                    )
                else:
                    select_sql = (
                        f"SELECT _source_row, {columns} FROM dataset{where} "
                        "ORDER BY _source_row"
                    )
                cursor = input_connection.execute(
                    select_sql,
                    parameters,
                )
                while rows := cursor.fetchmany(2_000):
                    target.executemany(insert, rows)
                    row_count += len(rows)
                    if row_count % 10_000 == 0:
                        target.commit()
            target.execute("CREATE TABLE cache_info (cached_rows INTEGER NOT NULL, total_rows INTEGER, complete INTEGER NOT NULL)")
            target.execute("INSERT INTO cache_info VALUES (?, ?, 1)", (row_count, row_count))
            target.commit()
            target.close()
            target = None
            marker = directory / "categorical-query.tmp"
            marker.touch()
            return DatasetHandle(
                source.source_path.parent / f"{title}.query",
                marker,
                database,
                DatasetMetadata(title, row_count, variables),
                row_count,
                True,
                kind="query",
                display_source=f"Categorical Table drill-down from {source.source_path}",
            )
        except BaseException:
            if target is not None:
                target.close()
            self.temp_manager.remove_dataset(directory)
            raise


def build_cell_filter(
    metadata: DatasetMetadata,
    config: CategoricalConfig,
    cell: CategoricalCell,
    *,
    denominator: bool = False,
) -> tuple[str, tuple[object, ...]]:
    """Build a source filter for ordinary numerator/denominator drill-down."""
    clauses: list[tuple[str, tuple[object, ...]]] = []
    if denominator and config.denominator.type == "population":
        clauses.append((config.denominator.population_filter.sql, config.denominator.population_filter.parameters))
    else:
        clauses.append((config.numerator_filter.sql, config.numerator_filter.parameters))
    treatment_name = config.treatment_variable
    if denominator and config.denominator.type == "population":
        treatment_name = (
            config.denominator.population_treatment_variable
            or config.treatment_variable
        )
    treatment = _field(metadata, treatment_name)
    if cell.treatment is not None:
        clauses.append(_exact_clause(treatment, cell.treatment))
    for name, value in cell.context.items():
        clauses.append(_exact_clause(_field(metadata, name), value))
    if denominator:
        if config.denominator.type == "nonmissing":
            value = _field(metadata, config.denominator.analysis_value_variable)
            clauses.append((_missing_sql(value.name, value.kind, missing=False), ()))
    else:
        item = _field(metadata, cell.item_variable)
        clauses.append(_exact_clause(item, cell.level))
    return _and(*clauses)


def build_n1_cell_filter(
    metadata: DatasetMetadata,
    config: CategoricalConfig,
    cell: CategoricalCell,
    *,
    denominator: bool = False,
) -> tuple[str, tuple[object, ...]]:
    """Return postbaseline rows whose subject has a matching baseline row."""
    treatment = _field(metadata, config.treatment_variable)
    subject = _field(metadata, config.subject_id_variable)
    analysis = _field(metadata, config.denominator.analysis_value_variable)
    match_fields = [treatment, subject, *(_field(metadata, name) for name in cell.context)]
    joins = " AND ".join(
        f"base.{quote_identifier(field.name)} = post.{quote_identifier(field.name)}"
        for field in match_fields
    )
    base_sql, base_params = _and(
        (config.numerator_filter.sql, config.numerator_filter.parameters),
        (config.denominator.baseline_filter.sql, config.denominator.baseline_filter.parameters),
        (_missing_sql(analysis.name, analysis.kind, missing=False), ()),
        (_missing_sql(subject.name, subject.kind, missing=False), ()),
    )
    post_sql, post_params = _and(
        (config.numerator_filter.sql, config.numerator_filter.parameters),
        (config.denominator.postbaseline_filter.sql, config.denominator.postbaseline_filter.parameters),
        (_missing_sql(analysis.name, analysis.kind, missing=False), ()),
        (_missing_sql(subject.name, subject.kind, missing=False), ()),
    )
    eligible = (
        "_source_row IN (SELECT post._source_row FROM dataset AS post WHERE "
        f"{post_sql} AND EXISTS (SELECT 1 FROM dataset AS base WHERE {base_sql} AND {joins}))"
    )
    outer: list[tuple[str, tuple[object, ...]]] = [(eligible, (*post_params, *base_params))]
    if cell.treatment is not None:
        outer.append(_exact_clause(treatment, cell.treatment))
    for name, value in cell.context.items():
        outer.append(_exact_clause(_field(metadata, name), value))
    if not denominator:
        outer.append(_exact_clause(_field(metadata, cell.item_variable), cell.level))
    return _and(*outer)
