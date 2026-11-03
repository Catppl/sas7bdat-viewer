from __future__ import annotations

import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from ..domain import DatasetHandle, VariableMetadata
from ..filter_engine import quote_identifier
from ..temp_manager import TempManager
from .configuration import write_shift_table_configuration
from .models import OrderedLevel, ShiftTableConfig
from .result_store import (
    ShiftCellValue,
    ShiftColumn,
    ShiftTableResultWriter,
)

ProgressCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class DuplicateShiftRecordsError(ValueError):
    subject: object
    parameter: object
    visit: object
    treatment: object
    record_count: int

    def __str__(self) -> str:
        return (
            "Multiple analysis records were found for the same subject, parameter, "
            "visit, and treatment group: "
            f"{self.subject!r} / {self.parameter!r} / {self.visit!r} / "
            f"{self.treatment!r} ({self.record_count} records)."
        )


def _missing_sql(variable: VariableMetadata, *, missing: bool) -> str:
    column = quote_identifier(variable.name)
    expression = (
        f"({column} IS NULL OR {column} = '')"
        if variable.kind == "character"
        else f"{column} IS NULL"
    )
    return expression if missing else f"NOT ({expression})"


def _level_key(value: object, variable: VariableMetadata) -> object:
    if variable.kind == "numeric":
        return float(value)
    return str(value).casefold()


def _format_percent(numerator: int, denominator: int, digits: int) -> ShiftCellValue:
    if not denominator:
        return ShiftCellValue(numerator, denominator, None, "0 (-)")
    percent = Decimal(numerator * 100) / Decimal(denominator)
    quantum = Decimal(1).scaleb(-digits)
    rounded = percent.quantize(quantum, rounding=ROUND_HALF_UP)
    return ShiftCellValue(
        numerator,
        denominator,
        float(rounded),
        f"{numerator:,} ({rounded:.{digits}f})",
    )


