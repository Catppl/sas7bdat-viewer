from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QMessageBox

from ...ae_table import (
    AeTableConfig,
    AeTableDenominator,
    AeTableEngine,
    AeTableLongResultBuilder,
    build_ae_table_configuration,
)
from ...ae_table.drilldown import build_cell_filter as build_ae_cell_filter
from ...ae_table.drilldown import lookup_cell as lookup_ae_cell
from ...categorical.drilldown import CategoricalQueryBuilder
from ...codegen.sas import SasAeTableGenerator
from ...dataset_utils import is_analysis_dataset
from ...domain import DatasetHandle
from ...filter_engine import FilterEngine
from ...ui.ae_table_builder import AeTableBuilderSelection
from ...ui.dataset_tab import DatasetTab
from ...ui.sas_code_dialog import SasCodeDialog
from ...workers import Worker
from ._base import AnalysisModuleController


@dataclass(frozen=True, slots=True)
class AeTableResultContext:
    source: DatasetHandle
    population: DatasetHandle | None
    config: AeTableConfig


class AeTableController(AnalysisModuleController):
    name = "ae_table"

    def __init__(self, owner: Any, temp_manager, settings) -> None:
        super().__init__(owner)
        self._host = owner._host
        self._panel = owner._panel
        self._settings = settings
        self._ae_table_engine = AeTableEngine(temp_manager)
        self._ae_table_long_result_builder = AeTableLongResultBuilder(temp_manager)
        self._ae_table_query_builder = CategoricalQueryBuilder(temp_manager)
        self._sas_ae_table_generator = SasAeTableGenerator()
        self._ae_table_source: DatasetTab | None = None
        self._ae_table_input_tabs: set[DatasetTab] = set()
        self._ae_table_results: dict[DatasetTab, AeTableResultContext] = {}

    def is_input_tab(self, tab: object) -> bool:
        """Return whether a dataset is retained by an active AE Table task."""
        return tab in self._ae_table_input_tabs

    def take_result_release_paths(self, tab: object) -> tuple[Path, ...] | None:
        """Detach an AE Table result and return retained source directories."""
        context = self._ae_table_results.pop(tab, None)
        if context is None:
            return None
        paths = {context.source.temporary_path.parent}
        if context.population is not None:
            paths.add(context.population.temporary_path.parent)
        return tuple(paths)

    @property
    def ae_table_source(self) -> DatasetTab | None:
        """Return AE Table Builder's fixed source while its tab remains open."""
        if self._ae_table_source is None:
            return None
        if not self._host.is_open_dataset_tab(self._ae_table_source):
            return None
        return self._ae_table_source

    def refresh_ae_table_adsl_sources(self, datasets=None) -> None:
        self._panel.ae_table_builder.set_adsl_sources(
            list(datasets) if datasets is not None else self._host.available_sas_dataset_tabs()
        )

    def select_ae_table_adsl(self, tab: DatasetTab) -> None:
        self._panel.ae_table_builder.select_adsl(tab)

    def show_ae_table_builder(self) -> None:
        """Open AE Table Builder with its first eligible source fixed until Clear."""
        if self.ae_table_source is None:
            active_tab = self._host.current_dataset_tab()
            if (
                active_tab is not None
                and is_analysis_dataset(active_tab.handle)
                and active_tab.cache_complete
            ):
                self._ae_table_source = active_tab
                self._set_ae_table_dataset(active_tab)
        self.refresh_ae_table_adsl_sources()
        self._panel.show_ae_table_tab()

    def clear_ae_table_source(self) -> None:
        """Release AE Table Builder's fixed source only after Clear."""
        self._ae_table_source = None
        self._set_ae_table_dataset(None)

    def _set_ae_table_dataset(self, tab: DatasetTab | None) -> None:
        builder = self._panel.ae_table_builder
        if tab is None:
            builder.set_dataset(None, "", "", "sas")
            return
        builder.set_dataset(
            tab.handle.metadata,
            str(tab.handle.source_path),
            tab.current_where_text(),
            tab.handle.kind,
        )

    def _ae_table_context(
        self, selection: AeTableBuilderSelection
    ) -> tuple[DatasetTab, DatasetTab | None, AeTableConfig] | None:
        tab = self.ae_table_source
        if tab is None or not is_analysis_dataset(tab.handle) or not tab.cache_complete:
            QMessageBox.warning(
                self._parent_widget(),
                "AE Table",
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
                "AE Table",
                "Open or browse a fully loaded ADSL dataset for Population N.",
            )
            return None
        try:
            dataset_filter = FilterEngine(tab.handle.metadata.variables).compile(
                selection.dataset_filter_text
            )
            population_filter = (
                FilterEngine(population_tab.handle.metadata.variables).compile(
                    selection.population_filter_text
                )
                if isinstance(population_tab, DatasetTab)
                else FilterEngine(tab.handle.metadata.variables).compile("")
            )
            config = AeTableConfig(
                selection.soc_variable,
                selection.pt_variable,
                selection.treatment_variable,
                "USUBJID",
                dataset_filter,
                selection.dataset_filter_text,
                AeTableDenominator(
                    selection.denominator_type,
                    population_filter,
                    selection.population_filter_text,
                    selection.population_treatment_variable,
                ),
                selection.include_any_ae,
                selection.any_ae_label,
                selection.include_total,
                selection.percent_digits,
                selection.hierarchy_missing_policy,
            )
            config.validate(
                tab.handle.metadata,
                population_tab.handle.metadata
                if isinstance(population_tab, DatasetTab)
                else None,
            )
        except ValueError as error:
            QMessageBox.warning(self._parent_widget(), "AE Table", str(error))
            return None
        return (
            tab,
            population_tab if isinstance(population_tab, DatasetTab) else None,
            config,
        )

    def run_ae_table(self, selection: AeTableBuilderSelection) -> None:
        context = self._ae_table_context(selection)
        if context is None:
            return
        source_tab, population_tab, config = context
        source_handle = source_tab.handle
        population_handle = population_tab.handle if population_tab else None
        builder = self._panel.ae_table_builder
        self._ae_table_input_tabs = {source_tab}
        if population_tab:
            self._ae_table_input_tabs.add(population_tab)
        builder.set_busy(True, "Calculating AE Table in the background…")

        def completed(handle: DatasetHandle) -> None:
            self._ae_table_input_tabs.clear()
            builder.set_busy(
                False, f"Created {handle.metadata.row_count:,} result rows."
            )
            result_tab = self._host.create_analysis_result_tab(handle)
            for directory in {
                source_handle.temporary_path.parent,
                *([population_handle.temporary_path.parent] if population_handle else []),
            }:
                self._host.retain_analysis_directory(directory)
            self._ae_table_results[result_tab] = AeTableResultContext(
                source_handle, population_handle, config
            )
            self._host.show_analysis_result_tab(result_tab, "AE Table Result")

        def failed(message: str, details: str) -> None:
            self._ae_table_input_tabs.clear()
            builder.set_busy(False, "AE Table failed.")
            self._host.show_analysis_error("AE Table Failed", message, details)

        self._host.submit_analysis_task(
            builder,
            lambda worker: self._ae_table_engine.run(
                source_handle, config, population_handle, worker.report
            ),
            completed,
            failed,
        )

    def generate_ae_table_sas_code(self, selection: AeTableBuilderSelection) -> None:
        """Generate AE SAS from the current Builder snapshot without running a table."""
        context = self._ae_table_context(selection)
        if context is None:
            return
        source_tab, population_tab, config = context
        if source_tab.handle.kind != "sas":
            return
        source_handle = source_tab.handle
        population_handle = population_tab.handle if population_tab else None
        builder = self._panel.ae_table_builder
        self._ae_table_input_tabs = {source_tab}
        if population_tab is not None:
            self._ae_table_input_tabs.add(population_tab)
        builder.set_busy(True, "Generating AE SAS code…")

        def completed(code: str) -> None:
            self._ae_table_input_tabs.clear()
            builder.set_busy(False, "SAS code generated.")
            safe_name = self._safe_source_name(source_handle)
            SasCodeDialog(
                code,
                str(source_handle.source_path),
                f"{safe_name}_ae_soc_pt.sas",
                self._parent_widget(),
            ).exec()

        def failed(message: str, details: str) -> None:
            self._ae_table_input_tabs.clear()
            builder.set_busy(False, "SAS Code Generator failed.")
            self._host.show_analysis_error(
                "AE SAS Code Generator Failed", message, details
            )

        def generate(_worker: Worker) -> str:
            configuration = build_ae_table_configuration(
                source_handle, config, population_handle, ()
            )
            return self._sas_ae_table_generator.generate(configuration)

        self._host.submit_analysis_task(builder, generate, completed, failed)

    def drilldown_ae_table(
        self, tab: DatasetTab, view_row: int, column_name: str, display: str
    ) -> None:
        """Open source records or subjects represented by an AE Table cell."""
        context = self._ae_table_results.get(tab)
        source_row = tab.model.source_row_id(view_row)
        if context is None or source_row is None or not display:
            return
        cell = lookup_ae_cell(tab.handle, source_row, column_name)
        if cell is None:
            return
        dialog, records, subjects, denominator = self._drilldown_dialog()
        dialog.setWindowTitle("AE Table Drill-down")
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
            return
        try:
            where_sql, parameters = build_ae_cell_filter(
                target.metadata, context.config, cell, denominator=is_denominator
            )
        except (KeyError, ValueError, StopIteration) as error:
            QMessageBox.warning(self._parent_widget(), "AE Table Drill-down", str(error))
            return
        mode = (
            "Denominator Subjects"
            if is_denominator
            else "Numerator Subjects"
            if selected is subjects
            else "Numerator Records"
        )
        title = self._host.unique_analysis_tab_title(f"Query: {mode}")

        self._host.submit_analysis_task(
            tab,
            lambda _worker: self._ae_table_query_builder.run(
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
            lambda handle: self._host.show_analysis_result_tab(
                self._host.create_analysis_result_tab(handle), title
            ),
            lambda message, details: self._host.show_analysis_error(
                "AE Table Drill-down Failed", message, details
            ),
        )

    def open_ae_table_long_result(self) -> None:
        tab = self._host.current_dataset_tab()
        context = self._ae_table_results.get(tab) if tab is not None else None
        if tab is None or tab.handle.kind != "ae_table" or context is None:
            return
        title = self._host.unique_analysis_tab_title("AE Table Long Result")

        def completed(handle: DatasetHandle) -> None:
            if not self._host.is_open_dataset_tab(tab):
                self._host.discard_analysis_result(handle)
                return
            long_tab = self._host.create_analysis_result_tab(handle)
            self._host.show_analysis_result_tab(long_tab, title)

        self._host.submit_analysis_task(
            tab,
            lambda _worker: self._ae_table_long_result_builder.run(
                tab.handle, context.source
            ),
            completed,
            lambda message, details: self._host.show_analysis_error(
                "AE Table Long Result Failed", message, details
            ),
        )
