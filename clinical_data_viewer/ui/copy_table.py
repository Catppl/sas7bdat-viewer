from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QMenu, QTableView


class CopyTableView(QTableView):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QTableView.ExtendedSelection)
        self.setSelectionBehavior(QTableView.SelectItems)
        # DatasetTab handles header clicks so the initial view keeps SAS source order.
        self.setSortingEnabled(False)
        self.setWordWrap(False)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        QShortcut(QKeySequence.Copy, self, activated=self.copy_selection)

    def copy_selection(self, include_headers: bool = False) -> None:
        indexes = (
            self.selectionModel().selectedIndexes() if self.selectionModel() else []
        )
        if not indexes:
            return
        rows = sorted({index.row() for index in indexes})
        columns = sorted({index.column() for index in indexes})
        selected = {
            (index.row(), index.column()): index.data(Qt.DisplayRole) or ""
            for index in indexes
        }
        lines: list[str] = []
        if include_headers:
            lines.append(
                "\t".join(
                    str(self.model().headerData(column, Qt.Horizontal) or "")
                    for column in columns
                )
            )
        for row in rows:
            lines.append(
                "\t".join(str(selected.get((row, column), "")) for column in columns)
            )
        QApplication.clipboard().setText("\n".join(lines))

    def _show_context_menu(self, position) -> None:
        menu = QMenu(self)
        copy_action = QAction("Copy", self)
        copy_action.triggered.connect(self.copy_selection)
        menu.addAction(copy_action)
        headers_action = QAction("Copy with Headers", self)
        headers_action.triggered.connect(lambda: self.copy_selection(True))
        menu.addAction(headers_action)
        menu.addSeparator()
        row_action = QAction("Select Row", self)
        row_action.triggered.connect(self._select_context_row)
        menu.addAction(row_action)
        self._context_index = self.indexAt(position)
        menu.exec(self.viewport().mapToGlobal(position))

    def _select_context_row(self) -> None:
        if getattr(self, "_context_index", None) and self._context_index.isValid():
            self.selectRow(self._context_index.row())
