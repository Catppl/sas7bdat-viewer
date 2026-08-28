from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QMessageBox

from ...categorical import (
    CategoricalConfig,
    CategoricalEngine,
    CategoricalLongResultBuilder,
    DenominatorConfig,
)
from ...categorical.drilldown import (
    CategoricalQueryBuilder,
    build_cell_filter,
    build_n1_cell_filter,
    lookup_cell,
)
from ...dataset_utils import is_analysis_dataset
from ...domain import DatasetHandle
from ...filter_engine import FilterEngine
from ...ui.categorical_builder import CategoricalBuilderSelection
from ...ui.dataset_tab import DatasetTab
from ._base import AnalysisModuleController


@dataclass(frozen=True, slots=True)
class CategoricalResultContext:
    source: DatasetHandle
    population: DatasetHandle | None
    config: CategoricalConfig


class CategoricalController(AnalysisModuleController):
    name = "categorical"

    def __init__(self, owner: Any, temp_manager, settings) -> None:
        super().__init__(owner)
        self._host = owner._host
        self._panel = owner._panel
        self._settings = settings
        self._categorical_engine = CategoricalEngine(temp_manager)
        self._categorical_long_result_builder = CategoricalLongResultBuilder(
            temp_manager
        )
        self._categorical_query_builder = CategoricalQueryBuilder(temp_manager)
        self._categorical_source: DatasetTab | None = None
        self._categorical_input_tabs: set[DatasetTab] = set()
        self._categorical_results: dict[DatasetTab, CategoricalResultContext] = {}

    def is_input_tab(self, tab: object) -> bool:
        """Return whether a dataset is retained by an active Categorical task."""
        return tab in self._categorical_input_tabs

    def take_result_release_paths(self, tab: object) -> tuple[Path, ...] | None:
        """Detach a Categorical result and return retained source directories."""
        context = self._categorical_results.pop(tab, None)
        if context is None:
            return None
        paths = {context.source.temporary_path.parent}
        if context.population is not None:
            paths.add(context.population.temporary_path.parent)
        return tuple(paths)

    @property
    def categorical_source(self) -> DatasetTab | None:
        """Return Categorical Builder's fixed source while its tab remains open."""
        if self._categorical_source is None:
            return None
        if not self._host.is_open_dataset_tab(self._categorical_source):
            return None
        return self._categorical_source

    def refresh_categorical_adsl_sources(self, datasets=None) -> None:
        self._panel.categorical_builder.set_adsl_sources(
            list(datasets) if datasets is not None else self._host.available_sas_dataset_tabs()
        )

    def select_categorical_adsl(self, tab: DatasetTab) -> None:
        self._panel.categorical_builder.select_adsl(tab)

    def show_categorical_builder(self) -> None:
        """Open Categorical Builder with its first eligible source fixed until Clear."""
        if self.categorical_source is None:
            active_tab = self._host.current_dataset_tab()
            if (
                active_tab is not None
                and is_analysis_dataset(active_tab.handle)
                and active_tab.cache_complete
            ):
                self._categorical_source = active_tab
                self._set_categorical_dataset(active_tab)
        self.refresh_categorical_adsl_sources()
        self._panel.show_categorical_tab()

    def clear_categorical_source(self) -> None:
        """Release Categorical Builder's fixed source only after Clear."""
        self._categorical_source = None
        self._set_categorical_dataset(None)

    def _set_categorical_dataset(self, tab: DatasetTab | None) -> None:
        builder = self._panel.categorical_builder
        if tab is None:
            builder.set_dataset(None, "")
            return
        builder.set_dataset(
            tab.handle.metadata,
            str(tab.handle.source_path),
            tab.current_where_text(),
        )

    def _categorical_context(
        self, selection: CategoricalBuilderSelection
    ) -> tuple[DatasetTab, DatasetTab | None, CategoricalConfig] | None:
        tab = self.categorical_source
        if tab is None or not is_analysis_dataset(tab.handle) or not tab.cache_complete:
            QMessageBox.warning(
                self._parent_widget(),
                "Categorical Table",
                "The Builder source is unavailable. Open a fully loaded source dataset, then open the Builder again.",
            )
            return None
        population_tab = selection.population_tab
        if selection.denominator_type == "population" and (
            not isinstance(population_tab, DatasetTab)
            or not population_tab.cache_complete
        ):
            QMessageBox.warning(
                self._parent_widget(),
                "Categorical Table",
                "Open or browse a fully loaded ADSL dataset for Population N.",
            )
            return None
        try:
            numerator_filter = FilterEngine(tab.handle.metadata.variables).compile(
                selection.numerator_filter_text
            )
            population_filter = (
                FilterEngine(population_tab.handle.metadata.variables).compile(
                    selection.population_filter_text
                )
                if isinstance(population_tab, DatasetTab)
                else FilterEngine(tab.handle.metadata.variables).compile("")
            )
            baseline_filter = FilterEngine(tab.handle.metadata.variables).compile(
                selection.baseline_filter_text
            )
            postbaseline_filter = FilterEngine(tab.handle.metadata.variables).compile(
                selection.postbaseline_filter_text
            )
            config = CategoricalConfig(
                selection.items,
                selection.treatment_variable,
                selection.subject_id_variable,
                selection.count_type,
                numerator_filter,
                selection.numerator_filter_text,
                DenominatorConfig(
                    selection.denominator_type,
                    selection.analysis_value_variable,
                    population_filter,
                    selection.population_filter_text,
                    baseline_filter,
                    selection.baseline_filter_text,
                    postbaseline_filter,
                    selection.postbaseline_filter_text,
                ),
                selection.include_total,
                selection.percent_digits,
            )
            config.validate(
                tab.handle.metadata,
                population_tab.handle.metadata
                if isinstance(population_tab, DatasetTab)
                else None,
            )
        except ValueError as error:
            QMessageBox.warning(self._parent_widget(), "Categorical Table", str(error))
            return None
        return (
            tab,
            population_tab if isinstance(population_tab, DatasetTab) else None,
            config,
        )

    def run_categorical(self, selection: CategoricalBuilderSelection) -> None:
        source_tab = self.categorical_source
        if source_tab is None or not is_analysis_dataset(source_tab.handle):
            return
        builder = self._panel.categorical_builder
        current_filter = source_tab.current_where_text()
        if not builder.current_filter_text() and current_filter:
            response = QMessageBox.question(
                self._parent_widget(),
                "Categorical Table",
                "Numerator WHERE is empty. Use the current dataset WHERE for this calculation?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if response == QMessageBox.Yes:
                builder.apply_current_filter(current_filter)
        selection = replace(
            selection, numerator_filter_text=builder.current_filter_text()
        )
        context = self._categorical_context(selection)
        if context is None:
            return
        source_tab, population_tab, config = context
        source_handle = source_tab.handle
        population_handle = population_tab.handle if population_tab else None
        self._categorical_input_tabs = {source_tab}
        if population_tab:
            self._categorical_input_tabs.add(population_tab)
        builder.set_busy(True, "Calculating categorical table in the background…")

        def completed(handle: DatasetHandle) -> None:
            self._categorical_input_tabs.clear()
            builder.set_busy(
                False, f"Created {handle.metadata.row_count:,} result rows."
            )
            result_tab = self._host.create_analysis_result_tab(handle)
            for directory in {
                source_handle.temporary_path.parent,
                *([population_handle.temporary_path.parent] if population_handle else []),
            }:
                self._host.retain_analysis_directory(directory)
            self._categorical_results[result_tab] = CategoricalResultContext(
                source_handle, population_handle, config
            )
            self._host.show_analysis_result_tab(result_tab, "Categorical Table Result")

        def failed(message: str, details: str) -> None:
            self._categorical_input_tabs.clear()
            builder.set_busy(False, "Categorical Table failed.")
            self._host.show_analysis_error("Categorical Table Failed", message, details)

        self._host.submit_analysis_task(
            builder,
            lambda worker: self._categorical_engine.run(
                source_handle, config, population_handle, worker.report
            ),
            completed,
            failed,
        )

    def drilldown_categorical(
        self, tab: DatasetTab, view_row: int, column_name: str, display: str
    ) -> None:
        context = self._categorical_results.get(tab)
        source_row = tab.model.source_row_id(view_row)
        if context is None or source_row is None or not display:
            return
        cell = lookup_cell(tab.handle, source_row, column_name)
        if cell is None:
            return
        dialog, records, subjects, denominator = self._drilldown_dialog()
        dialog.setWindowTitle("Categorical Table Drill-down")
        dialog.exec()
        selected = dialog.selected_button
        if selected not in {records, subjects, denominator}:
            return
        is_denominator = selected is denominator
        target = (
            context.population
            if is_denominator and context.config.denominator.type == "population"
            else context.source
        )
        if target is None:
            QMessageBox.warning(
                self._parent_widget(),
                "Categorical Table Drill-down",
                "The required source dataset is no longer available.",
            )
            return
        try:
            if context.config.denominator.type == "baseline_postbaseline":
                where_sql, parameters = build_n1_cell_filter(
                    context.source.metadata,
                    context.config,
                    cell,
                    denominator=is_denominator,
                )
            else:
                where_sql, parameters = build_cell_filter(
                    target.metadata,
                    context.config,
                    cell,
                    denominator=is_denominator,
                )
        except (StopIteration, ValueError) as error:
            QMessageBox.warning(
                self._parent_widget(), "Categorical Table Drill-down", str(error)
            )
            return
        mode = (
            "Denominator Subjects"
            if is_denominator
            else "Numerator Subjects"
            if selected is subjects
            else "Numerator Records"
        )
        title = self._host.unique_analysis_tab_title(f"Query: {mode}")
        self._host.set_analysis_task_status(f"Building {title}…")

        def completed(handle: DatasetHandle) -> None:
            if not self._host.is_open_dataset_tab(tab):
                self._host.discard_analysis_result(handle)
                return
            query_tab = self._host.create_analysis_result_tab(handle)
            self._host.show_analysis_result_tab(query_tab, title)

        self._host.submit_analysis_task(
            tab,
            lambda _worker: self._categorical_query_builder.run(
                target,
                where_sql,
                parameters,
                title,
                subject_id_variable=(
                    context.config.subject_id_variable
                    if selected is subjects or is_denominator
                    else None
                ),
            ),
            completed,
            lambda message, details: self._host.show_analysis_error(
                "Categorical Table Drill-down Failed", message, details
            ),
        )

    def open_categorical_long_result(self) -> None:
        tab = self._host.current_dataset_tab()
        context = self._categorical_results.get(tab) if tab is not None else None
        if tab is None or tab.handle.kind != "categorical" or context is None:
            return
        context_names = tuple(
            dict.fromkeys(
                name for item in context.config.items for name in item.context_variables
            )
        )
        fields = {
            variable.name.casefold(): variable
            for variable in context.source.metadata.variables
        }
        context_variables = tuple(fields[name.casefold()] for name in context_names)
        title = self._host.unique_analysis_tab_title("Categorical Table Long Result")
        self._host.set_analysis_task_status(f"Building {title}…")

        def completed(handle: DatasetHandle) -> None:
            if not self._host.is_open_dataset_tab(tab):
                self._host.discard_analysis_result(handle)
                return
            long_tab = self._host.create_analysis_result_tab(handle)
            self._host.show_analysis_result_tab(long_tab, title)

        self._host.submit_analysis_task(
            tab,
            lambda _worker: self._categorical_long_result_builder.run(
                tab.handle, context.source, context_variables
            ),
            completed,
            lambda message, details: self._host.show_analysis_error(
                "Categorical Long Result Failed", message, details
            ),
        )
