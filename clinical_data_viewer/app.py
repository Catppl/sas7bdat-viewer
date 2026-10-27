from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .filter_history import FilterHistory
from .resources import resource_path
from .settings import AppSettings, application_data_directory, temporary_root_directory
from .temp_manager import TempManager
from .ui.main_window import MainWindow
from .workspace import WorkspaceFavoritesStore

STYLE = """
QWidget {
    font-family: "Segoe UI";
    font-size: 10pt;
    color: #1f2937;
}
QMainWindow { background: #f4f8fc; }
QMenuBar {
    background: #f7fbff;
    border-bottom: 1px solid #cbdced;
}
QMenuBar::item:selected {
    background: #dceeff;
    color: #0f5f9f;
}
QMenu {
    background-color: #f7fbff;
    border: 1px solid #bfd4e8;
    padding: 4px;
}
QMenu::item {
    background-color: transparent;
    color: #1f2937;
    padding: 6px 24px 6px 10px;
    border-radius: 3px;
}
QMenu::item:selected {
    background-color: #dceeff;
    color: #0f5f9f;
}
QMenu::item:disabled { color: #94a3b8; }
QMenu::separator {
    height: 1px;
    background: #cbdced;
    margin: 4px 6px;
}
QToolBar#mainToolbar {
    background: #f3f8fe;
    border-bottom: 1px solid #c5d9ec;
    spacing: 6px;
    padding: 5px;
}
QToolBar#mainToolbar QToolButton {
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 6px 9px;
}
QToolBar#mainToolbar QToolButton:hover {
    background: #e1f0ff;
    border-color: #b5d7f5;
}
QToolBar::separator {
    background: #cbdced;
    width: 1px;
    margin: 6px 3px;
}
QDockWidget, QWidget#variablesPanel, QWidget#workspacePanel { background: #f7fbff; }
QTabWidget::pane { background: #ffffff; border: 1px solid #bfd4e8; }
QTabBar::tab {
    background: #edf4fb;
    border: 1px solid #c5d7e8;
    color: #334155;
    padding: 7px 18px;
}
QTabBar::tab:hover { background: #e1effd; }
QTabBar::tab:selected {
    background: #ffffff;
    color: #0f5f9f;
    border-bottom: 3px solid #1684d8;
    font-weight: 600;
}
QHeaderView::section {
    background: #eaf3fc;
    color: #243b53;
    border: 0;
    border-right: 1px solid #ceddea;
    border-bottom: 1px solid #bdd2e5;
    padding: 6px;
    font-weight: 600;
}
QDialog {
    background: #f4f8fc;
}
QDialog QLabel#panelTitle { color: #174b73; }
QScrollArea { background: transparent; border: 0; }
QGroupBox {
    background: #f8fbfe;
    border: 1px solid #bfd4e8;
    border-radius: 4px;
    margin-top: 10px;
    padding: 8px;
    padding-top: 14px;
    color: #174b73;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 9px;
    padding: 0 4px;
}
QTableView, QTableWidget, QTreeWidget, QListWidget, QPlainTextEdit { background: #ffffff; }
QTableView {
    alternate-background-color: #f8fbfe;
    gridline-color: #dce6ef;
    selection-background-color: #cfe8ff;
    selection-color: #102a43;
}
QTableWidget, QTreeWidget, QListWidget {
    border: 1px solid #bfd4e8;
    alternate-background-color: #f1f7fd;
    gridline-color: #dce6ef;
    selection-background-color: #cfe8ff;
    selection-color: #102a43;
}
QTableWidget::item:hover, QTreeWidget::item:hover, QListWidget::item:hover {
    background: #e1f0ff;
}
QTableWidget::item:selected, QTreeWidget::item:selected, QListWidget::item:selected {
    background: #cfe8ff;
    color: #102a43;
}
QTreeWidget { border: 0; alternate-background-color: #f1f7fd; }
QTreeWidget::item:hover { background: #e1f0ff; }
QTreeWidget::item:selected { background: #cfe8ff; color: #102a43; }
QLineEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    border: 1px solid #b8cde0;
    border-radius: 3px;
    padding: 4px;
    background: #ffffff;
    selection-background-color: #b9ddff;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border: 1px solid #1684d8; }
QComboBox QAbstractItemView {
    background-color: #f7fbff;
    color: #1f2937;
    border: 1px solid #bfd4e8;
    outline: 0;
    selection-background-color: #dceeff;
    selection-color: #0f5f9f;
}
QSpinBox, QDoubleSpinBox { min-height: 24px; padding-right: 22px; }
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border;
    subcontrol-position: top right;
    width: 18px;
    border-left: 1px solid #b8cde0;
    border-bottom: 1px solid #b8cde0;
    background: #edf5fc;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    subcontrol-position: bottom right;
    width: 18px;
    border-left: 1px solid #b8cde0;
    background: #edf5fc;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
    background: #dceeff;
}
QPushButton {
    background: #ffffff;
    border: 1px solid #a9c1d8;
    border-radius: 4px;
    min-width: 74px;
    padding: 5px 12px;
}
QPushButton:hover { background: #e7f3ff; border-color: #1684d8; }
QPushButton:pressed { background: #d5eaff; }
QPushButton:default {
    background: #1676bd;
    border-color: #0f66a6;
    color: #ffffff;
    font-weight: 600;
}
QPushButton:default:hover { background: #0f6cab; }
QFrame#wherePanel {
    background: #f7fbff;
    border: 1px solid #c5d9ec;
}
QFrame#findBar {
    background: #eef7ff;
    border: 1px solid #b7d6ef;
}
QFrame#columnFilterBar {
    background: #eef7ff;
    border: 1px solid #b7d6ef;
}
QPushButton#filterChip {
    background: #dceeff;
    color: #0f5f9f;
    border: 1px solid #9dc9eb;
    border-radius: 10px;
    min-width: 0;
    padding: 2px 8px;
}
QLabel#filterNotice {
    background: #fff8df;
    border: 1px solid #ead491;
    color: #6b5600;
    padding: 5px;
}
QWidget#procMeansBuilder, QWidget#procMeansBuilderContent,
QWidget#categoricalBuilder, QWidget#categoricalBuilderContent,
QWidget#ruleBasedBuilder, QWidget#ruleBasedBuilderContent {
    background: #f7fbff;
}
QWidget#procMeansVerticalBuilder, QWidget#procMeansVerticalBuilderContent {
    background: #f7fbff;
}
QTreeWidget#historyTable::item {
    min-height: 28px;
    padding: 3px 5px;
}
QTreeWidget#historyTable {
    border: 1px solid #bfd4e8;
}
QLabel#cacheNotice {
    background: #e6f3ff;
    border: 1px solid #a9d2f2;
    color: #0f5f9f;
    padding: 6px 9px;
}
QLabel#xptSubmissionWarning {
    background: #fff4e5;
    border: 1px solid #e7b66d;
    color: #7a4b00;
    padding: 5px 8px;
}
QLabel#whereTitle { color: #0f6fb5; font-weight: 700; }
QLabel#panelTitle { color: #174b73; font-weight: 700; }
QToolButton:hover { background: #e1f0ff; border-radius: 3px; }
QLabel#statusSegment {
    border-right: 1px solid #cbdced;
    padding: 0 14px 0 7px;
}
QLabel[filtered="true"] { color: #0878c9; font-weight: 700; }
QStatusBar { background: #f3f8fe; border-top: 1px solid #c5d9ec; }
QProgressBar { border: 1px solid #a9c6df; border-radius: 3px; background: #edf5fc; }
QProgressBar::chunk { background: #1684d8; }
"""


