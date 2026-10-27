from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QDialog, QLabel, QMessageBox, QPushButton, QVBoxLayout

from ...baseline_characteristics import (
    BaselineCharacteristicsConfig,
    BaselineCharacteristicsEngine,
    build_baseline_characteristics_configuration,
)
from ...baseline_characteristics.drilldown import (
    build_cell_filter,
    build_cell_where_text,
    lookup_cell,
)
from ...baseline_characteristics.result_store import (
    BaselineCharacteristicsLongResultBuilder,
)
from ...categorical.drilldown import CategoricalQueryBuilder
from ...codegen.sas import SasBaselineCharacteristicsGenerator
from ...dataset_utils import is_analysis_dataset
from ...domain import DatasetHandle
from ...filter_engine import FilterEngine
from ...shell_assets import load_baseline_characteristics_shell_preview
from ...ui.baseline_characteristics_builder import (
    BaselineCharacteristicsBuilderSelection,
)
from ...ui.dataset_tab import DatasetTab
from ...ui.sas_code_dialog import SasCodeDialog
from ...ui.shell_preview_dialog import ShellPreviewDialog
from ._base import AnalysisModuleController


@dataclass(frozen=True, slots=True)
class BaselineCharacteristicsResultContext:
    source: DatasetHandle
    config: BaselineCharacteristicsConfig


