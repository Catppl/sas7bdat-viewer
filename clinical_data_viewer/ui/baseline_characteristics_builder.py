from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..baseline_characteristics import (
    BaselineCategoricalItem,
    BaselineContinuousItem,
    BaselineItem,
)
from ..domain import DatasetMetadata, VariableMetadata
from .analysis_source_selector import AnalysisSourceSelector
from .proc_means_builder import FilterTextEditor
from .widgets import ClickOnlyComboBox, MouseOnlyComboBox


@dataclass(frozen=True, slots=True)
class BaselineCharacteristicsBuilderSelection:
    items: tuple[BaselineItem, ...]
    filter_text: str
    header_label: str
    treatment_variable: str = ""


class BaselineCharacteristicsBuilder(QWidget):
    source_selected = Signal(object)
    run_requested = Signal(object)
    sas_code_requested = Signal(object)
    shell_preview_requested = Signal()
    validation_error = Signal(str)
    cleared = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("baselineCharacteristicsBuilder")
        self._metadata: DatasetMetadata | None = None
        self._source_text = ""
        self._source_kind = "sas"
        self._items: list[BaselineItem] = []
        self._busy = False
        self._filter_initialized = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        layout.addWidget(QLabel("Source Dataset"))
        self.source_selector = AnalysisSourceSelector()
        self.source_selector.source_selected.connect(self.source_selected)
        layout.addWidget(self.source_selector)
        self.source_label = QLabel("Select a fully loaded source dataset.")
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)

        form = QFormLayout()
        self.subject_id = QLineEdit("USUBJID")
        self.subject_id.setReadOnly(True)
        self.header_label = QLineEdit("Overall")
        self.treatment_variable = ClickOnlyComboBox()
        self.treatment_variable.addItem("Overall (no treatment)", "")
        self.treatment_variable.currentIndexChanged.connect(self._treatment_changed)
        form.addRow("Participant ID", self.subject_id)
        form.addRow("Summary Column Header", self.header_label)
        form.addRow("Column Grouping Variable", self.treatment_variable)
        layout.addLayout(form)

        layout.addWidget(QLabel("Analysis Filter"))
        self.filter_editor = FilterTextEditor()
        self.filter_editor.setMaximumHeight(68)
        self.filter_editor.setPlaceholderText(
            'Optional WHERE condition, e.g. SAFFL = "Y"'
        )
        layout.addWidget(self.filter_editor)

        item_box = QGroupBox("Items (mixed order is preserved)")
        item_layout = QVBoxLayout(item_box)
        self.items = QTableWidget(0, 4)
        self.items.setHorizontalHeaderLabels(["Role", "Variable", "Storage", "Label"])
        self.items.verticalHeader().hide()
        self.items.setSelectionBehavior(QTableWidget.SelectRows)
        self.items.setSelectionMode(QTableWidget.SingleSelection)
        self.items.setEditTriggers(QTableWidget.NoEditTriggers)
        header = self.items.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        self.items.itemSelectionChanged.connect(self._load_selected_editor)
        item_layout.addWidget(self.items)
        item_buttons = QHBoxLayout()
        self.add_categorical_button = QPushButton("Add Categorical…")
        self.add_continuous_button = QPushButton("Add Continuous…")
        self.remove_button = QPushButton("Remove")
        self.up_button = QPushButton("Up")
        self.down_button = QPushButton("Down")
        self.add_categorical_button.clicked.connect(
            lambda: self._choose_item("categorical")
        )
        self.add_continuous_button.clicked.connect(
            lambda: self._choose_item("continuous")
        )
        self.remove_button.clicked.connect(self._remove_item)
        self.up_button.clicked.connect(lambda: self._move_item(-1))
        self.down_button.clicked.connect(lambda: self._move_item(1))
        for button in (
            self.add_categorical_button,
            self.add_continuous_button,
            self.remove_button,
            self.up_button,
            self.down_button,
        ):
            item_buttons.addWidget(button)
        item_layout.addLayout(item_buttons)
        layout.addWidget(item_box)

        self.editor_stack = QStackedWidget()
        self.editor_stack.addWidget(QLabel("Select an item to edit its settings."))
        self.editor_stack.addWidget(self._build_categorical_editor())
        self.editor_stack.addWidget(self._build_continuous_editor())
        layout.addWidget(self.editor_stack)

        note = QLabel(
            "Storage type is read from dataset metadata. Character variables can only be "
            "Categorical; numeric variables can be Categorical or Continuous."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        actions = QHBoxLayout()
        self.preview_button = QPushButton("Shell Preview…")
        self.sas_code_button = QPushButton("SAS Code Generator…")
        self.clear_button = QPushButton("Clear")
        self.run_button = QPushButton("Run")
        self.preview_button.clicked.connect(self.shell_preview_requested)
        self.sas_code_button.clicked.connect(self._generate_sas_code)
        self.clear_button.clicked.connect(self.clear)
        self.run_button.clicked.connect(self._run)
        action_grid = QGridLayout()
        for index, button in enumerate(
            (self.preview_button, self.sas_code_button, self.clear_button, self.run_button)
        ):
            action_grid.addWidget(button, index // 2, index % 2)
        action_grid.setColumnStretch(0, 1)
        action_grid.setColumnStretch(1, 1)
        actions.addLayout(action_grid)
        layout.addLayout(actions)
        self.set_dataset(None, "")

    def _build_categorical_editor(self) -> QWidget:
        box = QGroupBox("Categorical Item Settings")
        form = QFormLayout(box)
        self.categorical_label = QLineEdit()
        self.categorical_sort = QComboBox()
        self.categorical_sort.addItem(
            "Runtime value ascending", "runtime_value_ascending"
        )
        self.categorical_sort.addItem("Custom", "custom")
        self.categorical_order = QPlainTextEdit()
        self.categorical_order.setMaximumHeight(68)
        self.categorical_order.setPlaceholderText(
            "One raw level=display label per line, e.g. F=Female\nM=Male\n''=Missing"
        )
        self.percent_digits = QSpinBox()
        self.percent_digits.setRange(0, 4)
        self.percent_digits.setValue(1)
        form.addRow("Label", self.categorical_label)
        form.addRow("Level order", self.categorical_sort)
        form.addRow("Custom levels", self.categorical_order)
        form.addRow("Percent decimals", self.percent_digits)
        self.categorical_label.editingFinished.connect(self._save_selected_editor)
        self.categorical_sort.currentIndexChanged.connect(self._save_selected_editor)
        self.categorical_order.textChanged.connect(self._save_selected_editor)
        self.percent_digits.valueChanged.connect(self._save_selected_editor)
        return box

    def _build_continuous_editor(self) -> QWidget:
        box = QGroupBox("Continuous Item Settings")
        layout = QVBoxLayout(box)
        form = QFormLayout()
        self.continuous_label = QLineEdit()
        form.addRow("Label", self.continuous_label)
        layout.addLayout(form)
        layout.addWidget(QLabel("Statistics (select Q1+Q3 and Min+Max as pairs)"))
        statistics_grid = QGridLayout()
        labels = (
            ("n", "n"),
            ("mean", "Mean"),
            ("std", "SD"),
            ("median", "Median"),
            ("q1", "Q1"),
            ("q3", "Q3"),
            ("min", "Min"),
            ("max", "Max"),
        )
        self._statistic_checks: dict[str, QCheckBox] = {}
        self._decimal_combos: dict[str, QComboBox] = {}
        for row, (key, label) in enumerate(labels):
            check = QCheckBox(label)
            check.setChecked(True)
            check.stateChanged.connect(lambda _state, changed=key: self._statistic_changed(changed))
            self._statistic_checks[key] = check
            combo = MouseOnlyComboBox()
            for digits in range(5):
                combo.addItem(str(digits), digits)
            if key == "n":
                combo.setCurrentIndex(0)
                combo.setEnabled(False)
            combo.currentIndexChanged.connect(self._save_selected_editor)
            statistics_grid.addWidget(check, row, 0)
            statistics_grid.addWidget(combo, row, 1)
            self._decimal_combos[key] = combo
        layout.addLayout(statistics_grid)
        self.continuous_label.editingFinished.connect(self._save_selected_editor)
        return box

    def set_source_datasets(self, datasets, selected=None) -> None:
        self.source_selector.set_sources(datasets, selected)

    def set_dataset(
        self,
        metadata: DatasetMetadata | None,
        source_text: str,
        filter_text: str = "",
        source_kind: str = "sas",
    ) -> None:
        self._metadata = metadata
        self._source_text = source_text
        self._source_kind = source_kind
        self.source_label.setText(
            f"Source: {source_text}"
            if metadata
            else "Select a fully loaded source dataset."
        )
        if metadata is not None and not self._filter_initialized:
            self.filter_editor.setText(filter_text)
            self._filter_initialized = True
        self._refresh_treatment_variables()
        self._refresh_items()
        self._update_enabled()

    def _refresh_treatment_variables(self) -> None:
        selected = self.treatment_variable.currentData()
        self.treatment_variable.blockSignals(True)
        self.treatment_variable.clear()
        self.treatment_variable.addItem("Overall (no treatment)", "")
        if self._metadata is not None:
            for variable in self._metadata.variables:
                if variable.name.casefold() == "usubjid":
                    continue
                self.treatment_variable.addItem(variable.name, variable.name)
        index = self.treatment_variable.findData(selected or "")
        self.treatment_variable.setCurrentIndex(max(0, index))
        self.treatment_variable.blockSignals(False)

    def _treatment_changed(self, _index: int) -> None:
        self._update_enabled()

    def inherit_current_filter(self, filter_text: str) -> None:
        if not self.filter_editor.toPlainText().strip():
            self.filter_editor.setText(filter_text)

    def current_filter_text(self) -> str:
        return self.filter_editor.toPlainText().strip()

    def _variable(self, name: str) -> VariableMetadata | None:
        if self._metadata is None:
            return None
        return next(
            (
                item
                for item in self._metadata.variables
                if item.name.casefold() == name.casefold()
            ),
            None,
        )

    def _choose_item(self, role: str) -> None:
        if self._metadata is None:
            return
        used = {item.variable.casefold() for item in self._items}
        variables = [
            variable.name
            for variable in self._metadata.variables
            if variable.name.casefold() != "usubjid"
            and variable.name.casefold() not in used
            and (role == "categorical" or variable.kind == "numeric")
        ]
        if not variables:
            self.validation_error.emit(
                f"No available variables can be added as {role.title()}."
            )
            return
        variable, accepted = QInputDialog.getItem(
            self, f"Add {role.title()} Item", "Variable", variables, 0, False
        )
        if accepted:
            self.add_item(role, variable)

    def add_item(self, role: str, variable_name: str) -> None:
        variable = self._variable(variable_name)
        if variable is None:
            self.validation_error.emit(f'Variable "{variable_name}" does not exist.')
            return
        if variable.name.casefold() == "usubjid":
            self.validation_error.emit(
                "USUBJID cannot be selected as an analysis Item."
            )
            return
        if any(
            item.variable.casefold() == variable.name.casefold() for item in self._items
        ):
            self.validation_error.emit(
                f'Variable "{variable.name}" is already selected.'
            )
            return
        label = variable.label or variable.name
        if role == "categorical":
            item: BaselineItem = BaselineCategoricalItem(variable.name, label)
        elif role == "continuous" and variable.kind == "numeric":
            item = BaselineContinuousItem(variable.name, label)
        else:
            self.validation_error.emit(
                f'Continuous Item "{variable.name}" must use a numeric variable.'
            )
            return
        self._items.append(item)
        self._refresh_items(len(self._items) - 1)

    def _refresh_items(self, selected_row: int | None = None) -> None:
        if selected_row is None:
            selected_row = self.items.currentRow()
        self.items.blockSignals(True)
        self.items.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            variable = self._variable(item.variable)
            role = (
                "Categorical"
                if isinstance(item, BaselineCategoricalItem)
                else "Continuous"
            )
            values = (
                role,
                item.variable,
                variable.kind.title() if variable else "",
                item.label,
            )
            for column, value in enumerate(values):
                self.items.setItem(row, column, QTableWidgetItem(value))
        self.items.blockSignals(False)
        if self._items:
            self.items.selectRow(max(0, min(selected_row, len(self._items) - 1)))
        else:
            self.editor_stack.setCurrentIndex(0)
        self._update_enabled()

    def _load_selected_editor(self) -> None:
        row = self.items.currentRow()
        if not 0 <= row < len(self._items):
            self.editor_stack.setCurrentIndex(0)
            return
        item = self._items[row]
        if isinstance(item, BaselineCategoricalItem):
            self.editor_stack.setCurrentIndex(1)
            self.categorical_label.blockSignals(True)
            self.categorical_sort.blockSignals(True)
            self.categorical_order.blockSignals(True)
            self.percent_digits.blockSignals(True)
            self.categorical_label.setText(item.label)
            self.categorical_sort.setCurrentIndex(
                self.categorical_sort.findData(item.level_sort_method)
            )
            labels = dict(item.level_labels)
            lines = []
            for value in item.level_order:
                raw = "''" if value is None else str(value)
                lines.append(f"{raw}={labels.get(value, raw)}")
            self.categorical_order.setPlainText("\n".join(lines))
            self.percent_digits.setValue(item.percent_digits)
            for widget in (
                self.categorical_label,
                self.categorical_sort,
                self.categorical_order,
                self.percent_digits,
            ):
                widget.blockSignals(False)
            self.categorical_order.setEnabled(item.level_sort_method == "custom")
            return
        self.editor_stack.setCurrentIndex(2)
        self.continuous_label.blockSignals(True)
        self.continuous_label.setText(item.label)
        configured = dict(item.statistic_decimals)
        for key, combo in self._decimal_combos.items():
            combo.blockSignals(True)
            value = 0 if key == "n" else configured.get(key, 0)
            combo.setCurrentIndex(int(value))
            combo.blockSignals(False)
        for key, check in self._statistic_checks.items():
            check.blockSignals(True)
            check.setChecked(key in item.statistics)
            check.blockSignals(False)
        self.continuous_label.blockSignals(False)

    def _statistic_changed(self, key: str) -> None:
        if key in {"q1", "q3"}:
            other = "q3" if key == "q1" else "q1"
        elif key in {"min", "max"}:
            other = "max" if key == "min" else "min"
        else:
            other = None
        if other is not None:
            self._statistic_checks[other].blockSignals(True)
            self._statistic_checks[other].setChecked(
                self._statistic_checks[key].isChecked()
            )
            self._statistic_checks[other].blockSignals(False)
        self._save_selected_editor()

    def _save_selected_editor(self, *_args) -> None:
        row = self.items.currentRow()
        if not 0 <= row < len(self._items):
            return
        item = self._items[row]
        if isinstance(item, BaselineCategoricalItem):
            method = str(self.categorical_sort.currentData())
            self.categorical_order.setEnabled(method == "custom")
            variable = self._variable(item.variable)
            values: list[object] = []
            labels: list[tuple[object, str]] = []
            if method == "custom" and variable is not None:
                for text in self.categorical_order.toPlainText().splitlines():
                    text = text.strip()
                    if not text:
                        continue
                    raw_text, separator, display_text = text.partition("=")
                    raw_text = raw_text.strip()
                    display_text = display_text.strip() if separator else raw_text
                    if raw_text in {"''", '""'}:
                        raw_value: object = None
                    elif variable.kind == "numeric":
                        try:
                            raw_value = float(raw_text)
                        except ValueError:
                            raw_value = raw_text
                    else:
                        raw_value = raw_text
                    values.append(raw_value)
                    labels.append((raw_value, display_text))
            self._items[row] = BaselineCategoricalItem(
                item.variable,
                self.categorical_label.text().strip(),
                method,
                tuple(values),
                self.percent_digits.value(),
                tuple(labels),
            )
        else:
            decimals = tuple(
                (key, combo.currentData())
                for key, combo in self._decimal_combos.items()
                if key != "n"
            )
            statistics = tuple(
                key for key, check in self._statistic_checks.items() if check.isChecked()
            )
            self._items[row] = BaselineContinuousItem(
                item.variable,
                self.continuous_label.text().strip(),
                statistic_decimals=decimals,
                statistics=statistics,
            )
        label_item = self.items.item(row, 3)
        if label_item is not None:
            label_item.setText(self._items[row].label)

    def _remove_item(self) -> None:
        row = self.items.currentRow()
        if 0 <= row < len(self._items):
            del self._items[row]
            self._refresh_items(row)

    def _move_item(self, delta: int) -> None:
        row = self.items.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= len(self._items):
            return
        self._items[row], self._items[target] = self._items[target], self._items[row]
        self._refresh_items(target)

    def selection(self) -> BaselineCharacteristicsBuilderSelection:
        self._save_selected_editor()
        return BaselineCharacteristicsBuilderSelection(
            tuple(self._items),
            self.current_filter_text(),
            self.header_label.text().strip(),
            str(self.treatment_variable.currentData() or ""),
        )

    def _run(self) -> None:
        selection = self.selection()
        if not selection.items:
            self.validation_error.emit(
                "Add at least one Categorical or Continuous Item."
            )
            return
        if not selection.header_label:
            self.validation_error.emit("Summary Column Header cannot be empty.")
            return
        self.run_requested.emit(selection)

    def _generate_sas_code(self) -> None:
        selection = self.selection()
        if not selection.items:
            self.validation_error.emit(
                "Add at least one Categorical or Continuous Item."
            )
            return
        self.sas_code_requested.emit(selection)

    def clear(self) -> None:
        self._items.clear()
        self.filter_editor.clear()
        self._filter_initialized = False
        self.header_label.setText("Overall")
        self._refresh_treatment_variables()
        self._refresh_items()
        self.cleared.emit()

    def set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.status.setText(message)
        self._update_enabled()

    def _update_enabled(self) -> None:
        has_source = self._metadata is not None
        for button in (self.add_categorical_button, self.add_continuous_button):
            button.setEnabled(has_source and not self._busy)
        selected = bool(self._items) and self.items.currentRow() >= 0
        for button in (self.remove_button, self.up_button, self.down_button):
            button.setEnabled(selected and not self._busy)
        self.run_button.setEnabled(has_source and bool(self._items) and not self._busy)
        self.sas_code_button.setEnabled(
            has_source
            and self._source_kind == "sas"
            and not bool(self.treatment_variable.currentData())
            and bool(self._items)
            and not self._busy
        )
        self.clear_button.setEnabled(not self._busy)


__all__ = [
    "BaselineCharacteristicsBuilder",
    "BaselineCharacteristicsBuilderSelection",
]
