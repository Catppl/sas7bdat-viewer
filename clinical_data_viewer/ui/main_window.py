from __future__ import annotations

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
from ..domain import DatasetHandle
from ..filter_engine import FilterEngine
from ..filter_history import FilterHistory
from ..sas_reader import SasDatasetReader
from ..settings import AppSettings
from ..temp_manager import TempManager
from ..workers import Worker
from .dataset_tab import DatasetTab
from .history_dialog import HistoryDialog
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
        self.setWindowTitle("SASDataViewer")
        self.resize(1280, 790)
        self.setMinimumSize(850, 560)
        self._create_actions()
        self._create_menu()
        self._create_toolbar()
        self._create_center()
        self._create_variables_panel()
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
        self.menuBar().addMenu("&Tools")
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
        for value in files:
            self._open_path(Path(value))

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

        def failed(message: str, details: str) -> None:
            loading.progress.setRange(0, 1)
            loading.label.setText(f"Could not open {source_path.name}")
            self._show_error("Open Dataset Failed", message, details)

        self._submit(
            loading,
            lambda worker: self.reader.load(source_path, worker.report),
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
        return tab

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
            tab.model.append_page(page.rows, page.filtered_count)
            if offset == 0 and tab.pending_history_text:
                self.history.add(tab.handle.source_path, tab.pending_history_text)
                tab.pending_history_text = ""
            self._refresh_status(tab)

        def failed(message: str, details: str) -> None:
            if generation == tab.generation:
                tab.model.load_failed()
            self._show_error("Dataset Query Failed", message, details)

        self._submit(tab, query, completed, failed)

    def _apply_filter(self, tab: DatasetTab, where_text: str) -> None:
        try:
            compiled = FilterEngine(tab.handle.metadata.variables).compile(where_text)
        except ValueError as error:
            QMessageBox.warning(self, "Invalid WHERE Condition", str(error))
            return
        tab.apply_filter(compiled, where_text, add_history=True)
        self._refresh_status(tab)

    def _clear_filter(self, tab: DatasetTab) -> None:
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

    def _locate_variable(self, variable: str) -> None:
        tab = self.current_dataset_tab()
        if tab:
            tab.locate_variable(variable)

    def reload_current(self) -> None:
        tab = self.current_dataset_tab()
        if not tab:
            return
        source_path = tab.handle.source_path
        old_directory = tab.handle.temporary_path.parent
        preserved_visible = list(tab.visible_columns)
        editor_text = tab.where_editor.toPlainText()
        applied_where = tab.applied_where
        tab.setEnabled(False)
        self.task_status.setText(f"Reloading {source_path.name}…")

        def completed(handle: DatasetHandle) -> None:
            if self.tabs.indexOf(tab) < 0:
                self._remove_dataset_directory(handle.temporary_path.parent)
                return
            known = {variable.name for variable in handle.metadata.variables}
            visible = [name for name in preserved_visible if name in known]
            if not visible:
                visible = [variable.name for variable in handle.metadata.variables]
            try:
                compiled = FilterEngine(handle.metadata.variables).compile(
                    applied_where
                )
            except ValueError as error:
                compiled = FilterEngine(handle.metadata.variables).compile("")
                QMessageBox.warning(
                    self,
                    "WHERE Not Reapplied",
                    f"The dataset was reloaded, but the previous WHERE condition is no longer valid:\n{error}",
                )
            tab.replace_handle(handle, visible, compiled)
            tab.where_editor.setPlainText(editor_text)
            tab.applied_where = applied_where if compiled.sql else ""
            tab.setEnabled(True)
            self._pending_removals.setdefault(tab, []).append(old_directory)
            self._cleanup_pending(tab)
            self.variables_panel.set_dataset(handle.metadata, visible)
            self._refresh_status(tab)

        def failed(message: str, details: str) -> None:
            tab.setEnabled(True)
            self._show_error("Reload Failed", message, details)

        self._submit(
            tab,
            lambda worker: self.reader.load(source_path, worker.report),
            completed,
            failed,
        )

    def export_current(self) -> None:
        tab = self.current_dataset_tab()
        if not tab:
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
            self._pending_removals.setdefault(widget, []).append(
                widget.handle.temporary_path.parent
            )
        self._cleanup_pending(widget)
        widget.deleteLater()
        self._sync_active_tab()

    def _sync_active_tab(self, _index: int | None = None) -> None:
        tab = self.current_dataset_tab()
        enabled = tab is not None
        for action in (
            self.reload_action,
            self.export_action,
            self.clear_action,
            self.close_tab_action,
        ):
            action.setEnabled(
                enabled or (action is self.close_tab_action and self.tabs.count() > 0)
            )
        self.variables_panel.set_dataset(
            tab.handle.metadata, tab.visible_columns
        ) if tab else self.variables_panel.set_dataset(None)
        self._refresh_status(tab)

    def _refresh_status(self, tab: DatasetTab | None) -> None:
        if not tab:
            self.rows_status.setText("Rows: —")
            self.columns_status.setText("Columns: —")
            self.filter_status.setText("")
            self.source_status.setText("Source: —")
            return
        self.rows_status.setText(
            f"Rows: {tab.model.filtered_count:,} / {tab.handle.metadata.row_count:,}"
        )
        self.columns_status.setText(f"Columns: {len(tab.visible_columns)}")
        self.filter_status.setText("Filtered" if tab.compiled_filter.sql else "")
        self.filter_status.setProperty("filtered", bool(tab.compiled_filter.sql))
        self.filter_status.style().unpolish(self.filter_status)
        self.filter_status.style().polish(self.filter_status)
        self.source_status.setText(f"Source:  {tab.handle.source_path}")

    def _submit(self, owner: QWidget, function, completed, failed) -> None:
        worker = Worker(function)
        self._workers.setdefault(owner, set()).add(worker)
        worker.signals.progress.connect(self.task_status.setText)
        worker.signals.result.connect(completed)
        worker.signals.error.connect(failed)

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
