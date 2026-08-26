from __future__ import annotations

from collections import OrderedDict
from decimal import ROUND_HALF_UP, Decimal

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont

from .domain import DatasetMetadata, SortSpec

INVALID_INDEX = QModelIndex()

MANUAL_HIGHLIGHT_LABELS = {
    "#e8ddff": "Light Purple",
    "#dff3e5": "Light Green",
    "#ffe4c2": "Light Orange",
}


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
        self._page_highlights: OrderedDict[int, tuple[frozenset[str], ...]] = (
            OrderedDict()
        )
        self._page_row_warnings: OrderedDict[int, tuple[bool, ...]] = OrderedDict()
        self._page_decimal_bases: OrderedDict[int, tuple[int, ...]] = OrderedDict()
        self._page_source_rows: OrderedDict[int, tuple[int, ...]] = OrderedDict()
        self._loading_pages: set[int] = set()
        self._variable_by_name = {
            variable.name: variable for variable in metadata.variables
        }
        self.highlighted_columns: set[str] = set()
        self.highlighted_rows: set[int] = set()
        self.manual_row_highlights: dict[int, QColor] = {}

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
        is_categorical_header = self._is_categorical_header(page[local_row])
        if role in {Qt.DisplayRole, Qt.EditRole}:
            return self._display_value(
                value, self.columns[index.column()], offset, local_row
            )
        if role == Qt.TextAlignmentRole and isinstance(value, (int, float)):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.FontRole and is_categorical_header and index.column() == 0:
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ToolTipRole:
            tooltip = (
                "Missing"
                if value is None
                else self._display_value(
                    value, self.columns[index.column()], offset, local_row
                )
            )
            warning = dict(self.metadata.warning_column_messages).get(
                self.columns[index.column()]
            )
            return f"{tooltip}\n\nWarning: {warning}" if warning else tooltip
        if role == Qt.BackgroundRole and (
            self.columns[index.column()] in self.metadata.warning_columns
            or self.is_warning_row(index.row())
        ):
            return QColor("#ffd9d9")
        if (
            role == Qt.BackgroundRole
            and self.columns[index.column()]
            in self._page_highlights.get(offset, (frozenset(),) * len(page))[local_row]
        ):
            return QColor("#ffe29a")
        if (
            role == Qt.BackgroundRole
            and index.row() in self.highlighted_rows
            and self.columns[index.column()] in self.highlighted_columns
        ):
            return QColor("#fff2b2")
        if role == Qt.BackgroundRole and is_categorical_header:
            return QColor("#edf5fc")
        manual = self.manual_highlight_color(index.row())
        if role == Qt.BackgroundRole and manual is not None:
            return manual
        return None

    def _is_categorical_header(self, row: tuple[object, ...]) -> bool:
        item_level = self.metadata.categorical_item_level_column
        return bool(
            item_level
            and self.columns
            and self.columns[0] == item_level
            and row[0] not in {None, ""}
            and all(value in {None, ""} for value in row[1:])
        )

    def _display_value(
        self, value: object, column_name: str, offset: int, local_row: int
    ) -> str:
        if value is None:
            return ""
        offsets = dict(self.metadata.statistic_decimal_offsets)
        if column_name not in offsets or not isinstance(value, (int, float)):
            return str(value)
        bases = self._page_decimal_bases.get(offset, ())
        base = bases[local_row] if local_row < len(bases) else 0
        decimals = min(4, max(0, int(base) + int(offsets[column_name])))
        quantum = Decimal(1).scaleb(-decimals)
        rounded = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
        return f"{rounded:.{decimals}f}"

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole
    ):
        if orientation == Qt.Horizontal and 0 <= section < len(self.columns):
            variable = self._variable_by_name[self.columns[section]]
            if role == Qt.DisplayRole:
                return dict(self.metadata.display_column_names).get(
                    variable.name, variable.name
                )
            if role == Qt.ToolTipRole:
                details = [variable.name]
                if variable.label:
                    details.append(variable.label)
                details.append(variable.kind.title())
                if variable.length is not None:
                    details.append(f"Length: {variable.length}")
                if variable.format:
                    details.append(f"Format: {variable.format}")
                warning = dict(self.metadata.warning_column_messages).get(variable.name)
                if warning:
                    details.extend(("", f"Warning: {warning}"))
                return "\n".join(details)
            if (
                role == Qt.BackgroundRole
                and variable.name in self.metadata.warning_columns
            ):
                return QColor("#ffd9d9")
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
        self._page_highlights.clear()
        self._page_row_warnings.clear()
        self._page_decimal_bases.clear()
        self._page_source_rows.clear()
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
        cell_highlights: tuple[frozenset[str], ...] = (),
        row_warnings: tuple[bool, ...] = (),
        row_decimal_bases: tuple[int, ...] = (),
        source_rows: tuple[int, ...] = (),
    ) -> None:
        self._loading_pages.discard(offset)
        if filtered_count != self.filtered_count:
            self.beginResetModel()
            self.filtered_count = filtered_count
            self._pages.clear()
            self._page_highlights.clear()
            self._page_row_warnings.clear()
            self._page_decimal_bases.clear()
            self._page_source_rows.clear()
            self._loading_pages.clear()
            self.endResetModel()
        if not rows:
            return
        self._pages[offset] = rows
        self._page_highlights[offset] = cell_highlights or tuple(
            frozenset() for _row in rows
        )
        self._page_row_warnings[offset] = row_warnings or tuple(False for _row in rows)
        self._page_decimal_bases[offset] = row_decimal_bases or tuple(
            0 for _row in rows
        )
        self._page_source_rows[offset] = source_rows or tuple(
            offset + index + 1 for index, _row in enumerate(rows)
        )
        self._pages.move_to_end(offset)
        while len(self._pages) > self.max_cached_pages:
            expired, _rows = self._pages.popitem(last=False)
            self._page_highlights.pop(expired, None)
            self._page_row_warnings.pop(expired, None)
            self._page_decimal_bases.pop(expired, None)
            self._page_source_rows.pop(expired, None)
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
            self._page_highlights.clear()
            self._page_row_warnings.clear()
            self._page_decimal_bases.clear()
            self._page_source_rows.clear()
            self._loading_pages.clear()
            self.endResetModel()

    def load_failed(self, offset: int) -> None:
        self._loading_pages.discard(offset)

    def is_generated_highlight(self, row: int, column_name: str) -> bool:
        offset = self._page_offset(row)
        values = self._page_highlights.get(offset, ())
        local = row - offset
        return local < len(values) and column_name in values[local]

    def is_warning_row(self, row: int) -> bool:
        offset = self._page_offset(row)
        values = self._page_row_warnings.get(offset, ())
        local = row - offset
        return local < len(values) and values[local]

    def source_row_id(self, row: int) -> int | None:
        offset = self._page_offset(row)
        source_rows = self._page_source_rows.get(offset, ())
        local = row - offset
        return int(source_rows[local]) if 0 <= local < len(source_rows) else None

    def manual_highlight_color(self, row: int) -> QColor | None:
        source_row = self.source_row_id(row)
        return None if source_row is None else self.manual_row_highlights.get(source_row)

    def manual_highlights_for_export(self) -> dict[int, str]:
        """Return manual row highlights keyed by the persisted source row ID."""
        return {
            source_row: MANUAL_HIGHLIGHT_LABELS.get(
                color.name().casefold(), color.name()
            )
            for source_row, color in self.manual_row_highlights.items()
        }

    def set_manual_row_highlight(self, rows: set[int], color: QColor) -> None:
        source_rows = {
            source_row
            for row in rows
            if (source_row := self.source_row_id(row)) is not None
        }
        if not source_rows:
            return
        changed = {
            row
            for offset, page_source_rows in self._page_source_rows.items()
            for row, source_row in enumerate(page_source_rows, start=offset)
            if source_row in source_rows
        }
        for source_row in source_rows:
            self.manual_row_highlights[source_row] = color
        self._emit_background_changed(changed)

    def clear_manual_row_highlight(self, rows: set[int]) -> None:
        source_rows = {
            source_row
            for row in rows
            if (source_row := self.source_row_id(row)) is not None
        }
        if not source_rows:
            return
        changed = {
            row
            for offset, page_source_rows in self._page_source_rows.items()
            for row, source_row in enumerate(page_source_rows, start=offset)
            if source_row in source_rows and source_row in self.manual_row_highlights
        }
        for source_row in source_rows:
            self.manual_row_highlights.pop(source_row, None)
        self._emit_background_changed(changed)

    def clear_all_manual_highlights(self) -> None:
        if not self.manual_row_highlights:
            return
        highlighted = set(self.manual_row_highlights)
        self.manual_row_highlights.clear()
        changed = {
            row
            for offset, page_source_rows in self._page_source_rows.items()
            for row, source_row in enumerate(page_source_rows, start=offset)
            if source_row in highlighted
        }
        self._emit_background_changed(changed)

    def _emit_background_changed(self, rows: set[int]) -> None:
        if not self.columns:
            return
        for row in sorted(rows):
            self.dataChanged.emit(
                self.index(row, 0),
                self.index(row, len(self.columns) - 1),
                [Qt.BackgroundRole],
            )

    def set_highlighted_cells(self, rows: set[int], columns: set[str]) -> None:
        rows = {row for row in rows if 0 <= row < self.filtered_count}
        if columns == self.highlighted_columns and rows == self.highlighted_rows:
            return
        changed_rows = self.highlighted_rows | rows
        self.highlighted_columns = set(columns)
        self.highlighted_rows = rows
        if self.columns:
            for row in sorted(changed_rows):
                self.dataChanged.emit(
                    self.index(row, 0),
                    self.index(row, len(self.columns) - 1),
                    [Qt.BackgroundRole],
                )

    def clear_highlights(self) -> None:
        if self.highlighted_rows or self.highlighted_columns:
            changed_rows = set(self.highlighted_rows)
            self.highlighted_rows.clear()
            self.highlighted_columns.clear()
            for row in sorted(changed_rows):
                if self.columns and row < self.filtered_count:
                    self.dataChanged.emit(
                        self.index(row, 0),
                        self.index(row, len(self.columns) - 1),
                        [Qt.BackgroundRole],
                    )

    def sort(self, column: int, order: Qt.SortOrder = Qt.AscendingOrder) -> None:
        if not 0 <= column < len(self.columns):
            return
        self.sort_spec = SortSpec(self.columns[column], order == Qt.AscendingOrder)
        self.sort_requested.emit(self.sort_spec)
