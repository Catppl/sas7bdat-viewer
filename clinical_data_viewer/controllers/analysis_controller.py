from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox, QWidget

from ..codegen.sas import SasListingGenerator
from ..dataset_utils import is_analysis_dataset
from ..domain import DatasetHandle, DatasetMetadata
from ..filter_engine import FilterEngine
from ..listing import ListingConfig, ListingEngine
from ..listing.configuration import build_listing_configuration
from ..temp_manager import TempManager
from ..ui.analysis_panel import AnalysisPanel
from ..ui.dataset_tab import DatasetTab
from ..ui.listing_builder import ListingBuilderSelection
from ..ui.sas_code_dialog import SasCodeDialog
from ..workers import Worker


@dataclass(frozen=True, slots=True)
class ListingResultContext:
    """Input handles retained while a Listing Result tab remains open."""

    source: DatasetHandle
    adsl: DatasetHandle | None
    config: ListingConfig


@dataclass(frozen=True, slots=True)
class TabCloseBlocker:
    title: str
    message: str


class AnalysisControllerHost(Protocol):
    """Small MainWindow surface needed by the first controller migration."""

    def current_dataset_tab(self) -> DatasetTab | None: ...

    def is_open_dataset_tab(self, tab: DatasetTab) -> bool: ...

    def available_sas_dataset_tabs(self) -> list[tuple[DatasetTab, str]]: ...

    def create_analysis_result_tab(self, handle: DatasetHandle) -> DatasetTab: ...

    def show_analysis_result_tab(self, tab: DatasetTab, title: str) -> None: ...

    def submit_analysis_task(
        self,
        owner: QWidget,
        function: Callable[[Worker], object],
        completed: Callable[[object], None],
        failed: Callable[[str, str], None],
    ) -> None: ...

    def retain_analysis_directory(self, path: Path) -> None: ...

    def show_analysis_error(self, title: str, message: str, details: str = "") -> None: ...

    def browse_listing_adsl_dataset(self) -> None: ...


