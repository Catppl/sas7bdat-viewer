from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal

from .domain import DatasetMetadata, SortSpec

INVALID_INDEX = QModelIndex()


class DatasetTableModel(QAbstractTableModel):
    page_requested = Signal(int, int)
    sort_requested = Signal(object)

    def __init__(
        self, metadata: DatasetMetadata, columns: list[str], page_size: int = 500
    ) -> None:
        super().__init__()
        self.metadata = metadata
        self.columns = columns
        self.page_size = page_size
        self.rows: list[tuple[object, ...]] = []
        self.filtered_count = metadata.row_count
        self.loading = False
        self.sort_spec: SortSpec | None = None
        self._variable_by_name = {
            variable.name: variable for variable in metadata.variables
        }

    def rowCount(self, parent: QModelIndex = INVALID_INDEX) -> int:
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = INVALID_INDEX) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if (
            not index.isValid()
            or index.row() >= len(self.rows)
            or index.column() >= len(self.columns)
        ):
            return None
        value = self.rows[index.row()][index.column()]
        if role in {Qt.DisplayRole, Qt.EditRole}:
            return "" if value is None else str(value)
        if role == Qt.TextAlignmentRole and isinstance(value, (int, float)):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            return "Missing" if value is None else str(value)
        return None

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole
    ):
        if orientation == Qt.Horizontal and 0 <= section < len(self.columns):
            variable = self._variable_by_name[self.columns[section]]
            if role == Qt.DisplayRole:
                return variable.name
            if role == Qt.ToolTipRole:
                details = [variable.name]
                if variable.label:
                    details.append(variable.label)
                details.append(variable.kind.title())
                if variable.length is not None:
                    details.append(f"Length: {variable.length}")
                if variable.format:
                    details.append(f"Format: {variable.format}")
                return "\n".join(details)
        if orientation == Qt.Vertical and role == Qt.DisplayRole:
            return section + 1
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        return (
            Qt.ItemIsEnabled | Qt.ItemIsSelectable
            if index.isValid()
            else Qt.NoItemFlags
        )

    def canFetchMore(self, parent: QModelIndex = INVALID_INDEX) -> bool:
        return (
            not parent.isValid()
            and not self.loading
            and len(self.rows) < self.filtered_count
        )

    def fetchMore(self, parent: QModelIndex = INVALID_INDEX) -> None:
        if not self.canFetchMore(parent):
            return
        self.loading = True
        self.page_requested.emit(len(self.rows), self.page_size)

    def reset_query(
        self, *, columns: list[str] | None = None, filtered_count: int | None = None
    ) -> None:
        self.beginResetModel()
        if columns is not None:
            self.columns = list(columns)
        self.rows.clear()
        self.filtered_count = (
            self.metadata.row_count if filtered_count is None else filtered_count
        )
        self.loading = False
        self.endResetModel()
        self.fetchMore()

    def append_page(
        self, rows: tuple[tuple[object, ...], ...], filtered_count: int
    ) -> None:
        self.filtered_count = filtered_count
        if rows:
            first = len(self.rows)
            self.beginInsertRows(QModelIndex(), first, first + len(rows) - 1)
            self.rows.extend(rows)
            self.endInsertRows()
        self.loading = False

    def load_failed(self) -> None:
        self.loading = False

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder) -> None:
        if not 0 <= column < len(self.columns):
            return
        self.sort_spec = SortSpec(self.columns[column], order == Qt.AscendingOrder)
        self.sort_requested.emit(self.sort_spec)
