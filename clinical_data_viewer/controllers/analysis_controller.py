from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
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

from ..ae_table import (
    AeTableConfig,
    AeTableDenominator,
    AeTableEngine,
    AeTableLongResultBuilder,
    build_ae_table_configuration,
)
from ..ae_table.drilldown import (
    build_cell_filter as build_ae_cell_filter,
)
from ..ae_table.drilldown import (
    lookup_cell as lookup_ae_cell,
)
from ..categorical import (
    CategoricalConfig,
    CategoricalEngine,
    CategoricalLongResultBuilder,
    DenominatorConfig,
)
from ..categorical.drilldown import (
    CategoricalQueryBuilder,
    build_cell_filter,
    build_n1_cell_filter,
    lookup_cell,
)
from ..codegen import build_proc_means_configuration
from ..codegen.r import RProcMeansGenerator
from ..codegen.sas import (
    SasAeTableGenerator,
    SasListingGenerator,
    SasProcMeansGenerator,
    SasRuleBasedGenerator,
)
from ..data_store import DataStore
from ..dataset_utils import is_analysis_dataset
from ..domain import DatasetHandle, DatasetMetadata
from ..filter_engine import FilterEngine
from ..listing import ListingConfig, ListingEngine
from ..listing.configuration import build_listing_configuration
from ..proc_means import (
    ProcMeansConfig,
    ProcMeansEngine,
    ProcMeansQueryBuilder,
    build_drilldown_filter,
    build_drilldown_where_text,
)
from ..rule_based import (
    RuleBasedConfig,
    RuleBasedDenominator,
    RuleBasedEngine,
    RuleBasedLongResultBuilder,
    RuleBasedRow,
    build_rule_based_configuration,
)
from ..rule_based.drilldown import (
    build_cell_filter as build_rule_cell_filter,
)
from ..rule_based.drilldown import (
    build_population_cell_filter,
)
from ..rule_based.drilldown import (
    lookup_cell as lookup_rule_cell,
)
from ..settings import PROC_MEANS_STATISTICS, AppSettings
from ..temp_manager import TempManager
from ..ui.ae_table_builder import AeTableBuilderSelection
from ..ui.analysis_panel import AnalysisPanel
from ..ui.categorical_builder import CategoricalBuilderSelection
from ..ui.dataset_tab import DatasetTab
from ..ui.listing_builder import ListingBuilderSelection
from ..ui.proc_means_builder import ProcMeansBuilderSelection
from ..ui.rule_based_builder import RuleBasedBuilderSelection
from ..ui.sas_code_dialog import RCodeDialog, SasCodeDialog
from ..workers import Worker


@dataclass(frozen=True, slots=True)
class ListingResultContext:
    """Input handles retained while a Listing Result tab remains open."""

    source: DatasetHandle
    adsl: DatasetHandle | None
    config: ListingConfig


@dataclass(frozen=True, slots=True)
class RuleBasedResultContext:
    """Input handles retained while a Rule-based result tab remains open."""

    source: DatasetHandle
    population: DatasetHandle | None
    config: RuleBasedConfig


@dataclass(frozen=True, slots=True)
class AeTableResultContext:
    """Input handles retained while an AE Table result tab remains open."""

    source: DatasetHandle
    population: DatasetHandle | None
    config: AeTableConfig


@dataclass(frozen=True, slots=True)
class ProcMeansResultContext:
    """Source handle retained while a PROC MEANS result tab remains open."""

    source: DatasetHandle
    config: ProcMeansConfig


