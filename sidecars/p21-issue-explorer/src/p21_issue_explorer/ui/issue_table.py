from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QSizePolicy, QTableView

from ..models import IssueMatchResult, IssueStatus

STATUS_COLORS = {
    IssueStatus.MATCHED_EXACT: QColor("#dcfce7"),
    IssueStatus.REVIEW: QColor("#fef3c7"),
    IssueStatus.NOT_FOUND: QColor("#fee2e2"),
    IssueStatus.CANNOT_EVALUATE: QColor("#e5e7eb"),
}
INVALID_INDEX = QModelIndex()


class IssueTableModel(QAbstractTableModel):
    RESULT_ROLE = int(Qt.ItemDataRole.UserRole) + 1
    COLUMNS = (
        ("Status", lambda item: item.status.value),
        ("Rule ID", lambda item: item.issue.rule_id),
        ("Record", lambda item: item.issue.record),
        ("Variables", lambda item: item.issue.variables),
        ("Values", lambda item: item.issue.values),
        ("Message", lambda item: item.issue.message),
        ("Impact", lambda item: item.issue.impact),
        ("Type", lambda item: item.issue.issue_type),
        ("Exact Match Count", lambda item: item.exact_count),
        ("Partial/Review Match Count", lambda item: item.review_count),
        ("Current Match Count", lambda item: item.current_match_count),
    )

    def __init__(
        self,
        results: Sequence[IssueMatchResult] = (),
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._results = list(results)

    @property
    def results(self) -> tuple[IssueMatchResult, ...]:
        return tuple(self._results)

    def set_results(self, results: Sequence[IssueMatchResult]) -> None:
        self.beginResetModel()
        self._results = list(results)
        self.endResetModel()

    def result_at(self, row: int) -> IssueMatchResult | None:
        if 0 <= row < len(self._results):
            return self._results[row]
        return None

    def rowCount(self, parent: QModelIndex = INVALID_INDEX) -> int:
        return 0 if parent.isValid() else len(self._results)

    def columnCount(self, parent: QModelIndex = INVALID_INDEX) -> int:
        return 0 if parent.isValid() else len(self.COLUMNS)

    def data(
        self,
        index: QModelIndex,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ):
        if not index.isValid() or not 0 <= index.row() < len(self._results):
            return None
        result = self._results[index.row()]
        value = self.COLUMNS[index.column()][1](result)
        if role == Qt.ItemDataRole.DisplayRole:
            return value
        if role == Qt.ItemDataRole.ToolTipRole:
            return str(value)
        if role == Qt.ItemDataRole.BackgroundRole:
            return QBrush(STATUS_COLORS[result.status])
        if role == self.RESULT_ROLE:
            return result
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() >= 8:
            return Qt.AlignmentFlag.AlignCenter
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = int(Qt.ItemDataRole.DisplayRole),
    ):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(
            self.COLUMNS
        ):
            return self.COLUMNS[section][0]
        if orientation == Qt.Orientation.Vertical:
            return section + 1
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def sort(
        self,
        column: int,
        order: Qt.SortOrder = Qt.SortOrder.AscendingOrder,
    ) -> None:
        if not 0 <= column < len(self.COLUMNS):
            return
        accessor = self.COLUMNS[column][1]

        def key(result: IssueMatchResult) -> tuple[int, object]:
            value = accessor(result)
            if isinstance(value, int):
                return (0, value)
            return (1, str(value).casefold())

        self.layoutAboutToBeChanged.emit()
        self._results.sort(
            key=key,
            reverse=order == Qt.SortOrder.DescendingOrder,
        )
        self.layoutChanged.emit()


class IssueTableView(QTableView):
    result_activated = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("issueTable")
        self.issue_model = IssueTableModel(parent=self)
        self.setModel(self.issue_model)
        self.setSortingEnabled(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(False)
        self.setWordWrap(False)
        self.setMinimumHeight(100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate(
            (190, 150, 80, 240, 300, 360, 90, 100, 120, 170, 130)
        ):
            self.setColumnWidth(column, width)
        self.clicked.connect(self._activate_index)

    def set_results(self, results: Sequence[IssueMatchResult]) -> None:
        self.clearSelection()
        self.issue_model.set_results(results)

    def selected_result(self) -> IssueMatchResult | None:
        rows = self.selectionModel().selectedRows()
        return self.issue_model.result_at(rows[0].row()) if rows else None

    def select_first(self, *, activate: bool = False) -> None:
        if self.issue_model.rowCount() > 0:
            self.selectRow(0)
            result = self.issue_model.result_at(0)
            if activate and result is not None:
                self.result_activated.emit(result)

    def _activate_index(self, index: QModelIndex) -> None:
        result = self.issue_model.result_at(index.row())
        if result is not None:
            self.result_activated.emit(result)
