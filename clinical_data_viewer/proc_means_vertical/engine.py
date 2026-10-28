from __future__ import annotations

import sqlite3
from collections import defaultdict
from contextlib import closing
from decimal import ROUND_HALF_UP, Decimal

from ..domain import DatasetHandle, VariableMetadata
from ..filter_engine import quote_identifier
from ..statistics import calculate_value_statistics, observed_decimal_places
from ..temp_manager import TempManager
from .configuration import write_proc_means_vertical_configuration
from .models import ProcMeansVerticalConfig
from .result_store import ProcMeansVerticalResultWriter


def _missing(value: object, variable: VariableMetadata) -> bool:
    return value is None or (variable.kind == "character" and value == "")


def _group_sort_key(values: tuple[object, ...]) -> tuple[tuple[int, str], ...]:
    return tuple(
        (0, "") if value is None else (1, str(value).casefold()) for value in values
    )


def _format_value(value: object, decimals: int) -> str:
    if value is None:
        return "—"
    if isinstance(value, int):
        return f"{value:,}"
    rounded = Decimal(str(value)).quantize(
        Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP
    )
    return f"{rounded:,.{decimals}f}"


def _statistic_rows(
    statistics: tuple[str, ...],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    rows: list[tuple[str, tuple[str, ...]]] = []
    consumed: set[str] = set()
    for statistic in statistics:
        if statistic in consumed:
            continue
        if statistic in {"q1", "q3"} and {"q1", "q3"}.issubset(statistics):
            rows.append(("Q1, Q3", ("q1", "q3")))
            consumed.update({"q1", "q3"})
        elif statistic in {"min", "max"} and {"min", "max"}.issubset(statistics):
            rows.append(("Min, Max", ("min", "max")))
            consumed.update({"min", "max"})
        else:
            labels = {
                "n": "n",
                "subjects": "N",
                "mean": "Mean",
                "std": "SD",
                "stderr": "SE",
                "median": "Median",
                "q1": "Q1",
                "q3": "Q3",
                "min": "Min",
                "max": "Max",
                "nmiss": "NMISS",
                "lclm": "CI Lower",
                "uclm": "CI Upper",
            }
            rows.append((labels.get(statistic, statistic.upper()), (statistic,)))
            consumed.add(statistic)
    return tuple(rows)


class ProcMeansVerticalEngine:
    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    @staticmethod
    def _where(compiled) -> tuple[str, tuple[object, ...]]:
        return (
            f" WHERE {compiled.sql}" if compiled.sql else "",
            compiled.parameters,
        )

    def _population_counts(
        self,
        source: DatasetHandle,
        config: ProcMeansVerticalConfig,
        population: DatasetHandle | None,
    ) -> tuple[dict[object, int], int, VariableMetadata | None]:
        target = population or source
        compiled = config.population_filter if population else config.compiled_filter
        treatment_name = (
            config.population_treatment_variable
            if population
            else config.treatment_variable
        )
        fields = {
            variable.name.casefold(): variable for variable in target.metadata.variables
        }
        treatment = fields.get(treatment_name.casefold()) if treatment_name else None
        subject = fields.get("usubjid")
        if subject is None:
            raise ValueError("USUBJID is required to calculate Population N.")
        selected = [subject] + ([treatment] if treatment else [])
        where, parameters = self._where(compiled)
        sql = "SELECT " + ", ".join(quote_identifier(v.name) for v in selected)
        sql += f" FROM dataset{where}"
        counts: defaultdict[object, set[object]] = defaultdict(set)
        all_subjects: set[object] = set()
        with closing(
            sqlite3.connect(
                target.database_path.resolve().as_uri() + "?mode=ro", uri=True
            )
        ) as connection:
            for row in connection.execute(sql, parameters):
                subject_value = row[0]
                if _missing(subject_value, subject):
                    continue
                if treatment is None:
                    all_subjects.add(subject_value)
                    continue
                treatment_value = row[1]
                if _missing(treatment_value, treatment):
                    raise ValueError(
                        "Population Dataset contains missing treatment values. "
                        "Add a non-missing treatment condition to Population WHERE."
                    )
                counts[treatment_value].add(subject_value)
                all_subjects.add(subject_value)
        return (
            {key: len(value) for key, value in counts.items()},
            len(all_subjects),
            treatment,
        )

    def _run_rows(
        self,
        source: DatasetHandle,
        config: ProcMeansVerticalConfig,
        population: DatasetHandle | None,
        progress=None,
    ) -> DatasetHandle:
        """Build the same statistics with row groups instead of group columns."""
        fields = {
            variable.name.casefold(): variable for variable in source.metadata.variables
        }
        analyses = tuple(fields[name.casefold()] for name in config.analysis_variables)
        row_groups = tuple(
            fields[name.casefold()] for name in config.row_group_variables
        )
        column_groups = tuple(
            fields[name.casefold()] for name in config.group_variables
        )
        selected = [*row_groups, *column_groups, *analyses]
        subject = fields.get("usubjid")
        if subject and subject.name.casefold() not in {
            v.name.casefold() for v in selected
        }:
            selected.append(subject)
        where, parameters = self._where(config.compiled_filter)
        sql = "SELECT " + ", ".join(quote_identifier(v.name) for v in selected)
        sql += f" FROM dataset{where}"
        stats_by_analysis: dict[
            str, dict[tuple[tuple[object, ...], tuple[object, ...]], dict[str, object]]
        ] = {analysis.name: {} for analysis in analyses}
        row_keys: set[tuple[object, ...]] = set()
        column_keys: set[tuple[object, ...]] = set()
        decimal_bases: dict[
            tuple[str, tuple[object, ...], tuple[object, ...]], int
        ] = {}
        decimal_row_totals: dict[tuple[str, tuple[object, ...]], int] = {}
        with closing(
            sqlite3.connect(
                source.database_path.resolve().as_uri() + "?mode=ro", uri=True
            )
        ) as connection:
            for row in connection.execute(sql, parameters):
                row_values = tuple(
                    None if _missing(row[index], variable) else row[index]
                    for index, variable in enumerate(row_groups)
                )
                column_offset = len(row_groups)
                column_values = tuple(
                    None
                    if _missing(row[column_offset + index], variable)
                    else row[column_offset + index]
                    for index, variable in enumerate(column_groups)
                )
                row_keys.add(row_values)
                column_keys.add(column_values)
                subject_value = (
                    row[len(row_groups) + len(column_groups) + len(analyses)]
                    if subject
                    else None
                )
                for index, analysis in enumerate(analyses):
                    value = row[len(row_groups) + len(column_groups) + index]
                    key = (row_values, column_values)
                    bucket = stats_by_analysis[analysis.name].setdefault(
                        key, {"values": [], "subjects": set(), "records": 0}
                    )
                    bucket["records"] += 1
                    if value is None:
                        continue
                    numeric = float(value)
                    bucket["values"].append(numeric)
                    if subject and not _missing(subject_value, subject):
                        bucket["subjects"].add(subject_value)
                    decimal_key = (analysis.name, row_values, column_values)
                    decimal_bases[decimal_key] = max(
                        decimal_bases.get(decimal_key, 0),
                        observed_decimal_places(numeric),
                    )
                    total_key = (analysis.name, row_values)
                    decimal_row_totals[total_key] = max(
                        decimal_row_totals.get(total_key, 0),
                        observed_decimal_places(numeric),
                    )
        row_keys_ordered = sorted(row_keys, key=_group_sort_key)
        column_keys_ordered = sorted(column_keys, key=_group_sort_key)
        population_counts, total_population_n, _population_treatment = (
            self._population_counts(source, config, population)
        )
        treatment_index = (
            next(
                (
                    index
                    for index, name in enumerate(config.group_variables)
                    if name.casefold() == config.treatment_variable.casefold()
                ),
                None,
            )
            if config.treatment_variable
            else None
        )

        def label(values: tuple[object, ...]) -> str:
            return (
                " / ".join(
                    "(Missing)" if value is None else str(value) for value in values
                )
                or "All"
            )

        if column_groups:
            output_columns = list(column_keys_ordered)
            column_labels = [label(values) for values in output_columns]
            column_ns = [
                population_counts.get(values[treatment_index])
                if treatment_index is not None
                else total_population_n
                for values in output_columns
            ]
            if config.include_total:
                output_columns.append(None)
                column_labels.append("Total")
                column_ns.append(total_population_n)
        else:
            output_columns = [None]
            column_labels = ["Total" if config.include_total else "Overall"]
            column_ns = [total_population_n]
        total_column_flags = [False] * len(column_labels)
        if config.include_total:
            total_column_flags[-1] = True

        column_variables = [
            VariableMetadata("GROUP", "Group", "character", 200),
            VariableMetadata("ITEM", "Item", "character", 200),
            VariableMetadata("STATISTIC", "Statistic", "character", 32),
        ]
        display_names = [
            ("GROUP", "Group"),
            ("ITEM", "Item"),
            ("STATISTIC", "Statistic"),
        ]
        value_columns: list[str] = []
        for index, column_label in enumerate(column_labels, start=1):
            name = "TOTAL" if total_column_flags[index - 1] else f"COL{index}"
            n_text = (
                "" if column_ns[index - 1] is None else f" (N={column_ns[index - 1]:,})"
            )
            column_variables.append(
                VariableMetadata(name, f"{column_label}{n_text}", "character", 200)
            )
            display_names.append((name, f"{column_label}{n_text}"))
            value_columns.append(name)

        long_variables = (
            VariableMetadata("ITEM", "Item", "character", 200),
            VariableMetadata("STATISTIC", "Statistic", "character", 32),
            VariableMetadata("GROUP", "Group", "character", 200),
            VariableMetadata("VALUE", "Value", "numeric"),
            VariableMetadata("N", "n", "numeric"),
            VariableMetadata("SUBJECT_N", "N", "numeric"),
            VariableMetadata("DISPLAY_VALUE", "Display Value", "character", 200),
        )
        directory = self.temp_manager.create_dataset_directory()
        writer = ProcMeansVerticalResultWriter(
            directory / "dataset.sqlite", tuple(column_variables), long_variables
        )
        labels_by_variable = {
            key.casefold(): value for key, value in config.item_labels
        }
        configured_base_decimals = {
            key.casefold(): value for key, value in config.base_decimals
        }
        decimal_offsets = dict(config.statistic_decimals)
        try:
            rows_definition = _statistic_rows(config.statistics)
            for row_values in row_keys_ordered:
                row_label = label(row_values)
                for analysis in analyses:
                    item_label = labels_by_variable.get(
                        analysis.name.casefold(), analysis.label or analysis.name
                    )
                    values_by_column: list[dict[str, object]] = []
                    for column_values in output_columns:
                        if column_values is None:
                            matching = [
                                bucket
                                for (
                                    candidate_row,
                                    _candidate_column,
                                ), bucket in stats_by_analysis[analysis.name].items()
                                if candidate_row == row_values
                            ]
                            values: list[float] = []
                            subjects: set[object] = set()
                            records = 0
                            for bucket in matching:
                                values.extend(bucket["values"])
                                subjects.update(bucket["subjects"])
                                records += bucket["records"]
                            values_by_column.append(
                                calculate_value_statistics(
                                    values,
                                    records,
                                    len(subjects),
                                    config.confidence,
                                    set(config.statistics),
                                )
                            )
                        else:
                            bucket = stats_by_analysis[analysis.name].get(
                                (row_values, column_values),
                                {"values": [], "subjects": set(), "records": 0},
                            )
                            values_by_column.append(
                                calculate_value_statistics(
                                    bucket["values"],
                                    bucket["records"],
                                    len(bucket["subjects"]),
                                    config.confidence,
                                    set(config.statistics),
                                )
                            )
                    for statistic_label, statistic_keys in rows_definition:
                        display_values: list[str] = []
                        for index, statistics in enumerate(values_by_column):
                            column_values = output_columns[index]
                            if column_values is None:
                                base_decimals = decimal_row_totals.get(
                                    (analysis.name, row_values), 0
                                )
                            else:
                                base_decimals = decimal_bases.get(
                                    (analysis.name, row_values, column_values), 0
                                )
                            configured_base = configured_base_decimals.get(
                                analysis.name.casefold()
                            )
                            if configured_base is None:
                                configured_base = base_decimals
                            decimals = min(
                                4,
                                configured_base
                                + decimal_offsets.get(statistic_keys[0], 0),
                            )
                            display_values.append(
                                ", ".join(
                                    _format_value(statistics.get(key), decimals)
                                    for key in statistic_keys
                                )
                            )
                            group_label = row_label
                            if column_values is not None:
                                group_label = f"{row_label} / {label(column_values)}"
                            writer.add_long(
                                (
                                    item_label,
                                    statistic_label,
                                    group_label,
                                    statistics.get(statistic_keys[0]),
                                    statistics.get("n"),
                                    statistics.get("subjects"),
                                    display_values[-1],
                                )
                            )
                        result_row = writer.add_row(
                            (row_label, item_label, statistic_label, *display_values)
                        )
                        for index, column_name in enumerate(value_columns):
                            column_values = output_columns[index]
                            mapped_values = (
                                (*row_values, *column_values)
                                if column_values is not None
                                else row_values or None
                            )
                            writer.add_cell(
                                result_row,
                                column_name,
                                analysis.name,
                                statistic_keys[0],
                                statistic_label,
                                mapped_values,
                            )
            configuration_path = directory / "proc_means_vertical_config.json"
            write_proc_means_vertical_configuration(
                configuration_path, source, config, population
            )
            return writer.finish(
                directory, source, configuration_path, tuple(display_names)
            )
        except BaseException:
            writer.abort(self.temp_manager, directory)
            raise

    def run(
        self,
        source: DatasetHandle,
        config: ProcMeansVerticalConfig,
        population: DatasetHandle | None = None,
        progress=None,
    ) -> DatasetHandle:
        config.validate(source.metadata, population.metadata if population else None)
        if config.output_layout == "rows":
            return self._run_rows(source, config, population, progress)
        fields = {
            variable.name.casefold(): variable for variable in source.metadata.variables
        }
        analyses = tuple(fields[name.casefold()] for name in config.analysis_variables)
        groups = tuple(fields[name.casefold()] for name in config.group_variables)
        selected = [*groups, *analyses]
        subject = fields.get("usubjid")
        if subject and subject.name.casefold() not in {
            v.name.casefold() for v in selected
        }:
            selected.append(subject)
        where, parameters = self._where(config.compiled_filter)
        sql = "SELECT " + ", ".join(quote_identifier(v.name) for v in selected)
        sql += f" FROM dataset{where}"
        stats_by_analysis: dict[str, dict[tuple[object, ...], dict[str, object]]] = {
            analysis.name: {} for analysis in analyses
        }
        group_keys: set[tuple[object, ...]] = set()
        decimal_bases: dict[tuple[str, tuple[object, ...]], int] = {}
        decimal_total_bases: dict[str, int] = {}
        with closing(
            sqlite3.connect(
                source.database_path.resolve().as_uri() + "?mode=ro", uri=True
            )
        ) as connection:
            rows = connection.execute(sql, parameters)
            for row in rows:
                group_values = tuple(
                    None if _missing(row[index], variable) else row[index]
                    for index, variable in enumerate(groups)
                )
                group_keys.add(group_values)
                subject_value = row[len(groups) + len(analyses)] if subject else None
                for index, analysis in enumerate(analyses):
                    value = row[len(groups) + index]
                    bucket = stats_by_analysis[analysis.name].setdefault(
                        group_values, {"values": [], "subjects": set(), "records": 0}
                    )
                    bucket["records"] += 1
                    if value is None:
                        continue
                    bucket["values"].append(float(value))
                    if subject and not _missing(subject_value, subject):
                        bucket["subjects"].add(subject_value)
                    decimal_key = (analysis.name, group_values)
                    decimal_bases[decimal_key] = max(
                        decimal_bases.get(decimal_key, 0),
                        observed_decimal_places(float(value)),
                    )
                    decimal_total_bases[analysis.name] = max(
                        decimal_total_bases.get(analysis.name, 0),
                        observed_decimal_places(float(value)),
                    )
        group_keys_ordered = sorted(group_keys, key=_group_sort_key)
        population_counts, total_population_n, _population_treatment = (
            self._population_counts(source, config, population)
        )
        treatment_index = (
            next(
                (
                    index
                    for index, name in enumerate(config.group_variables)
                    if name.casefold() == config.treatment_variable.casefold()
                ),
                None,
            )
            if config.treatment_variable
            else None
        )
        group_labels: list[str] = []
        group_ns: list[int | None] = []
        for group_values in group_keys_ordered:
            label = (
                " / ".join(
                    "(Missing)" if value is None else str(value)
                    for value in group_values
                )
                or "All"
            )
            if treatment_index is not None:
                n_value = population_counts.get(group_values[treatment_index])
            else:
                n_value = total_population_n
            group_labels.append(label)
            group_ns.append(n_value)
        if config.include_total:
            group_labels.append("Total")
            group_ns.append(total_population_n)
        column_variables = [
            VariableMetadata("ITEM", "Item", "character", 200),
            VariableMetadata("STATISTIC", "Statistic", "character", 32),
        ]
        display_names = [("ITEM", "Item"), ("STATISTIC", "Statistic")]
        value_columns: list[str] = []
        for index, label in enumerate(group_labels, start=1):
            name = (
                "TOTAL"
                if config.include_total and index == len(group_labels)
                else f"COL{index}"
            )
            n_text = (
                "" if group_ns[index - 1] is None else f" (N={group_ns[index - 1]:,})"
            )
            column_variables.append(
                VariableMetadata(name, f"{label}{n_text}", "character", 200)
            )
            display_names.append((name, f"{label}{n_text}"))
            value_columns.append(name)
        long_variables = (
            VariableMetadata("ITEM", "Item", "character", 200),
            VariableMetadata("STATISTIC", "Statistic", "character", 32),
            VariableMetadata("GROUP", "Group", "character", 200),
            VariableMetadata("VALUE", "Value", "numeric"),
            VariableMetadata("N", "n", "numeric"),
            VariableMetadata("SUBJECT_N", "N", "numeric"),
            VariableMetadata("DISPLAY_VALUE", "Display Value", "character", 200),
        )
        directory = self.temp_manager.create_dataset_directory()
        writer = ProcMeansVerticalResultWriter(
            directory / "dataset.sqlite", tuple(column_variables), long_variables
        )
        labels = {key.casefold(): value for key, value in config.item_labels}
        configured_base_decimals = {
            key.casefold(): value for key, value in config.base_decimals
        }
        decimal_offsets = dict(config.statistic_decimals)
        try:
            rows_definition = _statistic_rows(config.statistics)
            for analysis in analyses:
                item_label = labels.get(
                    analysis.name.casefold(), analysis.label or analysis.name
                )
                values_by_group: list[dict[str, object]] = []
                for group_values in group_keys_ordered:
                    bucket = stats_by_analysis[analysis.name].get(
                        group_values, {"values": [], "subjects": set(), "records": 0}
                    )
                    values_by_group.append(
                        calculate_value_statistics(
                            bucket["values"],
                            bucket["records"],
                            len(bucket["subjects"]),
                            config.confidence,
                            set(config.statistics),
                        )
                    )
                total_stats = None
                if config.include_total:
                    all_values: list[float] = []
                    all_subjects: set[object] = set()
                    records = 0
                    for bucket in stats_by_analysis[analysis.name].values():
                        all_values.extend(bucket["values"])
                        all_subjects.update(bucket["subjects"])
                        records += bucket["records"]
                    total_stats = calculate_value_statistics(
                        all_values,
                        records,
                        len(all_subjects),
                        config.confidence,
                        set(config.statistics),
                    )
                    values_by_group.append(total_stats)
                for statistic_label, statistic_keys in rows_definition:
                    display_values: list[str] = []
                    for index, statistics in enumerate(values_by_group):
                        if index < len(group_keys_ordered):
                            base_decimals = decimal_bases.get(
                                (analysis.name, group_keys_ordered[index]), 0
                            )
                        else:
                            base_decimals = decimal_total_bases.get(analysis.name, 0)
                        configured_base = configured_base_decimals.get(
                            analysis.name.casefold()
                        )
                        if configured_base is None:
                            configured_base = base_decimals
                        decimals = min(
                            4,
                            configured_base + decimal_offsets.get(statistic_keys[0], 0),
                        )
                        formatted = [
                            _format_value(statistics.get(key), decimals)
                            for key in statistic_keys
                        ]
                        display = ", ".join(formatted)
                        display_values.append(display)
                        group_label = group_labels[index]
                        first_key = statistic_keys[0]
                        writer.add_long(
                            (
                                item_label,
                                statistic_label,
                                group_label,
                                statistics.get(first_key),
                                statistics.get("n"),
                                statistics.get("subjects"),
                                display,
                            )
                        )
                    result_row = writer.add_row(
                        (item_label, statistic_label, *display_values)
                    )
                    for index, column_name in enumerate(value_columns):
                        group_values = (
                            group_keys_ordered[index]
                            if index < len(group_keys_ordered)
                            else None
                        )
                        writer.add_cell(
                            result_row,
                            column_name,
                            analysis.name,
                            statistic_keys[0],
                            statistic_label,
                            group_values,
                        )
            configuration_path = directory / "proc_means_vertical_config.json"
            write_proc_means_vertical_configuration(
                configuration_path, source, config, population
            )
            return writer.finish(
                directory, source, configuration_path, tuple(display_names)
            )
        except BaseException:
            writer.abort(self.temp_manager, directory)
            raise


__all__ = ["ProcMeansVerticalEngine"]
