from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QStyle,
    QTabWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
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
from ..codegen.sas import SasProcMeansGenerator
from ..compare_engine import DatasetComparer, recommend_group_variables
from ..csv_exporter import CsvExporter
from ..data_store import DataStore
from ..domain import CacheProgress, DatasetHandle
from ..filter_engine import FilterEngine
from ..filter_history import FilterHistory
from ..proc_means import (
    ProcMeansConfig,
    ProcMeansEngine,
    ProcMeansQueryBuilder,
    build_drilldown_filter,
    build_drilldown_where_text,
)
from ..sas_reader import SasDatasetReader
from ..settings import PROC_MEANS_STATISTICS, AppSettings
from ..statistics import calculate_statistics
from ..temp_manager import TempManager
from ..workers import Worker
from .analysis_panel import AnalysisPanel
from .categorical_builder import CategoricalBuilderSelection
from .column_filter_dialog import ColumnFilterDialog
from .dataset_compare_panel import DatasetComparePanel
from .dataset_tab import DatasetTab
from .history_dialog import HistoryDialog
from .sas_code_dialog import RCodeDialog, SasCodeDialog
from .settings_dialog import SettingsDialog
from .variables_panel import VariablesPanel


class LoadingPage(QWidget):
    def __init__(self, source_path: Path, parent=None) -> None:
        super().__init__(parent)
        self.source_path = source_path
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        self.label = QLabel(f"Opening {source_path.name}…")
        self.label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setMaximumWidth(420)
        layout.addWidget(self.progress, alignment=Qt.AlignCenter)
        layout.addStretch(1)


@dataclass(frozen=True, slots=True)
class ProcMeansResultContext:
    source: DatasetHandle
    config: ProcMeansConfig


@dataclass(frozen=True, slots=True)
class CategoricalResultContext:
    source: DatasetHandle
    population: DatasetHandle | None
    config: CategoricalConfig


