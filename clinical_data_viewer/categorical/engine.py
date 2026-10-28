from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal

from ..domain import DatasetHandle, VariableMetadata
from ..filter_engine import quote_identifier
from ..temp_manager import TempManager
from .configuration import write_categorical_configuration
from .models import CategoricalConfig, CategoricalItem
from .result_store import CategoricalResultWriter

ProgressCallback = Callable[[str], None]


class MissingTreatmentError(ValueError):
    """Calculation is blocked when an analysis treatment value is missing."""


def _missing(value: object, variable: VariableMetadata) -> bool:
    return value is None or (variable.kind == "character" and value == "")


def _canonical(value: object, variable: VariableMetadata | None = None) -> object:
    """Use one stable representation for SQLite values, missing included."""
    if variable is not None and _missing(value, variable):
        return None
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _display(value: object) -> str:
    return "(Missing)" if value is None else str(value)


_LOWERCASE_DISPLAY_WORDS = re.compile(r"\b(?:and|or)\b", re.IGNORECASE)


def _display_level(value: object, *, propcase: bool) -> str:
    """Format one level for display without changing its raw identity."""

    text = _display(value)
    if value is None or not propcase:
        return text
    return _LOWERCASE_DISPLAY_WORDS.sub(
        lambda match: match.group(0).lower(), text.title()
    )


def _count_value(
    accumulator: dict[tuple[object, ...], int | set[object]],
    key: tuple[object, ...],
    subject: object,
    count_type: str,
    subject_variable: VariableMetadata | None = None,
) -> None:
    if count_type == "record":
        accumulator[key] = int(accumulator.get(key, 0)) + 1
    elif subject is not None and (
        subject_variable is None or not _missing(subject, subject_variable)
    ):
        values = accumulator.setdefault(key, set())
        assert isinstance(values, set)
        values.add(subject)


def _materialize_counts(
    accumulator: dict[tuple[object, ...], int | set[object]],
) -> dict[tuple[object, ...], int]:
    return {
        key: len(value) if isinstance(value, set) else value
        for key, value in accumulator.items()
    }


def _and(
    *clauses: tuple[str, tuple[object, ...]],
) -> tuple[str, tuple[object, ...]]:
    active = [(sql, parameters) for sql, parameters in clauses if sql]
    if not active:
        return "", ()
    return (
        " AND ".join(f"({sql})" for sql, _parameters in active),
        tuple(value for _sql, parameters in active for value in parameters),
    )


