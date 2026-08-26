from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..merge_datasets import MergeDatasetsConfig, MergeSortItem
from ..merge_datasets.result_store import build_result_schema


class _SortRow(QWidget):
    def __init__(self, variable: str, direction: str, parent=None) -> None:
        super().__init__(parent)
        self.variable = variable
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.addWidget(QLabel(variable), 1)
        self.direction = QComboBox()
        self.direction.addItem("ASC", "ASC")
        self.direction.addItem("DESC", "DESC")
        self.direction.setCurrentIndex(0 if direction == "ASC" else 1)
        self.direction.currentIndexChanged.connect(lambda _index: self.changed.emit())
        layout.addWidget(self.direction)
        self.up = QPushButton("↑")
        self.up.setToolTip("Move up")
        self.up.setFixedWidth(28)
        self.up.clicked.connect(lambda: self.move_requested.emit(-1))
        layout.addWidget(self.up)
        self.down = QPushButton("↓")
        self.down.setToolTip("Move down")
        self.down.setFixedWidth(28)
        self.down.clicked.connect(lambda: self.move_requested.emit(1))
        layout.addWidget(self.down)
        self.remove = QPushButton("Remove")
        self.remove.setFixedWidth(64)
        self.remove.clicked.connect(self.remove_requested)
        layout.addWidget(self.remove)

    changed = Signal()
    move_requested = Signal(int)
    remove_requested = Signal()

    def sort_item(self) -> MergeSortItem:
        return MergeSortItem(self.variable, self.direction.currentData())


