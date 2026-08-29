from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QObject
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..domain import DatasetHandle
from ..settings import AppSettings
from ..temp_manager import TempManager
from ..ui.ae_table_builder import AeTableBuilderSelection
from ..ui.analysis_panel import AnalysisPanel
from ..ui.categorical_builder import CategoricalBuilderSelection
from ..ui.dataset_tab import DatasetTab
from ..ui.listing_builder import ListingBuilderSelection
from ..ui.proc_means_builder import ProcMeansBuilderSelection
from ..ui.rule_based_builder import RuleBasedBuilderSelection
from ..workers import Worker
from .analysis import (
    AeTableController,
    CategoricalController,
    ListingController,
    ProcMeansController,
    RuleBasedController,
)
from .analysis.ae_table import AeTableResultContext as _AeTableResultContext
from .analysis.categorical import CategoricalResultContext as _CategoricalResultContext
from .analysis.listing import ListingResultContext as _ListingResultContext
from .analysis.proc_means import ProcMeansResultContext as _ProcMeansResultContext
from .analysis.rule_based import RuleBasedResultContext as _RuleBasedResultContext

# Backwards-compatible imports for callers that used the pre-migration module.
ListingResultContext = _ListingResultContext
RuleBasedResultContext = _RuleBasedResultContext
AeTableResultContext = _AeTableResultContext
ProcMeansResultContext = _ProcMeansResultContext
CategoricalResultContext = _CategoricalResultContext


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

    def browse_rule_based_adsl_dataset(self) -> None: ...

    def browse_ae_table_adsl_dataset(self) -> None: ...

    def browse_categorical_adsl_dataset(self) -> None: ...

    def unique_analysis_tab_title(self, base: str) -> str: ...

    def discard_analysis_result(self, handle: DatasetHandle) -> None: ...

    def set_analysis_task_status(self, text: str) -> None: ...

    def show_proc_means_query_result(
        self,
        handle: DatasetHandle,
        title: str,
        where_text: str,
        analysis_variable: str,
    ) -> None: ...


