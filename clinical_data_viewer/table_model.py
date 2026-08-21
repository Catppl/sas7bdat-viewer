from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal

from .domain import DatasetMetadata, SortSpec

INVALID_INDEX = QModelIndex()


class DatasetTableModel(QAbstractTableModel):
    """Virtual table model that keeps only a bounded number of SQLite pages."""

    page_requested = Signal(int, int)
    page_loaded = Signal(int, int)
    sort_requested = Signal(object)

    def __init__(
        self,
        metadata: DatasetMetadata,
        columns: list[str],
        page_size: int = 500,
        max_cached_pages: int = 12,
    ) -> None:
        super().__init__()
        self.metadata = metadata
        self.columns = columns
        self.page_size = page_size
        self.max_cached_pages = max_cached_pages
        self.filtered_count = metadata.row_count
        self.sort_spec: SortSpec | None = None
        self._pages: OrderedDict[int, tuple[tuple[object, ...], ...]] = OrderedDict()
        self._loading_pages: set[int] = set()
        self._variable_by_name = {
            variable.name: variable for variable in metadata.variables
        }

    def rowCount(self, parent: QModelIndex = INVALID_INDEX) -> int:
        return 0 if parent.isValid() else self.filtered_count

    def columnCount(self, parent: QModelIndex = INVALID_INDEX) -> int:
        return 0 if parent.isValid() else len(self.columns)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if (
            not index.isValid()
            or index.row() >= self.filtered_count
            or index.column() >= len(self.columns)
        ):
            return None
        offset = self._page_offset(index.row())
        page = self._pages.get(offset)
        if page is None:
            if role in {Qt.DisplayRole, Qt.EditRole, Qt.ToolTipRole}:
                self.request_row(index.row())
            return None
        self._pages.move_to_end(offset)
        local_row = index.row() - offset
        if local_row >= len(page):
            return None
        value = page[local_row][index.column()]
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

    def _page_offset(self, row: int) -> int:
        return (row // self.page_size) * self.page_size

    def request_row(self, row: int) -> None:
        if not self.columns or not 0 <= row < self.filtered_count:
            return
        offset = self._page_offset(row)
        if offset in self._pages or offset in self._loading_pages:
            return
        self._loading_pages.add(offset)
        QTimer.singleShot(
            0, lambda offset=offset: self.page_requested.emit(offset, self.page_size)
        )

    def is_row_loaded(self, row: int) -> bool:
        offset = self._page_offset(row)
        page = self._pages.get(offset)
        return page is not None and row - offset < len(page)

    def reset_query(
        self, *, columns: list[str] | None = None, filtered_count: int | None = None
    ) -> None:
        self.beginResetModel()
        if columns is not None:
            self.columns = list(columns)
        self._pages.clear()
        self._loading_pages.clear()
        self.filtered_count = (
            self.metadata.row_count if filtered_count is None else filtered_count
        )
        self.endResetModel()
        if self.columns and self.filtered_count:
            self.request_row(0)

    def set_page(
        self,
        offset: int,
        rows: tuple[tuple[object, ...], ...],
        filtered_count: int,
    ) -> None:
        self._loading_pages.discard(offset)
        if filtered_count != self.filtered_count:
            self.beginResetModel()
            self.filtered_count = filtered_count
            self._pages.clear()
            self._loading_pages.clear()
            self.endResetModel()
        if not rows:
            return
        self._pages[offset] = rows
        self._pages.move_to_end(offset)
        while len(self._pages) > self.max_cached_pages:
            self._pages.popitem(last=False)
        bottom = min(offset + len(rows), self.filtered_count) - 1
        if bottom >= offset and self.columns:
            self.dataChanged.emit(
                self.index(offset, 0),
                self.index(bottom, len(self.columns) - 1),
                [Qt.DisplayRole, Qt.EditRole, Qt.ToolTipRole],
            )
            self.page_loaded.emit(offset, bottom)

    def set_available_count(self, row_count: int) -> None:
        if row_count == self.filtered_count:
            return
        if row_count > self.filtered_count:
            self.beginInsertRows(QModelIndex(), self.filtered_count, row_count - 1)
            self.filtered_count = row_count
            self.endInsertRows()
        else:
            self.beginResetModel()
            self.filtered_count = row_count
            self._pages.clear()
            self._loading_pages.clear()
            self.endResetModel()

    def load_failed(self, offset: int) -> None:
        self._loading_pages.discard(offset)

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder) -> None:
        if not 0 <= column < len(self.columns):
            return
        self.sort_spec = SortSpec(self.columns[column], order == Qt.AscendingOrder)
        self.sort_requested.emit(self.sort_spec)
