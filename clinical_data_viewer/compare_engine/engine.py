from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing
from dataclasses import dataclass

from ..domain import DatasetHandle, VariableMetadata
from ..filter_engine import quote_identifier
from ..temp_manager import TempManager
from .comparator import differing_variables
from .matcher import match_group, pair_cost
from .models import CompareConfig, SourceRecord
from .normalize import group_sort_key, normalize_missing
from .result_store import CompareResultWriter, build_result_schema

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class _VariableLayout:
    comparable: tuple[VariableMetadata, ...]
    result: tuple[VariableMetadata, ...]
    main_names: dict[str, str | None]
    qc_names: dict[str, str | None]
    warning_columns: tuple[str, ...]
    warning_messages: tuple[tuple[str, str], ...]


class DatasetComparer:
    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    def compare(
        self,
        main: DatasetHandle,
        qc: DatasetHandle,
        config: CompareConfig,
        progress: ProgressCallback | None = None,
    ) -> DatasetHandle:
        config.validate()
        if not main.cache_complete or not qc.cache_complete:
            raise ValueError("Both Main and QC datasets must finish loading first.")
        notify = progress or (lambda _message: None)
        layout = self._variable_layout(main, qc)
        common_by_name = {variable.name: variable for variable in layout.comparable}
        requested = {
            *config.group_variables,
            *config.key_variables,
            *(variable.name for variable in config.match_variables),
        }
        unknown = sorted(requested - set(common_by_name))
        if unknown:
            raise ValueError(
                "Variables are not common to Main and QC: " + ", ".join(unknown)
            )
        for name in requested:
            main_kind = common_by_name[name].kind
            qc_kind = next(
                variable.kind
                for variable in qc.metadata.variables
                if variable.name == layout.qc_names[name]
            )
            if main_kind != qc_kind:
                raise ValueError(f"Variable {name} has different types in Main and QC.")

        result_directory = self.temp_manager.create_dataset_directory()
        writer = CompareResultWriter(
            result_directory / "dataset.sqlite",
            build_result_schema(
                layout.result, layout.warning_columns, layout.warning_messages
            ),
        )
        try:
            notify("Preparing grouped Main and QC observations…")
            self._run_groups(main, qc, layout, config, writer, notify)
            notify(f"Finalizing Compare Result… {writer.row_count:,} rows")
            return writer.finish(result_directory, main, qc)
        except BaseException:
            writer.abort()
            self.temp_manager.remove_dataset(result_directory)
            raise

    @staticmethod
    def _variable_layout(main: DatasetHandle, qc: DatasetHandle) -> _VariableLayout:
        qc_by_fold = {
            variable.name.casefold(): variable for variable in qc.metadata.variables
        }
        comparable: list[VariableMetadata] = []
        result: list[VariableMetadata] = []
        main_names: dict[str, str | None] = {}
        qc_names: dict[str, str | None] = {}
        warning_columns: list[str] = []
        warning_messages: list[tuple[str, str]] = []
        for variable in main.metadata.variables:
            qc_variable = qc_by_fold.get(variable.name.casefold())
            if qc_variable is None:
                result.append(variable)
                main_names[variable.name] = variable.name
                qc_names[variable.name] = None
                warning_columns.append(variable.name)
                warning_messages.append(
                    (variable.name, "Variable exists only in Main.")
                )
                continue
            result_kind = (
                variable.kind if variable.kind == qc_variable.kind else "character"
            )
            output = VariableMetadata(
                variable.name,
                variable.label or qc_variable.label,
                result_kind,
                variable.length,
                variable.format,
            )
            result.append(output)
            main_names[variable.name] = variable.name
            qc_names[variable.name] = qc_variable.name
            if variable.kind == qc_variable.kind:
                comparable.append(output)
            else:
                warning_columns.append(variable.name)
                warning_messages.append(
                    (variable.name, "Variable type differs between Main and QC.")
                )
        main_folds = {variable.name.casefold() for variable in main.metadata.variables}
        for variable in qc.metadata.variables:
            if variable.name.casefold() in main_folds:
                continue
            result.append(variable)
            main_names[variable.name] = None
            qc_names[variable.name] = variable.name
            warning_columns.append(variable.name)
            warning_messages.append((variable.name, "Variable exists only in QC."))
        if not comparable:
            raise ValueError(
                "Main and QC do not have any common variables with compatible types."
            )
        return _VariableLayout(
            tuple(comparable),
            tuple(result),
            main_names,
            qc_names,
            tuple(warning_columns),
            tuple(warning_messages),
        )

    def _run_groups(
        self,
        main: DatasetHandle,
        qc: DatasetHandle,
        layout: _VariableLayout,
        config: CompareConfig,
        writer: CompareResultWriter,
        notify: ProgressCallback,
    ) -> None:
        output_names = tuple(variable.name for variable in layout.result)
        kinds = {variable.name: variable.kind for variable in layout.comparable}
        main_groups = self._groups(main, output_names, layout.main_names, config, kinds)
        qc_groups = self._groups(qc, output_names, layout.qc_names, config, kinds)
        main_group = next(main_groups, None)
        qc_group = next(qc_groups, None)
        pair_id = 0
        groups_processed = 0

        while main_group is not None or qc_group is not None:
            if main_group is None:
                relation = 1
            elif qc_group is None:
                relation = -1
            else:
                relation = (main_group[0] > qc_group[0]) - (main_group[0] < qc_group[0])
            if relation < 0:
                for record in main_group[1]:
                    pair_id += 1
                    writer.add_row(
                        pair_id,
                        "Main",
                        "Main only",
                        record.source_row,
                        None,
                        None,
                        (),
                        record.values,
                    )
                main_group = next(main_groups, None)
            elif relation > 0:
                for record in qc_group[1]:
                    pair_id += 1
                    writer.add_row(
                        pair_id,
                        "QC",
                        "QC only",
                        record.source_row,
                        None,
                        None,
                        (),
                        record.values,
                    )
                qc_group = next(qc_groups, None)
            else:
                main_records = main_group[1]
                qc_records = qc_group[1]
                combinations = len(main_records) * len(qc_records)
                group_records = len(main_records) + len(qc_records)
                if group_records > config.max_group_records:
                    values = ", ".join(str(value) for value in main_group[2])
                    raise ValueError(
                        f"Group ({values}) has {group_records:,} total records; "
                        f"the safety limit is {config.max_group_records:,}."
                    )
                if combinations > config.max_group_pairs:
                    values = ", ".join(str(value) for value in main_group[2])
                    raise ValueError(
                        f"Group ({values}) has {combinations:,} candidate pairs; "
                        f"the safety limit is {config.max_group_pairs:,}."
                    )
                matches = match_group(
                    main_records,
                    qc_records,
                    config.match_variables,
                    config.threshold,
                    config.ambiguity_margin,
                )
                for decision in matches.decisions:
                    main_record = main_records[decision.main_index]
                    qc_record = qc_records[decision.qc_index]
                    pair_id += 1
                    if decision.ambiguous:
                        differences: tuple[str, ...] = ()
                        status = "Ambiguous"
                    else:
                        differences = differing_variables(
                            main_record,
                            qc_record,
                            kinds,
                            config.key_variables,
                            config.match_variables,
                        )
                        if not differences:
                            pair_id -= 1
                            continue
                        status = "Different"
                    for side, record in (("Main", main_record), ("QC", qc_record)):
                        writer.add_row(
                            pair_id,
                            side,
                            status,
                            record.source_row,
                            decision.cost,
                            decision.margin,
                            differences,
                            record.values,
                        )
                main_only, qc_only, unmatched_main, unmatched_qc = (
                    self._classify_residuals(
                        main_records,
                        qc_records,
                        matches.unmatched_main,
                        matches.unmatched_qc,
                        config,
                    )
                )
                for index in main_only:
                    pair_id += 1
                    record = main_records[index]
                    writer.add_row(
                        pair_id,
                        "Main",
                        "Main only",
                        record.source_row,
                        None,
                        None,
                        (),
                        record.values,
                    )
                for index in qc_only:
                    pair_id += 1
                    record = qc_records[index]
                    writer.add_row(
                        pair_id,
                        "QC",
                        "QC only",
                        record.source_row,
                        None,
                        None,
                        (),
                        record.values,
                    )
                for index in unmatched_main:
                    pair_id += 1
                    record = main_records[index]
                    writer.add_row(
                        pair_id,
                        "Main",
                        "Unmatched",
                        record.source_row,
                        None,
                        None,
                        (),
                        record.values,
                    )
                for index in unmatched_qc:
                    pair_id += 1
                    record = qc_records[index]
                    writer.add_row(
                        pair_id,
                        "QC",
                        "Unmatched",
                        record.source_row,
                        None,
                        None,
                        (),
                        record.values,
                    )
                main_group = next(main_groups, None)
                qc_group = next(qc_groups, None)
            groups_processed += 1
            if groups_processed % 50 == 0:
                notify(
                    f"Comparing groups… {groups_processed:,} groups, "
                    f"{writer.row_count:,} result rows"
                )

    @staticmethod
    def _classify_residuals(
        main_records: list[SourceRecord],
        qc_records: list[SourceRecord],
        unmatched_main: tuple[int, ...],
        unmatched_qc: tuple[int, ...],
        config: CompareConfig,
    ) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
        """Classify count-imbalance extras as side-only; retain peers as unmatched."""
        main_extra = max(0, len(unmatched_main) - len(unmatched_qc))
        qc_extra = max(0, len(unmatched_qc) - len(unmatched_main))

        def main_best(index: int) -> float:
            if not unmatched_qc:
                return float("inf")
            return min(
                pair_cost(
                    main_records[index], qc_records[other], config.match_variables
                )
                for other in unmatched_qc
            )

        def qc_best(index: int) -> float:
            if not unmatched_main:
                return float("inf")
            return min(
                pair_cost(
                    main_records[other], qc_records[index], config.match_variables
                )
                for other in unmatched_main
            )

        main_ranked = sorted(
            unmatched_main, key=lambda index: (-main_best(index), index)
        )
        qc_ranked = sorted(unmatched_qc, key=lambda index: (-qc_best(index), index))
        main_only = tuple(sorted(main_ranked[:main_extra]))
        qc_only = tuple(sorted(qc_ranked[:qc_extra]))
        main_only_set = set(main_only)
        qc_only_set = set(qc_only)
        return (
            main_only,
            qc_only,
            tuple(index for index in unmatched_main if index not in main_only_set),
            tuple(index for index in unmatched_qc if index not in qc_only_set),
        )

    @staticmethod
    def _groups(
        handle: DatasetHandle,
        output_names: tuple[str, ...],
        source_names: dict[str, str | None],
        config: CompareConfig,
        kinds: dict[str, str],
    ) -> Iterator[
        tuple[tuple[tuple[int, object], ...], list[SourceRecord], tuple[object, ...]]
    ]:
        selected_outputs = [
            name for name in output_names if source_names[name] is not None
        ]
        selected_source = [source_names[name] for name in selected_outputs]
        select = ", ".join(quote_identifier(name) for name in selected_source)
        order = ", ".join(
            quote_identifier(source_names[name]) for name in config.group_variables
        )
        uri = handle.database_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            cursor = connection.execute(
                f"SELECT _source_row, {select} FROM dataset "
                f"ORDER BY {order}, _source_row"
            )
            current_key = None
            current_raw = None
            records: list[SourceRecord] = []
            for row in cursor:
                values = {name: None for name in output_names}
                values.update(dict(zip(selected_outputs, row[1:], strict=True)))
                raw_key = tuple(
                    normalize_missing(values[name], kinds[name])
                    for name in config.group_variables
                )
                key = group_sort_key(
                    raw_key, tuple(kinds[name] for name in config.group_variables)
                )
                if current_key is not None and key != current_key:
                    yield current_key, records, current_raw
                    records = []
                current_key = key
                current_raw = raw_key
                records.append(SourceRecord(int(row[0]), values))
            if current_key is not None:
                yield current_key, records, current_raw