@dataclass(frozen=True, slots=True)
class StartupRequest:
    workspace_path: Path | None
    dataset_paths: tuple[Path, ...]


def startup_request_from_arguments(arguments: Sequence[str]) -> StartupRequest:
    """Parse the explicit Workspace flag while preserving file associations."""

    paths: list[Path] = []
    seen: set[str] = set()
    workspace_path: Path | None = None
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--workspace":
            if workspace_path is not None:
                raise ValueError("Only one --workspace folder may be specified.")
            index += 1
            if index >= len(arguments) or not arguments[index].strip():
                raise ValueError("--workspace requires a folder path.")
            workspace_path = (
                Path(_unquote_argument(arguments[index]))
                .expanduser()
                .resolve(strict=False)
            )
            index += 1
            continue
        if argument.startswith("--workspace="):
            if workspace_path is not None:
                raise ValueError("Only one --workspace folder may be specified.")
            value = _unquote_argument(argument.partition("=")[2])
            if not value:
                raise ValueError("--workspace requires a folder path.")
            workspace_path = Path(value).expanduser().resolve(strict=False)
            index += 1
            continue
        value = _unquote_argument(argument)
        candidate = Path(value).expanduser()
        if candidate.suffix.lower() in {".sas7bdat", ".xpt"}:
            resolved = candidate.resolve(strict=False)
            key = str(resolved).casefold() if sys.platform == "win32" else str(resolved)
            if key not in seen:
                seen.add(key)
                paths.append(resolved)
        index += 1
    return StartupRequest(workspace_path, tuple(paths))


def _unquote_argument(argument: str) -> str:
    value = argument.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def dataset_paths_from_arguments(arguments: Sequence[str]) -> tuple[Path, ...]:
    """Return dataset paths supplied by a shell or Windows file association."""

    return startup_request_from_arguments(arguments).dataset_paths


def apply_startup_navigation_mode(
    window: MainWindow, startup_request: StartupRequest
) -> None:
    """Choose the initial panel layout from the explicit launch mode only."""

    if startup_request.workspace_path is not None:
        window.set_startup_navigation_panels(
            folder_visible=True,
            variables_visible=True,
        )
    elif startup_request.dataset_paths:
        window.set_startup_navigation_panels(
            folder_visible=False,
            variables_visible=True,
        )


def main() -> int:
    startup_error = ""
    try:
        startup_request = startup_request_from_arguments(sys.argv[1:])
    except ValueError as error:
        startup_request = StartupRequest(None, ())
        startup_error = str(error)
    application = QApplication(sys.argv)
    application.setApplicationName("SASDataViewer")
    application.setOrganizationName("ClinicalDataViewer")
    application.setWindowIcon(QIcon(str(resource_path("assets/SASDataViewer.ico"))))
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
    settings = AppSettings.load()
    temp_manager = TempManager(temporary_root_directory())
    temp_manager.cleanup_stale(settings.temp_max_age_hours * 60 * 60)
    history = FilterHistory(
        application_data_directory() / "filter_history.sqlite", settings.history_limit
    )
    favorites = WorkspaceFavoritesStore(
        application_data_directory() / "workspace_favorites.sqlite"
    )
    window = MainWindow(settings, temp_manager, history, favorites)
    apply_startup_navigation_mode(window, startup_request)
    window.show()
    if startup_request.workspace_path is not None:
        QTimer.singleShot(
            0,
            lambda: window.workspace_controller.open_workspace(
                startup_request.workspace_path
            ),
        )
    if startup_request.dataset_paths:
        QTimer.singleShot(0, lambda: window.open_paths(startup_request.dataset_paths))
    if startup_error:
        QTimer.singleShot(
            0,
            lambda: QMessageBox.critical(
                window, "Invalid Startup Request", startup_error
            ),
        )
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