class CategoricalEngine:
    """Build a categorical n (%) table from the existing SQLite source caches."""

    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    def run(
        self,
        source: DatasetHandle,
        config: CategoricalConfig,
        population: DatasetHandle | None = None,
        progress: ProgressCallback | None = None,
    ) -> DatasetHandle:
        if not source.cache_complete:
            raise ValueError("The source dataset must finish loading first.")
        if population is not None and not population.cache_complete:
            raise ValueError("The ADSL dataset must finish loading first.")
        config.validate(source.metadata, population.metadata if population else None)
        notify = progress or (lambda _message: None)
        source_fields = self._fields(source)
        population_fields = self._fields(population) if population else {}
        context_names = tuple(
            dict.fromkeys(
                context for item in config.items for context in item.context_variables
            )
        )
        context_variables = tuple(
            source_fields[name.casefold()] for name in context_names
        )
        treatments = self.resolve_treatment_levels(source, config, population)
        directory = self.temp_manager.create_dataset_directory()
        writer = CategoricalResultWriter(
            directory / "dataset.sqlite",
            context_variables,
            tuple((key, label) for key, _value, label in treatments),
            config,
        )
        try:
            for index, item in enumerate(config.items, start=1):
                notify(
                    f"Calculating categorical table… item {index}/{len(config.items)}: {item.variable}"
                )
                numerator, denominator = self._calculate_item(
                    source,
                    population,
                    config,
                    item,
                    source_fields,
                    population_fields,
                )
                self._write_item(
                    writer,
                    config,
                    item,
                    numerator,
                    denominator,
                    treatments,
                    source_fields,
                )
            notify(f"Finalizing Categorical Table… {writer.row_count:,} rows")
            configuration_path = directory / "categorical_config.json"
            write_categorical_configuration(
                configuration_path,
                source,
                config,
                population,
                treatments,
            )
            return replace(
                writer.finish(directory, source),
                configuration_path=configuration_path,
            )
        except BaseException:
            writer.abort(self.temp_manager, directory)
            raise

    @staticmethod
    def _fields(handle: DatasetHandle | None) -> dict[str, VariableMetadata]:
        return (
            {
                variable.name.casefold(): variable
                for variable in handle.metadata.variables
            }
            if handle is not None
            else {}
        )

    def resolve_treatment_levels(
        self,
        source: DatasetHandle,
        config: CategoricalConfig,
        population: DatasetHandle | None = None,
    ) -> list[tuple[str, object, str]]:
        """Resolve the exact treatment order shared by results, JSON, and SAS."""

        if not source.cache_complete:
            raise ValueError("The source dataset must finish loading first.")
        if population is not None and not population.cache_complete:
            raise ValueError("The ADSL dataset must finish loading first.")
        config.validate(source.metadata, population.metadata if population else None)
        source_fields = self._fields(source)
        treatments = self._treatment_levels(source, config, source_fields)
        if config.denominator.type == "population" and population is not None:
            population_fields = self._fields(population)
            population_treatment_name = (
                config.denominator.population_treatment_variable
                or config.treatment_variable
            )
            treatments = self._merged_treatments(
                treatments,
                self._treatment_levels(
                    population,
                    config,
                    population_fields,
                    config.denominator.population_filter.sql,
                    config.denominator.population_filter.parameters,
                    population_fields[population_treatment_name.casefold()],
                ),
            )
        treatments.sort(key=lambda entry: self._treatment_sort_key(entry[1]))
        return treatments

    def display_warnings(
        self,
        source: DatasetHandle,
        config: CategoricalConfig,
    ) -> tuple[str, ...]:
        """Report raw levels that collapse to one Propcase display label.

        Collisions are checked within each Item/context group after applying
        Numerator WHERE. They are warnings only: raw levels remain separate
        rows and calculation continues unchanged.
        """

        if not source.cache_complete:
            raise ValueError("The source dataset must finish loading first.")
        fields = self._fields(source)
        warnings: list[str] = []
        with closing(self._connection(source)) as connection:
            for item in config.items:
                if not item.propcase_levels:
                    continue
                item_variable = fields[item.variable.casefold()]
                contexts = tuple(
                    fields[name.casefold()] for name in item.context_variables
                )
                columns = (*contexts, item_variable)
                query = (
                    "SELECT DISTINCT "
                    + ", ".join(quote_identifier(column.name) for column in columns)
                    + " FROM dataset"
                )
                if config.numerator_filter.sql:
                    query += " WHERE " + config.numerator_filter.sql
                displays: dict[tuple[str, str], set[str]] = defaultdict(set)
                for row in connection.execute(
                    query, config.numerator_filter.parameters
                ):
                    raw_level = _canonical(row[-1], item_variable)
                    if _missing(raw_level, item_variable):
                        continue
                    context_key = _json(
                        {
                            name: _canonical(value, variable)
                            for name, value, variable in zip(
                                item.context_variables, row[:-1], contexts
                            )
                        }
                    )
                    display = _display_level(raw_level, propcase=True)
                    displays[(context_key, display)].add(_json(raw_level))
                for (context_key, display), raw_values in displays.items():
                    if len(raw_values) < 2:
                        continue
                    context = json.loads(context_key)
                    context_text = ", ".join(
                        f"{name}={_display(context.get(name))}"
                        for name in item.context_variables
                    )
                    raw_text = ", ".join(
                        _display(json.loads(value)) for value in sorted(raw_values)
                    )
                    location = item.variable
                    if context_text:
                        location += f" ({context_text})"
                    warnings.append(
                        f'{location}: raw levels {raw_text} all display as "{display}". '
                        "They remain separate rows."
                    )
        return tuple(warnings)

    @staticmethod
    def _merged_treatments(
        first: list[tuple[str, object, str]], second: list[tuple[str, object, str]]
    ) -> list[tuple[str, object, str]]:
        known = {key for key, _value, _label in first}
        return first + [entry for entry in second if entry[0] not in known]

    @staticmethod
    def _treatment_sort_key(value: object) -> tuple[object, ...]:
        if value is None:
            return (1, 0, "")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return (0, 0, float(value), str(value))
        text = str(value)
        return (0, 1, text.casefold(), text)

    @staticmethod
    def _value_sort_key(
        value: object, variable: VariableMetadata
    ) -> tuple[object, ...]:
        """Sort raw categorical values consistently across Python and SAS."""

        if _missing(value, variable):
            return (1, 0, "")
        if variable.kind == "numeric":
            return (0, 0, float(value), str(value))
        text = str(value)
        return (0, 1, text.casefold(), text)

    def _treatment_levels(
        self,
        handle: DatasetHandle,
        config: CategoricalConfig,
        fields: dict[str, VariableMetadata],
        where_sql: str | None = None,
        parameters: tuple[object, ...] = (),
        treatment: VariableMetadata | None = None,
    ) -> list[tuple[str, object, str]]:
        treatment = treatment or fields[config.treatment_variable.casefold()]
        sql = where_sql if where_sql is not None else config.numerator_filter.sql
        params = (
            parameters if where_sql is not None else config.numerator_filter.parameters
        )
        where = f" WHERE {sql}" if sql else ""
        query = (
            f"SELECT DISTINCT {quote_identifier(treatment.name)} FROM dataset{where}"
        )
        with closing(self._connection(handle)) as connection:
            values = []
            for (raw_value,) in connection.execute(query, params):
                if _missing(raw_value, treatment):
                    raise MissingTreatmentError(
                        "Missing treatment values were found. Modify the Dataset or Population WHERE before running."
                    )
                values.append(_canonical(raw_value, treatment))
        values.sort(key=self._treatment_sort_key)
        return [(_json(value), value, _display(value)) for value in values]

    @staticmethod
    def _connection(handle: DatasetHandle) -> sqlite3.Connection:
        return sqlite3.connect(
            handle.database_path.resolve().as_uri() + "?mode=ro", uri=True
        )

    def _calculate_item(
        self,
        source: DatasetHandle,
        population: DatasetHandle | None,
        config: CategoricalConfig,
        item: CategoricalItem,
        source_fields: dict[str, VariableMetadata],
        population_fields: dict[str, VariableMetadata],
    ) -> tuple[dict[tuple[str, str, str], int], dict[tuple[str, str], int]]:
        if config.denominator.type == "baseline_postbaseline":
            return self._n1_item(source, config, item, source_fields)
        numerator = self._numerator(source, config, item, source_fields)
        if config.denominator.type == "population":
            assert population is not None
            denominator = self._population_denominator(
                population, config, item, population_fields
            )
        else:
            denominator = self._nonmissing_denominator(
                source, config, item, source_fields
            )
        return numerator, denominator

    def _selected_columns(
        self,
        treatment: VariableMetadata,
        contexts: Iterable[VariableMetadata],
        item: VariableMetadata | None,
        subject: VariableMetadata | None,
    ) -> tuple[list[VariableMetadata], dict[str, int]]:
        columns = [treatment, *contexts]
        if item is not None:
            columns.append(item)
        if subject is not None and all(
            subject.name != column.name for column in columns
        ):
            columns.append(subject)
        return columns, {column.name: index for index, column in enumerate(columns)}

    def _numerator(
        self,
        source: DatasetHandle,
        config: CategoricalConfig,
        item_config: CategoricalItem,
        fields: dict[str, VariableMetadata],
    ) -> dict[tuple[str, str, str], int]:
        treatment = fields[config.treatment_variable.casefold()]
        item = fields[item_config.variable.casefold()]
        contexts = tuple(
            fields[name.casefold()] for name in item_config.context_variables
        )
        subject = fields.get(config.subject_id_variable.casefold())
        columns, positions = self._selected_columns(treatment, contexts, item, subject)
        query = (
            "SELECT "
            + ", ".join(quote_identifier(column.name) for column in columns)
            + " FROM dataset"
        )
        if config.numerator_filter.sql:
            query += " WHERE " + config.numerator_filter.sql
        values: dict[tuple[object, ...], int | set[object]] = {}
        with closing(self._connection(source)) as connection:
            for row in connection.execute(query, config.numerator_filter.parameters):
                level = _canonical(row[positions[item.name]], item)
                if _missing(level, item) and not item_config.include_missing_level:
                    continue
                context_key = _json(
                    {
                        name: _canonical(row[positions[context.name]], context)
                        for name, context in zip(
                            item_config.context_variables, contexts
                        )
                    }
                )
                treatment_key = _json(
                    _canonical(row[positions[treatment.name]], treatment)
                )
                level_key = _json(level)
                identifier = (
                    _canonical(row[positions[subject.name]], subject)
                    if subject is not None
                    else None
                )
                _count_value(
                    values,
                    (context_key, treatment_key, level_key),
                    identifier,
                    config.count_type,
                    subject,
                )
                if config.include_total:
                    _count_value(
                        values,
                        (context_key, None, level_key),
                        identifier,
                        config.count_type,
                        subject,
                    )
        return _materialize_counts(values)

    def _population_denominator(
        self,
        population: DatasetHandle,
        config: CategoricalConfig,
        item_config: CategoricalItem,
        fields: dict[str, VariableMetadata],
    ) -> dict[tuple[str, str], int]:
        population_treatment_name = (
            config.denominator.population_treatment_variable
            or config.treatment_variable
        )
        treatment = fields[population_treatment_name.casefold()]
        contexts = tuple(
            fields[name.casefold()] for name in item_config.context_variables
        )
        subject = fields.get(config.subject_id_variable.casefold())
        columns, positions = self._selected_columns(treatment, contexts, None, subject)
        query = (
            "SELECT "
            + ", ".join(quote_identifier(column.name) for column in columns)
            + " FROM dataset"
        )
        if config.denominator.population_filter.sql:
            query += " WHERE " + config.denominator.population_filter.sql
        values: dict[tuple[object, ...], int | set[object]] = {}
        with closing(self._connection(population)) as connection:
            for row in connection.execute(
                query, config.denominator.population_filter.parameters
            ):
                if _missing(row[positions[treatment.name]], treatment):
                    raise MissingTreatmentError(
                        "Missing treatment values were found in denominator data."
                    )
                context_key = _json(
                    {
                        name: _canonical(row[positions[context.name]], context)
                        for name, context in zip(
                            item_config.context_variables, contexts
                        )
                    }
                )
                treatment_key = _json(
                    _canonical(row[positions[treatment.name]], treatment)
                )
                identifier = (
                    _canonical(row[positions[subject.name]], subject)
                    if subject is not None
                    else None
                )
                _count_value(
                    values,
                    (context_key, treatment_key),
                    identifier,
                    config.count_type,
                    subject,
                )
                if config.include_total:
                    _count_value(
                        values,
                        (context_key, None),
                        identifier,
                        config.count_type,
                        subject,
                    )
        return _materialize_counts(values)

    def _nonmissing_denominator(
        self,
        source: DatasetHandle,
        config: CategoricalConfig,
        item_config: CategoricalItem,
        fields: dict[str, VariableMetadata],
    ) -> dict[tuple[str, str], int]:
        treatment = fields[config.treatment_variable.casefold()]
        analysis = fields[config.denominator.analysis_value_variable.casefold()]
        contexts = tuple(
            fields[name.casefold()] for name in item_config.context_variables
        )
        subject = fields.get(config.subject_id_variable.casefold())
        columns, positions = self._selected_columns(
            treatment, contexts, analysis, subject
        )
        query = (
            "SELECT "
            + ", ".join(quote_identifier(column.name) for column in columns)
            + " FROM dataset"
        )
        if config.numerator_filter.sql:
            query += " WHERE " + config.numerator_filter.sql
        values: dict[tuple[object, ...], int | set[object]] = {}
        with closing(self._connection(source)) as connection:
            for row in connection.execute(query, config.numerator_filter.parameters):
                if _missing(row[positions[analysis.name]], analysis):
                    continue
                context_key = _json(
                    {
                        name: _canonical(row[positions[context.name]], context)
                        for name, context in zip(
                            item_config.context_variables, contexts
                        )
                    }
                )
                treatment_key = _json(
                    _canonical(row[positions[treatment.name]], treatment)
                )
                identifier = (
                    _canonical(row[positions[subject.name]], subject)
                    if subject is not None
                    else None
                )
                _count_value(
                    values,
                    (context_key, treatment_key),
                    identifier,
                    config.count_type,
                    subject,
                )
                if config.include_total:
                    _count_value(
                        values,
                        (context_key, None),
                        identifier,
                        config.count_type,
                        subject,
                    )
        return _materialize_counts(values)

    def _n1_item(
        self,
        source: DatasetHandle,
        config: CategoricalConfig,
        item_config: CategoricalItem,
        fields: dict[str, VariableMetadata],
    ) -> tuple[dict[tuple[str, str, str], int], dict[tuple[str, str], int]]:
        """Use postbaseline eligible records for both numerator and n1 denominator.

        This follows the agreed record-level n1 rule.  A baseline record and a
        postbaseline record must exist for the same treatment/context/subject.
        """
        treatment = fields[config.treatment_variable.casefold()]
        item = fields[item_config.variable.casefold()]
        analysis = fields[config.denominator.analysis_value_variable.casefold()]
        subject = fields[config.subject_id_variable.casefold()]
        contexts = tuple(
            fields[name.casefold()] for name in item_config.context_variables
        )
        columns, positions = self._selected_columns(treatment, contexts, item, subject)
        if analysis.name not in positions:
            columns.append(analysis)
            positions[analysis.name] = len(columns) - 1
        select = ", ".join(quote_identifier(column.name) for column in columns)
        base_sql, base_params = _and(
            (config.numerator_filter.sql, config.numerator_filter.parameters),
            (
                config.denominator.baseline_filter.sql,
                config.denominator.baseline_filter.parameters,
            ),
        )
        post_sql, post_params = _and(
            (config.numerator_filter.sql, config.numerator_filter.parameters),
            (
                config.denominator.postbaseline_filter.sql,
                config.denominator.postbaseline_filter.parameters,
            ),
        )

        def group_key(row: tuple[object, ...]) -> tuple[str, str, object]:
            context = _json(
                {
                    name: _canonical(row[positions[field.name]], field)
                    for name, field in zip(item_config.context_variables, contexts)
                }
            )
            return (
                context,
                _json(_canonical(row[positions[treatment.name]], treatment)),
                _canonical(row[positions[subject.name]], subject),
            )

        eligible: set[tuple[str, str, object]] = set()
        base_query = f"SELECT {select} FROM dataset" + (
            f" WHERE {base_sql}" if base_sql else ""
        )
        with closing(self._connection(source)) as connection:
            for row in connection.execute(base_query, base_params):
                if _missing(row[positions[treatment.name]], treatment):
                    raise MissingTreatmentError(
                        "Missing treatment values were found. Modify the Dataset WHERE before running."
                    )
                if not _missing(
                    row[positions[analysis.name]], analysis
                ) and not _missing(row[positions[subject.name]], subject):
                    eligible.add(group_key(row))
        numerator_values: dict[tuple[object, ...], int | set[object]] = {}
        denominator_values: dict[tuple[object, ...], int | set[object]] = {}
        post_query = f"SELECT {select} FROM dataset" + (
            f" WHERE {post_sql}" if post_sql else ""
        )
        with closing(self._connection(source)) as connection:
            for row in connection.execute(post_query, post_params):
                if _missing(row[positions[treatment.name]], treatment):
                    raise MissingTreatmentError(
                        "Missing treatment values were found. Modify the Dataset WHERE before running."
                    )
                if (
                    _missing(row[positions[analysis.name]], analysis)
                    or group_key(row) not in eligible
                ):
                    continue
                context_key, treatment_key, identifier = group_key(row)
                _count_value(
                    denominator_values,
                    (context_key, treatment_key),
                    identifier,
                    config.count_type,
                    subject,
                )
                if config.include_total:
                    _count_value(
                        denominator_values,
                        (context_key, None),
                        identifier,
                        config.count_type,
                        subject,
                    )
                level = _canonical(row[positions[item.name]], item)
                if _missing(level, item) and not item_config.include_missing_level:
                    continue
                level_key = _json(level)
                _count_value(
                    numerator_values,
                    (context_key, treatment_key, level_key),
                    identifier,
                    config.count_type,
                    subject,
                )
                if config.include_total:
                    _count_value(
                        numerator_values,
                        (context_key, None, level_key),
                        identifier,
                        config.count_type,
                        subject,
                    )
        return _materialize_counts(numerator_values), _materialize_counts(
            denominator_values
        )

    @staticmethod
    def _format_cell(freq: int, denom: int, digits: int) -> str:
        if denom == 0:
            return "0 (—)"
        quantizer = Decimal(1).scaleb(-digits)
        percent = (Decimal(freq) * Decimal(100) / Decimal(denom)).quantize(
            quantizer, rounding=ROUND_HALF_UP
        )
        return f"{freq} ({percent:.{digits}f})"

    def _write_item(
        self,
        writer: CategoricalResultWriter,
        config: CategoricalConfig,
        item: CategoricalItem,
        numerator: dict[tuple[str, str, str], int],
        denominator: dict[tuple[str, str], int],
        treatments: list[tuple[str, object, str]],
        fields: dict[str, VariableMetadata],
    ) -> None:
        all_treatments = [key for key, _value, _label in treatments]
        if config.include_total:
            all_treatments.append(None)
        rows: dict[tuple[str, str], dict[str, int]] = defaultdict(dict)
        for (context_key, treatment_key, level_key), freq in numerator.items():
            rows[(context_key, level_key)][treatment_key] = freq
        context_names = item.context_variables
        item_variable = fields[item.variable.casefold()]
        context_variables = tuple(
            fields[name.casefold()] for name in item.context_variables
        )

        def context_sort_key(context_key: str) -> tuple[object, ...]:
            context = json.loads(context_key)
            return (
                *(
                    self._value_sort_key(context.get(name), variable)
                    for name, variable in zip(item.context_variables, context_variables)
                ),
            )

        context_keys = sorted(
            {context_key for context_key, _level_key in rows},
            key=context_sort_key,
        )
        custom_ranks = {
            self._custom_level_key(value, item_variable): index
            for index, value in enumerate(item.level_order)
        }

        def level_frequency(level_key: str, context_key: str) -> int:
            values = rows[(context_key, level_key)]
            if config.include_total and None in values:
                return values[None]
            return sum(values.values())

        def level_sort_key(level_key: str, context_key: str) -> tuple[object, ...]:
            level = json.loads(level_key)
            if item.level_sort_method == "frequency_descending":
                return (
                    -level_frequency(level_key, context_key),
                    self._value_sort_key(level, item_variable),
                )
            if item.level_sort_method == "custom":
                rank = custom_ranks.get(self._custom_level_key(level, item_variable))
                if rank is not None:
                    return (0, rank)
                return (1, self._value_sort_key(level, item_variable))
            return self._value_sort_key(level, item_variable)

        for context_key in context_keys:
            levels = [
                level_key
                for current_context, level_key in rows
                if current_context == context_key
            ]
            if item.level_sort_method == "runtime_value_descending":
                nonmissing = [
                    level_key
                    for level_key in levels
                    if not _missing(json.loads(level_key), item_variable)
                ]
                missing = [
                    level_key
                    for level_key in levels
                    if _missing(json.loads(level_key), item_variable)
                ]
                levels = (
                    sorted(
                        nonmissing,
                        key=lambda level_key: self._value_sort_key(
                            json.loads(level_key), item_variable
                        ),
                        reverse=True,
                    )
                    + missing
                )
            elif item.level_sort_method == "frequency_descending":
                nonmissing = [
                    level_key
                    for level_key in levels
                    if not _missing(json.loads(level_key), item_variable)
                ]
                missing = [
                    level_key
                    for level_key in levels
                    if _missing(json.loads(level_key), item_variable)
                ]
                levels = sorted(
                    nonmissing,
                    key=lambda level_key: level_sort_key(level_key, context_key),
                ) + sorted(
                    missing,
                    key=lambda level_key: -level_frequency(level_key, context_key),
                )
            else:
                levels.sort(
                    key=lambda level_key: level_sort_key(level_key, context_key)
                )
            context = json.loads(context_key)
            context_text = ", ".join(
                f"{name}={_display(context.get(name))}" for name in context_names
            )
            item_title = item.label or item.variable
            header = (
                item_title if not context_text else f"{item_title} — {context_text}"
            )
            writer.add_header(header)
            for level_key in levels:
                level = json.loads(level_key)
                cells: dict[str | None, tuple[str, str]] = {}
                long_cells: dict[str | None, tuple[int, int]] = {}
                treatment_json: dict[str | None, str] = {}
                for treatment_key in all_treatments:
                    cell_frequency = rows[(context_key, level_key)].get(
                        treatment_key, 0
                    )
                    denom = denominator.get((context_key, treatment_key), 0)
                    cells[treatment_key] = (
                        self._format_cell(cell_frequency, denom, config.percent_digits),
                        "",
                    )
                    long_cells[treatment_key] = (cell_frequency, denom)
                    treatment_json[treatment_key] = treatment_key
                writer.add_level_row(
                    f"\u00a0\u00a0\u00a0\u00a0{_display_level(level, propcase=item.propcase_levels)}",
                    item.variable,
                    item.label or item.variable,
                    cells,
                    long_cells,
                    context_json=_json(
                        {name: context.get(name) for name in context_names}
                    ),
                    level_json=level_key,
                    treatment_json=treatment_json,
                )

    @staticmethod
    def _custom_level_key(value: object, variable: VariableMetadata) -> str:
        if _missing(value, variable):
            return "<missing>"
        if variable.kind == "character":
            return str(value).casefold()
        return _json(_canonical(value, variable))