@dataclass(frozen=True, slots=True)
class CategoricalResultContext:
    """Input handles retained while a Categorical Table result tab remains open."""

    source: DatasetHandle
    population: DatasetHandle | None
    config: CategoricalConfig


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
        self._listing_engine = ListingEngine(temp_manager)
        self._sas_listing_generator = SasListingGenerator()
        self._rule_based_engine = RuleBasedEngine(temp_manager)
        self._rule_based_long_result_builder = RuleBasedLongResultBuilder(temp_manager)
        self._rule_based_query_builder = CategoricalQueryBuilder(temp_manager)
        self._sas_rule_based_generator = SasRuleBasedGenerator()
        self._ae_table_engine = AeTableEngine(temp_manager)
        self._ae_table_long_result_builder = AeTableLongResultBuilder(temp_manager)
        self._ae_table_query_builder = CategoricalQueryBuilder(temp_manager)
        self._sas_ae_table_generator = SasAeTableGenerator()
        self._proc_means_engine = ProcMeansEngine(temp_manager)
        self._proc_means_query_builder = ProcMeansQueryBuilder(temp_manager)
        self._sas_proc_means_generator = SasProcMeansGenerator()
        self._r_proc_means_generator = RProcMeansGenerator()
        self._store = DataStore()
        self._categorical_engine = CategoricalEngine(temp_manager)
        self._categorical_long_result_builder = CategoricalLongResultBuilder(
            temp_manager
        )
        self._categorical_query_builder = CategoricalQueryBuilder(temp_manager)
        self._listing_source: DatasetTab | None = None
        self._listing_input_tabs: set[DatasetTab] = set()
        self._listing_results: dict[DatasetTab, ListingResultContext] = {}
        self._rule_based_source: DatasetTab | None = None
        self._rule_based_input_tabs: set[DatasetTab] = set()
        self._rule_based_results: dict[DatasetTab, RuleBasedResultContext] = {}
        self._ae_table_source: DatasetTab | None = None
        self._ae_table_input_tabs: set[DatasetTab] = set()
        self._ae_table_results: dict[DatasetTab, AeTableResultContext] = {}
        self._proc_means_source: DatasetTab | None = None
        self._proc_means_input_tabs: set[DatasetTab] = set()
        self._proc_means_results: dict[DatasetTab, ProcMeansResultContext] = {}
        self._categorical_source: DatasetTab | None = None
        self._categorical_input_tabs: set[DatasetTab] = set()
        self._categorical_results: dict[DatasetTab, CategoricalResultContext] = {}

        builder = self._panel.listing_builder
        builder.run_requested.connect(self.run_listing)
        builder.sas_code_requested.connect(self.generate_listing_sas_code)
        builder.browse_adsl_requested.connect(host.browse_listing_adsl_dataset)
        builder.cleared.connect(self.clear_listing_source)

        rule_builder = self._panel.rule_based_builder
        rule_builder.run_requested.connect(self.run_rule_based)
        rule_builder.sas_code_requested.connect(self.generate_rule_based_sas_code)
        rule_builder.validation_error.connect(
            lambda message: QMessageBox.warning(
                self._parent_widget(), "Rule-based Table", message
            )
        )
        rule_builder.browse_adsl_requested.connect(host.browse_rule_based_adsl_dataset)
        rule_builder.cleared.connect(self.clear_rule_based_source)

        ae_builder = self._panel.ae_table_builder
        ae_builder.run_requested.connect(self.run_ae_table)
        ae_builder.sas_code_requested.connect(self.generate_ae_table_sas_code)
        ae_builder.validation_error.connect(
            lambda message: QMessageBox.warning(self._parent_widget(), "AE Table", message)
        )
        ae_builder.browse_adsl_requested.connect(host.browse_ae_table_adsl_dataset)
        ae_builder.cleared.connect(self.clear_ae_table_source)

        proc_builder = self._panel.builder
        proc_builder.run_requested.connect(self.run_proc_means_builder)
        proc_builder.sas_code_requested.connect(self.generate_proc_means_sas_code)
        proc_builder.r_code_requested.connect(self.generate_proc_means_r_code)
        proc_builder.validation_error.connect(
            lambda message: QMessageBox.warning(
                self._parent_widget(), "PROC MEANS Builder", message
            )
        )
        proc_builder.cleared.connect(self.clear_proc_means_source)

        categorical_builder = self._panel.categorical_builder
        categorical_builder.run_requested.connect(self.run_categorical)
        categorical_builder.validation_error.connect(
            lambda message: QMessageBox.warning(
                self._parent_widget(), "Categorical Table", message
            )
        )
        categorical_builder.browse_adsl_requested.connect(
            host.browse_categorical_adsl_dataset
        )
        categorical_builder.cleared.connect(self.clear_categorical_source)

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

    def tab_close_blocker(self, tab: object) -> TabCloseBlocker | None:
        if tab in self._listing_input_tabs:
            return TabCloseBlocker(
                "Listing Running",
                "This dataset is currently used by a Listing. Wait for the calculation to finish before closing it.",
            )
        if tab in self._rule_based_input_tabs:
            return TabCloseBlocker(
                "Rule-based Table Running",
                "This dataset is currently used by a Rule-based Table. Wait for the calculation to finish before closing it.",
            )
        if tab in self._ae_table_input_tabs:
            return TabCloseBlocker(
                "AE Table Running",
                "This dataset is currently used by an AE Table. Wait for the calculation to finish before closing it.",
            )
        if tab in self._proc_means_input_tabs:
            return TabCloseBlocker(
                "PROC MEANS Running",
                "This dataset is currently being analyzed. Wait for PROC MEANS to finish before closing it.",
            )
        if tab in self._categorical_input_tabs:
            return TabCloseBlocker(
                "Categorical Table Running",
                "This dataset is currently used by a Categorical Table. Wait for the calculation to finish before closing it.",
            )
        return None

    def take_result_release_paths(self, tab: object) -> tuple[Path, ...]:
        """Detach a Listing Result and return source directories to release."""
        context = self._listing_results.pop(tab, None)
        if context is None:
            rule_context = self._rule_based_results.pop(tab, None)
            if rule_context is not None:
                paths = {rule_context.source.temporary_path.parent}
                if rule_context.population is not None:
                    paths.add(rule_context.population.temporary_path.parent)
                return tuple(paths)
            ae_context = self._ae_table_results.pop(tab, None)
            if ae_context is not None:
                paths = {ae_context.source.temporary_path.parent}
                if ae_context.population is not None:
                    paths.add(ae_context.population.temporary_path.parent)
                return tuple(paths)
            proc_context = self._proc_means_results.pop(tab, None)
            if proc_context is not None:
                return (proc_context.source.temporary_path.parent,)
            categorical_context = self._categorical_results.pop(tab, None)
            if categorical_context is None:
                return ()
            paths = {categorical_context.source.temporary_path.parent}
            if categorical_context.population is not None:
                paths.add(categorical_context.population.temporary_path.parent)
            return tuple(paths)
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

    @property
    def categorical_source(self) -> DatasetTab | None:
        """Return Categorical Builder's fixed source while its tab remains open."""
        if self._categorical_source is None:
            return None
        if not self._host.is_open_dataset_tab(self._categorical_source):
            return None
        return self._categorical_source

    def refresh_categorical_adsl_sources(
        self, datasets: Iterable[tuple[DatasetTab, str]] | None = None
    ) -> None:
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

    @property
    def ae_table_source(self) -> DatasetTab | None:
        """Return AE Table Builder's fixed source while its tab remains open."""
        if self._ae_table_source is None:
            return None
        if not self._host.is_open_dataset_tab(self._ae_table_source):
            return None
        return self._ae_table_source

    def refresh_ae_table_adsl_sources(
        self, datasets: Iterable[tuple[DatasetTab, str]] | None = None
    ) -> None:
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
        current_filter = source_tab.current_where_text()
        builder_filter = builder.current_filter_text()
        if not builder_filter or builder_filter != current_filter:
            response = QMessageBox.question(
                self._parent_widget(),
                "PROC MEANS Builder",
                "Apply the current dataset filter to PROC MEANS?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if response == QMessageBox.Yes:
                builder.apply_current_filter(current_filter)
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
