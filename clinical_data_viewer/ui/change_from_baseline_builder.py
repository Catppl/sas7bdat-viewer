from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..change_from_baseline.models import DEFAULT_STATISTIC_DECIMALS
from ..domain import DatasetMetadata
from .analysis_source_selector import AnalysisSourceSelector
from .widgets import ClickOnlyComboBox

STATISTICS = (
    ("n", "n (non-missing change records)"),
    ("mean", "Mean"),
    ("std", "SD"),
    ("median", "Median"),
    ("q1", "Q1"),
    ("q3", "Q3"),
    ("min", "Min"),
    ("max", "Max"),
)


@dataclass(frozen=True, slots=True)
class ChangeFromBaselineBuilderSelection:
    change_variable: str
    item_variable: str
    row_variables: tuple[str, ...]
    treatment_variable: str
    row_sort: tuple[tuple[str, bool], ...]
    statistics: tuple[str, ...]
    data_filter_text: str
    postbaseline_filter_text: str
    item_labels_text: str
    include_total: bool
    statistic_decimals: tuple[tuple[str, int], ...]


class RowVariableEditor(QWidget):
    changed = Signal()
    validation_error = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._metadata: DatasetMetadata | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.editor = QLineEdit()
        self.editor.setPlaceholderText("Type a variable and press Enter")
        self.editor.returnPressed.connect(self._add)
        row.addWidget(self.editor, 1)
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self._remove)
        row.addWidget(self.remove_button)
        layout.addLayout(row)
        self.values = QTableWidget(0, 2)
        self.values.setHorizontalHeaderLabels(("Variable", "Sort"))
        self.values.horizontalHeader().setStretchLastSection(True)
        self.values.setSelectionBehavior(QTableWidget.SelectRows)
        self.values.setSelectionMode(QTableWidget.SingleSelection)
        self.values.setMaximumHeight(110)
        layout.addWidget(self.values)
        actions = QHBoxLayout()
        self.up_button = QPushButton("Move Up")
        self.down_button = QPushButton("Move Down")
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        actions.addWidget(self.up_button)
        actions.addWidget(self.down_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.values.itemSelectionChanged.connect(self._sync_buttons)
        self._sync_buttons()

    def set_metadata(self, metadata: DatasetMetadata | None) -> None:
        self._metadata = metadata
        self.values.setRowCount(0)
        self.editor.clear()
        self.setEnabled(metadata is not None)
        self._sync_buttons()

    def selected(self) -> tuple[tuple[str, bool], ...]:
        result: list[tuple[str, bool]] = []
        for row in range(self.values.rowCount()):
            item = self.values.item(row, 0)
            combo = self.values.cellWidget(row, 1)
            if item is None or not isinstance(combo, QComboBox):
                continue
            result.append((item.text(), combo.currentData() == "asc"))
        return tuple(result)

    def set_variables(self, values: tuple[tuple[str, bool], ...]) -> None:
        self.values.setRowCount(0)
        for name, ascending in values:
            self._append(name, ascending)
        self._sync_buttons()

    def _add(self) -> None:
        if self._metadata is None:
            return
        requested = self.editor.text().strip()
        if not requested:
            return
        fields = {variable.name.casefold(): variable for variable in self._metadata.variables}
        variable = fields.get(requested.casefold())
        if variable is None:
            self.validation_error.emit(f'Variable "{requested}" does not exist in the current dataset.')
            return
        if variable.name.casefold() in {name.casefold() for name, _ascending in self.selected()}:
            return
        self._append(variable.name, True)
        self.editor.clear()
        self.changed.emit()

    def _append(self, name: str, ascending: bool) -> None:
        row = self.values.rowCount()
        self.values.insertRow(row)
        self.values.setItem(row, 0, QTableWidgetItem(name))
        combo = ClickOnlyComboBox()
        combo.addItem("Ascending", "asc")
        combo.addItem("Descending", "desc")
        combo.setCurrentIndex(0 if ascending else 1)
        combo.currentIndexChanged.connect(lambda _index: self.changed.emit())
        self.values.setCellWidget(row, 1, combo)
        self._sync_buttons()

    def _remove(self) -> None:
        row = self.values.currentRow()
        if row >= 0:
            self.values.removeRow(row)
            self.changed.emit()
        self._sync_buttons()

    def _move(self, offset: int) -> None:
        row = self.values.currentRow()
        target = row + offset
        if row < 0 or target < 0 or target >= self.values.rowCount():
            return
        values = list(self.selected())
        values[row], values[target] = values[target], values[row]
        self.set_variables(tuple(values))
        self.values.selectRow(target)
        self.changed.emit()

    def _sync_buttons(self) -> None:
        row = self.values.currentRow()
        count = self.values.rowCount()
        self.remove_button.setEnabled(row >= 0)
        self.up_button.setEnabled(row > 0)
        self.down_button.setEnabled(0 <= row < count - 1)


class ChangeFromBaselineBuilder(QWidget):
    source_selected = Signal(object)
    run_requested = Signal(object)
    sas_code_requested = Signal(object)
    shell_preview_requested = Signal()
    validation_error = Signal(str)
    cleared = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("changeFromBaselineBuilder")
        self._metadata: DatasetMetadata | None = None
        self._source_text = ""
        self._source_format = ""
        self._busy = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)
        outer.addWidget(scroll, 1)

        self.source_selector = AnalysisSourceSelector()
        self.source_selector.source_selected.connect(self.source_selected)
        layout.addWidget(self.source_selector)
        self.source_label = QLabel("Select a fully loaded source dataset.")
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)

        variables = QGroupBox("Variables")
        form = QFormLayout(variables)
        self.change_variable = ClickOnlyComboBox()
        self.item_variable = ClickOnlyComboBox()
        self.treatment_variable = ClickOnlyComboBox()
        form.addRow("Change Variable", self.change_variable)
        form.addRow("Item Variable", self.item_variable)
        form.addRow("Treatment (optional)", self.treatment_variable)
        self.row_variables = RowVariableEditor()
        self.row_variables.validation_error.connect(self.validation_error)
        form.addRow("Row Variables", self.row_variables)
        layout.addWidget(variables)

        filters = QGroupBox("Filters")
        filter_layout = QFormLayout(filters)
        self.data_filter = QPlainTextEdit()
        self.data_filter.setMaximumHeight(64)
        self.data_filter.setPlaceholderText('e.g. ANL01FL = "Y"')
        self.postbaseline_filter = QPlainTextEdit()
        self.postbaseline_filter.setMaximumHeight(64)
        self.postbaseline_filter.setPlaceholderText('e.g. AVISITN > 0')
        filter_layout.addRow("Dataset WHERE", self.data_filter)
        filter_layout.addRow("Postbaseline WHERE", self.postbaseline_filter)
        layout.addWidget(filters)

        statistics = QGroupBox("Statistics")
        statistics_layout = QHBoxLayout(statistics)
        self.statistics: dict[str, QCheckBox] = {}
        for key, label in STATISTICS:
            checkbox = QCheckBox(label)
            checkbox.setChecked(True)
            self.statistics[key] = checkbox
            statistics_layout.addWidget(checkbox)
        layout.addWidget(statistics)

        decimal_box = QGroupBox("Statistic Decimal Places")
        decimal_layout = QGridLayout(decimal_box)
        decimal_layout.setContentsMargins(4, 6, 4, 6)
        decimal_layout.setHorizontalSpacing(8)
        decimal_layout.addWidget(QLabel("Statistic"), 0, 0)
        decimal_layout.addWidget(QLabel("Places (0–4)"), 0, 1)
        self.statistic_decimals: dict[str, ClickOnlyComboBox] = {}
        default_decimals = dict(DEFAULT_STATISTIC_DECIMALS)
        for row, (key, _default) in enumerate(DEFAULT_STATISTIC_DECIMALS, start=1):
            label = next(label for statistic, label in STATISTICS if statistic == key)
            decimal_layout.addWidget(QLabel(label), row, 0)
            combo = ClickOnlyComboBox()
            for decimals in range(5):
                combo.addItem(str(decimals), decimals)
            combo.setCurrentIndex(default_decimals[key])
            self.statistic_decimals[key] = combo
            decimal_layout.addWidget(combo, row, 1)
        layout.addWidget(decimal_box)

        options = QGroupBox("Display")
        options_layout = QVBoxLayout(options)
        self.include_total = QCheckBox("Include Total")
        self.include_total.setChecked(True)
        options_layout.addWidget(self.include_total)
        options_layout.addWidget(QLabel("Item labels (one value=label per line)"))
        self.item_labels = QPlainTextEdit()
        self.item_labels.setMaximumHeight(90)
        self.item_labels.setPlaceholderText("ALB=Albumin\nALT=Alanine Aminotransferase")
        options_layout.addWidget(self.item_labels)
        layout.addWidget(options)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch(1)
        scroll.setWidget(content)

        buttons = QHBoxLayout()
        preview = QPushButton("Shell Preview…")
        preview.clicked.connect(self.shell_preview_requested)
        buttons.addWidget(preview)
        self.sas_code_button = QPushButton("SAS Code Generator…")
        self.sas_code_button.setToolTip("Generate SAS code for SAS7BDAT source datasets.")
        self.sas_code_button.clicked.connect(lambda: self.sas_code_requested.emit(self.selection()))
        buttons.addWidget(self.sas_code_button)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        buttons.addWidget(clear)
        self.run_button = QPushButton("Run Change from Baseline")
        self.run_button.setDefault(True)
        self.run_button.clicked.connect(self._run)
        buttons.addWidget(self.run_button)
        outer.addLayout(buttons)

    def set_source_datasets(self, datasets, selected=None) -> None:
        self.source_selector.set_sources(datasets, selected)

    def set_dataset(
        self,
        metadata: DatasetMetadata | None,
        source_text: str = "",
        current_filter: str = "",
    ) -> None:
        same_source = metadata is not None and source_text == self._source_text
        self._metadata = metadata
        self._source_text = source_text if metadata is not None else ""
        self._source_format = (
            Path(source_text).suffix.casefold().lstrip(".") if metadata is not None else ""
        )
        self.source_label.setText(
            f"Source: {source_text}" if metadata is not None else "Select a fully loaded source dataset."
        )
        if not same_source:
            self._fill_combo(self.change_variable, metadata, numeric_only=True)
            self._fill_combo(self.item_variable, metadata)
            self._fill_combo(self.treatment_variable, metadata, optional=True)
            self.row_variables.set_metadata(metadata)
        if metadata is None:
            self.data_filter.clear()
            self.postbaseline_filter.clear()
            self.item_labels.clear()
            self._reset_statistic_decimals()
        elif not same_source:
            self.data_filter.setPlainText(current_filter)
            self.postbaseline_filter.clear()
            self.item_labels.clear()
            self._reset_statistic_decimals()
            for checkbox in self.statistics.values():
                checkbox.setChecked(True)
            self.include_total.setChecked(True)
        self.setEnabled(metadata is not None)
        self._sync_busy()

    def set_source_unavailable(self) -> None:
        """Disable calculation after source closure without clearing inputs."""
        self._metadata = None
        self._source_text = ""
        self._source_format = ""
        self.source_label.setText(
            "The selected source dataset is closed. Reopen it or choose another dataset."
        )
        self.setEnabled(True)
        for widget in (
            self.change_variable,
            self.item_variable,
            self.treatment_variable,
            self.row_variables,
            self.data_filter,
            self.postbaseline_filter,
            self.item_labels,
            self.include_total,
            *self.statistics.values(),
            *self.statistic_decimals.values(),
        ):
            widget.setEnabled(False)
        self._sync_busy()

    @staticmethod
    def _fill_combo(combo, metadata, *, numeric_only=False, optional=False) -> None:
        combo.blockSignals(True)
        combo.clear()
        if optional:
            combo.addItem("None", "")
        if metadata is not None:
            for variable in metadata.variables:
                if numeric_only and variable.kind != "numeric":
                    continue
                combo.addItem(variable.name, variable.name)
        combo.blockSignals(False)

    def selection(self) -> ChangeFromBaselineBuilderSelection:
        labels = self.item_labels.toPlainText().strip()
        return ChangeFromBaselineBuilderSelection(
            str(self.change_variable.currentData() or ""),
            str(self.item_variable.currentData() or ""),
            tuple(name for name, _ascending in self.row_variables.selected()),
            str(self.treatment_variable.currentData() or ""),
            self.row_variables.selected(),
            tuple(key for key, checkbox in self.statistics.items() if checkbox.isChecked()),
            self.data_filter.toPlainText().strip(),
            self.postbaseline_filter.toPlainText().strip(),
            labels,
            self.include_total.isChecked(),
            tuple(
                (key, int(combo.currentData()))
                for key, combo in self.statistic_decimals.items()
            ),
        )

    def _run(self) -> None:
        if self._metadata is None:
            self.validation_error.emit("Select a fully loaded source dataset first.")
            return
        self.run_requested.emit(self.selection())

    def clear(self) -> None:
        self.change_variable.setCurrentIndex(-1)
        self.item_variable.setCurrentIndex(-1)
        self.treatment_variable.setCurrentIndex(0 if self.treatment_variable.count() else -1)
        self.row_variables.set_variables(())
        self.data_filter.clear()
        self.postbaseline_filter.clear()
        self.item_labels.clear()
        self._reset_statistic_decimals()
        for checkbox in self.statistics.values():
            checkbox.setChecked(True)
        self.include_total.setChecked(True)
        self.cleared.emit()

    def _reset_statistic_decimals(self) -> None:
        defaults = dict(DEFAULT_STATISTIC_DECIMALS)
        for key, combo in self.statistic_decimals.items():
            combo.setCurrentIndex(defaults[key])

    def set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.status.setText(message)
        self._sync_busy()

    def _sync_busy(self) -> None:
        enabled = self._metadata is not None and not self._busy
        self.run_button.setEnabled(enabled)
        self.sas_code_button.setEnabled(enabled and self._source_format == "sas7bdat")


__all__ = [
    "ChangeFromBaselineBuilder",
    "ChangeFromBaselineBuilderSelection",
]
