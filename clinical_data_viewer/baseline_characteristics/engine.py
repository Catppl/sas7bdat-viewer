from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from decimal import ROUND_HALF_UP, Decimal

from ..domain import DatasetHandle, VariableMetadata
from ..filter_engine import quote_identifier
from ..statistics import calculate_value_statistics
from ..temp_manager import TempManager
from .configuration import write_baseline_characteristics_configuration
from .models import (
    BaselineCategoricalItem,
    BaselineCharacteristicsConfig,
    BaselineContinuousItem,
)
from .result_store import BaselineCharacteristicsResultWriter

ProgressCallback = Callable[[str], None]
INDENT = "\u00a0" * 4


def _missing_sql(variable: VariableMetadata, *, missing: bool) -> str:
    column = quote_identifier(variable.name)
    expression = (
        f"({column} IS NULL OR {column} = '')"
        if variable.kind == "character"
        else f"{column} IS NULL"
    )
    return expression if missing else f"NOT ({expression})"


def _display_level(value: object, variable: VariableMetadata) -> str:
    if value is None:
        return "Missing"
    if variable.kind == "numeric":
        return format(float(value), ".15g")
    return str(value)


def _runtime_sort_key(value: object, variable: VariableMetadata) -> tuple[object, ...]:
    if value is None:
        return (-1, "")
    if variable.kind == "numeric":
        return (0, float(value))
    return (0, str(value).casefold(), str(value))


def _custom_key(value: object, variable: VariableMetadata) -> object:
    return float(value) if variable.kind == "numeric" else str(value).casefold()


def _format_decimal(value: object, decimals: int) -> str:
    if value is None:
        return "-"
    quantum = Decimal(1).scaleb(-decimals)
    rounded = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    return f"{rounded:.{decimals}f}"


def _format_percent(numerator: int, denominator: int, digits: int) -> str:
    if not denominator:
        return "0 (-)"
    percent = Decimal(numerator * 100) / Decimal(denominator)
    quantum = Decimal(1).scaleb(-digits)
    rounded = percent.quantize(quantum, rounding=ROUND_HALF_UP)
    return f"{numerator:,} ({rounded:.{digits}f})"


