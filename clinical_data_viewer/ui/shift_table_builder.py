from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..domain import DatasetMetadata, VariableMetadata
from ..shift_table import OrderedLevel
from .analysis_source_selector import AnalysisSourceSelector
from .proc_means_builder import FilterTextEditor
from .widgets import ClickOnlyComboBox, MouseOnlySpinBox


@dataclass(frozen=True, slots=True)
class ShiftTableBuilderSelection:
    subject_variable: str
    parameter_variable: str
    visit_variable: str
    treatment_variable: str
    baseline_grade_variable: str
    post_grade_variable: str
    analysis_flag_variable: str
    analysis_flag_value: object
    parameters: tuple[OrderedLevel, ...]
    visits: tuple[OrderedLevel, ...]
    treatments: tuple[OrderedLevel, ...]
    grade_levels: tuple[OrderedLevel, ...]
    filter_text: str
    include_overall_total: bool
    percent_digits: int
    population_dataset: object | None
    population_subject_variable: str
    population_treatment_variable: str
    population_filter_text: str


class OrderedLevelsEditor(QGroupBox):
    load_requested = Signal()

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(title, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(3)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Raw Value", "Display Label"])
        self.table.verticalHeader().hide()
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.table)
        buttons = QGridLayout()
        self.load_button = QPushButton("Load Values")
        self.remove_button = QPushButton("Remove")
        self.up_button = QPushButton("Move Up")
        self.down_button = QPushButton("Move Down")
        self.load_button.clicked.connect(self.load_requested)
        self.remove_button.clicked.connect(self._remove)
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        for index, button in enumerate(
            (self.load_button, self.remove_button, self.up_button, self.down_button)
        ):
            buttons.addWidget(button, index // 2, index % 2)
        buttons.setColumnStretch(0, 1)
        buttons.setColumnStretch(1, 1)
        layout.addLayout(buttons)

    def set_values(self, values: tuple[object, ...]) -> None:
        existing_labels = {
            self.table.item(row, 0).data(Qt.UserRole): self.table.item(row, 1).text()
            for row in range(self.table.rowCount())
            if self.table.item(row, 0) is not None
            and self.table.item(row, 1) is not None
        }
        self.table.setRowCount(len(values))
        for row, value in enumerate(values):
            raw = QTableWidgetItem(str(value))
            raw.setData(Qt.UserRole, value)
            raw.setFlags(raw.flags() & ~Qt.ItemIsEditable)
            label = QTableWidgetItem(existing_labels.get(value, str(value)))
            self.table.setItem(row, 0, raw)
            self.table.setItem(row, 1, label)
        if values:
            self.table.selectRow(0)

    def levels(self) -> tuple[OrderedLevel, ...]:
        values: list[OrderedLevel] = []
        for row in range(self.table.rowCount()):
            raw = self.table.item(row, 0)
            label = self.table.item(row, 1)
            if raw is None or label is None:
                continue
            values.append(
                OrderedLevel(raw.data(Qt.UserRole), label.text().strip(), row + 1)
            )
        return tuple(values)

    def _remove(self) -> None:
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            if self.table.rowCount():
                self.table.selectRow(min(row, self.table.rowCount() - 1))

    def _move(self, delta: int) -> None:
        row = self.table.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.table.rowCount():
            return
        values = self.levels()
        reordered = list(values)
        reordered[row], reordered[target] = reordered[target], reordered[row]
        self.table.setRowCount(0)
        self.table.setRowCount(len(reordered))
        for index, level in enumerate(reordered):
            raw = QTableWidgetItem(str(level.raw_value))
            raw.setData(Qt.UserRole, level.raw_value)
            raw.setFlags(raw.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(index, 0, raw)
            self.table.setItem(index, 1, QTableWidgetItem(level.display_label))
        self.table.selectRow(target)

    def set_busy(self, busy: bool) -> None:
        for button in (
            self.load_button,
            self.remove_button,
            self.up_button,
            self.down_button,
        ):
            button.setEnabled(not busy)


class ShiftTableBuilder(QWidget):
    source_selected = Signal(object)
    population_selected = Signal(object)
    levels_requested = Signal(str, str)
    run_requested = Signal(object)
    sas_code_requested = Signal(object)
    shell_preview_requested = Signal()
    validation_error = Signal(str)
    cleared = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("shiftTableBuilder")
        self._metadata: DatasetMetadata | None = None
        self._population_metadata: DatasetMetadata | None = None
        self._source_kind = "sas"
        self._busy = False
        self._filter_initialized = False
        self._population_filter_initialized = False
        self._saved_analysis_variables: dict[str, str] = {}
        self._saved_population_variables: dict[str, str] = {}

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

        layout.addWidget(QLabel("Analysis Source Dataset"))
        self.source_selector = AnalysisSourceSelector()
        self.source_selector.source_selected.connect(self.source_selected)
        layout.addWidget(self.source_selector)
        self.source_label = QLabel("Select a fully loaded source dataset.")
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)

        layout.addWidget(QLabel("Dataset Filter"))
        self.filter_editor = FilterTextEditor()
        self.filter_editor.setMaximumHeight(68)
        self.filter_editor.setPlaceholderText(
            'Optional WHERE condition, e.g. SAFFL = "Y"'
        )
        layout.addWidget(self.filter_editor)

        variable_box = QGroupBox("Analysis Variables")
        form = QFormLayout(variable_box)
        self.subject_variable = ClickOnlyComboBox()
        self.parameter_variable = ClickOnlyComboBox()
        self.visit_variable = ClickOnlyComboBox()
        self.treatment_variable = ClickOnlyComboBox()
        self.baseline_grade_variable = ClickOnlyComboBox()
        self.post_grade_variable = ClickOnlyComboBox()
        self.analysis_flag_variable = ClickOnlyComboBox()
        self.analysis_flag_value = QLineEdit("Y")
        for label, widget in (
            ("Subject Variable", self.subject_variable),
            ("Parameter Variable", self.parameter_variable),
            ("Visit Variable", self.visit_variable),
            ("Treatment Variable", self.treatment_variable),
            ("Baseline Grade Variable", self.baseline_grade_variable),
            ("Post-baseline Grade Variable", self.post_grade_variable),
            ("Analysis Flag Variable", self.analysis_flag_variable),
            ("Analysis Flag Value", self.analysis_flag_value),
        ):
            form.addRow(label, widget)
        layout.addWidget(variable_box)

        self.parameter_levels = OrderedLevelsEditor("Parameters")
        self.visit_levels = OrderedLevelsEditor("Visits")
        self.treatment_levels = OrderedLevelsEditor("Treatments")
        self.grade_levels = OrderedLevelsEditor("Grade Levels")
        self.parameter_levels.load_requested.connect(
            lambda: self._request_levels("parameters", self.parameter_variable)
        )
        self.visit_levels.load_requested.connect(
            lambda: self._request_levels("visits", self.visit_variable)
        )
        self.treatment_levels.load_requested.connect(
            lambda: self._request_levels("treatments", self.treatment_variable)
        )
        self.grade_levels.load_requested.connect(self._request_grade_levels)
        for editor in (
            self.parameter_levels,
            self.visit_levels,
            self.treatment_levels,
            self.grade_levels,
        ):
            layout.addWidget(editor)

        display_box = QGroupBox("Display")
        display_form = QFormLayout(display_box)
        self.include_overall_total = QCheckBox("Include Overall Total")
        self.percent_digits = MouseOnlySpinBox()
        self.percent_digits.setRange(0, 4)
        self.percent_digits.setValue(1)
        display_form.addRow("Treatment Total", self.include_overall_total)
        display_form.addRow("Percent Decimal Places", self.percent_digits)
        layout.addWidget(display_box)

        layout.addWidget(QLabel("Population Dataset (for Header N)"))
        self.population_selector = AnalysisSourceSelector()
        self.population_selector.source_selected.connect(self.population_selected)
        layout.addWidget(self.population_selector)
        self.population_label = QLabel(
            "If not selected, the Analysis Source can be used after confirmation."
        )
        self.population_label.setWordWrap(True)
        layout.addWidget(self.population_label)
        population_box = QGroupBox("Population N")
        population_form = QFormLayout(population_box)
        self.population_subject_variable = ClickOnlyComboBox()
        self.population_treatment_variable = ClickOnlyComboBox()
        population_form.addRow(
            "Population Subject Variable", self.population_subject_variable
        )
        population_form.addRow(
            "Population Treatment Variable", self.population_treatment_variable
        )
        layout.addWidget(population_box)
        layout.addWidget(QLabel("Population WHERE"))
        self.population_filter_editor = FilterTextEditor()
        self.population_filter_editor.setMaximumHeight(68)
        self.population_filter_editor.setPlaceholderText(
            'Optional WHERE condition, e.g. SAFFL = "Y"'
        )
        layout.addWidget(self.population_filter_editor)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QGridLayout()
        self.preview_button = QPushButton("Shell Preview…")
        self.clear_button = QPushButton("Clear")
        self.sas_code_button = QPushButton("SAS Code Generator…")
        self.run_button = QPushButton("Run")
        self.preview_button.clicked.connect(self.shell_preview_requested)
        self.clear_button.clicked.connect(self.clear)
        self.sas_code_button.clicked.connect(self._generate_sas_code)
        self.run_button.clicked.connect(self._run)
        for index, button in enumerate(
            (
                self.preview_button,
                self.clear_button,
                self.sas_code_button,
                self.run_button,
            )
        ):
            actions.addWidget(button, index // 2, index % 2)
        actions.setColumnStretch(0, 1)
        actions.setColumnStretch(1, 1)
        layout.addLayout(actions)
        self.set_dataset(None, "")
        self.set_population_dataset(None, "")

    def set_source_datasets(self, datasets, selected=None) -> None:
        self.source_selector.set_sources(datasets, selected)

    def set_population_datasets(self, datasets, selected=None) -> None:
        self.population_selector.set_sources(datasets, selected)

    @staticmethod
    def _select_default(combo: ClickOnlyComboBox, *names: str) -> None:
        for name in names:
            index = combo.findData(name)
            if index >= 0:
                combo.setCurrentIndex(index)
                return

    @staticmethod
    def _fill_combo(
        combo: ClickOnlyComboBox,
        variables: tuple[VariableMetadata, ...],
        selected: str,
    ) -> None:
        combo.blockSignals(True)
        combo.clear()
        for variable in variables:
            combo.addItem(variable.name, variable.name)
        index = combo.findData(selected)
        combo.setCurrentIndex(index if index >= 0 else (0 if combo.count() else -1))
        combo.blockSignals(False)

    def set_dataset(
        self,
        metadata: DatasetMetadata | None,
        source_text: str,
        filter_text: str = "",
        source_kind: str = "sas",
    ) -> None:
        variables_by_role = {
            "subject": self.subject_variable,
            "parameter": self.parameter_variable,
            "visit": self.visit_variable,
            "treatment": self.treatment_variable,
            "baseline_grade": self.baseline_grade_variable,
            "post_grade": self.post_grade_variable,
            "analysis_flag": self.analysis_flag_variable,
        }
        if self._metadata is not None:
            self._saved_analysis_variables.update(
                {
                    role: str(combo.currentData())
                    for role, combo in variables_by_role.items()
                    if combo.currentData()
                }
            )
        self._metadata = metadata
        self._source_kind = source_kind
        self.source_label.setText(
            f"Source: {source_text}"
            if metadata
            else "Select a fully loaded source dataset."
        )
        variables = metadata.variables if metadata is not None else ()
        for role, combo in variables_by_role.items():
            self._fill_combo(
                combo, variables, self._saved_analysis_variables.get(role, "")
            )
        if metadata is not None:
            defaults = {
                "subject": ("USUBJID",),
                "parameter": ("PARAMCD",),
                "visit": ("AVISIT", "VISIT"),
                "treatment": ("TRTA", "TRT01A"),
                "baseline_grade": ("BTOXGR", "BASEGR"),
                "post_grade": ("ATOXGR", "TOXGR"),
                "analysis_flag": ("ANL01FL", "ANLFL"),
            }
            for role, combo in variables_by_role.items():
                saved = self._saved_analysis_variables.get(role, "")
                if not saved or combo.findData(saved) < 0:
                    self._select_default(combo, *defaults[role])
                if combo.currentData():
                    self._saved_analysis_variables[role] = str(combo.currentData())
            if not self._filter_initialized:
                self.filter_editor.setText(filter_text)
                self._filter_initialized = True
        self._update_enabled()

    def set_population_dataset(
        self,
        metadata: DatasetMetadata | None,
        source_text: str,
        filter_text: str = "",
    ) -> None:
        variables_by_role = {
            "subject": self.population_subject_variable,
            "treatment": self.population_treatment_variable,
        }
        if self._population_metadata is not None:
            self._saved_population_variables.update(
                {
                    role: str(combo.currentData())
                    for role, combo in variables_by_role.items()
                    if combo.currentData()
                }
            )
        self._population_metadata = metadata
        variables = metadata.variables if metadata is not None else ()
        for role, combo in variables_by_role.items():
            self._fill_combo(
                combo, variables, self._saved_population_variables.get(role, "")
            )
        if metadata is not None:
            defaults = {
                "subject": ("USUBJID",),
                "treatment": ("TRT01A", "TRTA"),
            }
            for role, combo in variables_by_role.items():
                saved = self._saved_population_variables.get(role, "")
                if not saved or combo.findData(saved) < 0:
                    self._select_default(combo, *defaults[role])
                if combo.currentData():
                    self._saved_population_variables[role] = str(combo.currentData())
            if not self._population_filter_initialized:
                self.population_filter_editor.setText(filter_text)
                self._population_filter_initialized = True
        self.population_label.setText(
            f"Population: {source_text}"
            if metadata
            else "If not selected, the Analysis Source can be used after confirmation."
        )
        self._update_enabled()

    def _request_levels(self, role: str, combo: ClickOnlyComboBox) -> None:
        variable = str(combo.currentData() or "")
        if variable:
            self.levels_requested.emit(role, variable)

    def _request_grade_levels(self) -> None:
        baseline = str(self.baseline_grade_variable.currentData() or "")
        post = str(self.post_grade_variable.currentData() or "")
        if baseline and post:
            self.levels_requested.emit("grades", f"{baseline}\n{post}")

    def set_level_values(self, role: str, values: tuple[object, ...]) -> None:
        editors = {
            "parameters": self.parameter_levels,
            "visits": self.visit_levels,
            "treatments": self.treatment_levels,
            "grades": self.grade_levels,
        }
        editor = editors.get(role)
        if editor is not None:
            editor.set_values(values)

    def _variable(self, name: str) -> VariableMetadata | None:
        if self._metadata is None:
            return None
        return next(
            (
                variable
                for variable in self._metadata.variables
                if variable.name.casefold() == name.casefold()
            ),
            None,
        )

    def _flag_value(self) -> object:
        name = str(self.analysis_flag_variable.currentData() or "")
        variable = self._variable(name)
        text = self.analysis_flag_value.text().strip()
        if variable is not None and variable.kind == "numeric":
            try:
                return float(text)
            except ValueError:
                return text
        return text

    def selection(self) -> ShiftTableBuilderSelection:
        return ShiftTableBuilderSelection(
            str(self.subject_variable.currentData() or ""),
            str(self.parameter_variable.currentData() or ""),
            str(self.visit_variable.currentData() or ""),
            str(self.treatment_variable.currentData() or ""),
            str(self.baseline_grade_variable.currentData() or ""),
            str(self.post_grade_variable.currentData() or ""),
            str(self.analysis_flag_variable.currentData() or ""),
            self._flag_value(),
            self.parameter_levels.levels(),
            self.visit_levels.levels(),
            self.treatment_levels.levels(),
            self.grade_levels.levels(),
            self.filter_editor.toPlainText().strip(),
            self.include_overall_total.isChecked(),
            self.percent_digits.value(),
            self.population_selector.selected_source(),
            str(self.population_subject_variable.currentData() or ""),
            str(self.population_treatment_variable.currentData() or ""),
            self.population_filter_editor.toPlainText().strip(),
        )

    def _validate_selection(self, selection: ShiftTableBuilderSelection) -> bool:
        for label, values in (
            ("Parameter", selection.parameters),
            ("Visit", selection.visits),
            ("Treatment", selection.treatments),
            ("Grade", selection.grade_levels),
        ):
            if not values:
                self.validation_error.emit(
                    f"Load and select at least one {label} level."
                )
                return False
        return True

    def _run(self) -> None:
        selection = self.selection()
        if self._validate_selection(selection):
            self.run_requested.emit(selection)

    def _generate_sas_code(self) -> None:
        selection = self.selection()
        if self._validate_selection(selection):
            self.sas_code_requested.emit(selection)

    def clear(self) -> None:
        self.filter_editor.clear()
        self.population_filter_editor.clear()
        self.analysis_flag_value.setText("Y")
        for editor in (
            self.parameter_levels,
            self.visit_levels,
            self.treatment_levels,
            self.grade_levels,
        ):
            editor.set_values(())
        self.include_overall_total.setChecked(False)
        self.percent_digits.setValue(1)
        self._filter_initialized = False
        self._population_filter_initialized = False
        self._saved_analysis_variables.clear()
        self._saved_population_variables.clear()
        self.population_selector.set_selected(None)
        self.set_population_dataset(None, "")
        self.cleared.emit()

    def set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.status.setText(message)
        self.source_selector.setEnabled(not busy)
        self.population_selector.setEnabled(not busy)
        for editor in (
            self.parameter_levels,
            self.visit_levels,
            self.treatment_levels,
            self.grade_levels,
        ):
            editor.set_busy(busy)
        self._update_enabled()

    def _update_enabled(self) -> None:
        has_source = self._metadata is not None
        self.run_button.setEnabled(has_source and not self._busy)
        self.sas_code_button.setEnabled(
            has_source and self._source_kind == "sas" and not self._busy
        )
        self.clear_button.setEnabled(not self._busy)
        self.preview_button.setEnabled(not self._busy)


__all__ = ["OrderedLevelsEditor", "ShiftTableBuilder", "ShiftTableBuilderSelection"]
