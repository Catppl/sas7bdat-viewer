from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing

from ..domain import DatasetHandle, VariableMetadata
from ..filter_engine import quote_identifier
from ..statistics import calculate_value_statistics, observed_decimal_places
from ..temp_manager import TempManager
from .models import ProcMeansConfig
from .result_store import ProcMeansResultWriter, build_result_schema

ProgressCallback = Callable[[str], None]


def _normalized_group(value: object, kind: str) -> object:
    return None if value is None or (kind == "character" and value == "") else value


def _sortable_group(values: tuple[object, ...]) -> tuple[tuple[int, object], ...]:
    return tuple((0, "") if value is None else (1, value) for value in values)


class ProcMeansEngine:
    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    def run(
        self,
        source: DatasetHandle,
        config: ProcMeansConfig,
        progress: ProgressCallback | None = None,
    ) -> DatasetHandle:
        if not source.cache_complete:
            raise ValueError("The source dataset must finish loading first.")
        config.validate(source.metadata)
        notify = progress or (lambda _message: None)
        by_fold = {
            variable.name.casefold(): variable for variable in source.metadata.variables
        }
        analyses = tuple(by_fold[name.casefold()] for name in config.analysis_variables)
        groups = tuple(by_fold[name.casefold()] for name in config.group_variables)
        decimal_groups = tuple(
            by_fold[name.casefold()] for name in config.decimal_group_variables
        )
        result_directory = self.temp_manager.create_dataset_directory()
        schema = build_result_schema(groups, config.statistics)
        writer = ProcMeansResultWriter(
            result_directory / "dataset.sqlite", schema, config
        )
        try:
            notify("Determining observed decimal precision…")
            decimal_bases = self._decimal_bases(
                source, config, analyses, decimal_groups
            )
            notify("Calculating grouped PROC MEANS statistics…")
            self._calculate_groups(
                source,
                config,
                analyses,
                groups,
                decimal_groups,
                decimal_bases,
                writer,
                notify,
            )
            notify(f"Finalizing PROC MEANS Result… {writer.row_count:,} rows")
            return writer.finish(result_directory, source)
        except BaseException:
            writer.abort()
            self.temp_manager.remove_dataset(result_directory)
            raise

    @staticmethod
    def _where(config: ProcMeansConfig) -> tuple[str, tuple[object, ...]]:
        return (
            f" WHERE {config.compiled_filter.sql}"
            if config.compiled_filter.sql
            else "",
            config.compiled_filter.parameters,
        )

    def _decimal_bases(
        self,
        source: DatasetHandle,
        config: ProcMeansConfig,
        analyses: tuple[VariableMetadata, ...],
        decimal_groups: tuple[VariableMetadata, ...],
    ) -> dict[tuple[str, tuple[object, ...]], int]:
        columns = [*decimal_groups, *analyses]
        select = ", ".join(quote_identifier(variable.name) for variable in columns)
        where, parameters = self._where(config)
        bases: dict[tuple[str, tuple[object, ...]], int] = {}
        uri = source.database_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            rows = connection.execute(
                f"SELECT {select} FROM dataset{where}", parameters
            )
            for row in rows:
                group_values = tuple(
                    _normalized_group(row[index], variable.kind)
                    for index, variable in enumerate(decimal_groups)
                )
                values = row[len(decimal_groups) :]
                for analysis, value in zip(analyses, values, strict=True):
                    if value is None:
                        continue
                    key = (analysis.name, group_values)
                    bases[key] = max(
                        bases.get(key, 0), observed_decimal_places(float(value))
                    )
        return bases

    def _calculate_groups(
        self,
        source: DatasetHandle,
        config: ProcMeansConfig,
        analyses: tuple[VariableMetadata, ...],
        groups: tuple[VariableMetadata, ...],
        decimal_groups: tuple[VariableMetadata, ...],
        decimal_bases: dict[tuple[str, tuple[object, ...]], int],
        writer: ProcMeansResultWriter,
        notify: ProgressCallback,
    ) -> None:
        subject = next(
            (
                variable
                for variable in source.metadata.variables
                if variable.name.casefold() == "usubjid"
            ),
            None,
        )
        selected = [*groups, *analyses]
        if subject and all(subject.name != variable.name for variable in selected):
            selected.append(subject)
        select = ", ".join(quote_identifier(variable.name) for variable in selected)
        where, parameters = self._where(config)
        order = (
            ", ".join(quote_identifier(variable.name) for variable in groups)
            + ", _source_row"
            if groups
            else "_source_row"
        )
        uri = source.database_path.resolve().as_uri() + "?mode=ro"
        current_key: tuple[tuple[int, object], ...] | None = None
        current_values: tuple[object, ...] = ()
        group_count = 0
        values_by_analysis: dict[str, list[float]] = {}
        subjects_by_analysis: dict[str, set[object]] = {}
        groups_written = 0
        saw_rows = False

        def reset() -> None:
            nonlocal group_count, values_by_analysis, subjects_by_analysis
            group_count = 0
            values_by_analysis = {analysis.name: [] for analysis in analyses}
            subjects_by_analysis = {analysis.name: set() for analysis in analyses}

        def flush() -> None:
            nonlocal groups_written
            group_map = {
                variable.name: value
                for variable, value in zip(groups, current_values, strict=True)
            }
            decimal_values = tuple(
                group_map[variable.name] for variable in decimal_groups
            )
            for analysis in analyses:
                values = values_by_analysis[analysis.name]
                subject_count = (
                    len(subjects_by_analysis[analysis.name]) if subject else None
                )
                statistics = calculate_value_statistics(
                    values,
                    group_count,
                    subject_count,
                    config.confidence,
                    set(config.statistics),
                )
                writer.add_row(
                    group_map,
                    analysis,
                    statistics,
                    decimal_bases.get((analysis.name, decimal_values), 0),
                )
            groups_written += 1
            if groups_written % 100 == 0:
                notify(
                    f"Calculating grouped PROC MEANS… {groups_written:,} groups, "
                    f"{writer.row_count:,} result rows"
                )

        reset()
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            rows = connection.execute(
                f"SELECT {select} FROM dataset{where} ORDER BY {order}", parameters
            )
            positions = {
                variable.name: index for index, variable in enumerate(selected)
            }
            for row in rows:
                saw_rows = True
                raw_group = tuple(
                    _normalized_group(row[positions[variable.name]], variable.kind)
                    for variable in groups
                )
                key = _sortable_group(raw_group)
                if current_key is not None and key != current_key:
                    flush()
                    reset()
                current_key = key
                current_values = raw_group
                group_count += 1
                subject_value = row[positions[subject.name]] if subject else None
                subject_missing = subject_value is None or (
                    subject is not None
                    and subject.kind == "character"
                    and subject_value == ""
                )
                for analysis in analyses:
                    value = row[positions[analysis.name]]
                    if value is None:
                        continue
                    values_by_analysis[analysis.name].append(float(value))
                    if subject and not subject_missing:
                        subjects_by_analysis[analysis.name].add(subject_value)
        if current_key is not None:
            flush()
        elif not saw_rows and not groups:
            current_values = ()
            flush()
