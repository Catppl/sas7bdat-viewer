from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .filter_history import FilterHistory
from .settings import AppSettings, application_data_directory, temporary_root_directory
from .temp_manager import TempManager
from .ui.main_window import MainWindow

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
QMenuBar::item:selected, QMenu::item:selected {
    background: #dceeff;
    color: #0f5f9f;
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
QDockWidget, QWidget#variablesPanel { background: #f7fbff; }
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
QTableView, QTreeWidget, QPlainTextEdit { background: #ffffff; }
QTableView {
    alternate-background-color: #f8fbfe;
    gridline-color: #dce6ef;
    selection-background-color: #cfe8ff;
    selection-color: #102a43;
}
QTreeWidget { border: 0; alternate-background-color: #f1f7fd; }
QTreeWidget::item:hover { background: #e1f0ff; }
QTreeWidget::item:selected { background: #cfe8ff; color: #102a43; }
QLineEdit, QPlainTextEdit {
    border: 1px solid #b8cde0;
    border-radius: 3px;
    padding: 4px;
    selection-background-color: #b9ddff;
}
QLineEdit:focus, QPlainTextEdit:focus { border: 1px solid #1684d8; }
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


def main() -> int:
    application = QApplication(sys.argv)
    application.setApplicationName("SASDataViewer")
    application.setOrganizationName("ClinicalDataViewer")
    application.setStyle("Fusion")
    application.setStyleSheet(STYLE)
    settings = AppSettings.load()
    temp_manager = TempManager(temporary_root_directory())
    temp_manager.cleanup_stale(settings.temp_max_age_hours * 60 * 60)
    history = FilterHistory(
        application_data_directory() / "filter_history.sqlite", settings.history_limit
    )
    window = MainWindow(settings, temp_manager, history)
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
