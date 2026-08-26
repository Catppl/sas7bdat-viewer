from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import replace

from ..domain import DatasetHandle, DatasetMetadata, VariableMetadata
from ..filter_engine import quote_identifier
from ..temp_manager import TempManager
from .configuration import write_rule_based_configuration
from .models import RuleBasedConfig, RuleBasedRow
from .result_store import RuleBasedResultWriter

TOTAL_KEY = "__rule_based_total__"

ProgressCallback = Callable[[str], None]


class MissingTreatmentError(ValueError):
    """Calculation is blocked until missing treatment values are removed."""


def _missing(value: object, variable: VariableMetadata) -> bool:
    return value is None or (variable.kind == "character" and value == "")


def _canonical(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _display(value: object) -> str:
    return "(Missing)" if value is None else str(value)


def _and(*clauses: tuple[str, tuple[object, ...]]) -> tuple[str, tuple[object, ...]]:
    active = [(sql, params) for sql, params in clauses if sql]
    if not active:
        return "", ()
    return (
        " AND ".join(f"({sql})" for sql, _params in active),
        tuple(value for _sql, params in active for value in params),
    )


def _missing_sql(variable: VariableMetadata) -> str:
    column = quote_identifier(variable.name)
    return (
        f"({column} IS NULL OR {column} = '')"
        if variable.kind == "character"
        else f"{column} IS NULL"
    )


def _not_missing_sql(variable: VariableMetadata) -> str:
    return f"NOT ({_missing_sql(variable)})"


class RuleBasedEngine:
    """Calculate row-rule n (%) results from cached SQLite datasets."""

    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    def run(
        self,
        source: DatasetHandle,
        config: RuleBasedConfig,
        population: DatasetHandle | None = None,
        progress: ProgressCallback | None = None,
    ) -> DatasetHandle:
        if not source.cache_complete:
            raise ValueError("The source dataset must finish loading first.")
        if population is not None and not population.cache_complete:
            raise ValueError("The denominator dataset must finish loading first.")
        config.validate(source.metadata, population.metadata if population else None)
        notify = progress or (lambda _message: None)
        fields = self._fields(source.metadata)
        treatment = fields[config.treatment_variable.casefold()]
        subject = fields[config.subject_id_variable.casefold()]
        population_fields = self._fields(population.metadata) if population else {}
        if config.denominator.type == "population" and population is not None:
            denominator_treatment = population_fields[
                config.treatment_variable.casefold()
            ]
        else:
            denominator_treatment = treatment

        self._check_missing_treatments(source, config, treatment)
        if config.denominator.type == "population" and population is not None:
            self._check_missing_population_treatment(
                population,
                config.denominator.population_filter.sql,
                config.denominator.population_filter.parameters,
                denominator_treatment,
            )
        notify("Collecting treatment levels…")
        treatments: list[tuple[str, object, str]] = []
        for row in config.rows:
            row_sql, row_parameters = self._combined_filter(config, row)
            treatments = self._merge_levels(
                treatments,
                self._treatment_levels(
                    source,
                    row_sql,
                    row_parameters,
                    treatment,
                ),
            )
        if config.denominator.type == "population" and population is not None:
            treatments = self._merge_levels(
                treatments,
                self._treatment_levels(
                    population,
                    config.denominator.population_filter.sql,
                    config.denominator.population_filter.parameters,
                    denominator_treatment,
                ),
            )
        directory = self.temp_manager.create_dataset_directory()
        writer = RuleBasedResultWriter(directory / "dataset.sqlite", treatments, config)
        try:
            denominator = self._denominator(
                source, population, config, treatment, subject, notify
            )
            for index, row in enumerate(config.rows, start=1):
                notify(
                    f"Calculating Rule-based Table… row {index}/{len(config.rows)}: {row.item}"
                )
                numerator = self._numerator(source, config, row, treatment, subject)
                writer.add_row(row, numerator, denominator, treatments)
            notify(f"Finalizing Rule-based Table… {writer.row_count:,} rows")
            configuration_path = directory / "rule_based_config.json"
            write_rule_based_configuration(
                configuration_path,
                source,
                config,
                population,
                treatments,
            )
            result = writer.finish(directory, source)
            return replace(result, configuration_path=configuration_path)
        except BaseException:
            writer.abort(self.temp_manager, directory)
            raise

    @staticmethod
    def _fields(metadata: DatasetMetadata) -> dict[str, VariableMetadata]:
        return {variable.name.casefold(): variable for variable in metadata.variables}

    @staticmethod
    def _connection(handle: DatasetHandle) -> sqlite3.Connection:
        return sqlite3.connect(
            handle.database_path.resolve().as_uri() + "?mode=ro", uri=True
        )

    @staticmethod
    def _merge_levels(
        first: list[tuple[str, object, str]], second: list[tuple[str, object, str]]
    ) -> list[tuple[str, object, str]]:
        known = {key for key, _value, _label in first}
        return first + [entry for entry in second if entry[0] not in known]

    @staticmethod
    def _combined_filter(
        config: RuleBasedConfig, row: RuleBasedRow
    ) -> tuple[str, tuple[object, ...]]:
        return _and(
            (config.dataset_filter.sql, config.dataset_filter.parameters),
            (row.row_filter.sql, row.row_filter.parameters),
        )

    def _treatment_levels(
        self,
        handle: DatasetHandle,
        where_sql: str,
        parameters: tuple[object, ...],
        treatment: VariableMetadata,
    ) -> list[tuple[str, object, str]]:
        where = f" WHERE {where_sql}" if where_sql else ""
        query = (
            f"SELECT DISTINCT {quote_identifier(treatment.name)} FROM dataset{where}"
        )
        with closing(self._connection(handle)) as connection:
            values = [
                _canonical(row[0]) for row in connection.execute(query, parameters)
            ]
        values.sort(key=lambda value: (value is None, str(value)))
        return [(_json(value), value, _display(value)) for value in values]

    def _check_missing_treatments(
        self,
        source: DatasetHandle,
        config: RuleBasedConfig,
        treatment: VariableMetadata,
    ) -> None:
        details: list[str] = []
        for row in config.rows:
            sql, params = _and(
                (config.dataset_filter.sql, config.dataset_filter.parameters),
                (row.row_filter.sql, row.row_filter.parameters),
                (_missing_sql(treatment), ()),
            )
            where = f" WHERE {sql}" if sql else ""
            with closing(self._connection(source)) as connection:
                count = int(
                    connection.execute(
                        f"SELECT count(*) FROM dataset{where}", params
                    ).fetchone()[0]
                )
            if count:
                details.append(
                    f"- {row.item}: {count:,} record(s) with missing treatment"
                )
        if details:
            raise MissingTreatmentError(
                "Missing treatment values were found. Modify the Dataset Filter or Row Filter before running:\n"
                + "\n".join(details)
            )

    def _check_missing_population_treatment(
        self,
        population: DatasetHandle,
        where_sql: str,
        parameters: tuple[object, ...],
        treatment: VariableMetadata,
    ) -> None:
        where = _and((where_sql, parameters), (_missing_sql(treatment), ()))
        sql = f" WHERE {where[0]}" if where[0] else ""
        with closing(self._connection(population)) as connection:
            count = int(
                connection.execute(
                    f"SELECT count(*) FROM dataset{sql}", where[1]
                ).fetchone()[0]
            )
        if count:
            raise MissingTreatmentError(
                "Missing treatment values were found. Modify the Population WHERE before running:\n"
                f"- Population N (ADSL): {count:,} record(s) with missing treatment"
            )

    def _numerator(
        self,
        source: DatasetHandle,
        config: RuleBasedConfig,
        row: RuleBasedRow,
        treatment: VariableMetadata,
        subject: VariableMetadata,
    ) -> dict[str, int]:
        sql, params = self._combined_filter(config, row)
        where = f" WHERE {sql}" if sql else ""
        query = (
            f"SELECT {quote_identifier(treatment.name)}, "
            f"count(DISTINCT {quote_identifier(subject.name)}) "
            f"FROM dataset{where} "
            f"AND {_not_missing_sql(subject)} "
            f"GROUP BY {quote_identifier(treatment.name)}"
            if where
            else f"SELECT {quote_identifier(treatment.name)}, "
            f"count(DISTINCT {quote_identifier(subject.name)}) FROM dataset "
            f"WHERE {_not_missing_sql(subject)} "
            f"GROUP BY {quote_identifier(treatment.name)}"
        )
        with closing(self._connection(source)) as connection:
            counts = {
                _json(_canonical(value)): int(count)
                for value, count in connection.execute(query, params)
            }
            total_where = f" WHERE {sql}" if sql else ""
            total = connection.execute(
                f"SELECT count(DISTINCT {quote_identifier(subject.name)}) "
                f"FROM dataset{total_where}"
                f"{' AND ' if total_where else ' WHERE '}"
                f"{_not_missing_sql(subject)}",
                params,
            ).fetchone()[0]
            counts[TOTAL_KEY] = int(total)
            return counts

    def _denominator(
        self,
        source: DatasetHandle,
        population: DatasetHandle | None,
        config: RuleBasedConfig,
        treatment: VariableMetadata,
        subject: VariableMetadata,
        notify: ProgressCallback,
    ) -> dict[str, int]:
        if config.denominator.type == "population":
            assert population is not None
            denominator_treatment = self._fields(population.metadata)[
                config.treatment_variable.casefold()
            ]
            handle = population
            where_sql = config.denominator.population_filter.sql
            params = config.denominator.population_filter.parameters
            subject = self._fields(population.metadata)[
                config.subject_id_variable.casefold()
            ]
            treatment = denominator_treatment
        else:
            handle = source
            where_sql, params = _and(
                (config.dataset_filter.sql, config.dataset_filter.parameters),
                (
                    "NOT ("
                    + _missing_sql(
                        self._fields(source.metadata)[
                            config.denominator.analysis_value_variable.casefold()
                        ]
                    )
                    + ")",
                    (),
                )
                if config.denominator.type == "nonmissing"
                else ("", ()),
            )
        notify("Calculating denominator…")
        where = f" WHERE {where_sql}" if where_sql else ""
        query = (
            f"SELECT {quote_identifier(treatment.name)}, "
            f"count(DISTINCT {quote_identifier(subject.name)}) FROM dataset{where}"
            f"{' AND ' if where else ' WHERE '}"
            f"{_not_missing_sql(subject)} "
            f"GROUP BY {quote_identifier(treatment.name)}"
        )
        with closing(self._connection(handle)) as connection:
            counts = {
                _json(_canonical(value)): int(count)
                for value, count in connection.execute(query, params)
            }
            total_where = f" WHERE {where_sql}" if where_sql else ""
            total = connection.execute(
                f"SELECT count(DISTINCT {quote_identifier(subject.name)}) "
                f"FROM dataset{total_where}"
                f"{' AND ' if total_where else ' WHERE '}"
                f"{_not_missing_sql(subject)}",
                params,
            ).fetchone()[0]
            counts[TOTAL_KEY] = int(total)
            return counts
