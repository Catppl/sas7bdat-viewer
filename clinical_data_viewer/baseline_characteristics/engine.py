from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from decimal import ROUND_HALF_UP, Decimal

from ..domain import DatasetHandle, VariableMetadata
from ..filter_engine import quote_identifier
from ..statistics import calculate_value_statistics, maximum_observed_decimals
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
    if variable.kind == "numeric":
        return format(float(value), ".15g")
    return str(value)


def _runtime_sort_key(value: object, variable: VariableMetadata) -> tuple[object, ...]:
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
            header_n = int(
                connection.execute(
                    f"SELECT count(DISTINCT {quote_identifier(subject.name)}) FROM dataset{where}",
                    parameters,
                ).fetchone()[0]
            )

        directory = self.temp_manager.create_dataset_directory()
        writer = BaselineCharacteristicsResultWriter(
            directory / "dataset.sqlite", config.header_label, header_n
        )
        try:
            with closing(sqlite3.connect(uri, uri=True)) as connection:
                for item_order, item in enumerate(config.items, start=1):
                    notify(
                        f"Calculating Baseline Characteristics… item {item_order}/{len(config.items)}: {item.variable}"
                    )
                    variable = fields[item.variable.casefold()]
                    label = item.label or variable.label or variable.name
                    if isinstance(item, BaselineCategoricalItem):
                        self._write_categorical(
                            connection,
                            writer,
                            config,
                            item,
                            variable,
                            label,
                            item_order,
                            header_n,
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
                            header_n,
                        )
            configuration_path = directory / "baseline_characteristics_config.json"
            write_baseline_characteristics_configuration(
                configuration_path,
                source,
                config,
                runtime_header_n=header_n,
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
        header_n: int,
    ) -> None:
        writer.add_row(
            item_order=item_order,
            item_kind="categorical",
            variable=variable.name,
            item_label=label,
            row_label=f"{label} - n (%)",
        )
        where, parameters = self._where(config)
        nonmissing = _missing_sql(variable, missing=False)
        condition = where + (" AND " if where else " WHERE ") + nonmissing
        rows = connection.execute(
            f"SELECT {quote_identifier(variable.name)}, "
            f"count(DISTINCT {quote_identifier(config.subject_id_variable)}) "
            f"FROM dataset{condition} GROUP BY {quote_identifier(variable.name)}",
            parameters,
        ).fetchall()
        custom_ranks = {
            _custom_key(value, variable): index
            for index, value in enumerate(item.level_order)
        }

        def sort_key(row: tuple[object, int]) -> tuple[object, ...]:
            value = row[0]
            if item.level_sort_method == "custom":
                rank = custom_ranks.get(_custom_key(value, variable))
                if rank is not None:
                    return (0, rank)
                return (1, *_runtime_sort_key(value, variable))
            return _runtime_sort_key(value, variable)

        for value, numerator in sorted(rows, key=sort_key):
            numerator = int(numerator)
            percent = (numerator * 100.0 / header_n) if header_n else None
            writer.add_row(
                item_order=item_order,
                item_kind="categorical",
                variable=variable.name,
                item_label=label,
                row_label=INDENT + _display_level(value, variable),
                display_value=_format_percent(numerator, header_n, item.percent_digits),
                level_json=json.dumps(value, ensure_ascii=False),
                numerator=numerator,
                denominator=header_n,
                percent=percent,
                drilldown=True,
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
        header_n: int,
    ) -> None:
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
        rows = connection.execute(
            f"SELECT {quote_identifier(variable.name)}, "
            f"{quote_identifier(config.subject_id_variable)} FROM dataset{where}",
            parameters,
        ).fetchall()
        values = [float(value) for value, _subject in rows if value is not None]
        subjects = {_subject for value, _subject in rows if value is not None}
        statistics = calculate_value_statistics(
            values,
            header_n,
            len(subjects),
            0.95,
            {"n", "mean", "std", "median", "q1", "q3", "min", "max"},
        )
        base_decimals = maximum_observed_decimals(values)
        configured = dict(item.statistic_decimals)

        def decimals(key: str) -> int:
            value = configured.get(key)
            return base_decimals if value is None else value

        definitions = (
            ("n", "n", ("n",)),
            ("mean", "Mean", ("mean",)),
            ("std", "SD", ("std",)),
            ("median", "Median", ("median",)),
            ("q1_q3", "Q1, Q3", ("q1", "q3")),
            ("min_max", "Min, Max", ("min", "max")),
        )
        for statistic_key, statistic_label, keys in definitions:
            raw_values = tuple(statistics.get(key) for key in keys)
            if keys == ("n",):
                display = f"{int(raw_values[0] or 0):,}"
            else:
                display = ", ".join(
                    _format_decimal(raw, decimals(key))
                    for key, raw in zip(keys, raw_values, strict=True)
                )
            writer.add_row(
                item_order=item_order,
                item_kind="continuous",
                variable=variable.name,
                item_label=label,
                unit=item.unit,
                row_label=INDENT + statistic_label,
                display_value=display,
                statistic_key=statistic_key,
                raw_value_1=raw_values[0],
                raw_value_2=raw_values[1] if len(raw_values) > 1 else None,
                numerator=int(statistics["n"] or 0),
                denominator=header_n,
                drilldown=True,
            )


__all__ = ["BaselineCharacteristicsEngine"]
