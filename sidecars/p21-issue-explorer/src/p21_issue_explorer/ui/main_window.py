from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMainWindow

from .import_page import ImportPage


class P21IssueExplorerWindow(QMainWindow):
    def __init__(self, *, initial_report: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("P21 Issue Explorer")
        self.resize(760, 420)
        self.import_page = ImportPage()
        self.setCentralWidget(self.import_page)
        if initial_report is not None:
            self.import_page.load_report(initial_report)