class ShiftTableEngine:
    def __init__(self, temp_manager: TempManager) -> None:
        self.temp_manager = temp_manager

    @staticmethod
    def _fields(handle: DatasetHandle) -> dict[str, VariableMetadata]:
        return {
            variable.name.casefold(): variable for variable in handle.metadata.variables
        }

    @staticmethod
    def _in_clause(
        variable: VariableMetadata, levels: tuple[OrderedLevel, ...]
    ) -> tuple[str, tuple[object, ...]]:
        placeholders = ", ".join("?" for _ in levels)
        return (
            f"{quote_identifier(variable.name)} IN ({placeholders})",
            tuple(level.raw_value for level in levels),
        )

    def _analysis_where(
        self,
        source: DatasetHandle,
        config: ShiftTableConfig,
        *,
        complete_grades: bool,
        nonmissing_subject: bool = True,
    ) -> tuple[str, tuple[object, ...]]:
        fields = self._fields(source)
        clauses: list[str] = []
        parameters: list[object] = []
        if config.compiled_filter.sql:
            clauses.append(f"({config.compiled_filter.sql})")
            parameters.extend(config.compiled_filter.parameters)
        flag = fields[config.analysis_flag_variable.casefold()]
        clauses.append(f"{quote_identifier(flag.name)} = ?")
        parameters.append(config.analysis_flag_value)
        if nonmissing_subject:
            clauses.append(
                _missing_sql(fields[config.subject_variable.casefold()], missing=False)
            )
        for name, levels in (
            (config.parameter_variable, config.parameters),
            (config.visit_variable, config.visits),
            (config.treatment_variable, config.treatments),
        ):
            clause, values = self._in_clause(fields[name.casefold()], levels)
            clauses.append(clause)
            parameters.extend(values)
        if complete_grades:
            clauses.append(
                _missing_sql(
                    fields[config.baseline_grade_variable.casefold()], missing=False
                )
            )
            clauses.append(
                _missing_sql(
                    fields[config.post_grade_variable.casefold()], missing=False
                )
            )
        return " AND ".join(clauses), tuple(parameters)

    def missing_subject_count(
        self,
        source: DatasetHandle,
        config: ShiftTableConfig,
        population: DatasetHandle | None = None,
    ) -> int:
        config.validate(source.metadata, population.metadata if population else None)
        where, parameters = self._analysis_where(
            source,
            config,
            complete_grades=False,
            nonmissing_subject=False,
        )
        subject = self._fields(source)[config.subject_variable.casefold()]
        uri = source.database_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            return int(
                connection.execute(
                    f"SELECT count(*) FROM dataset WHERE {where} "
                    f"AND {_missing_sql(subject, missing=True)}",
                    parameters,
                ).fetchone()[0]
            )

    def find_duplicate(
        self,
        source: DatasetHandle,
        config: ShiftTableConfig,
        population: DatasetHandle | None = None,
    ) -> DuplicateShiftRecordsError | None:
        config.validate(source.metadata, population.metadata if population else None)
        where, parameters = self._analysis_where(source, config, complete_grades=False)
        columns = [
            config.subject_variable,
            config.parameter_variable,
            config.visit_variable,
            config.treatment_variable,
        ]
        sql_columns = ", ".join(quote_identifier(name) for name in columns)
        uri = source.database_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            row = connection.execute(
                f"SELECT {sql_columns}, count(*) FROM dataset WHERE {where} "
                f"GROUP BY {sql_columns} HAVING count(*) > 1 LIMIT 1",
                parameters,
            ).fetchone()
        if row is None:
            return None
        return DuplicateShiftRecordsError(row[0], row[1], row[2], row[3], int(row[4]))

    def _validate_runtime_grades(
        self,
        connection: sqlite3.Connection,
        source: DatasetHandle,
        config: ShiftTableConfig,
        where: str,
        parameters: tuple[object, ...],
    ) -> None:
        fields = self._fields(source)
        for variable_name in (
            config.baseline_grade_variable,
            config.post_grade_variable,
        ):
            variable = fields[variable_name.casefold()]
            rows = connection.execute(
                f"SELECT DISTINCT {quote_identifier(variable.name)} FROM dataset "
                f"WHERE {where} AND {_missing_sql(variable, missing=False)}",
                parameters,
            ).fetchall()
            configured = {
                _level_key(level.raw_value, variable) for level in config.grade_levels
            }
            unknown = [
                row[0] for row in rows if _level_key(row[0], variable) not in configured
            ]
            if unknown:
                rendered = ", ".join(repr(value) for value in unknown[:10])
                suffix = "…" if len(unknown) > 10 else ""
                raise ValueError(
                    f"{variable.name} contains unconfigured nonmissing grade levels: "
                    f"{rendered}{suffix}. Add them to Grade Levels before running."
                )

    @staticmethod
    def _group_counts(
        connection: sqlite3.Connection,
        *,
        select: tuple[str, ...],
        subject: str,
        where: str,
        parameters: tuple[object, ...],
    ) -> dict[tuple[object, ...], int]:
        columns = ", ".join(quote_identifier(name) for name in select)
        rows = connection.execute(
            f"SELECT {columns}, count(DISTINCT {quote_identifier(subject)}) "
            f"FROM dataset WHERE {where} GROUP BY {columns}",
            parameters,
        ).fetchall()
        return {tuple(row[:-1]): int(row[-1]) for row in rows}

    def _population_counts(
        self,
        source: DatasetHandle,
        config: ShiftTableConfig,
        population: DatasetHandle | None,
    ) -> tuple[dict[object, int], int]:
        population_source = population or source
        fields = self._fields(population_source)
        subject = fields[config.population_subject_variable.casefold()]
        treatment_name = (
            config.population_treatment_variable or config.treatment_variable
        )
        treatment = fields[treatment_name.casefold()]
        clauses: list[str] = []
        parameters: list[object] = []
        if config.population_filter.sql:
            clauses.append(f"({config.population_filter.sql})")
            parameters.extend(config.population_filter.parameters)
        in_clause, values = self._in_clause(treatment, config.treatments)
        clauses.append(in_clause)
        parameters.extend(values)
        clauses.append(_missing_sql(treatment, missing=False))
        clauses.append(_missing_sql(subject, missing=False))
        where = " AND ".join(clauses)
        uri = population_source.database_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            rows = connection.execute(
                f"SELECT {quote_identifier(treatment.name)}, "
                f"count(DISTINCT {quote_identifier(subject.name)}) "
                f"FROM dataset WHERE {where} GROUP BY {quote_identifier(treatment.name)}",
                tuple(parameters),
            ).fetchall()
            total = int(
                connection.execute(
                    f"SELECT count(DISTINCT {quote_identifier(subject.name)}) "
                    f"FROM dataset WHERE {where}",
                    tuple(parameters),
                ).fetchone()[0]
            )
        return ({row[0]: int(row[1]) for row in rows}, total)

    def run(
        self,
        source: DatasetHandle,
        config: ShiftTableConfig,
        progress: ProgressCallback | None = None,
        population: DatasetHandle | None = None,
    ) -> DatasetHandle:
        if not source.cache_complete:
            raise ValueError("The source dataset must finish loading first.")
        if population is not None and not population.cache_complete:
            raise ValueError("The Population Dataset must finish loading first.")
        config.validate(source.metadata, population.metadata if population else None)
        duplicate = self.find_duplicate(source, config, population)
        if duplicate is not None and not config.allow_duplicate_records:
            raise duplicate
        notify = progress or (lambda _message: None)
        notify("Validating Shift Table analysis records…")
        fields = self._fields(source)
        subject = fields[config.subject_variable.casefold()]
        parameter = fields[config.parameter_variable.casefold()]
        visit = fields[config.visit_variable.casefold()]
        treatment = fields[config.treatment_variable.casefold()]
        baseline = fields[config.baseline_grade_variable.casefold()]
        post = fields[config.post_grade_variable.casefold()]
        base_where, base_parameters = self._analysis_where(
            source, config, complete_grades=False
        )
        raw_where, raw_parameters = self._analysis_where(
            source,
            config,
            complete_grades=False,
            nonmissing_subject=False,
        )
        complete_where, complete_parameters = self._analysis_where(
            source, config, complete_grades=True
        )
        uri = source.database_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            self._validate_runtime_grades(
                connection, source, config, base_where, base_parameters
            )
            missing_subject_rows = int(
                connection.execute(
                    f"SELECT count(*) FROM dataset WHERE {raw_where} "
                    f"AND {_missing_sql(subject, missing=True)}",
                    raw_parameters,
                ).fetchone()[0]
            )
            missing_grade_rows = int(
                connection.execute(
                    f"SELECT count(*) FROM dataset WHERE {base_where} AND ("
                    f"{_missing_sql(baseline, missing=True)} OR {_missing_sql(post, missing=True)})",
                    base_parameters,
                ).fetchone()[0]
            )
            missing_grade_subjects = int(
                connection.execute(
                    f"SELECT count(DISTINCT {quote_identifier(subject.name)}) "
                    f"FROM dataset WHERE {base_where} AND ("
                    f"{_missing_sql(baseline, missing=True)} OR {_missing_sql(post, missing=True)})",
                    base_parameters,
                ).fetchone()[0]
            )

            p, v, t, b, g = (
                parameter.name,
                visit.name,
                treatment.name,
                baseline.name,
                post.name,
            )
            denom = self._group_counts(
                connection,
                select=(p, v, t),
                subject=subject.name,
                where=complete_where,
                parameters=complete_parameters,
            )
            detail = self._group_counts(
                connection,
                select=(p, v, t, b, g),
                subject=subject.name,
                where=complete_where,
                parameters=complete_parameters,
            )
            row_total = self._group_counts(
                connection,
                select=(p, v, t, b),
                subject=subject.name,
                where=complete_where,
                parameters=complete_parameters,
            )
            column_total = self._group_counts(
                connection,
                select=(p, v, t, g),
                subject=subject.name,
                where=complete_where,
                parameters=complete_parameters,
            )
            overall_denom = self._group_counts(
                connection,
                select=(p, v),
                subject=subject.name,
                where=complete_where,
                parameters=complete_parameters,
            )
            overall_detail = self._group_counts(
                connection,
                select=(p, v, b, g),
                subject=subject.name,
                where=complete_where,
                parameters=complete_parameters,
            )
            overall_row = self._group_counts(
                connection,
                select=(p, v, b),
                subject=subject.name,
                where=complete_where,
                parameters=complete_parameters,
            )
            overall_column = self._group_counts(
                connection,
                select=(p, v, g),
                subject=subject.name,
                where=complete_where,
                parameters=complete_parameters,
            )

        population_counts, population_total = self._population_counts(
            source, config, population
        )
        treatments = tuple(sorted(config.treatments, key=lambda item: item.order))
        grades = tuple(sorted(config.grade_levels, key=lambda item: item.order))
        columns: list[ShiftColumn] = []
        for treatment_index, treatment_level in enumerate(treatments, start=1):
            header_n = population_counts.get(treatment_level.raw_value, 0)
            for grade_index, grade in enumerate(grades, start=1):
                columns.append(
                    ShiftColumn(
                        f"T{treatment_index}_G{grade_index}",
                        f"{treatment_level.display_label}\n(N={header_n:,})\n{grade.display_label}",
                        treatment_level.raw_value,
                        grade.raw_value,
                        False,
                        "detail",
                    )
                )
            columns.append(
                ShiftColumn(
                    f"T{treatment_index}_TOTAL",
                    f"{treatment_level.display_label}\n(N={header_n:,})\nTotal",
                    treatment_level.raw_value,
                    None,
                    False,
                    "row_total",
                )
            )
        if config.include_overall_total:
            for grade_index, grade in enumerate(grades, start=1):
                columns.append(
                    ShiftColumn(
                        f"TOTAL_G{grade_index}",
                        f"Total\n(N={population_total:,})\n{grade.display_label}",
                        None,
                        grade.raw_value,
                        True,
                        "detail",
                    )
                )
            columns.append(
                ShiftColumn(
                    "TOTAL_ALL",
                    f"Total\n(N={population_total:,})\nTotal",
                    None,
                    None,
                    True,
                    "row_total",
                )
            )

        directory = self.temp_manager.create_dataset_directory()
        writer = ShiftTableResultWriter(directory / "dataset.sqlite", tuple(columns))
        parameters = tuple(sorted(config.parameters, key=lambda item: item.order))
        visits = tuple(sorted(config.visits, key=lambda item: item.order))
        treatment_by_raw = {level.raw_value: level for level in treatments}
        try:
            notify("Building ordered Shift Table result…")
            for parameter_level in parameters:
                for visit_level in visits:
                    if not any(
                        key[0] == parameter_level.raw_value
                        and key[1] == visit_level.raw_value
                        and value > 0
                        for key, value in denom.items()
                    ):
                        continue
                    for baseline_level in (*grades, None):
                        baseline_raw = (
                            baseline_level.raw_value
                            if baseline_level is not None
                            else None
                        )
                        cells: dict[
                            str,
                            tuple[
                                ShiftColumn,
                                int,
                                str,
                                int,
                                object | None,
                                str,
                                ShiftCellValue,
                            ],
                        ] = {}
                        for column in columns:
                            if column.is_overall:
                                denominator = overall_denom.get(
                                    (parameter_level.raw_value, visit_level.raw_value),
                                    0,
                                )
                                treatment_order = len(treatments) + 1
                                treatment_label = "Total"
                                if baseline_raw is None and column.post_grade is None:
                                    numerator = denominator
                                elif baseline_raw is None:
                                    numerator = overall_column.get(
                                        (
                                            parameter_level.raw_value,
                                            visit_level.raw_value,
                                            column.post_grade,
                                        ),
                                        0,
                                    )
                                elif column.post_grade is None:
                                    numerator = overall_row.get(
                                        (
                                            parameter_level.raw_value,
                                            visit_level.raw_value,
                                            baseline_raw,
                                        ),
                                        0,
                                    )
                                else:
                                    numerator = overall_detail.get(
                                        (
                                            parameter_level.raw_value,
                                            visit_level.raw_value,
                                            baseline_raw,
                                            column.post_grade,
                                        ),
                                        0,
                                    )
                            else:
                                treatment_level = treatment_by_raw[column.treatment]
                                denominator = denom.get(
                                    (
                                        parameter_level.raw_value,
                                        visit_level.raw_value,
                                        column.treatment,
                                    ),
                                    0,
                                )
                                treatment_order = treatment_level.order
                                treatment_label = treatment_level.display_label
                                if baseline_raw is None and column.post_grade is None:
                                    numerator = denominator
                                elif baseline_raw is None:
                                    numerator = column_total.get(
                                        (
                                            parameter_level.raw_value,
                                            visit_level.raw_value,
                                            column.treatment,
                                            column.post_grade,
                                        ),
                                        0,
                                    )
                                elif column.post_grade is None:
                                    numerator = row_total.get(
                                        (
                                            parameter_level.raw_value,
                                            visit_level.raw_value,
                                            column.treatment,
                                            baseline_raw,
                                        ),
                                        0,
                                    )
                                else:
                                    numerator = detail.get(
                                        (
                                            parameter_level.raw_value,
                                            visit_level.raw_value,
                                            column.treatment,
                                            baseline_raw,
                                            column.post_grade,
                                        ),
                                        0,
                                    )
                            post_level = next(
                                (
                                    level
                                    for level in grades
                                    if level.raw_value == column.post_grade
                                ),
                                None,
                            )
                            cells[column.name] = (
                                column,
                                treatment_order,
                                treatment_label,
                                post_level.order
                                if post_level is not None
                                else len(grades) + 1,
                                column.post_grade,
                                post_level.display_label
                                if post_level is not None
                                else "Total",
                                _format_percent(
                                    numerator, denominator, config.percent_digits
                                ),
                            )
                        writer.add_matrix_row(
                            parameter_order=parameter_level.order,
                            parameter_raw=parameter_level.raw_value,
                            parameter_label=parameter_level.display_label,
                            visit_order=visit_level.order,
                            visit_raw=visit_level.raw_value,
                            visit_label=visit_level.display_label,
                            baseline_order=(
                                baseline_level.order
                                if baseline_level is not None
                                else len(grades) + 1
                            ),
                            baseline_raw=baseline_raw,
                            baseline_label=(
                                baseline_level.display_label
                                if baseline_level is not None
                                else "Total"
                            ),
                            cells=cells,
                        )
            configuration_path = directory / "shift_table_config.json"
            write_shift_table_configuration(
                configuration_path, source, config, population=population
            )
            messages: list[str] = []
            if missing_subject_rows:
                messages.append(
                    f"Excluded {missing_subject_rows:,} analysis rows with missing {subject.name}."
                )
            if missing_grade_rows:
                messages.append(
                    f"Excluded {missing_grade_rows:,} records ({missing_grade_subjects:,} distinct subjects) with missing baseline or post-baseline grade."
                )
            if duplicate is not None:
                messages.append(
                    "Duplicate subject/parameter/visit/treatment records were retained after user confirmation."
                )
            return writer.finish(
                directory,
                source,
                configuration_path,
                exclusion_message=" ".join(messages),
            )
        except BaseException:
            writer.abort(self.temp_manager, directory)
            raise


__all__ = ["DuplicateShiftRecordsError", "ShiftTableEngine"]