class DatasetMergePanel(QWidget):
    """Small Builder for two already-open, fully cached datasets."""

    merge_requested = Signal(object, object, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("datasetMergePanel")
        self._datasets: list[tuple[object, str, bool]] = []
        self._previous_by: tuple[str, ...] = ()
        self._sort_items: tuple[MergeSortItem, ...] = ()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel("Merge Datasets")
        title.setObjectName("panelTitle")
        layout.addWidget(title)
        description = QLabel(
            "Join two fully cached datasets. All rows are used; current Viewer "
            "WHERE conditions are not applied."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        form = QFormLayout()
        self.left_dataset = QComboBox()
        self.right_dataset = QComboBox()
        for combo in (self.left_dataset, self.right_dataset):
            combo.currentIndexChanged.connect(self._datasets_changed)
            form.addRow(
                "Left Dataset" if combo is self.left_dataset else "Right Dataset", combo
            )
        layout.addLayout(form)

        layout.addWidget(QLabel("BY Variables (common variables only)"))
        self.by_variables = QListWidget()
        self.by_variables.setSelectionMode(QListWidget.NoSelection)
        self.by_variables.itemChanged.connect(self._by_changed)
        self.by_variables.setMinimumHeight(130)
        layout.addWidget(self.by_variables, 1)

        layout.addWidget(QLabel("Sort by (optional; output order)"))
        sort_input = QHBoxLayout()
        self.sort_editor = QLineEdit()
        self.sort_editor.setPlaceholderText("Result variable, then press Enter")
        self.sort_editor.returnPressed.connect(self._add_sort_from_editor)
        sort_input.addWidget(self.sort_editor, 1)
        self.sort_direction = QComboBox()
        self.sort_direction.addItem("ASC", "ASC")
        self.sort_direction.addItem("DESC", "DESC")
        sort_input.addWidget(self.sort_direction)
        add_sort = QPushButton("Add")
        add_sort.clicked.connect(self._add_sort_from_editor)
        sort_input.addWidget(add_sort)
        layout.addLayout(sort_input)
        self.sort_variables = QListWidget()
        self.sort_variables.setSelectionMode(QListWidget.NoSelection)
        self.sort_variables.setMaximumHeight(150)
        layout.addWidget(self.sort_variables)

        join_row = QHBoxLayout()
        join_row.addWidget(QLabel("Join Type"))
        self.join_type = QComboBox()
        self.join_type.addItem("Left Join", "left")
        self.join_type.addItem("Right Join", "right")
        self.join_type.addItem("Inner Join", "inner")
        self.join_type.addItem("Full Join", "full")
        join_row.addWidget(self.join_type, 1)
        layout.addLayout(join_row)

        self.summary = QLabel("Select two datasets and at least one BY variable.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.run_button = QPushButton("Run Merge")
        self.run_button.setDefault(True)
        self.run_button.clicked.connect(self._start_merge)
        buttons.addWidget(self.run_button)
        layout.addLayout(buttons)
        self._update_enabled()

    def set_datasets(self, datasets: list[tuple[object, str, bool]]) -> None:
        previous_left = self.left_dataset.currentData()
        previous_right = self.right_dataset.currentData()
        self._datasets = list(datasets)
        for combo, previous in (
            (self.left_dataset, previous_left),
            (self.right_dataset, previous_right),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Select dataset…", None)
            for owner, label, complete in self._datasets:
                combo.addItem(label if complete else f"{label} (loading…)", owner)
                combo.setItemData(combo.count() - 1, bool(complete), Qt.UserRole + 1)
                if not complete:
                    combo.model().item(combo.count() - 1).setEnabled(False)
            index = combo.findData(previous)
            combo.setCurrentIndex(max(index, 0))
            combo.blockSignals(False)
        self._datasets_changed()

    def _datasets_changed(self) -> None:
        left = self.left_dataset.currentData()
        right = self.right_dataset.currentData()
        self._rebuild_by_variables(left, right)
        self._update_enabled()

    def _rebuild_by_variables(self, left, right) -> None:
        self.by_variables.blockSignals(True)
        self.by_variables.clear()
        if left is None or right is None or left is right:
            self.by_variables.blockSignals(False)
            self._set_sort_items(())
            return
        right_by_name = {
            variable.name.casefold(): variable
            for variable in right.handle.metadata.variables
        }
        common = []
        for variable in left.handle.metadata.variables:
            other = right_by_name.get(variable.name.casefold())
            if other is not None:
                common.append((variable, other))
        selected = set(self._previous_by)
        for variable, other in common:
            item = QListWidgetItem(variable.name)
            item.setData(Qt.UserRole, variable.kind)
            item.setToolTip(
                f"Left: {variable.kind}; Right: {other.kind}"
                + (" (incompatible type)" if variable.kind != other.kind else "")
            )
            if variable.kind == other.kind and variable.name.casefold() in {
                name.casefold() for name in selected
            }:
                item.setCheckState(Qt.Checked)
            else:
                item.setCheckState(Qt.Unchecked)
            if variable.kind != other.kind:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.by_variables.addItem(item)
        self.by_variables.blockSignals(False)
        self._previous_by = tuple(
            item.text()
            for index in range(self.by_variables.count())
            if (item := self.by_variables.item(index)).checkState() == Qt.Checked
        )
        self._prune_sort_items()

    def _by_changed(self, _item: QListWidgetItem) -> None:
        self._previous_by = tuple(
            self.by_variables.item(index).text()
            for index in range(self.by_variables.count())
            if self.by_variables.item(index).checkState() == Qt.Checked
        )
        # The BY variables define the result schema.  Prune sort items now so
        # the editor never carries stale columns until Run is pressed.
        self._prune_sort_items()
        self._update_enabled()

    def _selected_by(self) -> tuple[str, ...]:
        return self._previous_by

    def _available_output_variables(self) -> tuple[str, ...]:
        left = self.left_dataset.currentData()
        right = self.right_dataset.currentData()
        if left is None or right is None or not self._selected_by():
            return ()
        try:
            schema, _mapping = build_result_schema(
                left.handle.metadata,
                right.handle.metadata,
                self._selected_by(),
            )
        except (KeyError, ValueError):
            return ()
        return tuple(variable.name for variable in schema.all_variables)

    def _prune_sort_items(self) -> None:
        available = {
            name.casefold(): name for name in self._available_output_variables()
        }
        preserved = tuple(
            MergeSortItem(available[item.variable.casefold()], item.direction)
            for item in self._sort_items
            if item.variable.casefold() in available
        )
        self._set_sort_items(preserved)

    def _set_sort_items(self, items: tuple[MergeSortItem, ...]) -> None:
        self._sort_items = items
        self.sort_variables.clear()
        for item in items:
            list_item = QListWidgetItem()
            row = _SortRow(item.variable, item.direction)
            row.changed.connect(self._sort_changed)
            row.move_requested.connect(
                lambda delta, variable=item.variable: self._move_sort(variable, delta)
            )
            row.remove_requested.connect(
                lambda variable=item.variable: self._remove_sort(variable)
            )
            list_item.setSizeHint(row.sizeHint())
            self.sort_variables.addItem(list_item)
            self.sort_variables.setItemWidget(list_item, row)

    def _sort_changed(self) -> None:
        self._sort_items = self._read_sort_items()
        self._update_enabled()

    def _read_sort_items(self) -> tuple[MergeSortItem, ...]:
        items: list[MergeSortItem] = []
        for index in range(self.sort_variables.count()):
            row = self.sort_variables.itemWidget(self.sort_variables.item(index))
            if isinstance(row, _SortRow):
                items.append(row.sort_item())
        return tuple(items)

    def _remove_sort(self, variable: str) -> None:
        self._set_sort_items(
            tuple(item for item in self._read_sort_items() if item.variable != variable)
        )
        self._update_enabled()

    def _move_sort(self, variable: str, delta: int) -> None:
        items = list(self._read_sort_items())
        try:
            index = next(
                index for index, item in enumerate(items) if item.variable == variable
            )
        except StopIteration:
            return
        target = index + delta
        if not 0 <= target < len(items):
            return
        items[index], items[target] = items[target], items[index]
        self._set_sort_items(tuple(items))
        self._update_enabled()

    def _add_sort_from_editor(self) -> None:
        text = self.sort_editor.text().strip()
        if not text:
            return
        available = {
            name.casefold(): name for name in self._available_output_variables()
        }
        variable = available.get(text.casefold())
        if variable is None:
            self.status.setText(
                f'Sort variable "{text}" does not exist in the Merge Result output.'
            )
            return
        if any(
            item.variable.casefold() == variable.casefold()
            for item in self._read_sort_items()
        ):
            self.status.setText(f'Sort variable "{variable}" is already present.')
            return
        direction = self.sort_direction.currentData() or "ASC"
        self._set_sort_items(
            (*self._read_sort_items(), MergeSortItem(variable, direction))
        )
        self.sort_editor.clear()
        self.sort_direction.setCurrentIndex(0)
        self._update_enabled()

    def _update_enabled(self) -> None:
        left = self.left_dataset.currentData()
        right = self.right_dataset.currentData()
        complete = (
            left is not None
            and right is not None
            and left is not right
            and bool(self.left_dataset.currentData(Qt.UserRole + 1))
            and bool(self.right_dataset.currentData(Qt.UserRole + 1))
        )
        self.run_button.setEnabled(complete and bool(self._selected_by()))
        if left is right and left is not None:
            self.status.setText("Left and Right must be different datasets.")
        elif not complete:
            self.status.setText("Select two fully loaded datasets.")
        elif not self._selected_by():
            self.status.setText("Select at least one BY variable.")
        else:
            self.status.setText("")
        self._sort_items = self._read_sort_items()
        if complete:
            sort_text = ", ".join(
                f"{item.variable} {item.direction}" for item in self._sort_items
            )
            self.summary.setText(
                f"Left rows: {left.handle.metadata.row_count:,}\n"
                f"Right rows: {right.handle.metadata.row_count:,}\n"
                f"BY: {', '.join(self._selected_by()) or '—'}\n"
                f"Sort: {sort_text or 'Default stable order'}"
            )
        else:
            self.summary.setText("Select two datasets and at least one BY variable.")

    def _start_merge(self) -> None:
        left = self.left_dataset.currentData()
        right = self.right_dataset.currentData()
        if left is None or right is None:
            return
        config = MergeDatasetsConfig(
            self._selected_by(),
            self.join_type.currentData(),
            self._read_sort_items(),
        )
        try:
            config.validate()
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.merge_requested.emit(left, right, config)

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.left_dataset.setEnabled(not busy)
        self.right_dataset.setEnabled(not busy)
        self.by_variables.setEnabled(not busy)
        self.sort_editor.setEnabled(not busy)
        self.sort_direction.setEnabled(not busy)
        self.sort_variables.setEnabled(not busy)
        self.join_type.setEnabled(not busy)
        self.run_button.setEnabled(not busy and bool(self._selected_by()))
        if message:
            self.status.setText(message)