class BaselineCharacteristicsEngine:
    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    @staticmethod
    def _where(config: BaselineCharacteristicsConfig) -> tuple[str, tuple[object, ...]]:
        if not config.compiled_filter.sql:
            return "", ()
        return f" WHERE {config.compiled_filter.sql}", config.compiled_filter.parameters

    def run(
        self,
        source: DatasetHandle,
        config: BaselineCharacteristicsConfig,
        progress: ProgressCallback | None = None,
    ) -> DatasetHandle:
        if not source.cache_complete:
            raise ValueError("The source dataset must finish loading first.")
        config.validate(source.metadata)
        notify = progress or (lambda _message: None)
        fields = {
            variable.name.casefold(): variable for variable in source.metadata.variables
        }
        subject = fields[config.subject_id_variable.casefold()]
        where, parameters = self._where(config)
        uri = source.database_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            missing_subjects = connection.execute(
                f"SELECT count(*) FROM dataset{where}"
                + (" AND " if where else " WHERE ")
                + _missing_sql(subject, missing=True),
                parameters,
            ).fetchone()[0]
            if missing_subjects:
                raise ValueError(
                    f"The filtered analysis universe contains {missing_subjects:,} rows with missing {subject.name}."
                )
            duplicate = connection.execute(
                f"SELECT {quote_identifier(subject.name)}, count(*) FROM dataset{where} "
                f"GROUP BY {quote_identifier(subject.name)} HAVING count(*) > 1 LIMIT 1",
                parameters,
            ).fetchone()
            if duplicate is not None:
                raise ValueError(
                    f'The filtered analysis universe must contain one row per {subject.name}; "{duplicate[0]}" occurs {duplicate[1]} times.'
                )
            if config.treatment_variable:
                treatment = fields[config.treatment_variable.casefold()]
                treatment_column = quote_identifier(treatment.name)
                group_rows = connection.execute(
                    f"SELECT {treatment_column}, count(DISTINCT {quote_identifier(subject.name)}) "
                    f"FROM dataset{where}{' AND ' if where else ' WHERE '}{_missing_sql(treatment, missing=False)} "
                    f"GROUP BY {treatment_column} ORDER BY {treatment_column}",
                    parameters,
                ).fetchall()
                groups = [
                    (
                        value,
                        f"TRT_{index}",
                        f"{_display_level(value, treatment)}\n(N={int(count):,})",
                        int(count),
                    )
                    for index, (value, count) in enumerate(group_rows, start=1)
                ]
                if not groups:
                    raise ValueError("The treatment variable has no nonmissing levels.")
            else:
                header_n = int(
                    connection.execute(
                        f"SELECT count(DISTINCT {quote_identifier(subject.name)}) FROM dataset{where}",
                        parameters,
                    ).fetchone()[0]
                )
                groups = [(None, "VALUE", f"{config.header_label}\n(N={header_n:,})", header_n)]

        directory = self.temp_manager.create_dataset_directory()
        writer = BaselineCharacteristicsResultWriter(
            directory / "dataset.sqlite",
            config.header_label,
            groups[0][3],
            tuple((column, label, count) for _value, column, label, count in groups),
        )
        try:
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                for item_order, item in enumerate(config.items, start=1):
                    notify(
                        f"Calculating Baseline Characteristics… item {item_order}/{len(config.items)}: {item.variable}"
                    )
                    variable = fields[item.variable.casefold()]
                    label = item.label
                    if isinstance(item, BaselineCategoricalItem):
                        self._write_categorical(
                            connection,
                            writer,
                            config,
                            item,
                            variable,
                            label,
                            item_order,
                            groups,
                        )
                    else:
                        assert isinstance(item, BaselineContinuousItem)
                        self._write_continuous(
                            connection,
                            writer,
                            config,
                            item,
                            variable,
                            label,
                            item_order,
                            groups,
                        )
            configuration_path = directory / "baseline_characteristics_config.json"
            write_baseline_characteristics_configuration(
                configuration_path,
                source,
                config,
                runtime_header_n=sum(group[3] for group in groups),
            )
            notify(f"Finalizing Baseline Characteristics… {writer.row_count:,} rows")
            return writer.finish(directory, source, configuration_path)
        except BaseException:
            writer.abort(self.temp_manager, directory)
            raise

    def _write_categorical(
        self,
        connection: sqlite3.Connection,
        writer: BaselineCharacteristicsResultWriter,
        config: BaselineCharacteristicsConfig,
        item: BaselineCategoricalItem,
        variable: VariableMetadata,
        label: str,
        item_order: int,
        groups: list[tuple[object | None, str, str, int]],
    ) -> None:
        writer.add_row(
            item_order=item_order,
            item_kind="categorical",
            variable=variable.name,
            item_label=label,
            row_label=f"{label} - n (%)",
        )
        where, parameters = self._where(config)
        base_where = where.removeprefix(" WHERE ")
        custom_ranks = {_custom_key(value, variable): index for index, value in enumerate(item.level_order)}
        level_labels = {_custom_key(value, variable): label for value, label in item.level_labels}
        group_data: dict[str, dict[object, tuple[int, int]]] = {}
        all_values: list[object] = []
        for treatment_value, column, _column_label, group_n in groups:
            clauses = [base_where] if base_where else []
            params = list(parameters)
            if config.treatment_variable:
                treatment_name = config.treatment_variable
                clauses.append(f"{quote_identifier(treatment_name)} = ?")
                params.append(treatment_value)
            nonmissing = _missing_sql(variable, missing=False)
            include_missing = any(value is None for value in item.level_order)
            if not include_missing or item.level_sort_method != "custom":
                clauses.append(nonmissing)
            condition = (
                " WHERE " + " AND ".join(f"({clause})" for clause in clauses if clause)
                if any(clauses)
                else ""
            )
            rows = connection.execute(
                f"SELECT {quote_identifier(variable.name)}, count(DISTINCT {quote_identifier(config.subject_id_variable)}) "
                f"FROM dataset{condition} GROUP BY {quote_identifier(variable.name)}",
                tuple(params),
            ).fetchall()
            group_data[column] = {
                (None if variable.kind == "character" and row[0] == "" else row[0]): int(row[1])
                for row in rows
            }
            for value, _count in rows:
                value = None if variable.kind == "character" and value == "" else value
                if value not in all_values:
                    all_values.append(value)

        def sort_key(value: object) -> tuple[object, ...]:
            if item.level_sort_method == "custom":
                rank = custom_ranks.get(_custom_key(value, variable))
                if rank is not None:
                    return (0, rank)
                return (1, *_runtime_sort_key(value, variable))
            return _runtime_sort_key(value, variable)

        for value in sorted(all_values, key=sort_key):
            display_values = {}
            first_numerator = None
            first_denominator = None
            for treatment_value, column, _column_label, group_n in groups:
                numerator = group_data[column].get(value, 0)
                display_values[column] = _format_percent(numerator, group_n, item.percent_digits)
                first_numerator = numerator if first_numerator is None else first_numerator
                first_denominator = group_n if first_denominator is None else first_denominator
            writer.add_row(
                item_order=item_order,
                item_kind="categorical",
                variable=variable.name,
                item_label=label,
                row_label=INDENT + level_labels.get(_custom_key(value, variable), _display_level(value, variable)),
                display_values=display_values,
                level_json=json.dumps(value, ensure_ascii=False),
                numerator=first_numerator,
                denominator=first_denominator,
                drilldown=True,
                treatment_levels={column: json.dumps(treatment_value, ensure_ascii=False) for treatment_value, column, _label, _n in groups},
            )

    def _write_continuous(
        self,
        connection: sqlite3.Connection,
        writer: BaselineCharacteristicsResultWriter,
        config: BaselineCharacteristicsConfig,
        item: BaselineContinuousItem,
        variable: VariableMetadata,
        label: str,
        item_order: int,
        groups: list[tuple[object | None, str, str, int]],
    ) -> None:
        # ``unit`` remains accepted for backwards-compatible JSON/model inputs;
        # the Builder no longer exposes a separate Unit field.
        heading = f"{label} ({item.unit})" if item.unit.strip() else label
        writer.add_row(
            item_order=item_order,
            item_kind="continuous",
            variable=variable.name,
            item_label=label,
            unit=item.unit,
            row_label=heading,
        )
        where, parameters = self._where(config)
        base_where = where.removeprefix(" WHERE ")
        group_stats: dict[str, tuple[dict[str, object], int]] = {}
        configured = dict(item.statistic_decimals)
        for treatment_value, column, _column_label, group_n in groups:
            clauses = [base_where] if base_where else []
            params = list(parameters)
            if config.treatment_variable:
                clauses.append(f"{quote_identifier(config.treatment_variable)} = ?")
                params.append(treatment_value)
            condition = (
                " WHERE " + " AND ".join(f"({clause})" for clause in clauses if clause)
                if any(clauses)
                else ""
            )
            rows = connection.execute(
                f"SELECT {quote_identifier(variable.name)}, {quote_identifier(config.subject_id_variable)} FROM dataset{condition}",
                tuple(params),
            ).fetchall()
            values = [float(value) for value, _subject in rows if value is not None]
            subjects = {_subject for value, _subject in rows if value is not None}
            group_stats[column] = (
                calculate_value_statistics(values, group_n, len(subjects), 0.95, set(item.statistics)),
                group_n,
            )

        def decimals(key: str) -> int:
            value = configured.get(key)
            return 0 if value is None else int(value)

        definitions = (
            ("n", "n", ("n",)),
            ("mean", "Mean", ("mean",)),
            ("std", "SD", ("std",)),
            ("median", "Median", ("median",)),
            ("q1_q3", "Q1, Q3", ("q1", "q3")),
            ("min_max", "Min, Max", ("min", "max")),
        )
        for statistic_key, statistic_label, keys in definitions:
            if not any(key in item.statistics for key in keys):
                continue
            display_values = {}
            first_raw_values = None
            for _treatment_value, column, _column_label, _group_n in groups:
                statistics, group_n = group_stats[column]
                raw_values = tuple(statistics.get(key) for key in keys)
                if first_raw_values is None:
                    first_raw_values = raw_values
                if keys == ("n",):
                    display_values[column] = f"{int(raw_values[0] or 0):,}"
                else:
                    display_values[column] = ", ".join(
                        _format_decimal(raw, decimals(key))
                        for key, raw in zip(keys, raw_values, strict=True)
                    )
            if keys == ("n",):
                display = next(iter(display_values.values()))
            else:
                display = next(iter(display_values.values()))
            writer.add_row(
                item_order=item_order,
                item_kind="continuous",
                variable=variable.name,
                item_label=label,
                unit=item.unit,
                row_label=INDENT + statistic_label,
                display_value=display,
                display_values=display_values,
                statistic_key=statistic_key,
                raw_value_1=first_raw_values[0] if first_raw_values else None,
                raw_value_2=first_raw_values[1] if first_raw_values and len(first_raw_values) > 1 else None,
                numerator=int(group_stats[groups[0][1]][0].get("n") or 0),
                denominator=groups[0][3],
                drilldown=True,
                treatment_levels={column: json.dumps(treatment_value, ensure_ascii=False) for treatment_value, column, _label, _n in groups},
            )


__all__ = ["BaselineCharacteristicsEngine"]
