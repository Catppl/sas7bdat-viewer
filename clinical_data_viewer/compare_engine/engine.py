from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing

from ..domain import DatasetHandle, VariableMetadata
from ..filter_engine import quote_identifier
from ..temp_manager import TempManager
from .comparator import differing_variables
from .matcher import match_group
from .models import CompareConfig, SourceRecord
from .normalize import group_sort_key, normalize_missing
from .result_store import CompareResultWriter, build_result_schema

ProgressCallback = Callable[[str], None]


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
        common, qc_names = self._common_variables(main, qc)
        common_by_name = {variable.name: variable for variable in common}
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
                if variable.name == qc_names[name]
            )
            if main_kind != qc_kind:
                raise ValueError(f"Variable {name} has different types in Main and QC.")

        result_directory = self.temp_manager.create_dataset_directory()
        writer = CompareResultWriter(
            result_directory / "dataset.sqlite", build_result_schema(common)
        )
        try:
            notify("Preparing grouped Main and QC observations…")
            self._run_groups(main, qc, common, qc_names, config, writer, notify)
            notify(f"Finalizing Compare Result… {writer.row_count:,} rows")
            return writer.finish(result_directory, main, qc)
        except BaseException:
            writer.abort()
            self.temp_manager.remove_dataset(result_directory)
            raise

    @staticmethod
    def _common_variables(
        main: DatasetHandle, qc: DatasetHandle
    ) -> tuple[tuple[VariableMetadata, ...], dict[str, str]]:
        qc_by_fold = {
            variable.name.casefold(): variable for variable in qc.metadata.variables
        }
        common: list[VariableMetadata] = []
        qc_names: dict[str, str] = {}
        for variable in main.metadata.variables:
            qc_variable = qc_by_fold.get(variable.name.casefold())
            if qc_variable is None:
                continue
            result_kind = (
                variable.kind if variable.kind == qc_variable.kind else "character"
            )
            common.append(
                VariableMetadata(
                    variable.name,
                    variable.label or qc_variable.label,
                    result_kind,
                    variable.length,
                    variable.format,
                )
            )
            qc_names[variable.name] = qc_variable.name
        if not common:
            raise ValueError("Main and QC do not have any common variables.")
        return tuple(common), qc_names

    def _run_groups(
        self,
        main: DatasetHandle,
        qc: DatasetHandle,
        common: tuple[VariableMetadata, ...],
        qc_names: dict[str, str],
        config: CompareConfig,
        writer: CompareResultWriter,
        notify: ProgressCallback,
    ) -> None:
        output_names = tuple(variable.name for variable in common)
        kinds = {variable.name: variable.kind for variable in common}
        main_groups = self._groups(
            main, output_names, {name: name for name in output_names}, config, kinds
        )
        qc_groups = self._groups(qc, output_names, qc_names, config, kinds)
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
                for index in matches.unmatched_main:
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
                for index in matches.unmatched_qc:
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
    def _groups(
        handle: DatasetHandle,
        output_names: tuple[str, ...],
        source_names: dict[str, str],
        config: CompareConfig,
        kinds: dict[str, str],
    ) -> Iterator[
        tuple[tuple[tuple[int, object], ...], list[SourceRecord], tuple[object, ...]]
    ]:
        selected_source = [source_names[name] for name in output_names]
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
                values = dict(zip(output_names, row[1:], strict=True))
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