class AnalysisController(QObject):
    """Coordinate Analysis UI workflows.

    Analysis Builder coordination lives here. Domain
    engines, UI widgets, workers, tab construction, and temporary-directory
    deletion keep their existing contracts while MainWindow supplies the small
    host surface above.
    """

    def __init__(
        self,
        host: AnalysisControllerHost,
        analysis_panel: AnalysisPanel,
        temp_manager: TempManager,
        settings: AppSettings,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._host = host
        self._panel = analysis_panel
        self._settings = settings

        # Per-module boundaries.  The facade keeps the established public
        # methods while each workflow is migrated into its own controller.
        self.listing = ListingController(self, temp_manager, settings)
        self.rule_based = RuleBasedController(self, temp_manager, settings)
        self.ae_table = AeTableController(self, temp_manager, settings)
        self.proc_means = ProcMeansController(self, temp_manager, settings)
        self.categorical = CategoricalController(self, temp_manager, settings)

        builder = self._panel.listing_builder
        builder.run_requested.connect(self.listing.run_listing)
        builder.sas_code_requested.connect(self.listing.generate_listing_sas_code)
        builder.browse_adsl_requested.connect(host.browse_listing_adsl_dataset)
        builder.cleared.connect(self.listing.clear_listing_source)

        rule_builder = self._panel.rule_based_builder
        rule_builder.run_requested.connect(self.rule_based.run_rule_based)
        rule_builder.sas_code_requested.connect(self.rule_based.generate_rule_based_sas_code)
        rule_builder.validation_error.connect(
            lambda message: QMessageBox.warning(
                self._parent_widget(), "Rule-based Table", message
            )
        )
        rule_builder.browse_adsl_requested.connect(host.browse_rule_based_adsl_dataset)
        rule_builder.cleared.connect(self.rule_based.clear_rule_based_source)

        ae_builder = self._panel.ae_table_builder
        ae_builder.run_requested.connect(self.ae_table.run_ae_table)
        ae_builder.sas_code_requested.connect(self.ae_table.generate_ae_table_sas_code)
        ae_builder.validation_error.connect(
            lambda message: QMessageBox.warning(self._parent_widget(), "AE Table", message)
        )
        ae_builder.browse_adsl_requested.connect(host.browse_ae_table_adsl_dataset)
        ae_builder.cleared.connect(self.ae_table.clear_ae_table_source)

        proc_builder = self._panel.builder
        proc_builder.run_requested.connect(self.proc_means.run_proc_means_builder)
        proc_builder.sas_code_requested.connect(
            self.proc_means.generate_proc_means_sas_code
        )
        proc_builder.r_code_requested.connect(self.proc_means.generate_proc_means_r_code)
        proc_builder.validation_error.connect(
            lambda message: QMessageBox.warning(
                self._parent_widget(), "PROC MEANS Builder", message
            )
        )
        proc_builder.cleared.connect(self.proc_means.clear_proc_means_source)

        categorical_builder = self._panel.categorical_builder
        categorical_builder.run_requested.connect(self.categorical.run_categorical)
        categorical_builder.validation_error.connect(
            lambda message: QMessageBox.warning(
                self._parent_widget(), "Categorical Table", message
            )
        )
        categorical_builder.browse_adsl_requested.connect(
            host.browse_categorical_adsl_dataset
        )
        categorical_builder.cleared.connect(self.categorical.clear_categorical_source)

    @property
    def listing_source(self) -> DatasetTab | None:
        return self.listing.listing_source

    def refresh_listing_adsl_sources(self, datasets=None) -> None:
        self.listing.refresh_listing_adsl_sources(datasets)

    def select_listing_adsl(self, tab: DatasetTab) -> None:
        self.listing.select_listing_adsl(tab)

    def show_listing_builder(self) -> None:
        self.listing.show_listing_builder()

    def clear_listing_source(self) -> None:
        self.listing.clear_listing_source()

    def run_listing(self, selection: ListingBuilderSelection) -> None:
        self.listing.run_listing(selection)

    def generate_listing_sas_code(self, selection: ListingBuilderSelection) -> None:
        self.listing.generate_listing_sas_code(selection)

    @property
    def rule_based_source(self) -> DatasetTab | None:
        return self.rule_based.rule_based_source

    def refresh_rule_based_adsl_sources(self, datasets=None) -> None:
        self.rule_based.refresh_rule_based_adsl_sources(datasets)

    def select_rule_based_adsl(self, tab: DatasetTab) -> None:
        self.rule_based.select_rule_based_adsl(tab)

    def show_rule_based_builder(self) -> None:
        self.rule_based.show_rule_based_builder()

    def clear_rule_based_source(self) -> None:
        self.rule_based.clear_rule_based_source()

    def run_rule_based(self, selection: RuleBasedBuilderSelection) -> None:
        self.rule_based.run_rule_based(selection)

    def generate_rule_based_sas_code(self, selection: RuleBasedBuilderSelection) -> None:
        self.rule_based.generate_rule_based_sas_code(selection)

    def drilldown_rule_based(self, tab, view_row, column_name, display) -> None:
        self.rule_based.drilldown_rule_based(tab, view_row, column_name, display)

    def open_rule_based_long_result(self) -> None:
        self.rule_based.open_rule_based_long_result()

    def bound_builder_titles(self, tab: object) -> list[str]:
        titles: list[str] = []
        if self.listing_source is tab:
            titles.append("Listing")
        if self.rule_based_source is tab:
            titles.append("Rule Based")
        if self.ae_table_source is tab:
            titles.append("AE Table")
        if self.proc_means_source is tab:
            titles.append("Proc Means")
        if self.categorical_source is tab:
            titles.append("Categorical")
        return titles

    def _tab_activity_blocker(
        self, tab: object, action: str
    ) -> TabCloseBlocker | None:
        if self.listing.is_input_tab(tab):
            return TabCloseBlocker(
                "Listing Running",
                f"This dataset is currently used by a Listing. Wait for the calculation to finish before {action} it.",
            )
        if self.rule_based.is_input_tab(tab):
            return TabCloseBlocker(
                "Rule-based Table Running",
                f"This dataset is currently used by a Rule-based Table. Wait for the calculation to finish before {action} it.",
            )
        if self.ae_table.is_input_tab(tab):
            return TabCloseBlocker(
                "AE Table Running",
                f"This dataset is currently used by an AE Table. Wait for the calculation to finish before {action} it.",
            )
        if self.proc_means.is_input_tab(tab):
            return TabCloseBlocker(
                "PROC MEANS Running",
                f"This dataset is currently being analyzed. Wait for PROC MEANS to finish before {action} it.",
            )
        if self.categorical.is_input_tab(tab):
            return TabCloseBlocker(
                "Categorical Table Running",
                f"This dataset is currently used by a Categorical Table. Wait for the calculation to finish before {action} it.",
            )
        return None

    def tab_close_blocker(self, tab: object) -> TabCloseBlocker | None:
        return self._tab_activity_blocker(tab, "closing")

    def tab_reload_blocker(self, tab: object) -> TabCloseBlocker | None:
        """Return an analysis-task blocker before replacing a tab handle."""
        return self._tab_activity_blocker(tab, "reloading")

    def source_reload_started(self, tab: DatasetTab) -> None:
        """Notify the bound analysis module before a dataset handle is replaced."""
        self.proc_means.source_reload_started(tab)
        categorical = getattr(self, "categorical", None)
        if categorical is not None:
            categorical.source_reload_started(tab)

    def source_reload_completed(self, tab: DatasetTab) -> None:
        """Notify the bound analysis module after a reload fully completes."""
        self.proc_means.source_reload_completed(tab)
        categorical = getattr(self, "categorical", None)
        if categorical is not None:
            categorical.source_reload_completed(tab)

    def source_reload_failed(self, tab: DatasetTab) -> None:
        """Notify the bound analysis module that a reload failed."""
        self.proc_means.source_reload_failed(tab)
        categorical = getattr(self, "categorical", None)
        if categorical is not None:
            categorical.source_reload_failed(tab)

    def take_result_release_paths(self, tab: object) -> tuple[Path, ...]:
        """Detach an analysis result and return source directories to release."""
        paths = self.listing.take_result_release_paths(tab)
        if paths is not None:
            return paths
        paths = self.rule_based.take_result_release_paths(tab)
        if paths is not None:
            return paths
        paths = self.ae_table.take_result_release_paths(tab)
        if paths is not None:
            return paths
        paths = self.proc_means.take_result_release_paths(tab)
        if paths is not None:
            return paths
        paths = self.categorical.take_result_release_paths(tab)
        if paths is not None:
            return paths
        return ()

    @property
    def categorical_source(self) -> DatasetTab | None:
        return self.categorical.categorical_source

    def refresh_categorical_adsl_sources(
        self, datasets=None
    ) -> None:
        self.categorical.refresh_categorical_adsl_sources(datasets)

    def select_categorical_adsl(self, tab: DatasetTab) -> None:
        self.categorical.select_categorical_adsl(tab)

    def show_categorical_builder(self) -> None:
        self.categorical.show_categorical_builder()

    def clear_categorical_source(self) -> None:
        self.categorical.clear_categorical_source()

    def run_categorical(self, selection: CategoricalBuilderSelection) -> None:
        self.categorical.run_categorical(selection)

    def drilldown_categorical(
        self, tab: DatasetTab, view_row: int, column_name: str, display: str
    ) -> None:
        self.categorical.drilldown_categorical(
            tab, view_row, column_name, display
        )

    def open_categorical_long_result(self) -> None:
        self.categorical.open_categorical_long_result()

    @property
    def ae_table_source(self) -> DatasetTab | None:
        return self.ae_table.ae_table_source

    def refresh_ae_table_adsl_sources(self, datasets=None) -> None:
        self.ae_table.refresh_ae_table_adsl_sources(datasets)

    def select_ae_table_adsl(self, tab: DatasetTab) -> None:
        self.ae_table.select_ae_table_adsl(tab)

    def show_ae_table_builder(self) -> None:
        self.ae_table.show_ae_table_builder()

    def clear_ae_table_source(self) -> None:
        self.ae_table.clear_ae_table_source()

    def run_ae_table(self, selection: AeTableBuilderSelection) -> None:
        self.ae_table.run_ae_table(selection)

    def generate_ae_table_sas_code(self, selection: AeTableBuilderSelection) -> None:
        self.ae_table.generate_ae_table_sas_code(selection)

    def drilldown_ae_table(self, tab, view_row, column_name, display) -> None:
        self.ae_table.drilldown_ae_table(tab, view_row, column_name, display)

    def open_ae_table_long_result(self) -> None:
        self.ae_table.open_ae_table_long_result()

    @property
    def proc_means_source(self) -> DatasetTab | None:
        return self.proc_means.proc_means_source

    def show_proc_means_builder(self) -> None:
        self.proc_means.show_proc_means_builder()

    def clear_proc_means_source(self) -> None:
        self.proc_means.clear_proc_means_source()

    def generate_proc_means_sas_code(
        self, selection: ProcMeansBuilderSelection
    ) -> None:
        self.proc_means.generate_proc_means_sas_code(selection)

    def generate_proc_means_r_code(
        self, selection: ProcMeansBuilderSelection
    ) -> None:
        self.proc_means.generate_proc_means_r_code(selection)

    def run_proc_means_builder(self, selection: ProcMeansBuilderSelection) -> None:
        self.proc_means.run_proc_means_builder(selection)

    def drilldown_proc_means(
        self, tab: DatasetTab, view_row: int, statistic_column: str, display: str
    ) -> None:
        self.proc_means.drilldown_proc_means(
            tab, view_row, statistic_column, display
        )

    def _drilldown_dialog(self):
        """Create the compact Rule-based drill-down chooser."""
        dialog = QDialog(self._parent_widget())
        dialog.setWindowTitle("Rule-based Table Drill-down")
        dialog.setModal(True)
        dialog.setMinimumSize(420, 250)
        dialog.selected_button = None
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Choose rows to display for this n (%) cell."))
        records = QPushButton("Show Numerator Records")
        subjects = QPushButton("Show Numerator Subjects")
        denominator = QPushButton("Show Denominator Subjects")
        cancel = QPushButton("Cancel")
        for button in (records, subjects, denominator):
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

            def choose(_checked=False, selected_button=button):
                dialog.selected_button = selected_button
                dialog.accept()

            button.clicked.connect(choose)
            layout.addWidget(button)
        cancel.clicked.connect(dialog.reject)
        layout.addWidget(cancel)
        return dialog, records, subjects, denominator

    def _safe_source_name(self, source: DatasetHandle) -> str:
        return (
            "".join(
                character if character.isalnum() or character in {"-", "_"} else "_"
                for character in source.metadata.name
            ).strip("_")
            or "dataset"
        ).lower()

    def _parent_widget(self) -> QWidget | None:
        parent = self.parent()
        return parent if isinstance(parent, QWidget) else None