class AnalysisController(QObject):
    """Coordinate Analysis UI workflows.

    Phase 1 intentionally owns only Listing Generator coordination.  Domain
    engines, UI widgets, workers, tab construction, and temporary-directory
    deletion keep their existing contracts while MainWindow supplies the small
    host surface above.
    """

    def __init__(
        self,
        host: AnalysisControllerHost,
        analysis_panel: AnalysisPanel,
        temp_manager: TempManager,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._panel = analysis_panel
        self._listing_engine = ListingEngine(temp_manager)
        self._sas_listing_generator = SasListingGenerator()
        self._listing_source: DatasetTab | None = None
        self._listing_input_tabs: set[DatasetTab] = set()
        self._listing_results: dict[DatasetTab, ListingResultContext] = {}

        builder = self._panel.listing_builder
        builder.run_requested.connect(self.run_listing)
        builder.sas_code_requested.connect(self.generate_listing_sas_code)
        builder.browse_adsl_requested.connect(host.browse_listing_adsl_dataset)
        builder.cleared.connect(self.clear_listing_source)

    @property
    def listing_source(self) -> DatasetTab | None:
        """Return the Listing Builder source only while its tab is still open."""
        if self._listing_source is None:
            return None
        if not self._host.is_open_dataset_tab(self._listing_source):
            return None
        return self._listing_source

    def refresh_listing_adsl_sources(
        self, datasets: Iterable[tuple[DatasetTab, str]] | None = None
    ) -> None:
        """Refresh only Listing's physical-SAS ADSL selector."""
        self._panel.listing_builder.set_adsl_sources(
            list(datasets) if datasets is not None else self._host.available_sas_dataset_tabs()
        )

    def select_listing_adsl(self, tab: DatasetTab) -> None:
        self._panel.listing_builder.select_adsl(tab)

    def show_listing_builder(self) -> None:
        """Open Listing with its first eligible source fixed until Clear."""
        if self.listing_source is None:
            active_tab = self._host.current_dataset_tab()
            if (
                active_tab is not None
                and is_analysis_dataset(active_tab.handle)
                and active_tab.cache_complete
            ):
                self._listing_source = active_tab
                self._set_listing_dataset(active_tab)
        self.refresh_listing_adsl_sources()
        self._panel.show_listing_tab()

    def clear_listing_source(self) -> None:
        """Release Listing's source only after its own Clear action."""
        self._listing_source = None
        self._set_listing_dataset(None)

    def bound_builder_titles(self, tab: object) -> list[str]:
        return ["Listing"] if self.listing_source is tab else []

    def tab_close_blocker(self, tab: object) -> TabCloseBlocker | None:
        if tab in self._listing_input_tabs:
            return TabCloseBlocker(
                "Listing Running",
                "This dataset is currently used by a Listing. Wait for the calculation to finish before closing it.",
            )
        return None

    def take_result_release_paths(self, tab: object) -> tuple[Path, ...]:
        """Detach a Listing Result and return source directories to release."""
        context = self._listing_results.pop(tab, None)
        if context is None:
            return ()
        paths = {context.source.temporary_path.parent}
        if context.adsl is not None:
            paths.add(context.adsl.temporary_path.parent)
        return tuple(paths)

    def _set_listing_dataset(self, tab: DatasetTab | None) -> None:
        builder = self._panel.listing_builder
        if tab is None:
            builder.set_dataset(None, "", "", "sas")
            return
        builder.set_dataset(
            tab.handle.metadata,
            str(tab.handle.source_path),
            tab.current_where_text(),
            tab.handle.kind,
        )

    def _listing_context(
        self, selection: ListingBuilderSelection
    ) -> tuple[DatasetTab, DatasetTab | None, ListingConfig, DatasetMetadata] | None:
        tab = self.listing_source
        if tab is None or not is_analysis_dataset(tab.handle) or not tab.cache_complete:
            QMessageBox.warning(
                self._parent_widget(),
                "Listing Generator",
                "The Builder source is unavailable. Open a fully loaded source dataset, then open the Builder again.",
            )
            return None
        adsl_tab = selection.adsl_tab if selection.merge_adsl.enabled else None
        if selection.merge_adsl.enabled and (
            not isinstance(adsl_tab, DatasetTab) or not adsl_tab.cache_complete
        ):
            QMessageBox.warning(
                self._parent_widget(),
                "Listing Generator",
                "Open a fully loaded ADSL dataset before running the Listing.",
            )
            return None
        try:
            preliminary = ListingConfig(
                selection.columns,
                data_filter_text=selection.data_filter_text,
                merge_adsl=selection.merge_adsl,
            )
            resolved = self._listing_engine.resolved_metadata(
                tab.handle,
                preliminary,
                adsl_tab.handle if isinstance(adsl_tab, DatasetTab) else None,
            )
            compiled = FilterEngine(resolved.variables).compile(
                selection.data_filter_text
            )
            config = replace(preliminary, data_filter=compiled)
            config.validate_basic()
            from ..listing.expressions import parse_expression

            for column in config.columns:
                parse_expression(column.expression_text, resolved.variables)
        except ValueError as error:
            QMessageBox.warning(self._parent_widget(), "Listing Generator", str(error))
            return None
        return (
            tab,
            adsl_tab if isinstance(adsl_tab, DatasetTab) else None,
            config,
            resolved,
        )

    def run_listing(self, selection: ListingBuilderSelection) -> None:
        context = self._listing_context(selection)
        if context is None:
            return
        source_tab, adsl_tab, config, _resolved = context
        warnings = self._listing_engine.warnings(
            source_tab.handle, config, adsl_tab.handle if adsl_tab else None
        )
        if warnings:
            QMessageBox.warning(
                self._parent_widget(), "Listing Generator", "\n\n".join(warnings)
            )
        source_handle = source_tab.handle
        adsl_handle = adsl_tab.handle if adsl_tab else None
        builder = self._panel.listing_builder
        self._listing_input_tabs = {source_tab}
        if adsl_tab:
            self._listing_input_tabs.add(adsl_tab)
        builder.set_busy(True, "Building Listing in the background…")

        def completed(handle) -> None:
            self._listing_input_tabs.clear()
            builder.set_busy(
                False, f"Created {handle.metadata.row_count:,} Listing records."
            )
            result_tab = self._host.create_analysis_result_tab(handle)
            for directory in {
                source_handle.temporary_path.parent,
                *([adsl_handle.temporary_path.parent] if adsl_handle else []),
            }:
                self._host.retain_analysis_directory(directory)
            self._listing_results[result_tab] = ListingResultContext(
                source_handle, adsl_handle, config
            )
            self._host.show_analysis_result_tab(result_tab, "Listing Result")

        def failed(message: str, details: str) -> None:
            self._listing_input_tabs.clear()
            builder.set_busy(False, "Listing failed.")
            self._host.show_analysis_error("Listing Generator Failed", message, details)

        self._host.submit_analysis_task(
            builder,
            lambda worker: self._listing_engine.run(
                source_handle, config, adsl_handle, worker.report
            ),
            completed,
            failed,
        )

    def generate_listing_sas_code(self, selection: ListingBuilderSelection) -> None:
        context = self._listing_context(selection)
        if context is None:
            return
        source_tab, adsl_tab, config, resolved = context
        if source_tab.handle.kind != "sas":
            return
        source_handle = source_tab.handle
        adsl_handle = adsl_tab.handle if adsl_tab else None
        builder = self._panel.listing_builder
        self._listing_input_tabs = {source_tab}
        if adsl_tab:
            self._listing_input_tabs.add(adsl_tab)
        builder.set_busy(True, "Generating Listing SAS code…")

        def completed(code) -> None:
            self._listing_input_tabs.clear()
            builder.set_busy(False, "SAS code generated.")
            name = source_handle.metadata.name.lower() or "dataset"
            SasCodeDialog(
                code,
                str(source_handle.source_path),
                f"{name}_listing.sas",
                self._parent_widget(),
            ).exec()

        def failed(message: str, details: str) -> None:
            self._listing_input_tabs.clear()
            builder.set_busy(False, "SAS Code Generator failed.")
            self._host.show_analysis_error(
                "Listing SAS Code Generator Failed", message, details
            )

        def generate(_worker: Worker) -> str:
            configuration = build_listing_configuration(
                source_handle, config, resolved, adsl_handle
            )
            return self._sas_listing_generator.generate(configuration)

        self._host.submit_analysis_task(builder, generate, completed, failed)

    def _parent_widget(self) -> QWidget | None:
        parent = self.parent()
        return parent if isinstance(parent, QWidget) else None
