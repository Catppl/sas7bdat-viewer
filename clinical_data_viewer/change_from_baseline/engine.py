from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass

from ..domain import DatasetHandle, DatasetMetadata, VariableMetadata
from ..filter_ast import serialize_filter_ast
from ..filter_engine import quote_identifier
from ..sqlite_access import connect_read_only
from ..statistics import calculate_value_statistics
from ..temp_manager import TempManager
from .configuration import write_change_from_baseline_configuration
from .models import ChangeFromBaselineConfig
from .result_store import (
    ChangeFromBaselineResultWriter,
    build_result_schema,
)

ProgressCallback = Callable[[str], None]


class DuplicateChangeWarning(UserWarning):
    """A warning that a group has multiple Change records."""


def _missing(value: object, variable: VariableMetadata) -> bool:
    return value is None or (variable.kind == "character" and value == "")


def _canonical(value: object, variable: VariableMetadata) -> object:
    return None if _missing(value, variable) else value


def _value_sort_key(value: object, variable: VariableMetadata) -> tuple[object, ...]:
    if _missing(value, variable):
        return (1, 0, "")
    if variable.kind == "numeric":
        return (0, 0, float(value), str(value))
    text = str(value)
    return (0, 1, text.casefold(), text)


def _literal_value(node: object) -> object | None:
    if not isinstance(node, dict) or node.get("type") != "literal":
        return None
    return node.get("value")


def _ordered_item_values(ast: object, item_name: str) -> tuple[object, ...]:
    """Extract explicit IN/OR equality order without inventing order for other WHERE clauses."""

    target = item_name.casefold()
    found: list[object] = []

    def visit(node: object) -> None:
        if not isinstance(node, dict):
            return
        node_type = node.get("type")
        if (
            node_type == "in"
            and not node.get("negated")
            and str(node.get("variable", "")).casefold() == target
        ):
            for operand in node.get("values", []):
                value = _literal_value(operand)
                if value is not None and value not in found:
                    found.append(value)
            return
        if (
            node_type == "comparison"
            and not node.get("prefix")
            and str(node.get("variable", "")).casefold() == target
            and node.get("operator") == "="
        ):
            value = _literal_value(node.get("operand"))
            if value is not None and value not in found:
                found.append(value)
            return
        if node_type == "boolean":
            visit(node.get("left"))
            visit(node.get("right"))

    visit(ast)
    return tuple(found)


def _ast_mentions_variable(node: object, variable_name: str) -> bool:
    if not isinstance(node, dict):
        return False
    target = variable_name.casefold()
    if str(node.get("variable", "")).casefold() == target:
        return True
    return any(
        _ast_mentions_variable(node.get(key), variable_name)
        for key in ("left", "right", "operand", "values")
    )


def _combined_filter(config: ChangeFromBaselineConfig) -> tuple[str, tuple[object, ...]]:
    clauses: list[str] = []
    parameters: list[object] = []
    for compiled in (config.data_filter, config.postbaseline_filter):
        if compiled.sql:
            clauses.append(f"({compiled.sql})")
            parameters.extend(compiled.parameters)
    return " AND ".join(clauses), tuple(parameters)


@dataclass
class _Group:
    values: list[float]
    subjects: set[object]
    records: int = 0


