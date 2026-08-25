from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
)

COMPARISON_HIGHLIGHT = QColor("#fff2b2")


class ComparisonHighlightDelegate(QStyledItemDelegate):
    """Keep comparison cells yellow even while their rows are selected."""

    @staticmethod
    def is_comparison_cell(index) -> bool:
        model = index.model()
        columns = getattr(model, "columns", ())
        rows = getattr(model, "highlighted_rows", set())
        highlighted_columns = getattr(model, "highlighted_columns", set())
        page_highlights = getattr(model, "_page_highlights", {})
        page_size = getattr(model, "page_size", 1)
        page = page_highlights.get((index.row() // page_size) * page_size, ())
        local_row = index.row() % page_size
        generated_highlight = (
            local_row < len(page)
            and 0 <= index.column() < len(columns)
            and columns[index.column()] in page[local_row]
        )
        return generated_highlight or (
            index.isValid()
            and index.row() in rows
            and 0 <= index.column() < len(columns)
            and columns[index.column()] in highlighted_columns
        )

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        if not self.is_comparison_cell(index):
            return
        option.backgroundBrush = COMPARISON_HIGHLIGHT
        option.palette.setColor(QPalette.Highlight, COMPARISON_HIGHLIGHT)
        option.palette.setColor(
            QPalette.HighlightedText, option.palette.color(QPalette.Text)
        )

    def paint(self, painter, option, index) -> None:
        if not self.is_comparison_cell(index):
            super().paint(painter, option, index)
            return
        # The application stylesheet supplies a blue selection background that
        # overrides BackgroundRole. Paint just these cells without Selected state;
        # their model background remains yellow while equal cells stay blue.
        comparison_option = QStyleOptionViewItem(option)
        comparison_option.state &= ~QStyle.State_Selected
        super().paint(painter, comparison_option, index)


class CopyTableView(QTableView):
    proc_means_requested = Signal(str)
    settings_requested = Signal()
    compare_rows_requested = Signal(object)
    clear_comparison_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setItemDelegate(ComparisonHighlightDelegate(self))
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
        self._context_index = self.indexAt(position)
        if self._context_index.isValid():
            selected_rows = self.selected_row_numbers()
            if self._context_index.row() not in selected_rows:
                self.setCurrentIndex(self._context_index)
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
        menu.addSeparator()
        variable = self._context_variable()
        proc_means = QAction("PROC MEANS", self)
        proc_means.setEnabled(self._context_variable_is_numeric())
        proc_means.triggered.connect(
            lambda: self.proc_means_requested.emit(variable) if variable else None
        )
        menu.addAction(proc_means)
        settings = QAction("Settings…", self)
        settings.triggered.connect(self.settings_requested)
        menu.addAction(settings)
        menu.addSeparator()
        selected_rows = self.selected_row_numbers()
        compare = QAction("Compare Selected Rows", self)
        compare.setEnabled(
            not bool(self.property("pairedDataset")) and 2 <= len(selected_rows) <= 20
        )
        compare.triggered.connect(
            lambda: self.compare_rows_requested.emit(self.selected_row_numbers())
        )
        menu.addAction(compare)
        clear_compare = QAction("Clear Row Comparison", self)
        clear_compare.triggered.connect(self.clear_comparison_requested)
        menu.addAction(clear_compare)
        menu.exec(self.viewport().mapToGlobal(position))

    def _select_context_row(self) -> None:
        if getattr(self, "_context_index", None) and self._context_index.isValid():
            self.selectRow(self._context_index.row())

    def selected_row_numbers(self) -> list[int]:
        if not self.selectionModel():
            return []
        return sorted(index.row() for index in self.selectionModel().selectedRows())

    def _context_variable(self) -> str:
        if (
            not getattr(self, "_context_index", None)
            or not self._context_index.isValid()
        ):
            return ""
        model = self.model()
        columns = getattr(model, "columns", [])
        column = self._context_index.column()
        return columns[column] if 0 <= column < len(columns) else ""

    def _context_variable_is_numeric(self) -> bool:
        if not bool(self.property("cacheComplete")):
            return False
        variable_name = self._context_variable()
        model = self.model()
        metadata = getattr(model, "metadata", None)
        if not variable_name or metadata is None:
            return False
        if metadata.pair_id_column:
            return False
        return any(
            variable.name == variable_name and variable.kind == "numeric"
            for variable in metadata.variables
        )
