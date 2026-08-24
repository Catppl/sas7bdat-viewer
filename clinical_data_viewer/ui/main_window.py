from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
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

from ..csv_exporter import CsvExporter
from ..data_store import DataStore
from ..domain import CacheProgress, DatasetHandle
from ..filter_engine import FilterEngine
from ..filter_history import FilterHistory
from ..sas_reader import SasDatasetReader
from ..settings import AppSettings
from ..statistics import calculate_statistics
from ..temp_manager import TempManager
from ..workers import Worker
from .analysis_panel import AnalysisPanel
from .column_filter_dialog import ColumnFilterDialog
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
        self.store = DataStore()
        self.exporter = CsvExporter()
        self.pool = QThreadPool.globalInstance()
        self._workers: dict[QWidget, set[Worker]] = {}
        self._pending_removals: dict[QWidget, list[Path]] = {}
        self._last_statistics_request: tuple[DatasetTab, str] | None = None
        self._statistics_owner: DatasetTab | None = None
        self._comparison_owner: DatasetTab | None = None
        self.setWindowTitle("SASDataViewer")
        self.resize(1280, 790)
        self.setMinimumSize(850, 560)
        self._create_actions()
        self._create_menu()
        self._create_toolbar()
        self._create_center()
        self._create_variables_panel()
        self._create_analysis_panel()
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
        self.analysis_action = QAction("Analysis", self)
        self.analysis_action.setCheckable(True)
        self.analysis_action.setChecked(False)
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
        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addAction(self.analysis_action)
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
            "SAS datasets (*.sas7bdat);;All files (*)",
        )
        if not files:
            return
        self.settings.last_open_directory = str(Path(files[0]).parent)
        self.settings.save()
        self.open_paths(Path(value) for value in files)

    def open_paths(self, paths: Iterable[Path]) -> None:
        for source_path in paths:
            self._open_path(Path(source_path))

    def _open_path(self, source_path: Path) -> None:
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
                self._continue_cache(tab)

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
            tab.model.set_page(offset, page.rows, displayed_count)
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
        dialog.exec()

    def _run_proc_means(self, tab: DatasetTab, variable_name: str) -> None:
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
        if variable is None or variable.kind != "numeric":
            return
        generation = tab.generation
        compiled = tab.compiled_filter
        confidence = self.settings.proc_means_confidence
        self._last_statistics_request = (tab, variable_name)
        self.analysis_dock.show()
        self.analysis_panel.tabs.setCurrentIndex(0)
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
                self.settings.proc_means_decimals,
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
        self.analysis_dock.show()
        self.analysis_panel.tabs.setCurrentIndex(1)
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
        if not tab or tab.reload_in_progress:
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
        if not tab or not tab.cache_complete or not tab.visible_columns:
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
        self.tabs.removeTab(index)
        for worker in self._workers.get(widget, set()):
            worker.cancel()
        if isinstance(widget, DatasetTab):
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
            tab.handle.metadata, tab.visible_columns
        ) if tab else self.variables_panel.set_dataset(None)
        self._refresh_status(tab)

    def _sync_dataset_actions(self, tab: DatasetTab | None) -> None:
        enabled = tab is not None
        self.reload_action.setEnabled(
            enabled
            and not tab.reload_in_progress
            and (tab.cache_complete or tab.cache_failed)
        )
        self.export_action.setEnabled(
            enabled and tab.cache_complete and bool(tab.visible_columns)
        )
        self.clear_action.setEnabled(enabled and tab.cache_complete)
        self.close_tab_action.setEnabled(self.tabs.count() > 0)

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
        self.source_status.setText(f"Source:  {tab.handle.source_path}")

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

    def _remove_dataset_directory(self, path: Path) -> None:
        try:
            self.temp_manager.remove_dataset(path)
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
