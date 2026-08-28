from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QMessageBox

from ...categorical.drilldown import CategoricalQueryBuilder
from ...codegen.sas import SasRuleBasedGenerator
from ...dataset_utils import is_analysis_dataset
from ...domain import DatasetHandle
from ...filter_engine import FilterEngine
from ...rule_based import (
    RuleBasedConfig,
    RuleBasedDenominator,
    RuleBasedEngine,
    RuleBasedLongResultBuilder,
    RuleBasedRow,
    build_rule_based_configuration,
)
from ...rule_based.drilldown import build_cell_filter as build_rule_cell_filter
from ...rule_based.drilldown import build_population_cell_filter
from ...rule_based.drilldown import lookup_cell as lookup_rule_cell
from ...ui.dataset_tab import DatasetTab
from ...ui.rule_based_builder import RuleBasedBuilderSelection
from ...ui.sas_code_dialog import SasCodeDialog
from ...workers import Worker
from ._base import AnalysisModuleController


@dataclass(frozen=True, slots=True)
class RuleBasedResultContext:
    source: DatasetHandle
    population: DatasetHandle | None
    config: RuleBasedConfig


class RuleBasedController(AnalysisModuleController):
    name = "rule_based"

    def __init__(self, owner: Any, temp_manager, settings) -> None:
        super().__init__(owner)
        self._host = owner._host
        self._panel = owner._panel
        self._settings = settings
        self._rule_based_engine = RuleBasedEngine(temp_manager)
        self._rule_based_long_result_builder = RuleBasedLongResultBuilder(temp_manager)
        self._rule_based_query_builder = CategoricalQueryBuilder(temp_manager)
        self._sas_rule_based_generator = SasRuleBasedGenerator()
        self._rule_based_source: DatasetTab | None = None
        self._rule_based_input_tabs: set[DatasetTab] = set()
        self._rule_based_results: dict[DatasetTab, RuleBasedResultContext] = {}

    def is_input_tab(self, tab: object) -> bool:
        """Return whether a dataset is retained by an active Rule-based task."""
        return tab in self._rule_based_input_tabs

    def take_result_release_paths(self, tab: object) -> tuple[Path, ...] | None:
        """Detach a Rule-based result and return retained source directories."""
        context = self._rule_based_results.pop(tab, None)
        if context is None:
            return None
        paths = {context.source.temporary_path.parent}
        if context.population is not None:
            paths.add(context.population.temporary_path.parent)
        return tuple(paths)

    @property
    def rule_based_source(self) -> DatasetTab | None:
        """Return Rule-based Builder's fixed source while its tab remains open."""
        if self._rule_based_source is None:
            return None
        if not self._host.is_open_dataset_tab(self._rule_based_source):
            return None
        return self._rule_based_source

    def refresh_rule_based_adsl_sources(
        self, datasets: Iterable[tuple[DatasetTab, str]] | None = None
    ) -> None:
        self._panel.rule_based_builder.set_adsl_sources(
            list(datasets) if datasets is not None else self._host.available_sas_dataset_tabs()
        )

    def select_rule_based_adsl(self, tab: DatasetTab) -> None:
        self._panel.rule_based_builder.select_adsl(tab)

    def show_rule_based_builder(self) -> None:
        """Open Rule-based Builder with its first eligible source fixed until Clear."""
        if self.rule_based_source is None:
            active_tab = self._host.current_dataset_tab()
            if (
                active_tab is not None
                and is_analysis_dataset(active_tab.handle)
                and active_tab.cache_complete
            ):
                self._rule_based_source = active_tab
                self._set_rule_based_dataset(active_tab)
        self.refresh_rule_based_adsl_sources()
        self._panel.show_rule_based_tab()

    def clear_rule_based_source(self) -> None:
        """Release Rule-based Builder's fixed source only after Clear."""
        self._rule_based_source = None
        self._set_rule_based_dataset(None)

    def _set_rule_based_dataset(self, tab: DatasetTab | None) -> None:
        builder = self._panel.rule_based_builder
        if tab is None:
            builder.set_dataset(None, "", "", "sas")
            return
        builder.set_dataset(
            tab.handle.metadata,
            str(tab.handle.source_path),
            tab.current_where_text(),
            tab.handle.kind,
        )

    def _rule_based_context(
        self, selection: RuleBasedBuilderSelection
    ) -> tuple[DatasetTab, DatasetTab | None, RuleBasedConfig] | None:
        tab = self.rule_based_source
        if tab is None or not is_analysis_dataset(tab.handle) or not tab.cache_complete:
            QMessageBox.warning(
                self._parent_widget(),
                "Rule-based Table",
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
                "Rule-based Table",
                "Open or browse a fully loaded ADSL dataset for Population N.",
            )
            return None
        try:
            dataset_filter = FilterEngine(tab.handle.metadata.variables).compile(
                selection.dataset_filter_text
            )
            rows = tuple(
                RuleBasedRow(
                    draft.row_id,
                    draft.item,
                    FilterEngine(tab.handle.metadata.variables).compile(
                        draft.filter_text
                    ),
                    draft.filter_text,
                    draft.indent,
                )
                for draft in selection.rows
            )
            population_filter = (
                FilterEngine(population_tab.handle.metadata.variables).compile(
                    selection.population_filter_text
                )
                if isinstance(population_tab, DatasetTab)
                else FilterEngine(tab.handle.metadata.variables).compile("")
            )
            config = RuleBasedConfig(
                rows,
                selection.treatment_variable,
                "USUBJID",
                dataset_filter,
                selection.dataset_filter_text,
                RuleBasedDenominator(
                    type=selection.denominator_type,
                    population_filter=population_filter,
                    population_filter_text=selection.population_filter_text,
                    population_treatment_variable=selection.population_treatment_variable,
                    analysis_value_variable=selection.nonmissing_value_variable,
                ),
                selection.include_total,
                1,
            )
            config.validate(
                tab.handle.metadata,
                population_tab.handle.metadata
                if isinstance(population_tab, DatasetTab)
                else None,
            )
        except ValueError as error:
            QMessageBox.warning(self._parent_widget(), "Rule-based Table", str(error))
            return None
        return (
            tab,
            population_tab if isinstance(population_tab, DatasetTab) else None,
            config,
        )

    def run_rule_based(self, selection: RuleBasedBuilderSelection) -> None:
        context = self._rule_based_context(selection)
        if context is None:
            return
        source_tab, population_tab, config = context
        source_handle = source_tab.handle
        population_handle = population_tab.handle if population_tab else None
        builder = self._panel.rule_based_builder
        self._rule_based_input_tabs = {source_tab}
        if population_tab:
            self._rule_based_input_tabs.add(population_tab)
        builder.set_busy(True, "Calculating Rule-based Table in the background…")

        def completed(handle: DatasetHandle) -> None:
            self._rule_based_input_tabs.clear()
            builder.set_busy(
                False, f"Created {handle.metadata.row_count:,} result rows."
            )
            result_tab = self._host.create_analysis_result_tab(handle)
            for directory in {
                source_handle.temporary_path.parent,
                *([population_handle.temporary_path.parent] if population_handle else []),
            }:
                self._host.retain_analysis_directory(directory)
            self._rule_based_results[result_tab] = RuleBasedResultContext(
                source_handle, population_handle, config
            )
            self._host.show_analysis_result_tab(result_tab, "Rule-based Table Result")

        def failed(message: str, details: str) -> None:
            self._rule_based_input_tabs.clear()
            builder.set_busy(False, "Rule-based Table failed.")
            if message.startswith("Missing treatment values"):
                QMessageBox.warning(self._parent_widget(), "Rule-based Table", message)
            else:
                self._host.show_analysis_error("Rule-based Table Failed", message, details)

        self._host.submit_analysis_task(
            builder,
            lambda worker: self._rule_based_engine.run(
                source_handle, config, population_handle, worker.report
            ),
            completed,
            failed,
        )

    def generate_rule_based_sas_code(
        self, selection: RuleBasedBuilderSelection
    ) -> None:
        """Generate Rule-based SAS from the current Builder snapshot."""
        context = self._rule_based_context(selection)
        if context is None:
            return
        source_tab, population_tab, config = context
        if source_tab.handle.kind != "sas":
            return
        source_handle = source_tab.handle
        population_handle = population_tab.handle if population_tab else None
        builder = self._panel.rule_based_builder
        self._rule_based_input_tabs = {source_tab}
        if population_tab is not None:
            self._rule_based_input_tabs.add(population_tab)
        builder.set_busy(True, "Resolving Rule-based treatment levels…")

        def completed(code: str) -> None:
            self._rule_based_input_tabs.clear()
            builder.set_busy(False, "SAS code generated.")
            safe_name = self._safe_source_name(source_handle)
            SasCodeDialog(
                code,
                str(source_handle.source_path),
                f"{safe_name}_rule_based.sas",
                self._parent_widget(),
            ).exec()

        def failed(message: str, details: str) -> None:
            self._rule_based_input_tabs.clear()
            builder.set_busy(False, "SAS Code Generator failed.")
            self._host.show_analysis_error(
                "Rule-based SAS Code Generator Failed", message, details
            )

        def generate(_worker: Worker) -> str:
            levels = self._rule_based_engine.resolve_treatment_levels(
                source_handle, config, population_handle
            )
            configuration = build_rule_based_configuration(
                source_handle, config, population_handle, levels
            )
            return self._sas_rule_based_generator.generate(configuration)

        self._host.submit_analysis_task(builder, generate, completed, failed)

    def drilldown_rule_based(
        self, tab: DatasetTab, view_row: int, column_name: str, display: str
    ) -> None:
        """Open source records or subjects represented by a Rule-based cell."""
        context = self._rule_based_results.get(tab)
        source_row = tab.model.source_row_id(view_row)
        if context is None or source_row is None or not display:
            return
        cell = lookup_rule_cell(tab.handle, source_row, column_name)
        if cell is None:
            return
        row = next(
            (candidate for candidate in context.config.rows if candidate.row_id == cell.row_id),
            None,
        )
        if row is None:
            QMessageBox.warning(
                self._parent_widget(),
                "Rule-based Table Drill-down",
                "The selected rule row is no longer available.",
            )
            return
        dialog, records, subjects, denominator = self._drilldown_dialog()
        dialog.setWindowTitle("Rule-based Table Drill-down")
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
                "Rule-based Table Drill-down",
                "The required denominator dataset is no longer available.",
            )
            return
        try:
            if is_denominator and context.config.denominator.type == "population":
                where_sql, parameters = build_population_cell_filter(
                    target.metadata, context.config, row, cell.treatment
                )
            else:
                where_sql, parameters = build_rule_cell_filter(
                    target.metadata,
                    context.config,
                    row,
                    cell.treatment,
                    denominator=is_denominator,
                )
        except (KeyError, ValueError) as error:
            QMessageBox.warning(
                self._parent_widget(), "Rule-based Table Drill-down", str(error)
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
            lambda _worker: self._rule_based_query_builder.run(
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
                "Rule-based Table Drill-down Failed", message, details
            ),
        )

    def open_rule_based_long_result(self) -> None:
        tab = self._host.current_dataset_tab()
        if tab is None or tab.handle.kind != "rule_based":
            return
        title = self._host.unique_analysis_tab_title("Rule-based Long Result")
        self._host.set_analysis_task_status(f"Building {title}…")

        def completed(handle: DatasetHandle) -> None:
            if not self._host.is_open_dataset_tab(tab):
                self._host.discard_analysis_result(handle)
                return
            long_tab = self._host.create_analysis_result_tab(handle)
            self._host.show_analysis_result_tab(long_tab, title)

        self._host.submit_analysis_task(
            tab,
            lambda _worker: self._rule_based_long_result_builder.run(tab.handle),
            completed,
            lambda message, details: self._host.show_analysis_error(
                "Rule-based Long Result Failed", message, details
            ),
        )
