from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QMessageBox

from ...codegen import build_proc_means_vertical_configuration
from ...codegen.sas import SasProcMeansVerticalGenerator
from ...dataset_utils import is_analysis_dataset
from ...domain import DatasetHandle
from ...filter_engine import FilterEngine
from ...proc_means.drilldown import ProcMeansQueryBuilder
from ...proc_means_vertical import ProcMeansVerticalConfig, ProcMeansVerticalEngine
from ...proc_means_vertical.drilldown import (
    build_cell_filter,
    build_cell_where_text,
    lookup_cell,
)
from ...proc_means_vertical.result_store import ProcMeansVerticalLongResultBuilder
from ...ui.dataset_tab import DatasetTab
from ...ui.proc_means_vertical_builder import ProcMeansVerticalBuilderSelection
from ...ui.sas_code_dialog import SasCodeDialog
from ._base import AnalysisModuleController


@dataclass(frozen=True, slots=True)
class ProcMeansVerticalResultContext:
    source: DatasetHandle
    population: DatasetHandle | None
    config: ProcMeansVerticalConfig


class ProcMeansVerticalController(AnalysisModuleController):
    name = "proc_means_vertical"

    def __init__(self, owner: Any, temp_manager, settings) -> None:
        super().__init__(owner)
        self._host = owner._host
        self._panel = owner._panel
        self._engine = ProcMeansVerticalEngine(temp_manager)
        self._query_builder = ProcMeansQueryBuilder(temp_manager)
        self._sas_generator = SasProcMeansVerticalGenerator()
        self._long_result_builder = ProcMeansVerticalLongResultBuilder(temp_manager)
        self._settings = settings
        self._source: DatasetTab | None = None
        self._population: DatasetTab | None = None
        self._input_tabs: set[DatasetTab] = set()
        self._results: dict[DatasetTab, ProcMeansVerticalResultContext] = {}

    @property
    def source(self) -> DatasetTab | None:
        if self._source is None or not self._host.is_open_dataset_tab(self._source):
            return None
        return self._source

    def is_input_tab(self, tab: object) -> bool:
        return tab in self._input_tabs

    def take_result_release_paths(self, tab: object) -> tuple[Path, ...] | None:
        context = self._results.pop(tab, None)
        if context is None:
            return None
        paths = [context.source.temporary_path.parent]
        if context.population is not None:
            paths.append(context.population.temporary_path.parent)
        return tuple(dict.fromkeys(paths))

    def refresh_sources(self, datasets=None) -> None:
        available = (
            list(datasets)
            if datasets is not None
            else self._host.available_analysis_dataset_tabs()
        )
        selected = self.source
        if self._source is not None and selected is None:
            self._source = None
            self._set_source_dataset(None)
        self._panel.proc_means_vertical_builder.set_source_datasets(available, selected)
        self._panel.proc_means_vertical_builder.set_population_datasets(
            available,
            self._population
            if self._population is not None
            and self._host.is_open_dataset_tab(self._population)
            else None,
        )

    def select_source(self, tab) -> None:
        if not isinstance(tab, DatasetTab) or not is_analysis_dataset(tab.handle) or not tab.cache_complete:
            return
        self._source = tab
        self._set_source_dataset(tab)

    def select_population(self, tab) -> None:
        if tab is None:
            self._population = None
            self._set_population_dataset(None)
            return
        if not isinstance(tab, DatasetTab) or not is_analysis_dataset(tab.handle) or not tab.cache_complete:
            return
        self._population = tab
        self._set_population_dataset(tab)

    def show_builder(self) -> None:
        self.refresh_sources()
        self._panel.show_proc_means_vertical_tab()

    def clear_source(self) -> None:
        self._source = None
        self._population = None
        self._set_source_dataset(None)
        self._set_population_dataset(None)
        self.refresh_sources()

    def open_long_result(self) -> None:
        tab = self._host.current_dataset_tab()
        context = self._results.get(tab) if tab is not None else None
        if tab is None or tab.handle.kind != "proc_means_vertical" or context is None:
            return
        title = self._host.unique_analysis_tab_title("PROC MEANS Vertical Long Result")

        def completed(handle: DatasetHandle) -> None:
            long_tab = self._host.create_analysis_result_tab(handle)
            self._host.show_analysis_result_tab(long_tab, title)

        self._host.submit_analysis_task(
            tab,
            lambda _worker: self._long_result_builder.run(tab.handle, context.source),
            completed,
            lambda message, details: self._host.show_analysis_error(
                "PROC MEANS Vertical Long Result Failed", message, details
            ),
        )

    def drilldown(
        self, tab: DatasetTab, view_row: int, column_name: str, display: str
    ) -> None:
        context = self._results.get(tab)
        source_row = tab.model.source_row_id(view_row)
        if context is None or source_row is None:
            QMessageBox.information(
                self._parent_widget(),
                "PROC MEANS Vertical Drill-down",
                "Drill-down is unavailable for this result tab.\n"
                "Please re-run PROC MEANS Table Builder (Vertical) and try again.",
            )
            return
        if not any(part.strip() not in {"", "—"} for part in display.split(",")):
            QMessageBox.information(
                self._parent_widget(),
                "PROC MEANS Vertical Drill-down",
                "This statistic has no calculated value to drill down from.",
            )
            return
        cell = lookup_cell(tab.handle, source_row, column_name)
        if cell is None:
            QMessageBox.information(
                self._parent_widget(),
                "PROC MEANS Vertical Drill-down",
                "The selected result cell does not have source-row mapping metadata.",
            )
            return
        try:
            compiled = build_cell_filter(context.source.metadata, context.config, cell)
            where_text = build_cell_where_text(
                context.source.metadata, context.config, cell
            )
        except (KeyError, TypeError, ValueError) as error:
            QMessageBox.warning(
                self._parent_widget(), "PROC MEANS Vertical Drill-down", str(error)
            )
            return

        base_title = f"Query: {cell.statistic_label}: {display}"
        self._host.set_analysis_task_status(f"Building {base_title}…")
        generation = tab.generation

        def build(worker):
            handle = self._query_builder.run(
                context.source, compiled, base_title, worker.report
            )
            return handle, where_text, cell.analysis_variable

        def completed(result: tuple[DatasetHandle, str, str]) -> None:
            handle, query_where, analysis_variable = result
            if not self._host.is_open_dataset_tab(tab) or generation != tab.generation:
                self._host.discard_analysis_result(handle)
                return
            title = self._host.unique_analysis_tab_title(base_title)
            self._host.show_proc_means_query_result(
                handle, title, query_where, analysis_variable
            )

        self._host.submit_analysis_task(
            tab,
            build,
            completed,
            lambda message, details: self._host.show_analysis_error(
                "PROC MEANS Vertical Drill-down Failed", message, details
            ),
        )

    def _set_source_dataset(self, tab: DatasetTab | None) -> None:
        builder = self._panel.proc_means_vertical_builder
        builder.set_dataset(
            tab.handle.metadata if tab else None,
            str(tab.handle.source_path) if tab else "",
            tab.current_where_text() if tab else "",
            tab.handle.kind if tab else "sas",
        )

    def _set_population_dataset(self, tab: DatasetTab | None) -> None:
        builder = self._panel.proc_means_vertical_builder
        builder.set_population_dataset(
            tab.handle.metadata if tab else None,
            str(tab.handle.source_path) if tab else "",
            tab.current_where_text() if tab else "",
        )

    @staticmethod
    def _item_labels(text: str) -> tuple[tuple[str, str], ...]:
        labels: list[tuple[str, str]] = []
        seen: set[str] = set()
        for line in text.splitlines():
            if not line.strip():
                continue
            if "=" not in line:
                raise ValueError("Item labels must use one variable=label pair per line.")
            value, label = (part.strip() for part in line.split("=", 1))
            if not value or not label or value.casefold() in seen:
                raise ValueError("Item labels must contain unique variable=label pairs.")
            seen.add(value.casefold())
            labels.append((value, label))
        return tuple(labels)

    def _context(self, selection: ProcMeansVerticalBuilderSelection):
        source = self.source
        if source is None or not source.cache_complete:
            QMessageBox.warning(
                self._parent_widget(),
                "PROC MEANS Table Builder (Vertical)",
                "Open a fully loaded source dataset before running this Builder.",
            )
            return None
        population = self._population if self._population and self._host.is_open_dataset_tab(self._population) else None
        if population is None:
            answer = QMessageBox.question(
                self._parent_widget(),
                "Population Dataset Not Selected",
                "No Population Dataset is selected. Use the current Analysis Dataset to calculate header N?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return None
        try:
            source_filter = FilterEngine(source.handle.metadata.variables).compile(selection.filter_text)
            population_filter = FilterEngine(
                (population or source).handle.metadata.variables
            ).compile(selection.population_filter_text)
            config = ProcMeansVerticalConfig(
                selection.analysis_variables,
                selection.group_variables,
                selection.treatment_variable,
                selection.population_treatment_variable,
                selection.statistics,
                source_filter,
                selection.filter_text,
                population_filter,
                selection.population_filter_text,
                self._item_labels(selection.item_labels_text),
                selection.include_total,
                tuple(self._settings.proc_means_decimal_offsets.items()),
                self._settings.proc_means_confidence,
                selection.base_decimals,
                selection.output_layout,
                selection.row_group_variables,
            )
            config.validate(source.handle.metadata, population.handle.metadata if population else None)
        except ValueError as error:
            QMessageBox.warning(self._parent_widget(), "PROC MEANS Table Builder (Vertical)", str(error))
            return None
        return source, population, config

    def run(self, selection: ProcMeansVerticalBuilderSelection) -> None:
        context = self._context(selection)
        if context is None:
            return
        source_tab, population_tab, config = context
        builder = self._panel.proc_means_vertical_builder
        self._input_tabs = {source_tab} | ({population_tab} if population_tab else set())
        builder.set_busy(True, "Calculating vertical PROC MEANS table in the background…")

        def completed(handle: DatasetHandle) -> None:
            self._input_tabs.clear()
            builder.set_busy(False, f"Created {handle.metadata.row_count:,} result rows.")
            result_tab = self._host.create_analysis_result_tab(handle)
            self._host.retain_analysis_directory(source_tab.handle.temporary_path.parent)
            if population_tab:
                self._host.retain_analysis_directory(population_tab.handle.temporary_path.parent)
            self._results[result_tab] = ProcMeansVerticalResultContext(
                source_tab.handle, population_tab.handle if population_tab else None, config
            )
            self._host.show_analysis_result_tab(result_tab, "PROC MEANS Vertical Result")

        def failed(message: str, details: str) -> None:
            self._input_tabs.clear()
            builder.set_busy(False, "Vertical PROC MEANS failed.")
            self._host.show_analysis_error("PROC MEANS Table Builder (Vertical) Failed", message, details)

        self._host.submit_analysis_task(
            builder,
            lambda worker: self._engine.run(
                source_tab.handle,
                config,
                population_tab.handle if population_tab else None,
                worker.report,
            ),
            completed,
            failed,
        )

    def generate_sas_code(self, selection: ProcMeansVerticalBuilderSelection) -> None:
        context = self._context(selection)
        if context is None:
            return
        source_tab, population_tab, config = context
        if source_tab.handle.kind == "merge":
            return
        try:
            configuration = build_proc_means_vertical_configuration(
                source_tab.handle,
                config,
                population_tab.handle if population_tab else None,
            )
            code = self._sas_generator.generate(configuration)
        except (KeyError, TypeError, ValueError) as error:
            QMessageBox.critical(
                self._parent_widget(), "SAS Code Generator Failed", str(error)
            )
            return
        SasCodeDialog(
            code,
            str(source_tab.handle.source_path),
            f"{self._safe_source_name(source_tab.handle)}_proc_means_vertical.sas",
            self._parent_widget(),
        ).exec()


__all__ = ["ProcMeansVerticalController", "ProcMeansVerticalResultContext"]
