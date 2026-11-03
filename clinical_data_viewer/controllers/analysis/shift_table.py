from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QMessageBox

from ...categorical.drilldown import CategoricalQueryBuilder
from ...codegen.sas import SasShiftTableGenerator
from ...dataset_utils import is_analysis_dataset
from ...domain import DatasetHandle, VariableMetadata
from ...filter_engine import FilterEngine, quote_identifier
from ...shell_assets import load_shift_table_shell_preview
from ...shift_table import (
    ShiftTableConfig,
    ShiftTableEngine,
    ShiftTableLongResultBuilder,
    build_shift_table_configuration,
)
from ...shift_table.drilldown import (
    build_cell_filter,
    build_cell_where_text,
    build_header_filter,
    build_header_where_text,
    lookup_cell,
    lookup_header,
)
from ...ui.dataset_tab import DatasetTab
from ...ui.sas_code_dialog import SasCodeDialog
from ...ui.shell_preview_dialog import ShellPreviewDialog
from ...ui.shift_table_builder import ShiftTableBuilderSelection
from ._base import AnalysisModuleController


@dataclass(frozen=True, slots=True)
class ShiftTableResultContext:
    source: DatasetHandle
    config: ShiftTableConfig
    population: DatasetHandle


class ShiftTableController(AnalysisModuleController):
    name = "shift_table"

    def __init__(self, owner: Any, temp_manager, settings) -> None:
        super().__init__(owner)
        self._host = owner._host
        self._panel = owner._panel
        self._engine = ShiftTableEngine(temp_manager)
        self._sas_generator = SasShiftTableGenerator()
        self._long_result_builder = ShiftTableLongResultBuilder(temp_manager)
        self._query_builder = CategoricalQueryBuilder(temp_manager)
        self._source: DatasetTab | None = None
        self._population: DatasetTab | None = None
        self._input_tabs: set[DatasetTab] = set()
        self._results: dict[DatasetTab, ShiftTableResultContext] = {}

    @property
    def source(self) -> DatasetTab | None:
        if self._source is None or not self._host.is_open_dataset_tab(self._source):
            return None
        return self._source

    @property
    def population(self) -> DatasetTab | None:
        if self._population is None or not self._host.is_open_dataset_tab(
            self._population
        ):
            return None
        return self._population

    def is_input_tab(self, tab: object) -> bool:
        return tab in self._input_tabs

    def take_result_release_paths(self, tab: object) -> tuple[Path, ...] | None:
        context = self._results.pop(tab, None)
        if context is None:
            return None
        return tuple(
            dict.fromkeys(
                (
                    context.source.temporary_path.parent,
                    context.population.temporary_path.parent,
                )
            )
        )

    def refresh_sources(self, datasets=None) -> None:
        available = (
            list(datasets)
            if datasets is not None
            else self._host.available_analysis_dataset_tabs()
        )
        selected = self.source
        if self._source is not None and selected is None:
            self._source = None
            self._set_dataset(None)
        elif selected is not None:
            self._set_dataset(selected)
        if self._population is not None and self.population is None:
            self._population = None
            self._set_population_dataset(None)
        builder = self._panel.shift_table_builder
        builder.set_source_datasets(available, selected)
        builder.set_population_datasets(available, self.population)

    def select_source(self, tab) -> None:
        if not isinstance(tab, DatasetTab):
            self._source = None
            self._set_dataset(None)
            return
        if not is_analysis_dataset(tab.handle) or not tab.cache_complete:
            return
        self._source = tab
        self._set_dataset(tab)

    def select_population(self, tab) -> None:
        if tab is None:
            self._population = None
            self._set_population_dataset(None)
            return
        if (
            not isinstance(tab, DatasetTab)
            or not is_analysis_dataset(tab.handle)
            or not tab.cache_complete
        ):
            return
        self._population = tab
        self._set_population_dataset(tab)

    def show_builder(self) -> None:
        self.refresh_sources()
        self._panel.show_shift_table_tab()

    def show_shell_preview(self) -> None:
        dialog = ShellPreviewDialog(
            self._parent_widget(), load_shift_table_shell_preview()
        )
        dialog.setWindowTitle("Shift from Baseline Shell Preview")
        dialog.exec()

    def clear_source(self) -> None:
        self._source = None
        self._population = None
        self._set_dataset(None)
        self._set_population_dataset(None)
        self.refresh_sources()

    def source_reload_started(self, tab: DatasetTab) -> None:
        if tab in {self._source, self._population}:
            self._panel.shift_table_builder.set_busy(
                True, "Source is reloading. Shift Table is temporarily unavailable."
            )

    def source_reload_completed(self, tab: DatasetTab) -> None:
        if self._source is tab:
            self._set_dataset(tab)
        if self._population is tab:
            self._set_population_dataset(tab)
        if tab in {self._source, self._population}:
            self._panel.shift_table_builder.set_busy(False, "Source reloaded.")

    def source_reload_failed(self, tab: DatasetTab) -> None:
        if tab in {self._source, self._population}:
            self._panel.shift_table_builder.set_busy(
                False, "Source reload failed; Builder configuration was retained."
            )

    def _set_dataset(self, tab: DatasetTab | None) -> None:
        builder = self._panel.shift_table_builder
        if tab is None:
            builder.set_dataset(None, "")
            return
        builder.set_dataset(
            tab.handle.metadata,
            str(tab.handle.source_path),
            tab.current_where_text(),
            tab.handle.kind,
        )

    def _set_population_dataset(self, tab: DatasetTab | None) -> None:
        builder = self._panel.shift_table_builder
        if tab is None:
            builder.set_population_dataset(None, "")
            return
        builder.set_population_dataset(
            tab.handle.metadata,
            str(tab.handle.source_path),
            tab.current_where_text(),
        )

    @staticmethod
    def _sort_values(
        values: set[object], variable: VariableMetadata
    ) -> tuple[object, ...]:
        if variable.kind == "numeric":
            return tuple(sorted(values, key=float))
        return tuple(
            sorted(values, key=lambda value: (str(value).casefold(), str(value)))
        )

    def load_levels(self, role: str, variable_text: str) -> None:
        tab = self.source
        if tab is None:
            return
        names = variable_text.splitlines()
        fields = {
            variable.name.casefold(): variable
            for variable in tab.handle.metadata.variables
        }
        try:
            variables = [fields[name.casefold()] for name in names]
        except KeyError as error:
            QMessageBox.warning(
                self._parent_widget(),
                "Shift Table",
                f"Unknown variable: {error.args[0]}",
            )
            return
        if len({variable.kind for variable in variables}) != 1:
            QMessageBox.warning(
                self._parent_widget(),
                "Shift Table",
                "Baseline and post-baseline grade variables must have the same storage type.",
            )
            return
        values: set[object] = set()
        uri = tab.handle.database_path.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as connection:
            for variable in variables:
                column = quote_identifier(variable.name)
                rows = connection.execute(
                    f"SELECT DISTINCT {column} FROM dataset WHERE {column} IS NOT NULL"
                    + (f" AND {column} <> ''" if variable.kind == "character" else "")
                    + f" ORDER BY {column}"
                ).fetchall()
                values.update(row[0] for row in rows)
        self._panel.shift_table_builder.set_level_values(
            role, self._sort_values(values, variables[0])
        )

    def _context(self, selection: ShiftTableBuilderSelection):
        tab = self.source
        if (
            tab is None
            or not tab.cache_complete
            or getattr(tab, "reload_in_progress", False)
        ):
            QMessageBox.warning(
                self._parent_widget(),
                "Shift Table",
                "The Builder source is unavailable. Open a fully loaded source dataset first.",
            )
            return None
        population_tab = self.population
        if population_tab is None:
            answer = QMessageBox.question(
                self._parent_widget(),
                "Population Dataset Not Selected",
                "No Population Dataset is selected. Use the current Analysis Dataset to calculate Header Population N?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return None
        population_handle = population_tab.handle if population_tab else tab.handle
        population_subject = (
            selection.population_subject_variable or selection.subject_variable
        )
        population_treatment = (
            selection.population_treatment_variable or selection.treatment_variable
        )
        try:
            compiled = FilterEngine(tab.handle.metadata.variables).compile(
                selection.filter_text
            )
            population_filter = FilterEngine(
                population_handle.metadata.variables
            ).compile(selection.population_filter_text)
            config = ShiftTableConfig(
                selection.subject_variable,
                selection.parameter_variable,
                selection.visit_variable,
                selection.treatment_variable,
                selection.baseline_grade_variable,
                selection.post_grade_variable,
                selection.analysis_flag_variable,
                selection.analysis_flag_value,
                selection.parameters,
                selection.visits,
                selection.treatments,
                selection.grade_levels,
                compiled,
                selection.filter_text,
                selection.include_overall_total,
                selection.percent_digits,
                population_subject,
                population_treatment,
                population_filter,
                selection.population_filter_text,
            )
            config.validate(tab.handle.metadata, population_handle.metadata)
            missing_subjects = self._engine.missing_subject_count(
                tab.handle, config, population_handle
            )
            if missing_subjects:
                QMessageBox.warning(
                    self._parent_widget(),
                    "Missing Subject IDs",
                    f"{missing_subjects:,} selected analysis records have a missing "
                    f"{selection.subject_variable}. They will be excluded from Shift Table counts.",
                )
            duplicate = self._engine.find_duplicate(
                tab.handle, config, population_handle
            )
            if duplicate is not None:
                answer = QMessageBox.warning(
                    self._parent_widget(),
                    "Multiple Shift Analysis Records",
                    str(duplicate)
                    + "\n\nA subject may contribute to more than one shift cell. "
                    "Review the analysis flag and filter before continuing.",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if answer != QMessageBox.Yes:
                    return None
                config = replace(config, allow_duplicate_records=True)
        except ValueError as error:
            QMessageBox.warning(self._parent_widget(), "Shift Table", str(error))
            return None
        return tab, population_tab, population_handle, config

    def run(self, selection: ShiftTableBuilderSelection) -> None:
        context = self._context(selection)
        if context is None:
            return
        tab, population_tab, population_handle, config = context
        source = tab.handle
        builder = self._panel.shift_table_builder
        self._input_tabs = {tab} | ({population_tab} if population_tab else set())
        builder.set_busy(True, "Calculating Shift Table in the background…")

        def completed(handle: DatasetHandle) -> None:
            self._input_tabs.clear()
            builder.set_busy(
                False, f"Created {handle.metadata.row_count:,} result rows."
            )
            result_tab = self._host.create_analysis_result_tab(handle)
            self._host.retain_analysis_directory(source.temporary_path.parent)
            self._host.retain_analysis_directory(
                population_handle.temporary_path.parent
            )
            self._results[result_tab] = ShiftTableResultContext(
                source, config, population_handle
            )
            self._host.show_analysis_result_tab(result_tab, "Shift Table Result")

        def failed(message: str, details: str) -> None:
            self._input_tabs.clear()
            builder.set_busy(False, "Shift Table failed.")
            self._host.show_analysis_error("Shift Table Failed", message, details)

        self._host.submit_analysis_task(
            builder,
            lambda worker: self._engine.run(
                source, config, worker.report, population_handle
            ),
            completed,
            failed,
        )

    def generate_sas_code(self, selection: ShiftTableBuilderSelection) -> None:
        context = self._context(selection)
        if context is None:
            return
        tab, population_tab, population_handle, config = context
        if tab.handle.kind != "sas" or population_handle.kind != "sas":
            QMessageBox.warning(
                self._parent_widget(),
                "Shift Table",
                "SAS Code Generator supports SAS datasets only; merged sources are not supported.",
            )
            return
        builder = self._panel.shift_table_builder
        self._input_tabs = {tab} | ({population_tab} if population_tab else set())
        builder.set_busy(True, "Generating Shift Table SAS code…")

        def completed(code: str) -> None:
            self._input_tabs.clear()
            builder.set_busy(False, "SAS code generated.")
            SasCodeDialog(
                code,
                f"adam.{tab.handle.metadata.name.casefold()}",
                f"{tab.handle.metadata.name.casefold()}_shift_table.sas",
                self._parent_widget(),
            ).exec()

        def failed(message: str, details: str) -> None:
            self._input_tabs.clear()
            builder.set_busy(False, "SAS Code Generator failed.")
            self._host.show_analysis_error(
                "Shift Table SAS Code Generator Failed", message, details
            )

        self._host.submit_analysis_task(
            builder,
            lambda _worker: self._sas_generator.generate(
                build_shift_table_configuration(
                    tab.handle, config, population=population_handle
                )
            ),
            completed,
            failed,
        )

    def open_long_result(self) -> None:
        tab = self._host.current_dataset_tab()
        context = self._results.get(tab) if tab is not None else None
        if tab is None or tab.handle.kind != "shift_table" or context is None:
            return
        title = self._host.unique_analysis_tab_title("Shift Table Long Result")

        def completed(handle: DatasetHandle) -> None:
            if not self._host.is_open_dataset_tab(tab):
                self._host.discard_analysis_result(handle)
                return
            self._host.show_analysis_result_tab(
                self._host.create_analysis_result_tab(handle), title
            )

        self._host.submit_analysis_task(
            tab,
            lambda _worker: self._long_result_builder.run(tab.handle, context.source),
            completed,
            lambda message, details: self._host.show_analysis_error(
                "Shift Table Long Result Failed", message, details
            ),
        )

    def _open_query(
        self,
        result_tab: DatasetTab,
        source: DatasetHandle,
        compiled,
        where_text: str,
        title: str,
    ) -> None:
        generation = result_tab.generation

        def completed(handle: DatasetHandle) -> None:
            if (
                not self._host.is_open_dataset_tab(result_tab)
                or generation != result_tab.generation
            ):
                self._host.discard_analysis_result(handle)
                return
            query_tab = self._host.create_analysis_result_tab(handle)
            query_tab.set_where_display(where_text)
            self._host.show_analysis_result_tab(query_tab, title)

        self._host.submit_analysis_task(
            result_tab,
            lambda _worker: self._query_builder.run(
                source, compiled.sql, compiled.parameters, title
            ),
            completed,
            lambda message, details: self._host.show_analysis_error(
                "Shift Table Drill-down Failed", message, details
            ),
        )

    def drilldown(
        self, tab: DatasetTab, view_row: int, column_name: str, display: str
    ) -> None:
        context = self._results.get(tab)
        source_row = tab.model.source_row_id(view_row)
        if context is None or source_row is None or not display:
            return
        cell = lookup_cell(tab.handle, source_row, column_name)
        if cell is None:
            return
        try:
            compiled = build_cell_filter(context.source.metadata, context.config, cell)
            where_text = build_cell_where_text(
                context.source.metadata, context.config, cell
            )
        except (KeyError, TypeError, ValueError) as error:
            QMessageBox.warning(
                self._parent_widget(), "Shift Table Drill-down", str(error)
            )
            return
        title = self._host.unique_analysis_tab_title("Query: Shift Cell Source Rows")
        self._open_query(tab, context.source, compiled, where_text, title)

    def drilldown_header(self, tab: DatasetTab, column_name: str) -> None:
        context = self._results.get(tab)
        if context is None:
            return
        header = lookup_header(tab.handle, column_name)
        if header is None:
            return
        try:
            compiled = build_header_filter(
                context.population.metadata, context.config, header
            )
            where_text = build_header_where_text(
                context.population.metadata, context.config, header
            )
        except (KeyError, TypeError, ValueError) as error:
            QMessageBox.warning(
                self._parent_widget(), "Shift Table Population Drill-down", str(error)
            )
            return
        title = self._host.unique_analysis_tab_title("Query: Population Header N")
        self._open_query(tab, context.population, compiled, where_text, title)


__all__ = ["ShiftTableController", "ShiftTableResultContext"]
