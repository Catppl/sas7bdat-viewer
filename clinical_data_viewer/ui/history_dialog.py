from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..filter_history import FilterHistory, format_history_timestamp


class HistoryDialog(QDialog):
    condition_selected = Signal(str)

    def __init__(
        self, history: FilterHistory, dataset_path: Path | None, parent=None
    ) -> None:
        super().__init__(parent)
        self.history = history
        self.dataset_path = dataset_path
        self.setWindowTitle("Filter History")
        self.resize(900, 460)
        layout = QVBoxLayout(self)
        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("Show:"))
        self.scope = QComboBox()
        self.scope.addItems(["Current dataset", "All datasets"])
        self.scope.setEnabled(dataset_path is not None)
        self.scope.currentIndexChanged.connect(self.refresh)
        scope_row.addWidget(self.scope)
        scope_row.addStretch(1)
        layout.addLayout(scope_row)
        self.table = QTreeWidget()
        self.table.setObjectName("historyTable")
        self.table.setColumnCount(3)
        self.table.setHeaderLabels(["Date / Time", "Dataset", "Filter"])
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setUniformRowHeights(True)
        self.table.setTextElideMode(Qt.ElideRight)
        self.table.header().resizeSection(0, 160)
        self.table.header().resizeSection(1, 170)
        self.table.header().setStretchLastSection(True)
        self.table.itemDoubleClicked.connect(lambda _item, _column: self.use_selected())
        layout.addWidget(self.table)
        action_row = QHBoxLayout()
        use_button = QPushButton("Use Condition")
        use_button.clicked.connect(self.use_selected)
        delete_button = QPushButton("Delete")
        delete_button.clicked.connect(self.delete_selected)
        clear_button = QPushButton("Clear History")
        clear_button.clicked.connect(self.clear_history)
        action_row.addWidget(use_button)
        action_row.addWidget(delete_button)
        action_row.addWidget(clear_button)
        action_row.addStretch(1)
        close_box = QDialogButtonBox(QDialogButtonBox.Close)
        close_box.rejected.connect(self.reject)
        action_row.addWidget(close_box)
        layout.addLayout(action_row)
        self.refresh()

    def _current_only(self) -> bool:
        return self.dataset_path is not None and self.scope.currentIndex() == 0

    def refresh(self) -> None:
        self.table.clear()
        entries = self.history.list(self.dataset_path if self._current_only() else None)
        for entry in entries:
            executed_at = format_history_timestamp(entry.executed_at)
            item = QTreeWidgetItem(
                [executed_at, entry.dataset_name, entry.where_text]
            )
            item.setData(0, Qt.UserRole, entry)
            item.setToolTip(
                0, format_history_timestamp(entry.executed_at, include_timezone=True)
            )
            item.setToolTip(1, entry.dataset_path)
            item.setToolTip(2, entry.where_text)
            self.table.addTopLevelItem(item)

    def use_selected(self) -> None:
        item = self.table.currentItem()
        if item:
            self.condition_selected.emit(item.data(0, Qt.UserRole).where_text)
            self.accept()

    def delete_selected(self) -> None:
        item = self.table.currentItem()
        if item:
            self.history.delete(item.data(0, Qt.UserRole).id)
            self.refresh()

    def clear_history(self) -> None:
        scope = "the current dataset" if self._current_only() else "all datasets"
        if (
            QMessageBox.question(
                self, "Clear Filter History", f"Clear history for {scope}?"
            )
            != QMessageBox.Yes
        ):
            return
        self.history.clear(self.dataset_path if self._current_only() else None)
        self.refresh()
