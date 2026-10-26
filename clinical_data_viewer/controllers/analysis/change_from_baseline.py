from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QMessageBox

from ...change_from_baseline import ChangeFromBaselineConfig, ChangeFromBaselineEngine
from ...change_from_baseline.configuration import (
    build_change_from_baseline_configuration,
)
from ...change_from_baseline.result_store import ChangeFromBaselineLongResultBuilder
from ...codegen.sas import SasChangeFromBaselineGenerator
from ...dataset_utils import is_analysis_dataset
from ...domain import DatasetHandle
from ...filter_engine import FilterEngine
from ...ui.change_from_baseline_builder import ChangeFromBaselineBuilderSelection
from ...ui.dataset_tab import DatasetTab
from ...ui.sas_code_dialog import SasCodeDialog
from ...ui.shell_preview_dialog import ShellPreviewDialog
from ._base import AnalysisModuleController


@dataclass(frozen=True, slots=True)
class ChangeFromBaselineResultContext:
    source: DatasetHandle
    config: ChangeFromBaselineConfig


class ChangeFromBaselineController(AnalysisModuleController):
    name = "change_from_baseline"

    def __init__(self, owner: Any, temp_manager, settings) -> None:
        super().__init__(owner)
        self._host = owner._host
        self._panel = owner._panel
        self._settings = settings
        self._engine = ChangeFromBaselineEngine(temp_manager)
        self._sas_generator = SasChangeFromBaselineGenerator()
        self._long_result_builder = ChangeFromBaselineLongResultBuilder(temp_manager)
        self._source: DatasetTab | None = None
        self._input_tabs: set[DatasetTab] = set()
        self._results: dict[DatasetTab, ChangeFromBaselineResultContext] = {}

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
        return (context.source.temporary_path.parent,)

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
        self._panel.change_from_baseline_builder.set_source_datasets(available, selected)

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
        self._panel.show_change_from_baseline_tab()

    def show_shell_preview(self) -> None:
        """Show the maintained PNG shell preview for this builder."""

        ShellPreviewDialog(self._parent_widget()).exec()

    def clear_source(self) -> None:
        self._source = None
        self._set_dataset(None)
        self.refresh_sources()

    def source_reload_started(self, tab: DatasetTab) -> None:
        if self._source is tab:
            self._panel.change_from_baseline_builder.set_busy(
                True, "Source is reloading. Change from Baseline is temporarily unavailable."
            )

    def source_reload_completed(self, tab: DatasetTab) -> None:
        if self._source is not tab:
            return
        self._set_dataset(tab)
        self._panel.change_from_baseline_builder.set_busy(False, "Source reloaded.")

    def source_reload_failed(self, tab: DatasetTab) -> None:
        if self._source is tab:
            self._panel.change_from_baseline_builder.set_busy(
                False, "Source reload failed; Builder configuration was retained."
            )

    def _set_dataset(self, tab: DatasetTab | None) -> None:
        if tab is None:
            self._panel.change_from_baseline_builder.set_source_unavailable()
            return
        self._panel.change_from_baseline_builder.set_dataset(
            tab.handle.metadata,
            str(tab.handle.source_path),
            tab.current_where_text(),
        )

    @staticmethod
    def _item_labels(text: str) -> tuple[tuple[str, str], ...]:
        labels: list[tuple[str, str]] = []
        seen: set[str] = set()
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if "=" not in line:
                raise ValueError("Item labels must use one value=label pair per line.")
            value, label = (part.strip() for part in line.split("=", 1))
            if not value or not label:
                raise ValueError("Item labels cannot contain an empty value or label.")
            if value.casefold() in seen:
                raise ValueError(f'Duplicate Item label value: "{value}".')
            seen.add(value.casefold())
            labels.append((value, label))
        return tuple(labels)

    def _context(
        self, selection: ChangeFromBaselineBuilderSelection
    ) -> tuple[DatasetTab, ChangeFromBaselineConfig] | None:
        tab = self.source
        if (
            tab is None
            or not tab.cache_complete
            or getattr(tab, "reload_in_progress", False)
        ):
            QMessageBox.warning(
                self._parent_widget(),
                "Change from Baseline",
                "The Builder source is unavailable. Open a fully loaded source dataset first.",
            )
            return None
        try:
            data_filter = FilterEngine(tab.handle.metadata.variables).compile(
                selection.data_filter_text
            )
            post_filter = FilterEngine(tab.handle.metadata.variables).compile(
                selection.postbaseline_filter_text
            )
            config = ChangeFromBaselineConfig(
                selection.change_variable,
                selection.item_variable,
                selection.row_variables,
                selection.treatment_variable,
                selection.row_sort,
                selection.statistics,
                data_filter,
                selection.data_filter_text,
                post_filter,
                selection.postbaseline_filter_text,
                self._item_labels(selection.item_labels_text),
                selection.include_total,
                selection.statistic_decimals,
            )
            config.validate(tab.handle.metadata)
        except ValueError as error:
            QMessageBox.warning(self._parent_widget(), "Change from Baseline", str(error))
            return None
        return tab, config

    def run(self, selection: ChangeFromBaselineBuilderSelection) -> None:
        context = self._context(selection)
        if context is None:
            return
        tab, config = context
        warnings = (
            self._engine.duplicate_warnings(tab.handle, config)
            + self._engine.item_order_warnings(tab.handle, config)
        )
        if warnings:
            QMessageBox.warning(self._parent_widget(), "Change from Baseline", "\n\n".join(warnings))
        source = tab.handle
        builder = self._panel.change_from_baseline_builder
        self._input_tabs = {tab}
        builder.set_busy(True, "Calculating Change from Baseline in the background…")

        def completed(handle: DatasetHandle) -> None:
            self._input_tabs.clear()
            builder.set_busy(False, f"Created {handle.metadata.row_count:,} result rows.")
            result_tab = self._host.create_analysis_result_tab(handle)
            self._host.retain_analysis_directory(source.temporary_path.parent)
            self._results[result_tab] = ChangeFromBaselineResultContext(source, config)
            self._host.show_analysis_result_tab(result_tab, "Change from Baseline Result")

        def failed(message: str, details: str) -> None:
            self._input_tabs.clear()
            builder.set_busy(False, "Change from Baseline failed.")
            self._host.show_analysis_error("Change from Baseline Failed", message, details)

        self._host.submit_analysis_task(
            builder,
            lambda worker: self._engine.run(source, config, worker.report),
            completed,
            failed,
        )

    def generate_sas_code(self, selection: ChangeFromBaselineBuilderSelection) -> None:
        """Generate reusable SAS from the current Builder configuration."""
        context = self._context(selection)
        if context is None:
            return
        tab, config = context
        if tab.handle.kind != "sas":
            QMessageBox.warning(
                self._parent_widget(),
                "Change from Baseline",
                "SAS Code Generator supports SAS7BDAT source datasets only.",
            )
            return
        builder = self._panel.change_from_baseline_builder
        source = tab.handle
        self._input_tabs = {tab}
        builder.set_busy(True, "Generating Change from Baseline SAS code…")

        def completed(code: str) -> None:
            self._input_tabs.clear()
            builder.set_busy(False, "SAS code generated.")
            safe_name = source.metadata.name.casefold()
            SasCodeDialog(
                code,
                f"adam.{source.metadata.name.casefold()}",
                f"{safe_name}_change_from_baseline.sas",
                self._parent_widget(),
            ).exec()

        def failed(message: str, details: str) -> None:
            self._input_tabs.clear()
            builder.set_busy(False, "SAS Code Generator failed.")
            self._host.show_analysis_error(
                "Change from Baseline SAS Code Generator Failed", message, details
            )

        def generate(_worker: object) -> str:
            levels = [
                {"value": value, "label": label}
                for _key, value, label in self._engine.resolve_treatment_levels(source, config)
            ]
            item_order = self._engine.resolve_item_order(source, config)
            configuration = build_change_from_baseline_configuration(
                source,
                config,
                resolved_treatment_levels=levels,
                item_order=item_order,
            )
            return self._sas_generator.generate(configuration)

        self._host.submit_analysis_task(builder, generate, completed, failed)

    def open_long_result(self) -> None:
        tab = self._host.current_dataset_tab()
        context = self._results.get(tab) if tab is not None else None
        if tab is None or tab.handle.kind != "change_from_baseline" or context is None:
            return
        title = self._host.unique_analysis_tab_title(
            "Change from Baseline Long Result"
        )

        def completed(handle: DatasetHandle) -> None:
            if not self._host.is_open_dataset_tab(tab):
                self._host.discard_analysis_result(handle)
                return
            long_tab = self._host.create_analysis_result_tab(handle)
            self._host.show_analysis_result_tab(long_tab, title)

        self._host.submit_analysis_task(
            tab,
            lambda _worker: self._long_result_builder.run(tab.handle, context.source),
            completed,
            lambda message, details: self._host.show_analysis_error(
                "Change from Baseline Long Result Failed", message, details
            ),
        )


__all__ = ["ChangeFromBaselineController", "ChangeFromBaselineResultContext"]
