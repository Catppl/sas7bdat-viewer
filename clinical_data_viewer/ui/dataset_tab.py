from __future__ import annotations

from PySide6.QtCore import QRect, QRegularExpression, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..domain import DatasetHandle, SortSpec
from ..filter_engine import CompiledFilter
from ..table_model import DatasetTableModel
from .copy_table import CopyTableView


class WhereEditor(QPlainTextEdit):
    apply_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self._update_line_number_width)
        self.updateRequest.connect(self._update_line_number_area)
        self._update_line_number_width()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if (
            event.key() in {Qt.Key_Return, Qt.Key_Enter}
            and event.modifiers() & Qt.ControlModifier
        ):
            self.apply_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def line_number_width(self) -> int:
        digits = max(1, len(str(max(1, self.blockCount()))))
        return 10 + self.fontMetrics().horizontalAdvance("9") * digits

    def _update_line_number_width(self, _count: int = 0) -> None:
        self.setViewportMargins(self.line_number_width(), 0, 0, 0)

    def _update_line_number_area(self, rectangle: QRect, vertical_delta: int) -> None:
        if vertical_delta:
            self.line_number_area.scroll(0, vertical_delta)
        else:
            self.line_number_area.update(
                0, rectangle.y(), self.line_number_area.width(), rectangle.height()
            )
        if rectangle.contains(self.viewport().rect()):
            self._update_line_number_width()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        contents = self.contentsRect()
        self.line_number_area.setGeometry(
            QRect(
                contents.left(),
                contents.top(),
                self.line_number_width(),
                contents.height(),
            )
        )

    def paint_line_numbers(self, event: QPaintEvent) -> None:
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), QColor("#f5f5f5"))
        painter.setPen(QColor("#777777"))
        block = self.firstVisibleBlock()
        number = block.blockNumber()
        top = round(
            self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        )
        bottom = top + round(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(
                    0,
                    top,
                    self.line_number_area.width() - 6,
                    self.fontMetrics().height(),
                    Qt.AlignRight,
                    str(number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            number += 1


class LineNumberArea(QWidget):
    def __init__(self, editor: WhereEditor) -> None:
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self) -> QSize:
        return QSize(self.editor.line_number_width(), 0)

    def paintEvent(self, event: QPaintEvent) -> None:
        self.editor.paint_line_numbers(event)


class WhereHighlighter(QSyntaxHighlighter):
    def __init__(self, document) -> None:
        super().__init__(document)
        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#0067b8"))
        keyword_format.setFontWeight(QFont.DemiBold)
        self.keyword = (
            QRegularExpression(
                r"\b(?:AND|OR|NOT|IN|CONTAINS|MISSING)\b",
                QRegularExpression.CaseInsensitiveOption,
            ),
            keyword_format,
        )
        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#d13438"))
        self.string = (
            QRegularExpression(r"(?:\"(?:\"\"|[^\"])*\"|'(?:''|[^'])*')"),
            string_format,
        )

    def highlightBlock(self, text: str) -> None:
        for expression, text_format in (self.keyword, self.string):
            iterator = expression.globalMatch(text)
            while iterator.hasNext():
                match = iterator.next()
                self.setFormat(
                    match.capturedStart(), match.capturedLength(), text_format
                )


class DatasetTab(QWidget):
    page_requested = Signal(int, int, int)
    apply_requested = Signal(str)
    clear_requested = Signal()
    history_requested = Signal()
    sort_changed = Signal(object)

    def __init__(self, handle: DatasetHandle, page_size: int, parent=None) -> None:
        super().__init__(parent)
        self.handle = handle
        self.visible_columns = [variable.name for variable in handle.metadata.variables]
        self.compiled_filter = CompiledFilter("", ())
        self.applied_where = ""
        self.pending_history_text = ""
        self.generation = 0
        self.recount_next = False
        self._page_size = page_size

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.table = CopyTableView()
        self.table.horizontalHeader().setSectionsMovable(False)
        self.table.horizontalHeader().setDefaultSectionSize(120)
        self.table.horizontalHeader().setMinimumSectionSize(40)
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.table.horizontalHeader().sectionClicked.connect(self._header_clicked)
        self.table.verticalHeader().setDefaultSectionSize(22)
        layout.addWidget(self.table, 1)

        where_frame = QFrame()
        where_frame.setObjectName("wherePanel")
        where_frame.setFrameShape(QFrame.StyledPanel)
        where_layout = QVBoxLayout(where_frame)
        where_layout.setContentsMargins(6, 4, 6, 5)
        where_layout.setSpacing(3)
        where_title = QLabel("WHERE")
        where_title.setObjectName("whereTitle")
        where_layout.addWidget(where_title)
        self.where_editor = WhereEditor()
        self.where_highlighter = WhereHighlighter(self.where_editor.document())
        self.where_editor.setPlaceholderText(
            'AESER = "Y" and TRTEMFL = "Y" and USUBJID = "101-001"'
        )
        self.where_editor.setMaximumHeight(78)
        self.where_editor.apply_requested.connect(self._emit_apply)
        where_layout.addWidget(self.where_editor)
        buttons = QHBoxLayout()
        apply_button = QPushButton("Apply")
        apply_button.setDefault(True)
        apply_button.clicked.connect(self._emit_apply)
        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self.clear_requested)
        history_button = QPushButton("History")
        history_button.clicked.connect(self.history_requested)
        buttons.addWidget(apply_button)
        buttons.addWidget(clear_button)
        buttons.addWidget(history_button)
        buttons.addStretch(1)
        where_layout.addLayout(buttons)
        layout.addWidget(where_frame)
        self._install_model()

    def _install_model(self) -> None:
        old_model = self.table.model()
        self.model = DatasetTableModel(
            self.handle.metadata, self.visible_columns, self._page_size
        )
        self.model.page_requested.connect(
            lambda offset, limit: self.page_requested.emit(
                self.generation, offset, limit
            )
        )
        self.model.sort_requested.connect(self._sort_requested)
        self.table.setModel(self.model)
        if old_model:
            old_model.deleteLater()

    def start(self) -> None:
        self.model.reset_query(filtered_count=self.handle.metadata.row_count)

    def _emit_apply(self) -> None:
        self.apply_requested.emit(self.where_editor.toPlainText())

    def _sort_requested(self, sort: SortSpec) -> None:
        self.generation += 1
        self.recount_next = False
        self.sort_changed.emit(sort)
        self.model.reset_query(filtered_count=self.model.filtered_count)

    def _header_clicked(self, column: int) -> None:
        if not 0 <= column < len(self.visible_columns):
            return
        current = self.model.sort_spec
        ascending = (
            not current.ascending
            if current and current.variable == self.visible_columns[column]
            else True
        )
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSortIndicator(
            column, Qt.AscendingOrder if ascending else Qt.DescendingOrder
        )
        self.model.sort(column, Qt.AscendingOrder if ascending else Qt.DescendingOrder)

    def set_visible_columns(self, columns: list[str]) -> None:
        if columns == self.visible_columns or not columns:
            return
        self.visible_columns = list(columns)
        self.generation += 1
        self.recount_next = False
        self.model.reset_query(
            columns=self.visible_columns, filtered_count=self.model.filtered_count
        )

    def apply_filter(
        self, compiled: CompiledFilter, where_text: str, *, add_history: bool
    ) -> None:
        self.compiled_filter = compiled
        self.applied_where = where_text.strip()
        self.pending_history_text = (
            self.applied_where if add_history and self.applied_where else ""
        )
        self.generation += 1
        self.recount_next = True
        self.model.reset_query(filtered_count=self.handle.metadata.row_count)

    def clear_filter(self) -> None:
        self.where_editor.clear()
        self.compiled_filter = CompiledFilter("", ())
        self.applied_where = ""
        self.pending_history_text = ""
        self.generation += 1
        self.recount_next = False
        self.model.reset_query(filtered_count=self.handle.metadata.row_count)

    def replace_handle(
        self,
        handle: DatasetHandle,
        visible_columns: list[str],
        compiled: CompiledFilter,
    ) -> None:
        previous_sort = self.model.sort_spec
        self.handle = handle
        self.visible_columns = visible_columns
        self.compiled_filter = compiled
        self.generation += 1
        self.recount_next = bool(compiled.sql)
        self._install_model()
        if previous_sort and previous_sort.variable in visible_columns:
            self.model.sort_spec = previous_sort
            section = visible_columns.index(previous_sort.variable)
            self.table.horizontalHeader().setSortIndicatorShown(True)
            self.table.horizontalHeader().setSortIndicator(
                section,
                Qt.AscendingOrder if previous_sort.ascending else Qt.DescendingOrder,
            )
        self.model.reset_query(filtered_count=handle.metadata.row_count)

    def locate_variable(self, variable: str) -> None:
        try:
            column = self.visible_columns.index(variable)
        except ValueError:
            return
        self.table.scrollTo(self.model.index(0, column), QTableView.PositionAtCenter)
        self.table.selectColumn(column)
