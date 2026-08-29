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

from ..compare_engine import DatasetComparer, recommend_group_variables
from ..controllers import AnalysisController
from ..csv_exporter import CsvExporter
from ..data_store import DataStore
from ..dataset_utils import is_analysis_dataset
from ..domain import CacheProgress, DatasetHandle
from ..filter_engine import FilterEngine
from ..filter_history import FilterHistory
from ..merge_datasets import MergeDatasetsEngine
from ..sas_reader import SasDatasetReader
from ..settings import AppSettings
from ..statistics import calculate_statistics
from ..temp_manager import TempManager
from ..workers import Worker
from .analysis_panel import AnalysisPanel
from .column_filter_dialog import ColumnFilterDialog
from .dataset_compare_panel import DatasetComparePanel
from .dataset_merge_panel import DatasetMergePanel
from .dataset_tab import DatasetTab
from .history_dialog import HistoryDialog
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
class MergeResultContext:
    left: DatasetHandle
    right: DatasetHandle


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
        self.merge_engine = MergeDatasetsEngine(temp_manager)
        self.store = DataStore()
        self.exporter = CsvExporter()
        self.pool = QThreadPool.globalInstance()
        self._workers: dict[QWidget, set[Worker]] = {}
        self._pending_removals: dict[QWidget, list[Path]] = {}
        self._last_statistics_request: tuple[DatasetTab, str] | None = None
        self._statistics_owner: DatasetTab | None = None
        self._comparison_owner: DatasetTab | None = None
        self._compare_input_tabs: set[DatasetTab] = set()
        self._merge_input_tabs: set[DatasetTab] = set()
        # Each Builder is deliberately bound to the dataset that was active
        # when the user opened it.  Tab navigation must never silently change
        # a calculation's source or discard the Builder's in-progress input.
        self._merge_sources: dict[DatasetTab, MergeResultContext] = {}
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
        self.analysis_controller = AnalysisController(
            self, self.analysis_panel, temp_manager, settings, self
        )
        self._create_compare_panel()
        self._create_merge_panel()
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
        self.sas_date_time_formats_action = QAction("Apply SAS Date/Time Formats", self)
        self.sas_date_time_formats_action.setCheckable(True)
        self.sas_date_time_formats_action.setChecked(
            self.settings.apply_sas_date_time_formats
        )
        self.sas_date_time_formats_action.triggered.connect(
            self._set_apply_sas_date_time_formats
        )
        self.open_categorical_long_action = QAction(
            "Open Categorical Long Result", self
        )
        self.open_categorical_long_action.triggered.connect(
            lambda: self.analysis_controller.open_categorical_long_result()
        )
        self.open_rule_based_long_action = QAction("Open Rule-based Long Result", self)
        self.open_rule_based_long_action.triggered.connect(
            lambda: self.analysis_controller.open_rule_based_long_result()
        )
        self.open_ae_table_long_action = QAction("Open AE Table Long Result", self)
        self.open_ae_table_long_action.triggered.connect(
            lambda: self.analysis_controller.open_ae_table_long_result()
        )
        self.analysis_action = QAction("Analysis", self)
        self.analysis_action.setCheckable(True)
        self.analysis_action.setChecked(False)
        self.proc_means_builder_action = QAction("PROC MEANS Builder", self)
        self.proc_means_builder_action.triggered.connect(self.show_proc_means_builder)
        self.categorical_builder_action = QAction("Categorical Table Builder", self)
        self.categorical_builder_action.triggered.connect(self.show_categorical_builder)
        self.rule_based_builder_action = QAction("Rule-based Table Builder", self)
        self.rule_based_builder_action.triggered.connect(self.show_rule_based_builder)
        self.ae_table_builder_action = QAction("AE Table Builder", self)
        self.ae_table_builder_action.triggered.connect(self.show_ae_table_builder)
        self.listing_builder_action = QAction("Listing Generator", self)
        self.listing_builder_action.triggered.connect(self.show_listing_builder)
        self.compare_datasets_action = QAction("Compare Datasets", self)
        self.compare_datasets_action.setCheckable(True)
        self.compare_datasets_action.setChecked(False)
        self.merge_datasets_action = QAction("Merge Datasets", self)
        self.merge_datasets_action.setCheckable(True)
        self.merge_datasets_action.setChecked(False)
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
        view_menu.addAction(self.sas_date_time_formats_action)
        view_menu.addAction(self.open_categorical_long_action)
        view_menu.addAction(self.open_rule_based_long_action)
        view_menu.addAction(self.open_ae_table_long_action)
        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addAction(self.analysis_action)
        tools_menu.addAction(self.proc_means_builder_action)
        tools_menu.addAction(self.categorical_builder_action)
        tools_menu.addAction(self.rule_based_builder_action)
        tools_menu.addAction(self.ae_table_builder_action)
        tools_menu.addAction(self.listing_builder_action)
        tools_menu.addAction(self.compare_datasets_action)
        tools_menu.addAction(self.merge_datasets_action)
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
        self.analysis_panel.builder.settings_requested.connect(self.show_settings)
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

    def _create_merge_panel(self) -> None:
        self.merge_panel = DatasetMergePanel()
        self.merge_dock = QDockWidget("Merge Datasets", self)
        self.merge_dock.setObjectName("mergeDatasetsDock")
        self.merge_dock.setAllowedAreas(Qt.RightDockWidgetArea)
        self.merge_dock.setWidget(self.merge_panel)
        self.merge_dock.setMinimumWidth(360)
        self.addDockWidget(Qt.RightDockWidgetArea, self.merge_dock)
        self.merge_dock.hide()
        self.merge_datasets_action.toggled.connect(self.merge_dock.setVisible)
        self.merge_dock.visibilityChanged.connect(self.merge_datasets_action.setChecked)
        self.merge_panel.merge_requested.connect(self._run_dataset_merge)

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

    # AnalysisController host surface.  These are deliberately thin wrappers
    # around the existing MainWindow lifecycle mechanisms during Phase 1.
    def is_open_dataset_tab(self, tab: DatasetTab) -> bool:
        return self.tabs.indexOf(tab) >= 0

    def available_sas_dataset_tabs(self) -> list[tuple[DatasetTab, str]]:
        datasets: list[tuple[DatasetTab, str]] = []
        for index in range(self.tabs.count()):
            tab = self.tabs.widget(index)
            if isinstance(tab, DatasetTab) and tab.handle.kind == "sas":
                datasets.append(
                    (
                        tab,
                        f"{tab.handle.metadata.name} — {tab.handle.source_path.name}",
                    )
                )
        return datasets

    def create_analysis_result_tab(self, handle: DatasetHandle) -> DatasetTab:
        return self._make_dataset_tab(handle)

    def show_analysis_result_tab(self, tab: DatasetTab, title: str) -> None:
        index = self.tabs.addTab(tab, title)
        self.tabs.setCurrentIndex(index)
        self._sync_active_tab()
        tab.start()

    def submit_analysis_task(self, owner, function, completed, failed) -> None:
        self._submit(owner, function, completed, failed)

    def retain_analysis_directory(self, path: Path) -> None:
        self._retain_directory(path)

    def show_analysis_error(
        self, title: str, message: str, details: str = ""
    ) -> None:
        self._show_error(title, message, details)

    def browse_listing_adsl_dataset(self) -> None:
        self._browse_listing_adsl()

    def browse_rule_based_adsl_dataset(self) -> None:
        self._browse_rule_based_adsl()

    def browse_ae_table_adsl_dataset(self) -> None:
        self._browse_ae_adsl()

    def unique_analysis_tab_title(self, base: str) -> str:
        return self._unique_dataset_tab_title(base)

    def _unique_dataset_tab_title(self, base: str) -> str:
        """Return a non-conflicting title for a generated dataset tab."""
        existing = {self.tabs.tabText(index) for index in range(self.tabs.count())}
        if base not in existing:
            return base
        suffix = 2
        while f"{base} ({suffix})" in existing:
            suffix += 1
        return f"{base} ({suffix})"

    def discard_analysis_result(self, handle: DatasetHandle) -> None:
        self._remove_dataset_directory(handle.temporary_path.parent)

    def set_analysis_task_status(self, text: str) -> None:
        self.task_status.setText(text)

    def show_proc_means_query_result(
        self,
        handle: DatasetHandle,
        title: str,
        where_text: str,
        analysis_variable: str,
    ) -> None:
        query_tab = self._make_dataset_tab(handle)
        query_tab.apply_filter(
            FilterEngine(handle.metadata.variables).compile(where_text),
            where_text,
            add_history=False,
        )
        self.show_analysis_result_tab(query_tab, title)
        query_tab.locate_variable(analysis_variable)

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
            self._refresh_merge_datasets()
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
            self._refresh_merge_datasets()
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

    def _refresh_merge_datasets(self) -> None:
        if not hasattr(self, "merge_panel"):
            return
        datasets: list[tuple[object, str, bool]] = []
        for index in range(self.tabs.count()):
            tab = self.tabs.widget(index)
            if isinstance(tab, DatasetTab) and is_analysis_dataset(tab.handle):
                datasets.append(
                    (
                        tab,
                        f"{tab.handle.metadata.name} — {tab.handle.source_path.name}",
                        tab.cache_complete,
                    )
                )
        self.merge_panel.set_datasets(datasets)

    def _refresh_categorical_sources(self) -> None:
        if not hasattr(self, "analysis_panel"):
            return
        datasets = self.available_sas_dataset_tabs()
        if hasattr(self, "analysis_controller"):
            self.analysis_controller.refresh_categorical_adsl_sources(datasets)
            self.analysis_controller.refresh_listing_adsl_sources(datasets)
            self.analysis_controller.refresh_rule_based_adsl_sources(datasets)
            self.analysis_controller.refresh_ae_table_adsl_sources(datasets)

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
            self.analysis_controller.select_categorical_adsl(tab)

        self._open_path(source, ready)

    def browse_categorical_adsl_dataset(self) -> None:
        """Host callback used by the Categorical Builder browse action."""
        self._browse_categorical_adsl()

    def _browse_rule_based_adsl(self) -> None:
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
            self.analysis_controller.select_rule_based_adsl(tab)

        self._open_path(source, ready)

    def _browse_ae_adsl(self) -> None:
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
            self.analysis_controller.select_ae_table_adsl(tab)

        self._open_path(source, ready)

    def _browse_listing_adsl(self) -> None:
        initial = self.settings.last_open_directory or str(Path.home())
        filename, _filter = QFileDialog.getOpenFileName(
            self,
            "Choose ADSL Dataset",
            initial,
            "SAS datasets (*.sas7bdat *.xpt);;All files (*)",
        )
        if not filename:
            self.analysis_panel.listing_builder.merge_enabled.setChecked(False)
            return
        source = Path(filename)
        self.settings.last_open_directory = str(source.parent)
        self.settings.save()

        def ready(tab: DatasetTab) -> None:
            self._refresh_categorical_sources()
            self.analysis_controller.select_listing_adsl(tab)

        self._open_path(source, ready)

    def _run_dataset_merge(
        self, left_tab: DatasetTab, right_tab: DatasetTab, config
    ) -> None:
        if (
            self.tabs.indexOf(left_tab) < 0
            or self.tabs.indexOf(right_tab) < 0
            or left_tab is right_tab
            or not left_tab.cache_complete
            or not right_tab.cache_complete
        ):
            QMessageBox.warning(
                self,
                "Merge Datasets",
                "Select two different, fully loaded datasets.",
            )
            return
        try:
            self.merge_engine.validate(left_tab.handle, right_tab.handle, config)
        except ValueError as error:
            QMessageBox.warning(self, "Merge Datasets", str(error))
            return
        self.merge_dock.show()
        self.merge_panel.set_busy(True, "Checking BY-key multiplicity…")
        self._merge_input_tabs = {left_tab, right_tab}
        left_handle = left_tab.handle
        right_handle = right_tab.handle

        def execute_merge() -> None:
            self.merge_panel.set_busy(
                True, "Merging source datasets in the background…"
            )

            def completed(result) -> None:
                self._merge_input_tabs.clear()
                handle = result.handle
                summary = result.summary
                sort_text = ", ".join(
                    f"{item.variable} {item.direction}" for item in config.sort_by
                )
                sort_clause = (
                    f" Sort order: {sort_text}."
                    if sort_text
                    else " Sort order: Default stable order."
                )
                self.merge_panel.set_busy(
                    False,
                    f"Created {handle.metadata.row_count:,} result rows: "
                    f"{summary.matched_rows:,} matched result rows, "
                    f"{summary.left_only_rows:,} left-only rows, "
                    f"{summary.right_only_rows:,} right-only rows." + sort_clause,
                )
                result_tab = self._make_dataset_tab(handle)
                self._retain_directory(left_handle.temporary_path.parent)
                self._retain_directory(right_handle.temporary_path.parent)
                self._merge_sources[result_tab] = MergeResultContext(
                    left_handle, right_handle
                )
                title = self._unique_dataset_tab_title(
                    f"Merge Result - {left_handle.metadata.name} + {right_handle.metadata.name}"
                )
                index = self.tabs.addTab(result_tab, title)
                self.tabs.setCurrentIndex(index)
                result_tab.start()

            def failed(message: str, details: str) -> None:
                self._merge_input_tabs.clear()
                self.merge_panel.set_busy(False, "Merge failed.")
                self._show_error("Merge Datasets Failed", message, details)

            self._submit(
                self.merge_panel,
                lambda worker: self.merge_engine.run(
                    left_handle, right_handle, config, worker.report
                ),
                completed,
                failed,
            )

        def inspected(summary) -> None:
            if self.tabs.indexOf(left_tab) < 0 or self.tabs.indexOf(right_tab) < 0:
                self._merge_input_tabs.clear()
                self.merge_panel.set_busy(False)
                return
            self._merge_input_tabs.clear()
            if summary.many_to_many:
                answer = QMessageBox.question(
                    self,
                    "Many-to-many join detected",
                    "Some BY combinations occur multiple times in both datasets.\n\n"
                    f"Left duplicated keys: {summary.left_duplicate_keys:,}\n"
                    f"Right duplicated keys: {summary.right_duplicate_keys:,}\n"
                    f"Many-to-many keys: {summary.many_to_many_keys:,}\n\n"
                    "The merge may expand the number of records. Continue?",
                    QMessageBox.Yes | QMessageBox.Cancel,
                    QMessageBox.Cancel,
                )
                if answer != QMessageBox.Yes:
                    self.merge_panel.set_busy(False, "Merge cancelled.")
                    return
            execute_merge()

        def failed(message: str, details: str) -> None:
            self._merge_input_tabs.clear()
            self.merge_panel.set_busy(False, "Merge pre-check failed.")
            self._show_error("Merge Datasets Failed", message, details)

        self._submit(
            self.merge_panel,
            lambda _worker: self.merge_engine.inspect(
                left_handle, right_handle, config
            ),
            inspected,
            failed,
        )

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
        tab = DatasetTab(
            handle,
            self.settings.page_size,
            apply_sas_date_time_formats=self.settings.apply_sas_date_time_formats,
        )
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
            lambda row, column, display, owner=tab: self.analysis_controller.drilldown_proc_means(
                owner, row, column, display
            )
        )
        tab.categorical_drilldown_requested.connect(
            lambda row, column, display, owner=tab: self.analysis_controller.drilldown_categorical(
                owner, row, column, display
            )
        )
        tab.rule_based_drilldown_requested.connect(
            lambda row, column, display, owner=tab: self.analysis_controller.drilldown_rule_based(
                owner, row, column, display
            )
        )
        tab.ae_table_drilldown_requested.connect(
            lambda row, column, display, owner=tab: self.analysis_controller.drilldown_ae_table(
                owner, row, column, display
            )
        )
        return tab

    def _set_apply_sas_date_time_formats(self, enabled: bool) -> None:
        self.settings.apply_sas_date_time_formats = enabled
        self.settings.save()
        for index in range(self.tabs.count()):
            tab = self.tabs.widget(index)
            if isinstance(tab, DatasetTab):
                tab.set_apply_sas_date_time_formats(enabled)

    def _continue_cache(self, tab: DatasetTab, when_complete=None, when_failed=None) -> None:
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
                total_rows_known=progress.total_rows_known,
            )
            tab.set_cache_state(
                progress.cached_rows,
                progress.total_rows,
                progress.complete,
                progress.total_rows_known,
            )
            self._refresh_status(tab)

        def completed(handle: DatasetHandle) -> None:
            if self.tabs.indexOf(tab) < 0:
                return
            tab.handle = handle
            tab.set_cache_state(
                handle.cached_row_count,
                handle.metadata.row_count,
                True,
                handle.total_rows_known,
            )
            self._sync_active_tab()
            self._refresh_status(tab)
            self._refresh_compare_datasets()
            self._refresh_merge_datasets()
            if when_complete is not None:
                when_complete(handle)

        def failed(message: str, details: str) -> None:
            if when_failed is not None:
                when_failed()
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
        self.analysis_controller.show_proc_means_builder()
        self.analysis_dock.show()

    def show_categorical_builder(self) -> None:
        self.analysis_controller.show_categorical_builder()
        self.analysis_dock.show()

    def show_rule_based_builder(self) -> None:
        self.analysis_controller.show_rule_based_builder()
        self.analysis_dock.show()

    def show_ae_table_builder(self) -> None:
        self.analysis_controller.show_ae_table_builder()
        self.analysis_dock.show()

    def show_listing_builder(self) -> None:
        self.analysis_controller.show_listing_builder()
        self.analysis_dock.show()

    def _run_proc_means(self, tab: DatasetTab, variable_name: str) -> None:
        if (
            self.tabs.indexOf(tab) < 0
            or not tab.cache_complete
            or not is_analysis_dataset(tab.handle)
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
        if blocker := self.analysis_controller.tab_reload_blocker(tab):
            QMessageBox.information(self, blocker.title, blocker.message)
            return
        if tab in self._compare_input_tabs:
            QMessageBox.information(
                self,
                "Dataset Compare Running",
                "This dataset is currently being compared. Wait for the comparison "
                "to finish before reloading it.",
            )
            return
        if tab in self._merge_input_tabs:
            QMessageBox.information(
                self,
                "Merge Running",
                "This dataset is currently used by a Merge. Wait for the merge "
                "to finish before reloading it.",
            )
            return
        source_path = tab.handle.source_path
        old_directory = tab.handle.temporary_path.parent
        preserved_visible = list(tab.visible_columns)
        editor_text = tab.where_editor.toPlainText()
        editor_was_dirty = tab.where_editor_is_dirty()
        applied_where = tab.applied_where
        preserved_column_filters = dict(tab.column_filters)
        tab.reload_in_progress = True
        self.analysis_controller.source_reload_started(tab)
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
                else:
                    tab.apply_filter(compiled, applied_where, add_history=False)
                    tab.applied_where = applied_where
                    tab.restore_column_filters(preserved_column_filters)
                    if editor_was_dirty:
                        tab.where_editor.setPlainText(editor_text)
                    self._refresh_status(tab)
                finally:
                    # Always release the Builder's reload lock, including when
                    # the old Dataset WHERE is no longer valid after Reload.
                    self.analysis_controller.source_reload_completed(tab)

            if handle.cache_complete:
                reapply(handle)
            else:
                self._continue_cache(
                    tab,
                    reapply,
                    lambda: self.analysis_controller.source_reload_failed(tab),
                )

        def failed(message: str, details: str) -> None:
            tab.reload_in_progress = False
            self.analysis_controller.source_reload_failed(tab)
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
                handle,
                path,
                columns,
                compiled,
                sort,
                worker.report,
                tab.manual_highlights_for_export(),
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
        bound_builders = []
        bound_builders.extend(self.analysis_controller.bound_builder_titles(widget))
        if bound_builders:
            QMessageBox.warning(
                self,
                "Builder Source Dataset",
                "This dataset is the fixed source for: "
                + ", ".join(bound_builders)
                + ".\n\nClick Clear in the Builder before closing this source dataset.",
            )
            return
        if widget in self._compare_input_tabs:
            QMessageBox.information(
                self,
                "Dataset Compare Running",
                "This dataset is currently being compared. Wait for the comparison "
                "to finish before closing it.",
            )
            return
        if blocker := self.analysis_controller.tab_close_blocker(widget):
            QMessageBox.information(
                self,
                blocker.title,
                blocker.message,
            )
            return
        if widget in self._merge_input_tabs:
            QMessageBox.information(
                self,
                "Merge Running",
                "This dataset is currently used by a Merge. Wait for the merge "
                "to finish before closing it.",
            )
            return
        self.tabs.removeTab(index)
        for worker in self._workers.get(widget, set()):
            worker.cancel()
        if isinstance(widget, DatasetTab):
            if directories := self.analysis_controller.take_result_release_paths(widget):
                self._pending_directory_releases.setdefault(widget, []).extend(
                    directories
                )
            merge_context = self._merge_sources.pop(widget, None)
            if merge_context is not None:
                self._pending_directory_releases.setdefault(widget, []).extend(
                    (
                        merge_context.left.temporary_path.parent,
                        merge_context.right.temporary_path.parent,
                    )
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
        self._refresh_merge_datasets()
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
        self._refresh_compare_datasets()
        self._refresh_merge_datasets()
        self._refresh_categorical_sources()
        self._refresh_status(tab)

    def _sync_dataset_actions(self, tab: DatasetTab | None) -> None:
        enabled = tab is not None
        self.reload_action.setEnabled(
            enabled
            and is_analysis_dataset(tab.handle)
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
        self.open_rule_based_long_action.setEnabled(
            tab is not None and tab.handle.kind == "rule_based"
        )
        self.open_ae_table_long_action.setEnabled(
            tab is not None and tab.handle.kind == "ae_table"
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
        elif not tab.handle.total_rows_known:
            self.rows_status.setText(f"Rows cached: {tab.handle.cached_row_count:,}")
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