class BaselineCharacteristicsController(AnalysisModuleController):
    name = "baseline_characteristics"

    def __init__(self, owner: Any, temp_manager, settings) -> None:
        super().__init__(owner)
        self._host = owner._host
        self._panel = owner._panel
        self._engine = BaselineCharacteristicsEngine(temp_manager)
        self._sas_generator = SasBaselineCharacteristicsGenerator()
        self._long_result_builder = BaselineCharacteristicsLongResultBuilder(
            temp_manager
        )
        self._query_builder = CategoricalQueryBuilder(temp_manager)
        self._source: DatasetTab | None = None
        self._input_tabs: set[DatasetTab] = set()
        self._results: dict[DatasetTab, BaselineCharacteristicsResultContext] = {}

    @property
    def source(self) -> DatasetTab | None:
        if self._source is None or not self._host.is_open_dataset_tab(self._source):
            return None
        return self._source

    def is_input_tab(self, tab: object) -> bool:
        return tab in self._input_tabs

    def take_result_release_paths(self, tab: object) -> tuple[Path, ...] | None:
        context = self._results.pop(tab, None)
        return None if context is None else (context.source.temporary_path.parent,)

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
        self._panel.baseline_characteristics_builder.set_source_datasets(
            available, selected
        )

    def select_source(self, tab) -> None:
        if not isinstance(tab, DatasetTab):
            self._source = None
            self._set_dataset(None)
            return
        if not is_analysis_dataset(tab.handle) or not tab.cache_complete:
            return
        self._source = tab
        self._set_dataset(tab)

    def show_builder(self) -> None:
        self.refresh_sources()
        self._panel.show_baseline_characteristics_tab()

    def show_shell_preview(self) -> None:
        dialog = ShellPreviewDialog(
            self._parent_widget(), load_baseline_characteristics_shell_preview()
        )
        dialog.setWindowTitle("Baseline Characteristics Shell Preview")
        dialog.exec()

    def clear_source(self) -> None:
        self._source = None
        self._set_dataset(None)
        self.refresh_sources()

    def source_reload_started(self, tab: DatasetTab) -> None:
        if self._source is tab:
            self._panel.baseline_characteristics_builder.set_busy(
                True,
                "Source is reloading. Baseline Characteristics is temporarily unavailable.",
            )

    def source_reload_completed(self, tab: DatasetTab) -> None:
        if self._source is tab:
            self._set_dataset(tab)
            self._panel.baseline_characteristics_builder.set_busy(
                False, "Source reloaded."
            )

    def source_reload_failed(self, tab: DatasetTab) -> None:
        if self._source is tab:
            self._panel.baseline_characteristics_builder.set_busy(
                False, "Source reload failed; Builder configuration was retained."
            )

    def _set_dataset(self, tab: DatasetTab | None) -> None:
        builder = self._panel.baseline_characteristics_builder
        if tab is None:
            builder.set_dataset(None, "")
            return
        builder.set_dataset(
            tab.handle.metadata,
            str(tab.handle.source_path),
            tab.current_where_text(),
            tab.handle.kind,
        )

    def _context(self, selection: BaselineCharacteristicsBuilderSelection):
        tab = self.source
        if (
            tab is None
            or not tab.cache_complete
            or getattr(tab, "reload_in_progress", False)
        ):
            QMessageBox.warning(
                self._parent_widget(),
                "Baseline Characteristics",
                "The Builder source is unavailable. Open a fully loaded source dataset first.",
            )
            return None
        try:
            compiled = FilterEngine(tab.handle.metadata.variables).compile(
                selection.filter_text
            )
            config = BaselineCharacteristicsConfig(
                selection.items, compiled, selection.filter_text, selection.header_label
            )
            config.validate(tab.handle.metadata)
        except ValueError as error:
            QMessageBox.warning(
                self._parent_widget(), "Baseline Characteristics", str(error)
            )
            return None
        return tab, config

    def run(self, selection: BaselineCharacteristicsBuilderSelection) -> None:
        context = self._context(selection)
        if context is None:
            return
        tab, config = context
        source = tab.handle
        builder = self._panel.baseline_characteristics_builder
        self._input_tabs = {tab}
        builder.set_busy(
            True, "Calculating Baseline Characteristics in the background…"
        )

        def completed(handle: DatasetHandle) -> None:
            self._input_tabs.clear()
            builder.set_busy(
                False, f"Created {handle.metadata.row_count:,} result rows."
            )
            result_tab = self._host.create_analysis_result_tab(handle)
            self._host.retain_analysis_directory(source.temporary_path.parent)
            self._results[result_tab] = BaselineCharacteristicsResultContext(
                source, config
            )
            self._host.show_analysis_result_tab(
                result_tab, "Baseline Characteristics Result"
            )

        def failed(message: str, details: str) -> None:
            self._input_tabs.clear()
            builder.set_busy(False, "Baseline Characteristics failed.")
            self._host.show_analysis_error(
                "Baseline Characteristics Failed", message, details
            )

        self._host.submit_analysis_task(
            builder,
            lambda worker: self._engine.run(source, config, worker.report),
            completed,
            failed,
        )

    def generate_sas_code(
        self, selection: BaselineCharacteristicsBuilderSelection
    ) -> None:
        context = self._context(selection)
        if context is None:
            return
        tab, config = context
        if tab.handle.kind != "sas":
            QMessageBox.warning(
                self._parent_widget(),
                "Baseline Characteristics",
                "SAS Code Generator supports a single SAS source only; merged sources are not supported.",
            )
            return
        builder = self._panel.baseline_characteristics_builder
        source = tab.handle
        self._input_tabs = {tab}
        builder.set_busy(True, "Generating Baseline Characteristics SAS code…")

        def completed(code: str) -> None:
            self._input_tabs.clear()
            builder.set_busy(False, "SAS code generated.")
            SasCodeDialog(
                code,
                f"analysis.{source.metadata.name.casefold()}",
                f"{source.metadata.name.casefold()}_baseline_characteristics.sas",
                self._parent_widget(),
            ).exec()

        def failed(message: str, details: str) -> None:
            self._input_tabs.clear()
            builder.set_busy(False, "SAS Code Generator failed.")
            self._host.show_analysis_error(
                "Baseline Characteristics SAS Code Generator Failed", message, details
            )

        self._host.submit_analysis_task(
            builder,
            lambda _worker: self._sas_generator.generate(
                build_baseline_characteristics_configuration(source, config)
            ),
            completed,
            failed,
        )

    def open_long_result(self) -> None:
        tab = self._host.current_dataset_tab()
        context = self._results.get(tab) if tab is not None else None
        if (
            tab is None
            or tab.handle.kind != "baseline_characteristics"
            or context is None
        ):
            return
        title = self._host.unique_analysis_tab_title(
            "Baseline Characteristics Long Result"
        )

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
                "Baseline Characteristics Long Result Failed", message, details
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
        denominator = False
        if cell.item_kind == "categorical":
            selected = self._choose_categorical_drilldown()
            if selected is None:
                return
            denominator = selected == "denominator"
        try:
            compiled = build_cell_filter(
                context.source.metadata, context.config, cell, denominator=denominator
            )
            where_text = build_cell_where_text(
                context.source.metadata, context.config, cell, denominator=denominator
            )
        except (KeyError, TypeError, ValueError) as error:
            QMessageBox.warning(
                self._parent_widget(), "Baseline Characteristics Drill-down", str(error)
            )
            return
        mode = (
            "Denominator Participants"
            if denominator
            else "Numerator Participants"
            if cell.item_kind == "categorical"
            else "Nonmissing Source Rows"
        )
        title = self._host.unique_analysis_tab_title(f"Query: {mode}")
        generation = tab.generation

        def completed(handle: DatasetHandle) -> None:
            if not self._host.is_open_dataset_tab(tab) or generation != tab.generation:
                self._host.discard_analysis_result(handle)
                return
            query_tab = self._host.create_analysis_result_tab(handle)
            self._host.show_analysis_result_tab(query_tab, title)
            query_tab.setWindowTitle(where_text)

        self._host.submit_analysis_task(
            tab,
            lambda _worker: self._query_builder.run(
                context.source,
                compiled.sql,
                compiled.parameters,
                title,
                subject_id_variable=(
                    context.config.subject_id_variable
                    if cell.item_kind == "categorical"
                    else None
                ),
            ),
            completed,
            lambda message, details: self._host.show_analysis_error(
                "Baseline Characteristics Drill-down Failed", message, details
            ),
        )

    def _choose_categorical_drilldown(self) -> str | None:
        dialog = QDialog(self._parent_widget())
        dialog.setWindowTitle("Baseline Characteristics Drill-down")
        dialog.selected = None
        layout = QVBoxLayout(dialog)
        layout.addWidget(
            QLabel("Choose the participant set to display for this n (%) cell.")
        )
        for value, label in (
            ("numerator", "Show Numerator Participants"),
            ("denominator", "Show Denominator Participants"),
        ):
            button = QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, chosen=value: (
                    setattr(dialog, "selected", chosen),
                    dialog.accept(),
                )
            )
            layout.addWidget(button)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(dialog.reject)
        layout.addWidget(cancel)
        dialog.exec()
        return dialog.selected


__all__ = ["BaselineCharacteristicsController", "BaselineCharacteristicsResultContext"]
