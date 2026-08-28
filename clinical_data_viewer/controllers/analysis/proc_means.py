from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QMessageBox

from ...codegen import build_proc_means_configuration
from ...codegen.r import RProcMeansGenerator
from ...codegen.sas import SasProcMeansGenerator
from ...data_store import DataStore
from ...dataset_utils import is_analysis_dataset
from ...domain import DatasetHandle
from ...filter_engine import FilterEngine
from ...proc_means import (
    ProcMeansConfig,
    ProcMeansEngine,
    ProcMeansQueryBuilder,
    build_drilldown_filter,
    build_drilldown_where_text,
)
from ...settings import PROC_MEANS_STATISTICS
from ...ui.dataset_tab import DatasetTab
from ...ui.proc_means_builder import ProcMeansBuilderSelection
from ...ui.sas_code_dialog import RCodeDialog, SasCodeDialog
from ...workers import Worker
from ._base import AnalysisModuleController


@dataclass(frozen=True, slots=True)
class ProcMeansResultContext:
    source: DatasetHandle
    config: ProcMeansConfig


class ProcMeansController(AnalysisModuleController):
    name = "proc_means"

    def __init__(self, owner: Any, temp_manager, settings) -> None:
        super().__init__(owner)
        self._host = owner._host
        self._panel = owner._panel
        self._settings = settings
        self._proc_means_engine = ProcMeansEngine(temp_manager)
        self._proc_means_query_builder = ProcMeansQueryBuilder(temp_manager)
        self._sas_proc_means_generator = SasProcMeansGenerator()
        self._r_proc_means_generator = RProcMeansGenerator()
        self._store = DataStore()
        self._proc_means_source: DatasetTab | None = None
        self._proc_means_input_tabs: set[DatasetTab] = set()
        self._proc_means_results: dict[DatasetTab, ProcMeansResultContext] = {}

    def is_input_tab(self, tab: object) -> bool:
        """Return whether a dataset is retained by an active PROC MEANS task."""
        return tab in self._proc_means_input_tabs

    def take_result_release_paths(self, tab: object) -> tuple[Path, ...] | None:
        """Detach a PROC MEANS result and return its retained source directory."""
        context = self._proc_means_results.pop(tab, None)
        if context is None:
            return None
        return (context.source.temporary_path.parent,)

    @property
    def proc_means_source(self) -> DatasetTab | None:
        """Return PROC MEANS Builder's fixed source while its tab remains open."""
        if self._proc_means_source is None:
            return None
        if not self._host.is_open_dataset_tab(self._proc_means_source):
            return None
        return self._proc_means_source

    def show_proc_means_builder(self) -> None:
        """Open PROC MEANS Builder with its first eligible source fixed until Clear."""
        if self.proc_means_source is None:
            active_tab = self._host.current_dataset_tab()
            if (
                active_tab is not None
                and is_analysis_dataset(active_tab.handle)
                and active_tab.cache_complete
            ):
                self._proc_means_source = active_tab
                self._set_proc_means_dataset(active_tab)
        self._panel.show_builder_tab()

    def clear_proc_means_source(self) -> None:
        """Release PROC MEANS Builder's fixed source only after Clear."""
        self._proc_means_source = None
        self._set_proc_means_dataset(None)

    def _set_proc_means_dataset(self, tab: DatasetTab | None) -> None:
        builder = self._panel.builder
        if tab is None:
            builder.set_dataset(None, "", "", "sas")
            return
        builder.set_dataset(
            tab.handle.metadata,
            str(tab.handle.source_path),
            tab.current_where_text(),
            tab.handle.kind,
        )

    def _proc_means_context(
        self, selection: ProcMeansBuilderSelection, action_title: str
    ) -> tuple[DatasetTab, ProcMeansConfig] | None:
        tab = self.proc_means_source
        if tab is None or not is_analysis_dataset(tab.handle) or not tab.cache_complete:
            QMessageBox.warning(
                self._parent_widget(),
                action_title,
                "The Builder source is unavailable. Open a fully loaded source dataset, then open the Builder again.",
            )
            return None
        filter_text = self._panel.builder.current_filter_text()
        try:
            compiled_filter = FilterEngine(tab.handle.metadata.variables).compile(
                filter_text
            )
            config = ProcMeansConfig(
                selection.analysis_variables,
                selection.by_variables,
                selection.class_variables,
                selection.statistics,
                compiled_filter,
                filter_text,
                selection.decimal_group_variables,
                tuple(self._settings.proc_means_decimal_offsets.items()),
                self._settings.proc_means_confidence,
            )
            config.validate(tab.handle.metadata)
        except ValueError as error:
            QMessageBox.warning(self._parent_widget(), action_title, str(error))
            return None
        return tab, config

    def generate_proc_means_sas_code(
        self, selection: ProcMeansBuilderSelection
    ) -> None:
        context = self._proc_means_context(selection, "SAS Code Generator")
        if context is None:
            return
        tab, config = context
        if tab.handle.kind == "merge":
            return
        try:
            configuration = build_proc_means_configuration(tab.handle, config)
            code = self._sas_proc_means_generator.generate(configuration)
        except (KeyError, TypeError, ValueError) as error:
            QMessageBox.critical(
                self._parent_widget(), "SAS Code Generator Failed", str(error)
            )
            return
        SasCodeDialog(
            code,
            str(tab.handle.source_path),
            f"{self._safe_source_name(tab.handle)}_proc_means.sas",
            self._parent_widget(),
        ).exec()

    def generate_proc_means_r_code(
        self, selection: ProcMeansBuilderSelection
    ) -> None:
        context = self._proc_means_context(selection, "R Code Generator")
        if context is None:
            return
        tab, config = context
        if tab.handle.kind == "merge":
            return
        try:
            configuration = build_proc_means_configuration(tab.handle, config)
            code = self._r_proc_means_generator.generate(configuration)
        except (KeyError, TypeError, ValueError) as error:
            QMessageBox.critical(
                self._parent_widget(), "R Code Generator Failed", str(error)
            )
            return
        RCodeDialog(
            code,
            str(tab.handle.source_path),
            f"{self._safe_source_name(tab.handle)}_proc_means.R",
            self._parent_widget(),
        ).exec()

    def run_proc_means_builder(self, selection: ProcMeansBuilderSelection) -> None:
        source_tab = self.proc_means_source
        if source_tab is None or not is_analysis_dataset(source_tab.handle):
            return
        builder = self._panel.builder
        context = self._proc_means_context(selection, "PROC MEANS Builder")
        if context is None:
            return
        tab, config = context
        source_handle = tab.handle
        self._proc_means_input_tabs = {tab}
        builder.set_busy(True, "Calculating PROC MEANS in the background…")

        def completed(handle: DatasetHandle) -> None:
            self._proc_means_input_tabs.clear()
            builder.set_busy(
                False, f"Created {handle.metadata.row_count:,} result rows."
            )
            result_tab = self._host.create_analysis_result_tab(handle)
            self._host.retain_analysis_directory(source_handle.temporary_path.parent)
            self._proc_means_results[result_tab] = ProcMeansResultContext(
                source_handle, config
            )
            self._host.show_analysis_result_tab(result_tab, "PROC MEANS Result")

        def failed(message: str, details: str) -> None:
            self._proc_means_input_tabs.clear()
            builder.set_busy(False, "PROC MEANS failed.")
            self._host.show_analysis_error("PROC MEANS Builder Failed", message, details)

        self._host.submit_analysis_task(
            builder,
            lambda worker: self._proc_means_engine.run(
                source_handle, config, worker.report
            ),
            completed,
            failed,
        )

    def drilldown_proc_means(
        self, tab: DatasetTab, view_row: int, statistic_column: str, display: str
    ) -> None:
        context = self._proc_means_results.get(tab)
        metadata = tab.handle.metadata
        analysis_column = metadata.proc_means_analysis_column
        statistic_key = dict(metadata.proc_means_statistic_keys).get(statistic_column)
        if context is None or analysis_column is None or statistic_key is None:
            return
        if display in {"", "—"}:
            QMessageBox.information(
                self._parent_widget(),
                "PROC MEANS Drill-down",
                "This statistic has no calculated value to drill down from.",
            )
            return
        generation = tab.generation
        result_filter = tab.compiled_filter
        result_sort = tab.model.sort_spec
        group_columns = context.config.group_variables
        lookup_columns = (*group_columns, analysis_column)
        labels = dict(PROC_MEANS_STATISTICS)
        statistic_label = labels.get(statistic_key, statistic_column.title())
        base_title = f"Query: {statistic_label}: {display}"
        self._host.set_analysis_task_status(f"Building {base_title}…")

        def build(worker: Worker):
            values = self._store.view_row_values(
                tab.handle.database_path,
                metadata,
                result_filter,
                result_sort,
                view_row,
                lookup_columns,
            )
            if values is None:
                raise ValueError("The selected PROC MEANS result row no longer exists.")
            group_values = dict(zip(group_columns, values[:-1], strict=True))
            analysis_variable = str(values[-1])
            compiled = build_drilldown_filter(
                context.source.metadata,
                context.config,
                group_values,
                analysis_variable,
                statistic_key,
            )
            where_text = build_drilldown_where_text(
                context.source.metadata,
                context.config,
                group_values,
                analysis_variable,
                statistic_key,
            )
            handle = self._proc_means_query_builder.run(
                context.source, compiled, base_title, worker.report
            )
            return handle, analysis_variable, where_text

        def completed(result: tuple[DatasetHandle, str, str]) -> None:
            handle, analysis_variable, where_text = result
            if not self._host.is_open_dataset_tab(tab) or generation != tab.generation:
                self._host.discard_analysis_result(handle)
                return
            title = self._host.unique_analysis_tab_title(base_title)
            self._host.show_proc_means_query_result(
                handle, title, where_text, analysis_variable
            )

        self._host.submit_analysis_task(
            tab,
            build,
            completed,
            lambda message, details: self._host.show_analysis_error(
                "PROC MEANS Drill-down Failed", message, details
            ),
        )
