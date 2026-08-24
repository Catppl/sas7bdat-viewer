from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..column_filters import ColumnFilterSpec
from ..domain import DistinctValuesResult, VariableMetadata


class ColumnFilterDialog(QDialog):
    def __init__(
        self,
        variable: VariableMetadata,
        values: DistinctValuesResult,
        existing: ColumnFilterSpec | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.variable = variable
        self.values = values
        self.existing = existing
        self.result_spec: ColumnFilterSpec | None = existing
        self.setWindowTitle(f"Filter {variable.name}")
        self.resize(390, 500)
        layout = QVBoxLayout(self)
        title = QLabel(f"{variable.name}  —  {variable.label or variable.kind.title()}")
        title.setObjectName("panelTitle")
        layout.addWidget(title)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._create_values_tab()
        self._create_condition_tab()
        if existing and existing.mode in {"condition", "between", "contains"}:
            self.tabs.setCurrentIndex(1)
        clear_button = QPushButton("Clear Column Filter")
        clear_button.clicked.connect(self._clear)
        layout.addWidget(clear_button)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._accept_filter)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _create_values_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.value_search = QLineEdit()
        self.value_search.setPlaceholderText("Search loaded values")
        self.value_search.textChanged.connect(self._filter_items)
        layout.addWidget(self.value_search)
        self.select_all = QCheckBox("Select All")
        self.select_all.setTristate(False)
        self.select_all.clicked.connect(self._toggle_all)
        layout.addWidget(self.select_all)
        self.value_list = QListWidget()
        layout.addWidget(self.value_list, 1)
        existing = self.existing
        include_mode = not existing or existing.mode == "exclude"
        included = (
            set(existing.values) if existing and existing.mode == "include" else set()
        )
        excluded = (
            set(existing.values) if existing and existing.mode == "exclude" else set()
        )
        for value in self.values.values:
            item = QListWidgetItem(str(value))
            item.setData(Qt.UserRole, value)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            checked = value not in excluded if include_mode else value in included
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            self.value_list.addItem(item)
        self.missing_item: QListWidgetItem | None = None
        if self.values.has_missing:
            self.missing_item = QListWidgetItem("(Missing)")
            self.missing_item.setData(Qt.UserRole, None)
            self.missing_item.setFlags(
                self.missing_item.flags() | Qt.ItemIsUserCheckable
            )
            include_missing = existing.include_missing if existing else True
            self.missing_item.setCheckState(
                Qt.Checked if include_missing else Qt.Unchecked
            )
            self.value_list.insertItem(0, self.missing_item)
        self._initial_all = include_mode
        self.select_all.setChecked(include_mode)
        self.value_list.itemChanged.connect(self._sync_select_all)
        if self.values.truncated:
            note = QLabel(
                f"Showing first {len(self.values.values):,} of "
                f"{self.values.total_distinct:,} distinct values. Unchecking values "
                "excludes only those values; use Condition for other values."
            )
            note.setWordWrap(True)
            note.setObjectName("filterNotice")
            layout.addWidget(note)
        self.tabs.addTab(page, "Values")

    def _create_condition_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        row = QHBoxLayout()
        self.operator = QComboBox()
        operators = ["=", "!=", ">", ">=", "<", "<=", "Between"]
        if self.variable.kind == "character":
            operators.append("Contains")
        self.operator.addItems(operators)
        self.operator.currentTextChanged.connect(self._operator_changed)
        row.addWidget(self.operator)
        self.condition_stack = QStackedWidget()
        single = QWidget()
        single_layout = QHBoxLayout(single)
        single_layout.setContentsMargins(0, 0, 0, 0)
        self.first_value = QLineEdit()
        single_layout.addWidget(self.first_value)
        between = QWidget()
        between_layout = QHBoxLayout(between)
        between_layout.setContentsMargins(0, 0, 0, 0)
        self.lower_value = QLineEdit()
        self.upper_value = QLineEdit()
        between_layout.addWidget(self.lower_value)
        between_layout.addWidget(QLabel("and"))
        between_layout.addWidget(self.upper_value)
        self.condition_stack.addWidget(single)
        self.condition_stack.addWidget(between)
        row.addWidget(self.condition_stack, 1)
        layout.addLayout(row)
        layout.addStretch(1)
        existing = self.existing
        if existing and existing.mode in {"condition", "between", "contains"}:
            label = (
                "Between"
                if existing.mode == "between"
                else "Contains"
                if existing.mode == "contains"
                else existing.operator
            )
            self.operator.setCurrentText(label)
            self.first_value.setText(
                "" if existing.lower is None else str(existing.lower)
            )
            self.lower_value.setText(
                "" if existing.lower is None else str(existing.lower)
            )
            self.upper_value.setText(
                "" if existing.upper is None else str(existing.upper)
            )
        self._operator_changed(self.operator.currentText())
        self.tabs.addTab(page, "Condition")

    def _filter_items(self, text: str) -> None:
        needle = text.casefold()
        for index in range(self.value_list.count()):
            item = self.value_list.item(index)
            item.setHidden(needle not in item.text().casefold())

    def _toggle_all(self, checked: bool) -> None:
        self.value_list.blockSignals(True)
        for index in range(self.value_list.count()):
            self.value_list.item(index).setCheckState(
                Qt.Checked if checked else Qt.Unchecked
            )
        self.value_list.blockSignals(False)
        self._initial_all = checked

    def _sync_select_all(self) -> None:
        checked = sum(
            self.value_list.item(index).checkState() == Qt.Checked
            for index in range(self.value_list.count())
        )
        self.select_all.blockSignals(True)
        self.select_all.setChecked(checked == self.value_list.count())
        self.select_all.blockSignals(False)

    def _operator_changed(self, text: str) -> None:
        self.condition_stack.setCurrentIndex(1 if text == "Between" else 0)

    def _coerce(self, text: str) -> object:
        if self.variable.kind == "numeric":
            try:
                return float(text)
            except ValueError as error:
                raise ValueError("Enter a valid numeric value.") from error
        return text

    def _accept_filter(self) -> None:
        try:
            if self.tabs.currentIndex() == 1:
                operator = self.operator.currentText()
                if operator == "Between":
                    lower = self._coerce(self.lower_value.text().strip())
                    upper = self._coerce(self.upper_value.text().strip())
                    self.result_spec = ColumnFilterSpec(
                        self.variable.name, "between", lower=lower, upper=upper
                    )
                else:
                    value = self._coerce(self.first_value.text().strip())
                    mode = "contains" if operator == "Contains" else "condition"
                    self.result_spec = ColumnFilterSpec(
                        self.variable.name,
                        mode,
                        operator=operator,
                        lower=value,
                    )
            else:
                checked: list[object] = []
                unchecked: list[object] = []
                include_missing = (
                    self.existing.include_missing if self.existing else True
                )
                for index in range(self.value_list.count()):
                    item = self.value_list.item(index)
                    value = item.data(Qt.UserRole)
                    is_checked = item.checkState() == Qt.Checked
                    if value is None:
                        include_missing = is_checked
                    elif is_checked:
                        checked.append(value)
                    else:
                        unchecked.append(value)
                if self._initial_all:
                    self.result_spec = ColumnFilterSpec(
                        self.variable.name,
                        "exclude",
                        tuple(unchecked),
                        include_missing,
                    )
                else:
                    self.result_spec = ColumnFilterSpec(
                        self.variable.name,
                        "include",
                        tuple(checked),
                        include_missing,
                    )
        except ValueError as error:
            QMessageBox.warning(self, "Invalid Column Filter", str(error))
            self.first_value.setFocus()
            return
        self.accept()

    def _clear(self) -> None:
        self.result_spec = None
        self.accept()
