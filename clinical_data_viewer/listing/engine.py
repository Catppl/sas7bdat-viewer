from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace

from ..domain import DatasetHandle, DatasetMetadata, VariableMetadata
from ..filter_engine import FilterEngine, quote_identifier
from ..temp_manager import TempManager
from .configuration import write_listing_configuration
from .expressions import evaluate, infer_kind, infer_length, parse_expression
from .models import ListingConfig, is_reserved_listing_name
from .result_store import ListingResultWriter


def _uri(handle: DatasetHandle) -> str:
    return handle.database_path.resolve().as_uri() + "?mode=ro"


def _missing_sql(variable: VariableMetadata, prefix: str = "") -> str:
    name = prefix + quote_identifier(variable.name)
    return (
        f"({name} IS NULL OR {name} = '')"
        if variable.kind == "character"
        else f"{name} IS NULL"
    )


def _safe_name(value: str) -> str:
    return "_adsl_" + value


class ListingEngine:
    def __init__(self, temp_manager: TempManager):
        self.temp_manager = temp_manager

    @staticmethod
    def _fields(metadata: DatasetMetadata) -> dict[str, VariableMetadata]:
        return {variable.name.casefold(): variable for variable in metadata.variables}

    def resolved_metadata(
        self,
        source: DatasetHandle,
        config: ListingConfig,
        adsl: DatasetHandle | None = None,
    ) -> DatasetMetadata:
        config.validate_basic()
        if not source.cache_complete:
            raise ValueError("The Listing source must finish loading first.")
        source_fields = self._fields(source.metadata)
        if not config.merge_adsl.enabled:
            return source.metadata
        if adsl is None or not adsl.cache_complete:
            raise ValueError(
                "Open a fully loaded ADSL dataset before running the Listing."
            )
        adsl_fields = self._fields(adsl.metadata)
        by_key = config.merge_adsl.by_variable.casefold()
        if by_key not in source_fields or by_key not in adsl_fields:
            raise ValueError(
                f'BY variable "{config.merge_adsl.by_variable}" must exist in source and ADSL.'
            )
        if source_fields[by_key].kind != adsl_fields[by_key].kind:
            raise ValueError(
                "Source and ADSL BY variables have incompatible types. Change the BY variable before running."
            )
        selected = self._selected_adsl_variables(adsl.metadata, config)
        rename_map = {
            source.casefold(): target for source, target in config.merge_adsl.rename_map
        }
        variables = list(source.metadata.variables)
        used = {variable.name.casefold() for variable in variables}
        for variable in selected:
            if variable.name.casefold() == by_key:
                continue
            if variable.name.casefold() in used:
                if config.merge_adsl.duplicate_policy == "ignore":
                    continue
                target = rename_map.get(variable.name.casefold())
                if not target:
                    raise ValueError(
                        f'Resolve duplicate ADSL variable "{variable.name}" by Ignore or Rename.'
                    )
                if is_reserved_listing_name(target):
                    raise ValueError(
                        f'ADSL rename target "{target}" uses a reserved Listing name.'
                    )
                if target.casefold() in used:
                    raise ValueError(
                        f'ADSL rename target "{target}" conflicts with an existing variable.'
                    )
                variables.append(replace(variable, name=target))
                used.add(target.casefold())
            else:
                variables.append(variable)
                used.add(variable.name.casefold())
        return DatasetMetadata(
            source.metadata.name, source.metadata.row_count, tuple(variables)
        )

    def _selected_adsl_variables(
        self, metadata: DatasetMetadata, config: ListingConfig
    ) -> tuple[VariableMetadata, ...]:
        merge = config.merge_adsl
        fields = self._fields(metadata)
        requested = merge.keep or tuple(
            variable.name
            for variable in metadata.variables
            if variable.name.casefold() not in {item.casefold() for item in merge.drop}
        )
        names = {name.casefold() for name in requested}
        names.add(merge.by_variable.casefold())
        unknown = [name for name in names if name not in fields]
        if unknown:
            raise ValueError("Unknown ADSL variable: " + unknown[0])
        return tuple(
            variable
            for variable in metadata.variables
            if variable.name.casefold() in names
        )

    def warnings(
        self,
        source: DatasetHandle,
        config: ListingConfig,
        adsl: DatasetHandle | None = None,
    ) -> tuple[str, ...]:
        messages: list[str] = []
        if not any(column.sort_order is not None for column in config.columns):
            messages.append(
                "No Sort Order is set. Source record order will be retained."
            )
        if any(
            column.include_in_report and column.report_type == "GROUP"
            for column in config.columns
        ):
            messages.append(
                "GROUP may consolidate rows with identical group values in PROC REPORT."
            )
        if not config.merge_adsl.enabled:
            return tuple(messages)
        if adsl is None:
            return tuple(messages)
        source_by = self._fields(source.metadata)[
            config.merge_adsl.by_variable.casefold()
        ]
        adsl_by = self._fields(adsl.metadata)[config.merge_adsl.by_variable.casefold()]
        with closing(sqlite3.connect(_uri(source), uri=True)) as connection:
            missing_source = connection.execute(
                f"SELECT count(*) FROM dataset WHERE {_missing_sql(source_by)}"
            ).fetchone()[0]
        with closing(sqlite3.connect(_uri(adsl), uri=True)) as connection:
            missing_adsl = connection.execute(
                f"SELECT count(*) FROM dataset WHERE {_missing_sql(adsl_by)}"
            ).fetchone()[0]
        if missing_adsl:
            messages.append(
                f"ADSL has {missing_adsl:,} record(s) with missing {adsl_by.name}; they will not be merged."
            )
        if missing_source:
            messages.append(
                f"Source has {missing_source:,} record(s) with missing {source_by.name}; they will be retained without ADSL matches."
            )
        return tuple(messages)

    def _validate_adsl_keys(self, adsl: DatasetHandle, config: ListingConfig) -> None:
        variable = self._fields(adsl.metadata)[config.merge_adsl.by_variable.casefold()]
        col = quote_identifier(variable.name)
        with closing(sqlite3.connect(_uri(adsl), uri=True)) as connection:
            duplicate = connection.execute(
                f"SELECT 1 FROM dataset WHERE NOT {_missing_sql(variable)} GROUP BY {col} HAVING count(*) > 1 LIMIT 1"
            ).fetchone()
        if duplicate:
            raise ValueError(
                f"ADSL is not unique by {variable.name}. Merge may duplicate Listing records."
            )

    def _base_table(
        self,
        connection: sqlite3.Connection,
        source: DatasetHandle,
        config: ListingConfig,
        adsl: DatasetHandle | None,
        metadata: DatasetMetadata,
    ) -> None:
        connection.execute("ATTACH DATABASE ? AS source_db", (_uri(source),))
        select = ["s._source_row AS _listing_row"]
        source_fields = self._fields(source.metadata)
        for variable in source.metadata.variables:
            select.append(
                f"s.{quote_identifier(variable.name)} AS {quote_identifier(variable.name)}"
            )
        join = ""
        if config.merge_adsl.enabled and adsl is not None:
            connection.execute("ATTACH DATABASE ? AS adsl_db", (_uri(adsl),))
            self._validate_adsl_keys(adsl, config)
            by = config.merge_adsl.by_variable
            source_by, adsl_by = (
                source_fields[by.casefold()],
                self._fields(adsl.metadata)[by.casefold()],
            )
            selected = self._selected_adsl_variables(adsl.metadata, config)
            rename_map = {
                old.casefold(): new for old, new in config.merge_adsl.rename_map
            }
            for variable in selected:
                if variable.name.casefold() == by.casefold():
                    continue
                if variable.name.casefold() in source_fields:
                    if config.merge_adsl.duplicate_policy == "ignore":
                        continue
                    output = rename_map[variable.name.casefold()]
                else:
                    output = variable.name
                select.append(
                    f"a.{quote_identifier(variable.name)} AS {quote_identifier(output)}"
                )
            join = (
                " LEFT JOIN adsl_db.dataset AS a ON "
                f"NOT {_missing_sql(source_by, 's.')} AND NOT {_missing_sql(adsl_by, 'a.')} "
                f"AND s.{quote_identifier(by)} = a.{quote_identifier(by)}"
            )
        connection.execute(
            "CREATE TABLE base AS SELECT "
            + ", ".join(select)
            + " FROM source_db.dataset AS s"
            + join
        )

    def run(
        self,
        source: DatasetHandle,
        config: ListingConfig,
        adsl: DatasetHandle | None = None,
        progress=None,
    ) -> DatasetHandle:
        notify = progress or (lambda _message: None)
        metadata = self.resolved_metadata(source, config, adsl)
        expressions = [
            parse_expression(column.expression_text, metadata.variables)
            for column in config.columns
        ]
        fields = self._fields(metadata)
        for expression in expressions:
            # parse_expression already validates every referenced input variable.
            infer_kind(expression)
        notify("Preparing Listing source data…")
        directory = self.temp_manager.create_dataset_directory()
        output_variables = []
        for column, expression in zip(config.columns, expressions, strict=True):
            kind = infer_kind(expression)
            source_variable = (
                fields.get(str(expression.get("name", "")).casefold())
                if expression["type"] == "variable"
                else None
            )
            format_text = column.format.strip() or (
                source_variable.format if source_variable is not None else ""
            )
            output_variables.append(
                VariableMetadata(
                    column.output_name,
                    column.label or column.output_name,
                    kind,
                    infer_length(expression, fields) if kind == "character" else None,
                    format_text,
                )
            )
        output_variables = tuple(output_variables)
        writer: ListingResultWriter | None = None
        try:
            writer = ListingResultWriter(directory / "dataset.sqlite", output_variables)
            connection = writer.connection
            self._base_table(connection, source, config, adsl, metadata)
            # Validate/execute the existing Viewer WHERE grammar against the resolved schema.
            compiled = FilterEngine(metadata.variables).compile(config.data_filter_text)
            query = "SELECT * FROM base" + (
                f" WHERE {compiled.sql}" if compiled.sql else ""
            )
            rows: list[tuple[tuple[object, ...], int, tuple[object, ...]]] = []
            cursor = connection.execute(query, compiled.parameters)
            names = tuple(item[0] for item in cursor.description)
            for record in cursor:
                row = dict(zip(names, record, strict=True))
                source_row = int(row.pop("_listing_row"))
                raw_values = tuple(
                    evaluate(
                        expression,
                        row,
                        fields,
                        division_by_zero_missing=column.division_by_zero_missing,
                    )
                    for column, expression in zip(
                        config.columns, expressions, strict=True
                    )
                )
                keys = []
                for index, column in enumerate(config.columns):
                    if column.sort_order is not None:
                        value = raw_values[index]
                        keys.append((column.sort_order, column.sort_direction, value))
                rows.append((raw_values, source_row, tuple(keys)))
            notify("Sorting Listing records…")

            def normalize(value):
                return (value is not None, value)

            ordered = rows
            # Stable multi-key sorting: source row is the final deterministic tie breaker.
            ordered.sort(key=lambda item: item[1])
            for column in sorted(
                (column for column in config.columns if column.sort_order is not None),
                key=lambda column: column.sort_order,
                reverse=True,
            ):
                ordered.sort(
                    key=lambda item: normalize(
                        next(
                            value
                            for order, _direction, value in item[2]
                            if order == column.sort_order
                        )
                    ),
                    reverse=column.sort_direction == "DESC",
                )
            for result_row, (values, _source_row, _keys) in enumerate(ordered, 1):
                # The result cache's natural order is the requested Listing order.
                writer.add(result_row, values)
            path = directory / "listing_config.json"
            write_listing_configuration(path, source, config, metadata, adsl)
            return replace(
                writer.finish(
                    directory,
                    source,
                    display_source=f"Listing from {source.source_path}",
                ),
                configuration_path=path,
            )
        except BaseException:
            if writer is not None:
                writer.abort(self.temp_manager, directory)
            else:
                self.temp_manager.remove_dataset(directory)
            raise