class ChangeFromBaselineEngine:
    """Calculate a horizontal table from an existing Change variable."""

    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    @staticmethod
    def _fields(metadata: DatasetMetadata) -> dict[str, VariableMetadata]:
        return {variable.name.casefold(): variable for variable in metadata.variables}

    @staticmethod
    def _item_label(value: object, labels: dict[str, str]) -> str:
        if value is None:
            return "(Missing)"
        return labels.get(str(value).casefold(), str(value))

    def _resolved_item_order(
        self,
        source: DatasetHandle,
        config: ChangeFromBaselineConfig,
        values: list[object],
        item_variable: VariableMetadata,
    ) -> list[object]:
        explicit: list[object] = []
        for text in (config.data_filter_text, config.postbaseline_filter_text):
            if not text.strip():
                continue
            ast = serialize_filter_ast(text, source.metadata.variables)
            for value in _ordered_item_values(ast, item_variable.name):
                canonical = _canonical(value, item_variable)
                if canonical not in explicit:
                    explicit.append(canonical)
        present = list(dict.fromkeys(values))
        explicit_present = [value for value in explicit if value in present]
        remaining = [value for value in present if value not in explicit_present]
        remaining.sort(key=lambda value: _value_sort_key(value, item_variable))
        return explicit_present + remaining

    def resolve_treatment_levels(
        self,
        source: DatasetHandle,
        config: ChangeFromBaselineConfig,
    ) -> list[tuple[str, object, str]]:
        config.validate(source.metadata)
        if not config.treatment_variable or (
            config.treatment_variable.casefold()
            in {name.casefold() for name in config.row_variables}
        ):
            return []
        fields = self._fields(source.metadata)
        treatment = fields[config.treatment_variable.casefold()]
        where, parameters = _combined_filter(config)
        suffix = f" WHERE {where}" if where else ""
        values: list[object] = []
        with closing(connect_read_only(source.database_path)) as connection:
            for (value,) in connection.execute(
                f"SELECT DISTINCT {quote_identifier(treatment.name)} FROM dataset{suffix}",
                parameters,
            ):
                canonical = _canonical(value, treatment)
                if canonical is None:
                    raise ValueError(
                        "Missing treatment values were found. Modify the WHERE conditions before running."
                    )
                if canonical not in values:
                    values.append(canonical)
        values.sort(key=lambda value: _value_sort_key(value, treatment))
        return [
            (json.dumps(value, ensure_ascii=False, separators=(",", ":")), value, str(value))
            for value in values
        ]

    def duplicate_warnings(
        self, source: DatasetHandle, config: ChangeFromBaselineConfig
    ) -> tuple[str, ...]:
        config.validate(source.metadata)
        fields = self._fields(source.metadata)
        item = fields[config.item_variable.casefold()]
        subject = fields["usubjid"]
        row_fields = tuple(fields[name.casefold()] for name in config.row_variables)
        treatment = fields.get(config.treatment_variable.casefold()) if config.treatment_variable else None
        selected = [item, *row_fields]
        if treatment is not None and treatment.name.casefold() not in {v.name.casefold() for v in selected}:
            selected.append(treatment)
        selected.append(subject)
        where, parameters = _combined_filter(config)
        suffix = f" WHERE {where}" if where else ""
        group_names = ", ".join(quote_identifier(variable.name) for variable in selected)
        with closing(connect_read_only(source.database_path)) as connection:
            query = (
                f"SELECT {group_names}, count(*) FROM dataset{suffix} "
                f"GROUP BY {group_names} HAVING count(*) > 1 LIMIT 20"
            )
            count = sum(1 for _row in connection.execute(query, parameters))
        if not count:
            return ()
        message = (
            f"{count:,} Change group(s) contain multiple records. "
            "The rows will be retained; verify the Item, Row, Treatment, and WHERE conditions."
        )
        return (message,)

    def item_order_warnings(
        self, source: DatasetHandle, config: ChangeFromBaselineConfig
    ) -> tuple[str, ...]:
        """Warn when a WHERE mentions Item but does not provide an explicit order."""
        fields = self._fields(source.metadata)
        item = fields[config.item_variable.casefold()]
        for text in (config.data_filter_text, config.postbaseline_filter_text):
            if not text.strip():
                continue
            ast = serialize_filter_ast(text, source.metadata.variables)
            if _ast_mentions_variable(ast, item.name) and not _ordered_item_values(
                ast, item.name
            ):
                return (
                    (
                        "Item levels are not explicitly ordered by IN or equality "
                        "literals; a stable default sort will be used."
                    ),
                )
        return ()

    def run(
        self,
        source: DatasetHandle,
        config: ChangeFromBaselineConfig,
        progress: ProgressCallback | None = None,
    ) -> DatasetHandle:
        if not source.cache_complete:
            raise ValueError("The source dataset must finish loading first.")
        config.validate(source.metadata)
        notify = progress or (lambda _message: None)
        fields = self._fields(source.metadata)
        item = fields[config.item_variable.casefold()]
        change = fields[config.change_variable.casefold()]
        subject = fields["usubjid"]
        row_fields = tuple(fields[name.casefold()] for name in config.row_variables)
        treatment = fields.get(config.treatment_variable.casefold()) if config.treatment_variable else None
        grouping_treatment = (
            None
            if treatment is not None
            and treatment.name.casefold()
            in {variable.name.casefold() for variable in row_fields}
            else treatment
        )
        treatment_levels = self.resolve_treatment_levels(source, config)
        selected = [item, *row_fields, change, subject]
        if treatment is not None and treatment.name.casefold() not in {
            variable.name.casefold() for variable in selected
        }:
            selected.append(treatment)
        selected = list(dict.fromkeys(selected))
        where, parameters = _combined_filter(config)
        suffix = f" WHERE {where}" if where else ""
        query = (
            "SELECT _source_row, "
            + ", ".join(quote_identifier(variable.name) for variable in selected)
            + f" FROM dataset{suffix} ORDER BY _source_row"
        )
        positions = {
            variable.name.casefold(): index + 1
            for index, variable in enumerate(selected)
        }
        groups: dict[tuple[object, tuple[object, ...], object], _Group] = {}
        item_values: list[object] = []
        duplicate_keys: defaultdict[tuple[object, ...], int] = defaultdict(int)
        with closing(connect_read_only(source.database_path)) as connection:
            rows = connection.execute(query, parameters)
            for row in rows:
                raw_item = _canonical(row[positions[item.name.casefold()]], item)
                row_values = tuple(
                    _canonical(row[positions[variable.name.casefold()]], variable)
                    for variable in row_fields
                )
                raw_treatment = (
                    _canonical(
                        row[positions[grouping_treatment.name.casefold()]],
                        grouping_treatment,
                    )
                    if grouping_treatment is not None
                    else None
                )
                if grouping_treatment is not None and raw_treatment is None:
                    raise ValueError(
                        "Missing treatment values were found. Modify the WHERE conditions before running."
                    )
                raw_change = row[positions[change.name.casefold()]]
                raw_subject = _canonical(
                    row[positions[subject.name.casefold()]], subject
                )
                group_key = (raw_item, row_values, raw_treatment)
                group = groups.setdefault(group_key, _Group([], set()))
                group.records += 1
                duplicate_keys[(raw_item, row_values, raw_treatment)] += 1
                if raw_change is not None:
                    group.values.append(float(raw_change))
                    if raw_subject is not None:
                        group.subjects.add(raw_subject)
                if raw_item not in item_values:
                    item_values.append(raw_item)

        notify("Calculating Change from Baseline statistics…")
        item_order = self._resolved_item_order(source, config, item_values, item)
        item_rank = {value: index for index, value in enumerate(item_order)}
        row_sort = {name.casefold(): ascending for name, ascending in config.row_sort}

        base_keys = list(
            {(raw_item, row_values) for raw_item, row_values, _trt in groups}
        )
        base_keys.sort(key=lambda key: item_rank.get(key[0], 999999))
        # Sort row variables within each Item while keeping missing values at
        # the end for both directions.  Stable passes preserve the user's
        # selected row-variable priority.
        ordered_keys: list[tuple[object, tuple[object, ...]]] = []
        for item_value in item_order:
            item_keys = [key for key in base_keys if key[0] == item_value]
            for index in reversed(range(len(row_fields))):
                variable = row_fields[index]
                ascending = row_sort.get(variable.name.casefold(), True)
                present = [
                    key
                    for key in item_keys
                    if not _missing(key[1][index], variable)
                ]
                missing = [
                    key
                    for key in item_keys
                    if _missing(key[1][index], variable)
                ]
                present.sort(
                    key=lambda key: _value_sort_key(key[1][index], variable),
                    reverse=not ascending,
                )
                item_keys = present + missing
            ordered_keys.extend(item_keys)
        base_keys = ordered_keys
        schema = build_result_schema(
            source,
            config.row_variables,
            config.statistics,
            treatment_levels,
            config.include_total,
        )
        directory = self.temp_manager.create_dataset_directory()
        writer = ChangeFromBaselineResultWriter(directory / "dataset.sqlite", schema)
        labels = {str(value).casefold(): label for value, label in config.item_labels}
        try:
            group_specs = list(treatment_levels)
            if not group_specs:
                group_specs = [("__all__", None, "All")]
            for raw_item, row_values in base_keys:
                wide: list[object] = [self._item_label(raw_item, labels), *row_values]
                long_rows: list[tuple[object, ...]] = []
                for treatment_key, treatment_value, treatment_label in group_specs:
                    key = (raw_item, row_values, treatment_value)
                    group = groups.get(key, _Group([], set()))
                    stats = calculate_value_statistics(
                        group.values,
                        group.records,
                        len(group.subjects),
                        0.95,
                        set(config.statistics),
                    )
                    for statistic in config.statistics:
                        value = stats.get(statistic)
                        wide.append(value)
                        long_rows.append(
                            (
                                self._item_label(raw_item, labels),
                                *row_values,
                                treatment_label,
                                statistic.upper(),
                                value,
                                stats.get("n"),
                                stats.get("subjects"),
                            )
                        )
                    wide.append(stats.get("subjects"))
                    long_rows.append(
                        (
                            self._item_label(raw_item, labels),
                            *row_values,
                            treatment_label,
                            "SUBJECT_N",
                            stats.get("subjects"),
                            stats.get("n"),
                            stats.get("subjects"),
                        )
                    )
                if config.include_total and treatment_levels:
                    total = _Group([], set())
                    for treatment_value in (value for _key, value, _label in treatment_levels):
                        group = groups.get((raw_item, row_values, treatment_value))
                        if group is None:
                            continue
                        total.values.extend(group.values)
                        total.subjects.update(group.subjects)
                        total.records += group.records
                    stats = calculate_value_statistics(
                        total.values,
                        total.records,
                        len(total.subjects),
                        0.95,
                        set(config.statistics),
                    )
                    for statistic in config.statistics:
                        wide.append(stats.get(statistic))
                        long_rows.append(
                            (
                                self._item_label(raw_item, labels),
                                *row_values,
                                "Total",
                                statistic.upper(),
                                stats.get(statistic),
                                stats.get("n"),
                                stats.get("subjects"),
                            )
                        )
                    wide.append(stats.get("subjects"))
                    long_rows.append(
                        (
                            self._item_label(raw_item, labels),
                            *row_values,
                            "Total",
                            "SUBJECT_N",
                            stats.get("subjects"),
                            stats.get("n"),
                            stats.get("subjects"),
                        )
                    )
                writer.add_wide(tuple(wide))
                for long_row in long_rows:
                    writer.add_long(long_row)
            configuration_path = directory / "change_from_baseline_config.json"
            write_change_from_baseline_configuration(
                configuration_path,
                source,
                config,
                resolved_treatment_levels=[
                    {"value": value, "label": label}
                    for _key, value, label in treatment_levels
                ],
                item_order=item_order,
            )
            return writer.finish(directory, source, configuration_path)
        except BaseException:
            writer.abort(self.temp_manager, directory)
            raise


__all__ = ["ChangeFromBaselineEngine", "DuplicateChangeWarning"]
