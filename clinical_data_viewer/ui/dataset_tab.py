from __future__ import annotations

from PySide6.QtCore import QRect, QRegularExpression, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QShortcut,
    QSyntaxHighlighter,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..domain import DatasetHandle, FindResult, SortSpec
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
                r"\b(?:AND|OR|NOT|IN|CONTAINS|MISSING|IS|NULL|BETWEEN|LIKE|ESCAPE|EQ|NE|GT|LT|GE|LE)\b",
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
    find_requested = Signal(str, bool, int, int)

    def __init__(self, handle: DatasetHandle, page_size: int, parent=None) -> None:
        super().__init__(parent)
        self.handle = handle
        self.visible_columns = [variable.name for variable in handle.metadata.variables]
        self.compiled_filter = CompiledFilter("", ())
        self.applied_where = ""
        self.pending_history_text = ""
        self.generation = 0
        self.recount_next = False
        self.cache_complete = handle.cache_complete
        self.cache_failed = False
        self.reload_in_progress = False
        self._pending_selection: tuple[int, int] | None = None
        self._page_size = page_size

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.cache_notice = QLabel()
        self.cache_notice.setObjectName("cacheNotice")
        self.cache_notice.setVisible(not self.cache_complete)
        layout.addWidget(self.cache_notice)

        self.find_frame = QFrame()
        self.find_frame.setObjectName("findBar")
        find_layout = QHBoxLayout(self.find_frame)
        find_layout.setContentsMargins(6, 3, 6, 3)
        find_layout.addWidget(QLabel("Find:"))
        self.find_editor = QLineEdit()
        self.find_editor.setPlaceholderText("Search text in displayed columns")
        self.find_editor.returnPressed.connect(lambda: self._request_find(True))
        find_layout.addWidget(self.find_editor, 1)
        previous_button = QPushButton("Previous")
        previous_button.clicked.connect(lambda: self._request_find(False))
        find_layout.addWidget(previous_button)
        next_button = QPushButton("Next")
        next_button.clicked.connect(lambda: self._request_find(True))
        find_layout.addWidget(next_button)
        self.find_status = QLabel("")
        find_layout.addWidget(self.find_status)
        close_find = QPushButton("×")
        close_find.setFixedWidth(28)
        close_find.clicked.connect(self.find_frame.hide)
        find_layout.addWidget(close_find)
        self.find_frame.hide()
        layout.addWidget(self.find_frame)

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
        self.apply_button = QPushButton("Apply")
        self.apply_button.setDefault(True)
        self.apply_button.clicked.connect(self._emit_apply)
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear_requested)
        history_button = QPushButton("History")
        history_button.clicked.connect(self.history_requested)
        buttons.addWidget(self.apply_button)
        buttons.addWidget(self.clear_button)
        buttons.addWidget(history_button)
        buttons.addStretch(1)
        where_layout.addLayout(buttons)
        layout.addWidget(where_frame)
        self._install_model()
        QShortcut(QKeySequence.Find, self, activated=self.show_find)
        QShortcut(QKeySequence("F3"), self, activated=lambda: self._request_find(True))
        QShortcut(
            QKeySequence("Shift+F3"),
            self,
            activated=lambda: self._request_find(False),
        )
        QShortcut(QKeySequence("Ctrl+G"), self, activated=self.prompt_goto_row)
        self.set_cache_state(
            handle.cached_row_count, handle.metadata.row_count, handle.cache_complete
        )

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
        self.model.page_loaded.connect(self._finish_pending_selection)
        self.table.setModel(self.model)
        if old_model:
            old_model.deleteLater()

    def start(self) -> None:
        initial_count = (
            self.handle.metadata.row_count
            if self.cache_complete
            else self.handle.cached_row_count
        )
        self.model.reset_query(filtered_count=initial_count)

    def _emit_apply(self) -> None:
        if not self.cache_complete or not self.visible_columns:
            return
        self.apply_requested.emit(self.where_editor.toPlainText())

    def _sort_requested(self, sort: SortSpec) -> None:
        self.generation += 1
        self.recount_next = False
        self.sort_changed.emit(sort)
        self.model.reset_query(filtered_count=self.model.filtered_count)

    def _header_clicked(self, column: int) -> None:
        if not self.cache_complete:
            return
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
        if columns == self.visible_columns:
            return
        self.visible_columns = list(columns)
        self.generation += 1
        self.recount_next = False
        self.model.reset_query(
            columns=self.visible_columns, filtered_count=self.model.filtered_count
        )
        self.apply_button.setEnabled(self.cache_complete and bool(self.visible_columns))

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
        self.cache_failed = False
        self.visible_columns = visible_columns
        self.compiled_filter = compiled
        self.generation += 1
        self.recount_next = bool(compiled.sql)
        self._install_model()
        if (
            handle.cache_complete
            and previous_sort
            and previous_sort.variable in visible_columns
        ):
            self.model.sort_spec = previous_sort
            section = visible_columns.index(previous_sort.variable)
            self.table.horizontalHeader().setSortIndicatorShown(True)
            self.table.horizontalHeader().setSortIndicator(
                section,
                Qt.AscendingOrder if previous_sort.ascending else Qt.DescendingOrder,
            )
        self.set_cache_state(
            handle.cached_row_count, handle.metadata.row_count, handle.cache_complete
        )
        initial_count = (
            handle.metadata.row_count
            if handle.cache_complete
            else handle.cached_row_count
        )
        self.model.reset_query(filtered_count=initial_count)

    def set_cache_state(
        self, cached_rows: int, total_rows: int, complete: bool
    ) -> None:
        self.cache_complete = complete
        if complete:
            self.cache_failed = False
        self.cache_notice.setVisible(not complete)
        self.cache_notice.setText(
            "Preparing full dataset cache in the background — "
            f"{cached_rows:,} / {total_rows:,} rows available. "
            "Filter, sort, find, go to row, and export will be enabled when complete."
        )
        self.apply_button.setEnabled(complete and bool(self.visible_columns))
        self.clear_button.setEnabled(complete)
        self.where_editor.setReadOnly(False)
        if not self.compiled_filter.sql and self.model.sort_spec is None:
            self.model.set_available_count(cached_rows)

    def show_find(self) -> None:
        self.find_frame.show()
        self.find_editor.setFocus()
        self.find_editor.selectAll()

    def _request_find(self, forward: bool) -> None:
        if not self.cache_complete or not self.visible_columns:
            self.find_status.setText("Available after loading completes")
            return
        text = self.find_editor.text()
        if not text:
            self.show_find()
            return
        current = self.table.currentIndex()
        start_row = (
            current.row()
            if current.isValid()
            else (-1 if forward else self.model.filtered_count)
        )
        self.find_status.setText("Searching…")
        self.find_requested.emit(text, forward, start_row, self.generation)

    def show_find_result(self, result: FindResult | None) -> None:
        if result is None:
            self.find_status.setText("Not found")
            return
        try:
            column = self.visible_columns.index(result.column_name)
        except ValueError:
            column = 0
        self.find_status.setText(f"Row {result.row_index + 1:,}")
        self.select_position(result.row_index, column)

    def prompt_goto_row(self) -> None:
        if (
            not self.cache_complete
            or not self.visible_columns
            or self.model.filtered_count == 0
        ):
            self.cache_notice.setVisible(True)
            return
        row, accepted = QInputDialog.getInt(
            self,
            "Go to Row",
            f"Row number (1–{self.model.filtered_count:,}):",
            min(self.table.currentIndex().row() + 1, self.model.filtered_count)
            if self.table.currentIndex().isValid()
            else 1,
            1,
            max(1, self.model.filtered_count),
            1,
        )
        if accepted:
            self.select_position(row - 1, 0)

    def select_position(self, row: int, column: int) -> None:
        if not (0 <= row < self.model.filtered_count) or not self.visible_columns:
            return
        self._pending_selection = (row, column)
        self.model.request_row(row)
        self.table.scrollTo(self.model.index(row, column), QTableView.PositionAtCenter)
        if self.model.is_row_loaded(row):
            self._finish_pending_selection(row, row)

    def _finish_pending_selection(self, first: int, last: int) -> None:
        if self._pending_selection is None:
            return
        row, column = self._pending_selection
        if first <= row <= last:
            self.table.setCurrentIndex(self.model.index(row, column))
            self.table.scrollTo(
                self.model.index(row, column), QTableView.PositionAtCenter
            )
            self._pending_selection = None

    def locate_variable(self, variable: str) -> None:
        try:
            column = self.visible_columns.index(variable)
        except ValueError:
            return
        self.table.scrollTo(self.model.index(0, column), QTableView.PositionAtCenter)
        self.table.selectColumn(column)