class MainWindow(QMainWindow):
    def __init__(
        self,
        settings: AppSettings,
        temp_manager: TempManager,
        history: FilterHistory,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.temp_manager = temp_manager
        self.history = history
        self.reader = SasDatasetReader(temp_manager)
        self.comparer = DatasetComparer(temp_manager)
        self.proc_means_engine = ProcMeansEngine(temp_manager)
        self.categorical_engine = CategoricalEngine(temp_manager)
        self.categorical_long_result_builder = CategoricalLongResultBuilder(temp_manager)
        self.categorical_query_builder = CategoricalQueryBuilder(temp_manager)
        self.proc_means_query_builder = ProcMeansQueryBuilder(temp_manager)
        self.sas_proc_means_generator = SasProcMeansGenerator()
        self.r_proc_means_generator = RProcMeansGenerator()
        self.store = DataStore()
        self.exporter = CsvExporter()
        self.pool = QThreadPool.globalInstance()
        self._workers: dict[QWidget, set[Worker]] = {}
        self._pending_removals: dict[QWidget, list[Path]] = {}
        self._last_statistics_request: tuple[DatasetTab, str] | None = None
        self._statistics_owner: DatasetTab | None = None
        self._comparison_owner: DatasetTab | None = None
        self._compare_input_tabs: set[DatasetTab] = set()
        self._proc_means_input_tabs: set[DatasetTab] = set()
        self._categorical_input_tabs: set[DatasetTab] = set()
        self._proc_means_sources: dict[DatasetTab, ProcMeansResultContext] = {}
        self._categorical_sources: dict[DatasetTab, CategoricalResultContext] = {}
        self._retained_directories: dict[Path, int] = {}
        self._deferred_directory_removals: set[Path] = set()
        self._pending_directory_releases: dict[QWidget, list[Path]] = {}
        self._compare_sources: dict[DatasetTab, dict[str, tuple[DatasetTab, Path]]] = {}
        self._recommendation_generation = 0
        self.setWindowTitle("SASDataViewer")
        self.resize(1280, 790)
        self.setMinimumSize(850, 560)
        self._create_actions()
        self._create_menu()
        self._create_toolbar()
        self._create_center()
        self._create_variables_panel()
        self._create_analysis_panel()
        self._create_compare_panel()
        self._create_status_bar()
        self._sync_active_tab()

    def _create_actions(self) -> None:
        style = self.style()
        self.open_action = QAction(
            style.standardIcon(QStyle.SP_DialogOpenButton), "Open", self
        )
        self.open_action.setShortcut(QKeySequence.Open)
        self.open_action.triggered.connect(self.open_files)
        self.reload_action = QAction(
            style.standardIcon(QStyle.SP_BrowserReload), "Reload", self
        )
        self.reload_action.setShortcut(QKeySequence.Refresh)
        self.reload_action.triggered.connect(self.reload_current)
        self.export_action = QAction(
            style.standardIcon(QStyle.SP_DialogSaveButton), "Export CSV", self
        )
        self.export_action.setShortcut("Ctrl+Shift+S")
        self.export_action.triggered.connect(self.export_current)
        self.clear_action = QAction(
            style.standardIcon(QStyle.SP_DialogResetButton), "Clear Filter", self
        )
        self.clear_action.setShortcut("Ctrl+Shift+L")
        self.clear_action.triggered.connect(self.clear_current_filter)
        self.history_action = QAction(
            style.standardIcon(QStyle.SP_FileDialogDetailedView), "Filter History", self
        )
        self.history_action.triggered.connect(self.show_history)
        self.close_tab_action = QAction("Close Dataset", self)
        self.close_tab_action.setShortcut(QKeySequence.Close)
        self.close_tab_action.triggered.connect(
            lambda: self.close_tab(self.tabs.currentIndex())
        )
        self.exit_action = QAction("Exit", self)
        self.exit_action.triggered.connect(self.close)
        self.variables_action = QAction("Variables Panel", self)
        self.variables_action.setCheckable(True)
        self.variables_action.setChecked(True)
        self.open_categorical_long_action = QAction(
            "Open Categorical Long Result", self
        )
        self.open_categorical_long_action.triggered.connect(
            self._open_categorical_long_result
        )
        self.analysis_action = QAction("Analysis", self)
        self.analysis_action.setCheckable(True)
        self.analysis_action.setChecked(False)
        self.proc_means_builder_action = QAction("PROC MEANS Builder", self)
        self.proc_means_builder_action.triggered.connect(self.show_proc_means_builder)
        self.categorical_builder_action = QAction("Categorical Table Builder", self)
        self.categorical_builder_action.triggered.connect(self.show_categorical_builder)
        self.compare_datasets_action = QAction("Compare Datasets", self)
        self.compare_datasets_action.setCheckable(True)
        self.compare_datasets_action.setChecked(False)
        self.settings_action = QAction("Settings…", self)
        self.settings_action.triggered.connect(self.show_settings)

    def _create_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction(self.open_action)
        file_menu.addAction(self.reload_action)
        file_menu.addAction(self.export_action)
        file_menu.addSeparator()
        file_menu.addAction(self.close_tab_action)
        file_menu.addAction(self.exit_action)
        edit_menu = self.menuBar().addMenu("&Edit")
        edit_menu.addAction(self.clear_action)
        edit_menu.addAction(self.history_action)
        view_menu = self.menuBar().addMenu("&View")
        view_menu.addAction(self.variables_action)
        view_menu.addAction(self.open_categorical_long_action)
        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addAction(self.analysis_action)
        tools_menu.addAction(self.proc_means_builder_action)
        tools_menu.addAction(self.categorical_builder_action)
        tools_menu.addAction(self.compare_datasets_action)
        tools_menu.addSeparator()
        tools_menu.addAction(self.settings_action)
        help_menu = self.menuBar().addMenu("&Help")
        about = QAction("About SASDataViewer", self)
        about.triggered.connect(
            lambda: QMessageBox.about(
                self,
                "About SASDataViewer",
                "SASDataViewer 0.1.0\nRead-only clinical SAS dataset browser.",
            )
        )
        help_menu.addAction(about)

    def _create_toolbar(self) -> None:
        toolbar = QToolBar("Main")
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toolbar.addAction(self.open_action)
        toolbar.addSeparator()
        toolbar.addAction(self.reload_action)
        toolbar.addSeparator()
        toolbar.addAction(self.export_action)
        toolbar.addSeparator()
        toolbar.addAction(self.clear_action)
        toolbar.addSeparator()
        toolbar.addAction(self.history_action)
        expanding = QWidget()
        expanding.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        toolbar.addWidget(expanding)
        self.variable_search = QLineEdit()
        self.variable_search.setPlaceholderText("Search Variable")
        self.variable_search.setClearButtonEnabled(True)
        self.variable_search.setMaximumWidth(220)
        toolbar.addWidget(self.variable_search)
        self.addToolBar(toolbar)

    def _create_center(self) -> None:
        self.tabs = QTabWidget()
        self.tabs.setTabsClosable(True)
        self.tabs.setMovable(True)
        self.tabs.setDocumentMode(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._sync_active_tab)
        plus = QToolButton()
        plus.setText("+")
        plus.setToolTip("Open dataset")
        plus.clicked.connect(self.open_files)
        self.tabs.setCornerWidget(plus, Qt.TopRightCorner)
        self.setCentralWidget(self.tabs)

    def _create_variables_panel(self) -> None:
        self.variables_panel = VariablesPanel()
        self.variables_dock = QDockWidget("Variables", self)
        self.variables_dock.setObjectName("variablesDock")
        self.variables_dock.setAllowedAreas(
            Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea
        )
        self.variables_dock.setFeatures(QDockWidget.DockWidgetMovable)
        self.variables_dock.setTitleBarWidget(QWidget())
        self.variables_dock.setWidget(self.variables_panel)
        self.variables_dock.setMinimumWidth(230)
        self.variables_dock.resize(285, self.height())
        self.addDockWidget(Qt.LeftDockWidgetArea, self.variables_dock)
        self.variables_action.toggled.connect(self.variables_dock.setVisible)
        self.variables_dock.visibilityChanged.connect(self.variables_action.setChecked)
        self.variables_panel.collapse_requested.connect(
            lambda: self.variables_dock.setVisible(False)
        )
        self.variables_panel.visibility_changed.connect(self._visible_columns_changed)
        self.variables_panel.variable_activated.connect(self._locate_variable)
        self.variable_search.textChanged.connect(self.variables_panel.set_search)
        self.variables_panel.search.textChanged.connect(self._sync_top_search)

    def _create_analysis_panel(self) -> None:
        self.analysis_panel = AnalysisPanel()
        self.analysis_panel.builder.set_default_statistics(
            self.settings.proc_means_statistics
        )
        self.analysis_dock = QDockWidget("Analysis", self)
        self.analysis_dock.setObjectName("analysisDock")
        self.analysis_dock.setAllowedAreas(Qt.RightDockWidgetArea)
        self.analysis_dock.setWidget(self.analysis_panel)
        self.analysis_dock.setMinimumWidth(310)
        self.addDockWidget(Qt.RightDockWidgetArea, self.analysis_dock)
        self.analysis_dock.hide()
        self.analysis_action.toggled.connect(self.analysis_dock.setVisible)
        self.analysis_dock.visibilityChanged.connect(self.analysis_action.setChecked)
        self.analysis_panel.locate_variable_requested.connect(self._locate_variable)
        self.analysis_panel.settings_requested.connect(self.show_settings)
        self.analysis_panel.recalculate_requested.connect(self._recalculate_statistics)
        self.analysis_panel.clear_comparison_requested.connect(
            self._clear_row_comparison
        )
        self.analysis_panel.all_tabs_closed.connect(self.analysis_dock.hide)
        self.analysis_panel.builder.run_requested.connect(self._run_proc_means_builder)
        self.analysis_panel.builder.sas_code_requested.connect(
            self._generate_proc_means_sas_code
        )
        self.analysis_panel.builder.r_code_requested.connect(
            self._generate_proc_means_r_code
        )
        self.analysis_panel.builder.validation_error.connect(
            lambda message: QMessageBox.warning(self, "PROC MEANS Builder", message)
        )
        self.analysis_panel.builder.settings_requested.connect(self.show_settings)
        self.analysis_panel.categorical_builder.run_requested.connect(
            self._run_categorical_builder
        )
        self.analysis_panel.categorical_builder.validation_error.connect(
            lambda message: QMessageBox.warning(self, "Categorical Table", message)
        )
        self.analysis_panel.categorical_builder.browse_adsl_requested.connect(
            self._browse_categorical_adsl
        )

    def _create_compare_panel(self) -> None:
        self.compare_panel = DatasetComparePanel()
        self.compare_dock = QDockWidget("Dataset Compare", self)
        self.compare_dock.setObjectName("datasetCompareDock")
        self.compare_dock.setAllowedAreas(Qt.RightDockWidgetArea)
        self.compare_dock.setWidget(self.compare_panel)
        self.compare_dock.setMinimumWidth(430)
        self.addDockWidget(Qt.RightDockWidgetArea, self.compare_dock)
        self.compare_dock.hide()
        self.compare_datasets_action.toggled.connect(self.compare_dock.setVisible)
        self.compare_dock.visibilityChanged.connect(
            self.compare_datasets_action.setChecked
        )
        self.compare_panel.browse_requested.connect(self._browse_compare_dataset)
        self.compare_panel.compare_requested.connect(self._run_dataset_compare)
        self.compare_panel.recommendation_requested.connect(
            self._recommend_compare_groups
        )
        self.compare_panel.advanced_toggled.connect(self._set_compare_advanced)

    def _sync_top_search(self, text: str) -> None:
        if self.variable_search.text() == text:
            return
        self.variable_search.blockSignals(True)
        self.variable_search.setText(text)
        self.variable_search.blockSignals(False)

    def _create_status_bar(self) -> None:
        self.rows_status = QLabel("Rows: —")
        self.columns_status = QLabel("Columns: —")
        self.filter_status = QLabel("")
        self.source_status = QLabel("Source: —")
        for label in (
            self.rows_status,
            self.columns_status,
            self.filter_status,
            self.source_status,
        ):
            label.setObjectName("statusSegment")
            self.statusBar().addWidget(label)
        self.source_status.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.task_status = QLabel("")
        self.statusBar().addPermanentWidget(self.task_status, 1)

    def current_dataset_tab(self) -> DatasetTab | None:
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, DatasetTab) else None

    def open_files(self) -> None:
        initial = self.settings.last_open_directory or str(Path.home())
        files, _filter = QFileDialog.getOpenFileNames(
            self,
            "Open SAS Dataset",
            initial,
            "SAS datasets (*.sas7bdat *.xpt);;All files (*)",
        )
        if not files:
            return
        self.settings.last_open_directory = str(Path(files[0]).parent)
        self.settings.save()
        self.open_paths(Path(value) for value in files)

    def open_paths(self, paths: Iterable[Path]) -> None:
        for source_path in paths:
            self._open_path(Path(source_path))

    def _open_path(self, source_path: Path, when_ready=None) -> None:
        loading = LoadingPage(source_path)
        index = self.tabs.addTab(loading, source_path.name)
        self.tabs.setCurrentIndex(index)

        def completed(handle: DatasetHandle) -> None:
            current_index = self.tabs.indexOf(loading)
            if current_index < 0:
                self._remove_dataset_directory(handle.temporary_path.parent)
                return
            tab = self._make_dataset_tab(handle)
            self.tabs.removeTab(current_index)
            self.tabs.insertTab(current_index, tab, source_path.name)
            self.tabs.setCurrentIndex(current_index)
            tab.start()
            if not handle.cache_complete:
                self._continue_cache(
                    tab,
                    (lambda _handle: when_ready(tab)) if when_ready else None,
                )
            elif when_ready:
                when_ready(tab)
            self._refresh_compare_datasets()
            self._refresh_categorical_sources()

        def failed(message: str, details: str) -> None:
            loading.progress.setRange(0, 1)
            loading.label.setText(f"Could not open {source_path.name}")
            self._show_error("Open Dataset Failed", message, details)

        self._submit(
            loading,
            lambda worker: self.reader.load_initial(source_path, worker.report),
            completed,
            failed,
        )

    def _browse_compare_dataset(self, side: str) -> None:
        initial = self.settings.last_open_directory or str(Path.home())
        filename, _filter = QFileDialog.getOpenFileName(
            self,
            f"Choose {side.title()} SAS Dataset",
            initial,
            "SAS datasets (*.sas7bdat *.xpt);;All files (*)",
        )
        if not filename:
            return
        source = Path(filename)
        self.settings.last_open_directory = str(source.parent)
        self.settings.save()

        def ready(tab: DatasetTab) -> None:
            self._refresh_compare_datasets()
            self._refresh_categorical_sources()
            self.compare_panel.select_dataset(side, tab)

        self._open_path(source, ready)

    def _refresh_compare_datasets(self) -> None:
        if not hasattr(self, "compare_panel"):
            return
        datasets: list[tuple[object, str, bool]] = []
        for index in range(self.tabs.count()):
            tab = self.tabs.widget(index)
            if isinstance(tab, DatasetTab) and tab.handle.kind == "sas":
                datasets.append(
                    (
                        tab,
                        f"{tab.handle.source_path.name} — {tab.handle.source_path.parent}",
                        tab.cache_complete,
                    )
                )
        self.compare_panel.set_datasets(datasets)

    def _refresh_categorical_sources(self) -> None:
        if not hasattr(self, "analysis_panel"):
            return
        datasets: list[tuple[object, str]] = []
        for index in range(self.tabs.count()):
            tab = self.tabs.widget(index)
            if isinstance(tab, DatasetTab) and tab.handle.kind == "sas":
                datasets.append(
                    (
                        tab,
                        f"{tab.handle.metadata.name} — {tab.handle.source_path.name}",
                    )
                )
        self.analysis_panel.categorical_builder.set_adsl_sources(datasets)

    def _browse_categorical_adsl(self) -> None:
        initial = self.settings.last_open_directory or str(Path.home())
        filename, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose ADSL Dataset",
            initial,
            "SAS datasets (*.sas7bdat *.xpt);;All files (*)",
        )
        if not filename:
            return
        source = Path(filename)
        self.settings.last_open_directory = str(source.parent)
        self.settings.save()

        def ready(tab: DatasetTab) -> None:
            self._refresh_categorical_sources()
            self.analysis_panel.categorical_builder.select_adsl(tab)

        self._open_path(source, ready)

    def _run_dataset_compare(
        self, main_tab: DatasetTab, qc_tab: DatasetTab, config
    ) -> None:
        if (
            self.tabs.indexOf(main_tab) < 0
            or self.tabs.indexOf(qc_tab) < 0
            or not main_tab.cache_complete
            or not qc_tab.cache_complete
        ):
            QMessageBox.warning(
                self, "Dataset Compare", "Main and QC must both be fully loaded."
            )
            return
        self.compare_dock.show()
        self.compare_panel.set_busy(True, "Comparing datasets in the background…")
        self._compare_input_tabs = {main_tab, qc_tab}
        main_handle = main_tab.handle
        qc_handle = qc_tab.handle

        def completed(handle: DatasetHandle) -> None:
            self._compare_input_tabs.clear()
            self.compare_panel.set_busy(
                False, f"Created {handle.metadata.row_count:,} result rows."
            )
            tab = self._make_dataset_tab(handle)
            tab.set_advanced_visible(self.compare_panel.advanced.isChecked())
            self._compare_sources[tab] = {
                "Main": (main_tab, main_handle.database_path),
                "QC": (qc_tab, qc_handle.database_path),
            }
            title = (
                f"Compare Result: {main_handle.metadata.name} "
                f"vs {qc_handle.metadata.name}"
            )
            index = self.tabs.addTab(tab, title)
            self.tabs.setCurrentIndex(index)
            tab.start()

        def failed(message: str, details: str) -> None:
            self._compare_input_tabs.clear()
            self.compare_panel.set_busy(False, "Comparison failed.")
            self._show_error("Dataset Compare Failed", message, details)

        self._submit(
            self.compare_panel,
            lambda worker: self.comparer.compare(
                main_handle, qc_handle, config, worker.report
            ),
            completed,
            failed,
        )

    def _recommend_compare_groups(
        self, main_tab: DatasetTab, qc_tab: DatasetTab
    ) -> None:
        if (
            self.tabs.indexOf(main_tab) < 0
            or self.tabs.indexOf(qc_tab) < 0
            or not main_tab.cache_complete
            or not qc_tab.cache_complete
        ):
            return
        self._recommendation_generation += 1
        generation = self._recommendation_generation
        main_handle = main_tab.handle
        qc_handle = qc_tab.handle

        def completed(variables: tuple[str, ...]) -> None:
            if generation != self._recommendation_generation:
                return
            self.compare_panel.apply_group_recommendation(main_tab, qc_tab, variables)

        self._submit(
            self.compare_panel,
            lambda _worker: recommend_group_variables(main_handle, qc_handle, limit=3),
            completed,
            lambda message, details: self._show_error(
                "Group Recommendation Failed", message, details
            ),
        )

    def _set_compare_advanced(self, checked: bool) -> None:
        tab = self.current_dataset_tab()
        if tab is None or tab.handle.kind != "compare":
            return
        tab.set_advanced_visible(checked)
        self.variables_panel.set_dataset(
            tab.handle.metadata, tab.visible_columns, tab.available_columns()
        )
        self._refresh_status(tab)
        self._sync_dataset_actions(tab)

    def _make_dataset_tab(self, handle: DatasetHandle) -> DatasetTab:
        tab = DatasetTab(handle, self.settings.page_size)
        tab.page_requested.connect(
            lambda generation, offset, limit, owner=tab: self._load_page(
                owner, generation, offset, limit
            )
        )
        tab.apply_requested.connect(
            lambda text, owner=tab: self._apply_filter(owner, text)
        )
        tab.clear_requested.connect(lambda owner=tab: self._clear_filter(owner))
        tab.history_requested.connect(lambda owner=tab: self.show_history(owner))
        tab.sort_changed.connect(lambda _sort, owner=tab: self._refresh_status(owner))
        tab.find_requested.connect(
            lambda text, forward, start, generation, owner=tab: self._find_text(
                owner, text, forward, start, generation
            )
        )
        tab.column_filter_requested.connect(
            lambda variable, owner=tab: self._show_column_filter(owner, variable)
        )
        tab.proc_means_requested.connect(
            lambda variable, owner=tab: self._run_proc_means(owner, variable)
        )
        tab.settings_requested.connect(self.show_settings)
        tab.compare_rows_requested.connect(
            lambda rows, owner=tab: self._compare_rows(owner, rows)
        )
        tab.clear_comparison_requested.connect(self._clear_row_comparison)
        tab.analysis_invalidated.connect(
            lambda owner=tab: self._analysis_invalidated(owner)
        )
        tab.comparison_invalidated.connect(
            lambda owner=tab: self._comparison_invalidated(owner)
        )
        tab.source_navigation_requested.connect(
            lambda row, variable, owner=tab: self._navigate_compare_source(
                owner, row, variable
            )
        )
        tab.proc_means_drilldown_requested.connect(
            lambda row, column, display, owner=tab: self._drilldown_proc_means(
                owner, row, column, display
            )
        )
        tab.categorical_drilldown_requested.connect(
            lambda row, column, display, owner=tab: self._drilldown_categorical(
                owner, row, column, display
            )
        )
        return tab

    def _continue_cache(self, tab: DatasetTab, when_complete=None) -> None:
        initial_handle = tab.handle

        def progress_changed(progress: CacheProgress) -> None:
            if self.tabs.indexOf(tab) < 0:
                return
            metadata = replace(
                tab.handle.metadata,
                row_count=max(tab.handle.metadata.row_count, progress.total_rows),
            )
            tab.handle = replace(
                tab.handle,
                metadata=metadata,
                cached_row_count=progress.cached_rows,
                cache_complete=progress.complete,
            )
            tab.set_cache_state(
                progress.cached_rows, progress.total_rows, progress.complete
            )
            self._refresh_status(tab)

        def completed(handle: DatasetHandle) -> None:
            if self.tabs.indexOf(tab) < 0:
                return
            tab.handle = handle
            tab.set_cache_state(
                handle.cached_row_count, handle.metadata.row_count, True
            )
            self._sync_active_tab()
            self._refresh_status(tab)
            self._refresh_compare_datasets()
            if when_complete is not None:
                when_complete(handle)

        def failed(message: str, details: str) -> None:
            if self.tabs.indexOf(tab) < 0:
                return
            tab.cache_notice.setVisible(True)
            tab.cache_notice.setText(
                "Background caching stopped. Reload the dataset to try again."
            )
            tab.cache_failed = True
            self._sync_active_tab()
            self._show_error("Dataset Cache Failed", message, details)

        self._submit(
            tab,
            lambda worker: self.reader.continue_cache(
                initial_handle, worker.report, worker.report_data
            ),
            completed,
            failed,
            progress_data=progress_changed,
        )

    def _load_page(
        self, tab: DatasetTab, generation: int, offset: int, limit: int
    ) -> None:
        if self.tabs.indexOf(tab) < 0 or generation != tab.generation:
            return
        handle = tab.handle
        columns = tuple(tab.visible_columns)
        compiled = tab.compiled_filter
        sort = tab.model.sort_spec
        known_count = (
            None if offset == 0 and tab.recount_next else tab.model.filtered_count
        )

        def query(_worker: Worker):
            return self.store.query_page(
                handle.database_path,
                handle.metadata,
                columns,
                compiled,
                sort,
                offset,
                limit,
                known_count,
            )

        def completed(page) -> None:
            if self.tabs.indexOf(tab) < 0 or generation != tab.generation:
                return
            tab.recount_next = False
            displayed_count = page.filtered_count
            if not compiled.sql and sort is None:
                displayed_count = max(
                    displayed_count,
                    tab.handle.metadata.row_count
                    if tab.cache_complete
                    else tab.handle.cached_row_count,
                )
            tab.model.set_page(
                offset,
                page.rows,
                displayed_count,
                page.cell_highlights,
                page.row_warnings,
                page.row_decimal_bases,
                page.source_rows,
            )
            if offset == 0 and tab.pending_history_text:
                self.history.add(tab.handle.source_path, tab.pending_history_text)
                tab.pending_history_text = ""
            self._refresh_status(tab)

        def failed(message: str, details: str) -> None:
            if generation == tab.generation:
                tab.model.load_failed(offset)
            self._show_error("Dataset Query Failed", message, details)

        self._submit(tab, query, completed, failed)

    def _find_text(
        self,
        tab: DatasetTab,
        text: str,
        forward: bool,
        start_row: int,
        generation: int,
    ) -> None:
        if (
            self.tabs.indexOf(tab) < 0
            or generation != tab.generation
            or not tab.cache_complete
            or not tab.visible_columns
        ):
            return
        handle = tab.handle
        columns = tuple(tab.visible_columns)
        compiled = tab.compiled_filter
        sort = tab.model.sort_spec

        def completed(result) -> None:
            if self.tabs.indexOf(tab) >= 0 and generation == tab.generation:
                tab.show_find_result(result)

        self._submit(
            tab,
            lambda _worker: self.store.find_text(
                handle.database_path,
                handle.metadata,
                columns,
                compiled,
                sort,
                text,
                start_row,
                forward=forward,
            ),
            completed,
            lambda message, details: self._show_error("Find Failed", message, details),
        )

    def _navigate_compare_source(
        self, compare_tab: DatasetTab, view_row: int, variable_name: str
    ) -> None:
        sources = self._compare_sources.get(compare_tab)
        if sources is None or self.tabs.indexOf(compare_tab) < 0:
            QMessageBox.information(
                self,
                "Source Navigation",
                "The source dataset link is no longer available for this result.",
            )
            return
        generation = compare_tab.generation

        def completed(target) -> None:
            if (
                target is None
                or generation != compare_tab.generation
                or self.tabs.indexOf(compare_tab) < 0
            ):
                return
            side, source_row = target
            source = sources.get(side)
            if source is None:
                return
            source_tab, original_database = source
            if (
                self.tabs.indexOf(source_tab) < 0
                or source_tab.handle.database_path != original_database
                or source_tab.reload_in_progress
            ):
                QMessageBox.information(
                    self,
                    "Source Navigation",
                    f"The {side} source tab was closed or reloaded after this "
                    "comparison. The viewer will not jump to potentially changed data.",
                )
                return
            source_variable = next(
                (
                    variable.name
                    for variable in source_tab.handle.metadata.variables
                    if variable.name.casefold() == variable_name.casefold()
                ),
                None,
            )
            if source_variable is None:
                QMessageBox.information(
                    self,
                    "Source Navigation",
                    f"{variable_name} does not exist on the {side} side.",
                )
                return
            self._locate_source_observation(
                source_tab, source_row, source_variable, allow_clear_prompt=True
            )

        self._submit(
            compare_tab,
            lambda _worker: self.store.compare_navigation_target(
                compare_tab.handle.database_path,
                compare_tab.handle.metadata,
                compare_tab.compiled_filter,
                compare_tab.model.sort_spec,
                view_row,
            ),
            completed,
            lambda message, details: self._show_error(
                "Source Navigation Failed", message, details
            ),
        )

    def _locate_source_observation(
        self,
        source_tab: DatasetTab,
        source_row: int,
        variable_name: str,
        *,
        allow_clear_prompt: bool,
    ) -> None:
        if self.tabs.indexOf(source_tab) < 0:
            return
        generation = source_tab.generation
        compiled = source_tab.compiled_filter
        sort = source_tab.model.sort_spec

        def completed(view_row: int | None) -> None:
            if self.tabs.indexOf(source_tab) < 0 or generation != source_tab.generation:
                return
            if view_row is None:
                if allow_clear_prompt and compiled.sql:
                    answer = QMessageBox.question(
                        self,
                        "Source Row Is Filtered Out",
                        "The source observation is hidden by the source tab's current "
                        "filter. Clear that filter and locate the observation?",
                    )
                    if answer == QMessageBox.Yes:
                        source_tab.clear_filter()
                        self._refresh_status(source_tab)
                        self._locate_source_observation(
                            source_tab,
                            source_row,
                            variable_name,
                            allow_clear_prompt=False,
                        )
                else:
                    QMessageBox.information(
                        self,
                        "Source Navigation",
                        "The source observation could not be found in the current "
                        "source dataset.",
                    )
                return
            if variable_name not in source_tab.visible_columns:
                source_tab.set_visible_columns(
                    [*source_tab.visible_columns, variable_name]
                )
            self.tabs.setCurrentWidget(source_tab)
            column = source_tab.visible_columns.index(variable_name)
            source_tab.select_position(view_row, column)

        self._submit(
            source_tab,
            lambda _worker: self.store.source_row_view_index(
                source_tab.handle.database_path,
                source_tab.handle.metadata,
                compiled,
                sort,
                source_row,
            ),
            completed,
            lambda message, details: self._show_error(
                "Source Navigation Failed", message, details
            ),
        )

    def _apply_filter(self, tab: DatasetTab, where_text: str) -> None:
        if not tab.cache_complete or not tab.visible_columns:
            return
        if tab.column_filters and not tab.where_editor_is_dirty():
            tab.reapply_current_filter(add_history=True)
            self._refresh_status(tab)
            return
        try:
            compiled = FilterEngine(tab.handle.metadata.variables).compile(where_text)
        except ValueError as error:
            QMessageBox.warning(self, "Invalid WHERE Condition", str(error))
            return
        tab.apply_filter(compiled, where_text, add_history=True)
        self._refresh_status(tab)

    def _clear_filter(self, tab: DatasetTab) -> None:
        if not tab.cache_complete:
            return
        tab.clear_filter()
        self._refresh_status(tab)

    def clear_current_filter(self) -> None:
        tab = self.current_dataset_tab()
        if tab:
            self._clear_filter(tab)

    def _visible_columns_changed(self, columns: list[str]) -> None:
        tab = self.current_dataset_tab()
        if tab:
            tab.set_visible_columns(columns)
            self._refresh_status(tab)
            self._sync_dataset_actions(tab)

    def _locate_variable(self, variable: str) -> None:
        tab = self.current_dataset_tab()
        if tab:
            tab.locate_variable(variable)

    def _show_column_filter(self, tab: DatasetTab, variable_name: str) -> None:
        if self.tabs.indexOf(tab) < 0 or not tab.cache_complete:
            return
        variable = next(
            (
                item
                for item in tab.handle.metadata.variables
                if item.name == variable_name
            ),
            None,
        )
        if variable is None:
            return
        editor_was_dirty = tab.where_editor_is_dirty()
        editor_text = tab.where_editor.toPlainText()
        promoted_filter = None
        if editor_was_dirty:
            try:
                promoted_filter = FilterEngine(tab.handle.metadata.variables).compile(
                    editor_text
                )
            except ValueError as error:
                QMessageBox.warning(self, "Invalid WHERE Condition", str(error))
                return
        generation = tab.generation
        context_filter = (
            promoted_filter
            if promoted_filter is not None
            else tab.filter_context_without(variable_name)
        )
        self.task_status.setText(f"Loading values for {variable_name}…")

        def completed(values) -> None:
            if (
                self.tabs.indexOf(tab) < 0
                or generation != tab.generation
                or tab is not self.current_dataset_tab()
            ):
                return
            if editor_was_dirty and tab.where_editor.toPlainText() != editor_text:
                self.task_status.setText(
                    "WHERE changed while values were loading; reopen the column filter."
                )
                return
            dialog = ColumnFilterDialog(
                variable,
                values,
                tab.column_filters.get(variable_name),
                self,
            )
            if dialog.exec():
                if promoted_filter is not None:
                    tab.apply_filter(promoted_filter, editor_text, add_history=False)
                tab.set_column_filter(variable_name, dialog.result_spec)
                self._refresh_status(tab)

        self._submit(
            tab,
            lambda _worker: self.store.distinct_values(
                tab.handle.database_path,
                tab.handle.metadata,
                variable_name,
                context_filter,
            ),
            completed,
            lambda message, details: self._show_error(
                "Column Filter Failed", message, details
            ),
        )

    def show_settings(self) -> None:
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self.analysis_panel.builder.set_default_statistics(
                self.settings.proc_means_statistics
            )
            if self._last_statistics_request is not None:
                self._recalculate_statistics()

    def show_proc_means_builder(self) -> None:
        self.analysis_panel.show_builder_tab()
        self.analysis_dock.show()

    def show_categorical_builder(self) -> None:
        active_tab = self.current_dataset_tab()
        if (
            active_tab is not None
            and active_tab.handle.kind == "sas"
            and active_tab.cache_complete
        ):
            # Opening the Builder after applying a source-tab WHERE should
            # seed Numerator WHERE.  An explicitly edited Builder filter is
            # preserved by inherit_current_filter().
            self.analysis_panel.categorical_builder.set_dataset(
                active_tab.handle.metadata,
                str(active_tab.handle.source_path),
                active_tab.current_where_text(),
            )
            self.analysis_panel.categorical_builder.inherit_current_filter(
                active_tab.current_where_text()
            )
        self._refresh_categorical_sources()
        self.analysis_panel.show_categorical_tab()
        self.analysis_dock.show()

    def _categorical_builder_context(
        self, selection: CategoricalBuilderSelection
    ) -> tuple[DatasetTab, DatasetTab | None, CategoricalConfig] | None:
        tab = self.current_dataset_tab()
        if tab is None or tab.handle.kind != "sas" or not tab.cache_complete:
            QMessageBox.warning(
                self,
                "Categorical Table",
                "Select a fully loaded SAS source dataset before using the Builder.",
            )
            return None
        population_tab = selection.population_tab
        if selection.denominator_type == "population":
            if not isinstance(population_tab, DatasetTab) or not population_tab.cache_complete:
                QMessageBox.warning(
                    self,
                    "Categorical Table",
                    "Open or browse a fully loaded ADSL dataset for Population N.",
                )
                return None
        try:
            # Keep the Builder's Numerator WHERE independent from the source
            # DatasetTab WHERE.  The selection contains the editable Builder
            # snapshot, so running a table never mutates the source tab.
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
            QMessageBox.warning(self, "Categorical Table", str(error))
            return None
        return tab, population_tab if isinstance(population_tab, DatasetTab) else None, config

    def _run_categorical_builder(self, selection: CategoricalBuilderSelection) -> None:
        active_tab = self.current_dataset_tab()
        if active_tab is None or active_tab.handle.kind != "sas":
            return
        builder = self.analysis_panel.categorical_builder
        current_filter = active_tab.current_where_text()
        builder_filter = builder.current_filter_text()
        # An empty Builder value can still be offered the current source WHERE
        # as a convenience.  A non-empty, different value is an intentional
        # independent Numerator WHERE and must never be silently replaced.
        if not builder_filter and current_filter:
            response = QMessageBox.question(
                self,
                "Categorical Table",
                "Numerator WHERE is empty. Use the current dataset WHERE for this "
                "calculation?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if response == QMessageBox.Yes:
                builder.apply_current_filter(current_filter)
        # The confirmation above can change the Builder value after the
        # selection signal was emitted.  Always build the config from the
        # final, independent Numerator WHERE shown in the Builder.
        selection = replace(
            selection,
            numerator_filter_text=builder.current_filter_text(),
        )
        context = self._categorical_builder_context(selection)
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
            builder.set_busy(False, f"Created {handle.metadata.row_count:,} result rows.")
            result_tab = self._make_dataset_tab(handle)
            retained = {source_handle.temporary_path.parent}
            if population_handle is not None:
                retained.add(population_handle.temporary_path.parent)
            for directory in retained:
                self._retain_directory(directory)
            self._categorical_sources[result_tab] = CategoricalResultContext(
                source_handle, population_handle, config
            )
            index = self.tabs.addTab(result_tab, "Categorical Table Result")
            self.tabs.setCurrentIndex(index)
            self._sync_active_tab()
            result_tab.start()

        def failed(message: str, details: str) -> None:
            self._categorical_input_tabs.clear()
            builder.set_busy(False, "Categorical Table failed.")
            self._show_error("Categorical Table Failed", message, details)

        self._submit(
            builder,
            lambda worker: self.categorical_engine.run(
                source_handle, config, population_handle, worker.report
            ),
            completed,
            failed,
        )

    def _proc_means_builder_context(self, selection, action_title: str):
        tab = self.current_dataset_tab()
        if tab is None or tab.handle.kind != "sas" or not tab.cache_complete:
            QMessageBox.warning(
                self,
                action_title,
                "Select a fully loaded SAS source dataset before using the Builder.",
            )
            return None
        filter_text = self.analysis_panel.builder.current_filter_text()
        try:
            compiled_filter = FilterEngine(tab.handle.metadata.variables).compile(
                filter_text
            )
        except ValueError as error:
            QMessageBox.warning(self, action_title, str(error))
            return None
        config = ProcMeansConfig(
            selection.analysis_variables,
            selection.by_variables,
            selection.class_variables,
            selection.statistics,
            compiled_filter,
            filter_text,
            selection.decimal_group_variables,
            tuple(self.settings.proc_means_decimal_offsets.items()),
            self.settings.proc_means_confidence,
        )
        try:
            config.validate(tab.handle.metadata)
        except ValueError as error:
            QMessageBox.warning(self, action_title, str(error))
            return None
        return tab, config

    def _generate_proc_means_sas_code(self, selection) -> None:
        context = self._proc_means_builder_context(selection, "SAS Code Generator")
        if context is None:
            return
        tab, config = context
        try:
            configuration = build_proc_means_configuration(tab.handle, config)
            code = self.sas_proc_means_generator.generate(configuration)
        except (KeyError, TypeError, ValueError) as error:
            QMessageBox.critical(self, "SAS Code Generator Failed", str(error))
            return
        safe_name = (
            "".join(
                character if character.isalnum() or character in {"-", "_"} else "_"
                for character in tab.handle.metadata.name
            ).strip("_")
            or "dataset"
        )
        safe_name = safe_name.lower()
        dialog = SasCodeDialog(
            code,
            str(tab.handle.source_path),
            f"{safe_name}_proc_means.sas",
            self,
        )
        dialog.exec()

    def _generate_proc_means_r_code(self, selection) -> None:
        context = self._proc_means_builder_context(selection, "R Code Generator")
        if context is None:
            return
        tab, config = context
        try:
            configuration = build_proc_means_configuration(tab.handle, config)
            code = self.r_proc_means_generator.generate(configuration)
        except (KeyError, TypeError, ValueError) as error:
            QMessageBox.critical(self, "R Code Generator Failed", str(error))
            return
        safe_name = (
            "".join(
                character if character.isalnum() or character in {"-", "_"} else "_"
                for character in tab.handle.metadata.name
            ).strip("_")
            or "dataset"
        ).lower()
        dialog = RCodeDialog(
            code,
            str(tab.handle.source_path),
            f"{safe_name}_proc_means.R",
            self,
        )
        dialog.exec()

    def _run_proc_means_builder(self, selection) -> None:
        active_tab = self.current_dataset_tab()
        if active_tab is None or active_tab.handle.kind != "sas":
            return
        current_filter = active_tab.current_where_text()
        builder_filter = self.analysis_panel.builder.current_filter_text()
        if not builder_filter or builder_filter != current_filter:
            response = QMessageBox.question(
                self,
                "PROC MEANS Builder",
                "Apply the current dataset filter to PROC MEANS?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if response == QMessageBox.Yes:
                self.analysis_panel.builder.apply_current_filter(current_filter)
        context = self._proc_means_builder_context(selection, "PROC MEANS Builder")
        if context is None:
            return
        tab, config = context
        source_handle = tab.handle
        self._proc_means_input_tabs = {tab}
        self.analysis_panel.builder.set_busy(
            True, "Calculating PROC MEANS in the background…"
        )

        def completed(handle: DatasetHandle) -> None:
            self._proc_means_input_tabs.clear()
            self.analysis_panel.builder.set_busy(
                False, f"Created {handle.metadata.row_count:,} result rows."
            )
            result_tab = self._make_dataset_tab(handle)
            source_directory = source_handle.temporary_path.parent
            self._retain_directory(source_directory)
            self._proc_means_sources[result_tab] = ProcMeansResultContext(
                source_handle, config
            )
            index = self.tabs.addTab(result_tab, "PROC MEANS Result")
            self.tabs.setCurrentIndex(index)
            # Ensure the shared Variables panel is bound to the result metadata
            # before the result model starts requesting its first page.
            self._sync_active_tab()
            result_tab.start()

        def failed(message: str, details: str) -> None:
            self._proc_means_input_tabs.clear()
            self.analysis_panel.builder.set_busy(False, "PROC MEANS failed.")
            self._show_error("PROC MEANS Builder Failed", message, details)

        self._submit(
            self.analysis_panel.builder,
            lambda worker: self.proc_means_engine.run(
                source_handle, config, worker.report
            ),
            completed,
            failed,
        )

    def _drilldown_proc_means(
        self, tab: DatasetTab, view_row: int, statistic_column: str, display: str
    ) -> None:
        context = self._proc_means_sources.get(tab)
        metadata = tab.handle.metadata
        analysis_column = metadata.proc_means_analysis_column
        statistic_key = dict(metadata.proc_means_statistic_keys).get(statistic_column)
        if context is None or analysis_column is None or statistic_key is None:
            return
        if display in {"", "—"}:
            QMessageBox.information(
                self,
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
        self.task_status.setText(f"Building {base_title}…")

        def build(worker: Worker):
            values = self.store.view_row_values(
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
            handle = self.proc_means_query_builder.run(
                context.source, compiled, base_title, worker.report
            )
            return handle, analysis_variable, where_text

        def completed(result) -> None:
            handle, analysis_variable, where_text = result
            if self.tabs.indexOf(tab) < 0 or generation != tab.generation:
                self._remove_dataset_directory(handle.temporary_path.parent)
                return
            title = self._unique_dataset_tab_title(base_title)
            query_tab = self._make_dataset_tab(handle)
            query_tab.apply_filter(
                FilterEngine(handle.metadata.variables).compile(where_text),
                where_text,
                add_history=False,
            )
            index = self.tabs.addTab(query_tab, title)
            self.tabs.setCurrentIndex(index)
            query_tab.start()
            query_tab.locate_variable(analysis_variable)

        self._submit(
            tab,
            build,
            completed,
            lambda message, details: self._show_error(
                "PROC MEANS Drill-down Failed", message, details
            ),
        )

    def _unique_dataset_tab_title(self, base: str) -> str:
        existing = {self.tabs.tabText(index) for index in range(self.tabs.count())}
        if base not in existing:
            return base
        suffix = 2
        while f"{base} ({suffix})" in existing:
            suffix += 1
        return f"{base} ({suffix})"

    def _drilldown_categorical(
        self, tab: DatasetTab, view_row: int, column_name: str, display: str
    ) -> None:
        context = self._categorical_sources.get(tab)
        source_row = tab.model.source_row_id(view_row)
        if context is None or source_row is None or not display:
            return
        cell = lookup_cell(tab.handle, source_row, column_name)
        if cell is None:
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Categorical Table Drill-down")
        dialog.setText("Choose rows to display for this n (%) cell.")
        records = dialog.addButton("Show Numerator Records", QMessageBox.ActionRole)
        subjects = dialog.addButton("Show Numerator Subjects", QMessageBox.ActionRole)
        denominator = dialog.addButton("Show Denominator Subjects", QMessageBox.ActionRole)
        dialog.addButton(QMessageBox.Cancel)
        dialog.exec()
        selected = dialog.clickedButton()
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
                self, "Categorical Table Drill-down", "The required source dataset is no longer available."
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
            QMessageBox.warning(self, "Categorical Table Drill-down", str(error))
            return
        mode = (
            "Denominator Subjects"
            if is_denominator
            else "Numerator Subjects"
            if selected is subjects
            else "Numerator Records"
        )
        title = self._unique_dataset_tab_title(f"Query: {mode}")
        self.task_status.setText(f"Building {title}…")

        def completed(handle: DatasetHandle) -> None:
            if self.tabs.indexOf(tab) < 0:
                self._remove_dataset_directory(handle.temporary_path.parent)
                return
            query_tab = self._make_dataset_tab(handle)
            index = self.tabs.addTab(query_tab, title)
            self.tabs.setCurrentIndex(index)
            query_tab.start()

        self._submit(
            tab,
            lambda _worker: self.categorical_query_builder.run(
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
            lambda message, details: self._show_error(
                "Categorical Table Drill-down Failed", message, details
            ),
        )

    def _open_categorical_long_result(self) -> None:
        tab = self.current_dataset_tab()
        context = self._categorical_sources.get(tab) if tab is not None else None
        if tab is None or tab.handle.kind != "categorical" or context is None:
            return
        context_names = tuple(
            dict.fromkeys(
                name
                for item in context.config.items
                for name in item.context_variables
            )
        )
        fields = {
            variable.name.casefold(): variable
            for variable in context.source.metadata.variables
        }
        context_variables = tuple(fields[name.casefold()] for name in context_names)
        title = self._unique_dataset_tab_title("Categorical Table Long Result")
        self.task_status.setText(f"Building {title}…")

        def completed(handle: DatasetHandle) -> None:
            if self.tabs.indexOf(tab) < 0:
                self._remove_dataset_directory(handle.temporary_path.parent)
                return
            long_tab = self._make_dataset_tab(handle)
            index = self.tabs.addTab(long_tab, title)
            self.tabs.setCurrentIndex(index)
            long_tab.start()

        self._submit(
            tab,
            lambda _worker: self.categorical_long_result_builder.run(
                tab.handle, context.source, context_variables
            ),
            completed,
            lambda message, details: self._show_error(
                "Categorical Long Result Failed", message, details
            ),
        )

    def _run_proc_means(self, tab: DatasetTab, variable_name: str) -> None:
        if (
            self.tabs.indexOf(tab) < 0
            or not tab.cache_complete
            or tab.handle.kind != "sas"
        ):
            return
        variable = next(
            (
                item
                for item in tab.handle.metadata.variables
                if item.name == variable_name
            ),
            None,
        )
        if variable is None or variable.kind != "numeric":
            return
        generation = tab.generation
        compiled = tab.compiled_filter
        confidence = self.settings.proc_means_confidence
        self._last_statistics_request = (tab, variable_name)
        self.analysis_panel.show_statistics_tab()
        self.analysis_dock.show()
        self.analysis_panel.statistics_scope.setText(
            f"Calculating {variable_name} on the current filtered result…"
        )

        def completed(result) -> None:
            if (
                self.tabs.indexOf(tab) < 0
                or generation != tab.generation
                or tab is not self.current_dataset_tab()
            ):
                return
            self._statistics_owner = tab
            self.analysis_panel.show_statistics(
                result,
                self.settings.proc_means_statistics,
                self.settings.proc_means_decimal_offsets,
                tab.filter_description(),
            )

        self._submit(
            tab,
            lambda _worker: calculate_statistics(
                tab.handle.database_path,
                tab.handle.metadata,
                variable_name,
                compiled,
                confidence,
            ),
            completed,
            lambda message, details: self._show_error(
                "PROC MEANS Failed", message, details
            ),
        )

    def _recalculate_statistics(self) -> None:
        if self._last_statistics_request is None:
            return
        tab, variable = self._last_statistics_request
        if self.tabs.indexOf(tab) >= 0:
            self._run_proc_means(tab, variable)

    def _compare_rows(self, tab: DatasetTab, rows: list[int]) -> None:
        if self.tabs.indexOf(tab) < 0 or not tab.cache_complete:
            return
        if len(rows) < 2 or len(rows) > 20:
            QMessageBox.information(
                self,
                "Compare Rows",
                "Select between 2 and 20 rows. Use Ctrl+click on row headers "
                "to select non-adjacent rows.",
            )
            return
        generation = tab.generation
        compiled = tab.compiled_filter
        sort = tab.model.sort_spec
        self.analysis_panel.show_comparison_tab()
        self.analysis_dock.show()
        self.analysis_panel.comparison_scope.setText("Comparing selected rows…")

        def completed(result) -> None:
            if (
                self.tabs.indexOf(tab) < 0
                or generation != tab.generation
                or tab is not self.current_dataset_tab()
            ):
                return
            if self._comparison_owner and self._comparison_owner is not tab:
                self._comparison_owner.clear_comparison_highlights()
            self._comparison_owner = tab
            tab.show_comparison_highlights(
                result.differing_variables,
                tuple(row.view_row for row in result.rows),
            )
            self.analysis_panel.show_comparison(result, tab.handle.metadata)

        self._submit(
            tab,
            lambda _worker: self.store.compare_view_rows(
                tab.handle.database_path,
                tab.handle.metadata,
                compiled,
                sort,
                rows,
            ),
            completed,
            lambda message, details: self._show_error(
                "Row Comparison Failed", message, details
            ),
        )

    def _clear_row_comparison(self) -> None:
        if self._comparison_owner:
            self._comparison_owner.clear_comparison_highlights()
        self._comparison_owner = None
        self.analysis_panel.clear_comparison()

    def _analysis_invalidated(self, tab: DatasetTab) -> None:
        tab.clear_comparison_highlights()
        if self._comparison_owner is tab:
            self._comparison_owner = None
        if tab is self.current_dataset_tab():
            self.analysis_panel.clear_comparison()
            if self._statistics_owner is tab:
                self.analysis_panel.mark_statistics_stale()

    def _comparison_invalidated(self, tab: DatasetTab) -> None:
        tab.clear_comparison_highlights()
        if self._comparison_owner is tab:
            self._comparison_owner = None
            self.analysis_panel.clear_comparison()

    def reload_current(self) -> None:
        tab = self.current_dataset_tab()
        if not tab or tab.reload_in_progress or tab.handle.kind != "sas":
            return
        source_path = tab.handle.source_path
        old_directory = tab.handle.temporary_path.parent
        preserved_visible = list(tab.visible_columns)
        editor_text = tab.where_editor.toPlainText()
        editor_was_dirty = tab.where_editor_is_dirty()
        applied_where = tab.applied_where
        preserved_column_filters = dict(tab.column_filters)
        tab.reload_in_progress = True
        tab.setEnabled(False)
        self._sync_active_tab()
        self.task_status.setText(f"Reloading {source_path.name}…")

        def completed(handle: DatasetHandle) -> None:
            if self.tabs.indexOf(tab) < 0:
                self._remove_dataset_directory(handle.temporary_path.parent)
                return
            known = {variable.name for variable in handle.metadata.variables}
            visible = [name for name in preserved_visible if name in known]
            empty_filter = FilterEngine(handle.metadata.variables).compile("")
            tab.column_filters = {}
            tab.replace_handle(handle, visible, empty_filter)
            tab.where_editor.setPlainText(editor_text)
            tab.applied_where = ""
            tab.reload_in_progress = False
            tab.setEnabled(True)
            self._pending_removals.setdefault(tab, []).append(old_directory)
            self._cleanup_pending(tab)
            self.variables_panel.set_dataset(handle.metadata, visible)
            self._refresh_status(tab)
            self._sync_active_tab()

            def reapply(final_handle: DatasetHandle) -> None:
                try:
                    compiled = FilterEngine(final_handle.metadata.variables).compile(
                        applied_where
                    )
                except ValueError as error:
                    QMessageBox.warning(
                        self,
                        "WHERE Not Reapplied",
                        "The dataset was reloaded, but the previous WHERE condition "
                        f"is no longer valid:\n{error}",
                    )
                    return
                tab.apply_filter(compiled, applied_where, add_history=False)
                tab.applied_where = applied_where
                tab.restore_column_filters(preserved_column_filters)
                if editor_was_dirty:
                    tab.where_editor.setPlainText(editor_text)
                self._refresh_status(tab)

            if handle.cache_complete:
                reapply(handle)
            else:
                self._continue_cache(tab, reapply)

        def failed(message: str, details: str) -> None:
            tab.reload_in_progress = False
            tab.setEnabled(True)
            self._sync_active_tab()
            self._show_error("Reload Failed", message, details)

        self._submit(
            tab,
            lambda worker: self.reader.load_initial(source_path, worker.report),
            completed,
            failed,
        )

    def export_current(self) -> None:
        tab = self.current_dataset_tab()
        if not tab or not tab.cache_complete or not self._exportable_columns(tab):
            return
        initial_directory = Path(
            self.settings.last_export_directory or str(tab.handle.source_path.parent)
        )
        suggested = initial_directory / f"{tab.handle.source_path.stem}.csv"
        destination, _filter = QFileDialog.getSaveFileName(
            self, "Export Current View", str(suggested), "CSV files (*.csv)"
        )
        if not destination:
            return
        path = Path(destination)
        if path.suffix.lower() != ".csv":
            path = path.with_suffix(".csv")
        self.settings.last_export_directory = str(path.parent)
        self.settings.save()
        handle = tab.handle
        columns = tuple(tab.visible_columns)
        compiled = tab.compiled_filter
        sort = tab.model.sort_spec

        def completed(row_count: int) -> None:
            self.task_status.setText(f"Exported {row_count:,} rows to {path.name}")
            QMessageBox.information(
                self, "Export Complete", f"Exported {row_count:,} rows to:\n{path}"
            )

        self._submit(
            tab,
            lambda worker: self.exporter.export(
                handle, path, columns, compiled, sort, worker.report
            ),
            completed,
            lambda message, details: self._show_error(
                "CSV Export Failed", message, details
            ),
        )

    def show_history(self, tab: DatasetTab | None = None) -> None:
        owner = tab or self.current_dataset_tab()
        dialog = HistoryDialog(
            self.history, owner.handle.source_path if owner else None, self
        )
        if owner:
            dialog.condition_selected.connect(owner.where_editor.setPlainText)
        dialog.exec()

    def close_tab(self, index: int) -> None:
        if index < 0:
            return
        widget = self.tabs.widget(index)
        if widget in self._compare_input_tabs:
            QMessageBox.information(
                self,
                "Dataset Compare Running",
                "This dataset is currently being compared. Wait for the comparison "
                "to finish before closing it.",
            )
            return
        if widget in self._proc_means_input_tabs:
            QMessageBox.information(
                self,
                "PROC MEANS Running",
                "This dataset is currently being analyzed. Wait for PROC MEANS "
                "to finish before closing it.",
            )
            return
        if widget in self._categorical_input_tabs:
            QMessageBox.information(
                self,
                "Categorical Table Running",
                "This dataset is currently used by a Categorical Table. Wait for the calculation to finish before closing it.",
            )
            return
        self.tabs.removeTab(index)
        for worker in self._workers.get(widget, set()):
            worker.cancel()
        if isinstance(widget, DatasetTab):
            proc_context = self._proc_means_sources.pop(widget, None)
            if proc_context is not None:
                self._pending_directory_releases.setdefault(widget, []).append(
                    proc_context.source.temporary_path.parent
                )
            categorical_context = self._categorical_sources.pop(widget, None)
            if categorical_context is not None:
                directories = {categorical_context.source.temporary_path.parent}
                if categorical_context.population is not None:
                    directories.add(categorical_context.population.temporary_path.parent)
                self._pending_directory_releases.setdefault(widget, []).extend(
                    directories
                )
            self._compare_sources.pop(widget, None)
            if self._comparison_owner is widget:
                self._clear_row_comparison()
            if self._statistics_owner is widget:
                self._statistics_owner = None
                self._last_statistics_request = None
                self.analysis_panel.clear_statistics()
            self._pending_removals.setdefault(widget, []).append(
                widget.handle.temporary_path.parent
            )
        self._cleanup_pending(widget)
        widget.deleteLater()
        self._refresh_compare_datasets()
        self._refresh_categorical_sources()
        self._sync_active_tab()

    def _sync_active_tab(self, _index: int | None = None) -> None:
        tab = self.current_dataset_tab()
        if self._comparison_owner is not None and self._comparison_owner is not tab:
            self._clear_row_comparison()
        if self._statistics_owner is not None and self._statistics_owner is not tab:
            self._statistics_owner = None
            self._last_statistics_request = None
            self.analysis_panel.clear_statistics()
        if (
            self._last_statistics_request
            and self._last_statistics_request[0] is not tab
        ):
            self._last_statistics_request = None
        self._sync_dataset_actions(tab)
        self.variables_panel.set_dataset(
            tab.handle.metadata, tab.visible_columns, tab.available_columns()
        ) if tab else self.variables_panel.set_dataset(None)
        if tab and tab.handle.kind == "compare":
            self.compare_panel.set_advanced_checked(tab.advanced_visible)
        builder_source = (
            tab
            if tab is not None and tab.handle.kind == "sas" and tab.cache_complete
            else None
        )
        self.analysis_panel.builder.set_dataset(
            builder_source.handle.metadata if builder_source else None,
            str(builder_source.handle.source_path) if builder_source else "",
            builder_source.current_where_text() if builder_source else "",
        )
        self.analysis_panel.categorical_builder.set_dataset(
            builder_source.handle.metadata if builder_source else None,
            str(builder_source.handle.source_path) if builder_source else "",
            builder_source.current_where_text() if builder_source else "",
        )
        self._refresh_compare_datasets()
        self._refresh_categorical_sources()
        self._refresh_status(tab)

    def _sync_dataset_actions(self, tab: DatasetTab | None) -> None:
        enabled = tab is not None
        self.reload_action.setEnabled(
            enabled
            and tab.handle.kind == "sas"
            and not tab.reload_in_progress
            and (tab.cache_complete or tab.cache_failed)
        )
        self.export_action.setEnabled(
            enabled and tab.cache_complete and bool(self._exportable_columns(tab))
        )
        self.clear_action.setEnabled(enabled and tab.cache_complete)
        self.close_tab_action.setEnabled(self.tabs.count() > 0)
        self.open_categorical_long_action.setEnabled(
            tab is not None and tab.handle.kind == "categorical"
        )

    @staticmethod
    def _exportable_columns(tab: DatasetTab | None) -> list[str]:
        if tab is None:
            return []
        excluded = set(tab.handle.metadata.export_excluded_columns)
        return [column for column in tab.visible_columns if column not in excluded]

    def _refresh_status(self, tab: DatasetTab | None) -> None:
        if not tab:
            self.rows_status.setText("Rows: —")
            self.columns_status.setText("Columns: —")
            self.filter_status.setText("")
            self.source_status.setText("Source: —")
            return
        if tab.cache_complete:
            self.rows_status.setText(
                f"Rows: {tab.model.filtered_count:,} / {tab.handle.metadata.row_count:,}"
            )
        else:
            self.rows_status.setText(
                f"Rows cached: {tab.handle.cached_row_count:,} / "
                f"{tab.handle.metadata.row_count:,}"
            )
        self.columns_status.setText(f"Columns: {len(tab.visible_columns)}")
        self.filter_status.setText("Filtered" if tab.compiled_filter.sql else "")
        self.filter_status.setProperty("filtered", bool(tab.compiled_filter.sql))
        self.filter_status.style().unpolish(self.filter_status)
        self.filter_status.style().polish(self.filter_status)
        source = tab.handle.display_source or str(tab.handle.source_path)
        self.source_status.setText(f"Source:  {source}")
        if (
            tab is self.current_dataset_tab()
            and tab.handle.kind == "sas"
            and tab.cache_complete
        ):
            self.analysis_panel.builder.set_dataset(
                tab.handle.metadata,
                str(tab.handle.source_path),
                tab.current_where_text(),
            )

    def _submit(
        self, owner: QWidget, function, completed, failed, progress_data=None
    ) -> None:
        worker = Worker(function)
        self._workers.setdefault(owner, set()).add(worker)
        worker.signals.progress.connect(self.task_status.setText)
        worker.signals.result.connect(completed)
        worker.signals.error.connect(failed)
        if progress_data is not None:
            worker.signals.progress_data.connect(progress_data)

        def finished() -> None:
            self._workers.get(owner, set()).discard(worker)
            self._cleanup_pending(owner)
            if not any(self._workers.values()):
                self.task_status.clear()

        worker.signals.finished.connect(finished)
        self.pool.start(worker)

    def _cleanup_pending(self, owner: QWidget) -> None:
        if self._workers.get(owner):
            return
        for path in self._pending_removals.pop(owner, []):
            self._remove_dataset_directory(path)
        for path in self._pending_directory_releases.pop(owner, []):
            self._release_directory(path)

    def _retain_directory(self, path: Path) -> None:
        directory = path.resolve()
        self._retained_directories[directory] = (
            self._retained_directories.get(directory, 0) + 1
        )

    def _release_directory(self, path: Path) -> None:
        directory = path.resolve()
        remaining = self._retained_directories.get(directory, 0) - 1
        if remaining > 0:
            self._retained_directories[directory] = remaining
            return
        self._retained_directories.pop(directory, None)
        if directory in self._deferred_directory_removals:
            self._deferred_directory_removals.discard(directory)
            self._remove_dataset_directory(directory)

    def _remove_dataset_directory(self, path: Path) -> None:
        directory = path.resolve()
        if self._retained_directories.get(directory, 0) > 0:
            self._deferred_directory_removals.add(directory)
            return
        try:
            self.temp_manager.remove_dataset(directory)
        except OSError:
            # The session-level cleanup retries after every worker has stopped and at exit.
            pass

    def _show_error(self, title: str, message: str, details: str = "") -> None:
        box = QMessageBox(QMessageBox.Critical, title, message, parent=self)
        if details:
            box.setDetailedText(details)
        box.exec()

    def closeEvent(self, event: QCloseEvent) -> None:
        for workers in self._workers.values():
            for worker in workers:
                worker.cancel()
        self.task_status.setText(
            "Finishing background work and cleaning temporary files…"
        )
        self.pool.waitForDone(-1)
        try:
            self.temp_manager.cleanup()
        except OSError as error:
            QMessageBox.warning(
                self,
                "Temporary Cleanup",
                f"Some temporary files could not be removed:\n{error}",
            )
        self.settings.save()
        event.accept()
