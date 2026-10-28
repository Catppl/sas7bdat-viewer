from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..domain import DatasetMetadata
from ..settings import PROC_MEANS_STATISTICS
from .analysis_source_selector import AnalysisSourceSelector
from .proc_means_builder import FilterTextEditor, VariableTokenEditor
from .widgets import ClickOnlyComboBox


@dataclass(frozen=True, slots=True)
class ProcMeansVerticalBuilderSelection:
    analysis_variables: tuple[str, ...]
    group_variables: tuple[str, ...]
    treatment_variable: str
    population_treatment_variable: str
    statistics: tuple[str, ...]
    base_decimals: tuple[tuple[str, int | None], ...]
    filter_text: str
    population_filter_text: str
    item_labels_text: str
    include_total: bool
    output_layout: str = "columns"
    row_group_variables: tuple[str, ...] = ()


class ProcMeansVerticalBuilder(QWidget):
    source_selected = Signal(object)
    population_selected = Signal(object)
    settings_requested = Signal()
    run_requested = Signal(object)
    sas_code_requested = Signal(object)
    validation_error = Signal(str)
    cleared = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("procMeansVerticalBuilder")
        self._metadata: DatasetMetadata | None = None
        self._population_metadata: DatasetMetadata | None = None
        self._source_text = ""
        self._source_kind = "sas"
        self._population_source_text = ""
        self._busy = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        content.setObjectName("procMeansVerticalBuilderContent")
        layout = QVBoxLayout(content)
        # Keep the form usable at the compact Analysis Dock width.  The
        # surrounding dock supplies the visual breathing room; smaller
        # content margins avoid an unnecessary horizontal scrollbar.
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(5)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        layout.addWidget(QLabel("Source Dataset"))
        self.source_selector = AnalysisSourceSelector()
        self.source_selector.source_selected.connect(self.source_selected)
        layout.addWidget(self.source_selector)
        self.source_label = QLabel("Select a fully loaded source dataset.")
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)

        layout.addWidget(QLabel("Analysis Variables"))
        self.analysis_variables = VariableTokenEditor(
            "Type a numeric variable and press Enter", numeric_only=True
        )
        self.analysis_variables.validation_error.connect(self.validation_error)
        self.analysis_variables.changed.connect(self._refresh_base_decimals)
        layout.addWidget(self.analysis_variables)

        layout.addWidget(QLabel("Base Decimal Places by Analysis Variable"))
        self.base_decimal_table = QTableWidget(0, 2)
        self.base_decimal_table.setHorizontalHeaderLabels(["Variable", "Base decimals"])
        self.base_decimal_table.verticalHeader().hide()
        header = self.base_decimal_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setStretchLastSection(False)
        self.base_decimal_table.setMaximumHeight(130)
        self.base_decimal_table.setMinimumWidth(0)
        layout.addWidget(self.base_decimal_table)

        layout.addWidget(QLabel("Column Group Variables"))
        self.group_variables = VariableTokenEditor("Type a variable and press Enter")
        self.group_variables.validation_error.connect(self.validation_error)
        layout.addWidget(self.group_variables)

        self.layout_box = QGroupBox("Output Layout")
        layout_options = QVBoxLayout(self.layout_box)
        self.groups_as_columns = QRadioButton("Groups as Columns")
        self.groups_as_rows = QRadioButton("Groups as Rows")
        self.groups_as_columns.setChecked(True)
        self.groups_as_columns.toggled.connect(self._update_layout_controls)
        layout_options.addWidget(self.groups_as_columns)
        layout_options.addWidget(self.groups_as_rows)
        layout.addWidget(self.layout_box)

        layout.addWidget(QLabel("Row Group Variables (e.g. PARAMCD / AVISIT)"))
        self.row_group_variables = VariableTokenEditor(
            "Type a row-group variable and press Enter"
        )
        self.row_group_variables.validation_error.connect(self.validation_error)
        layout.addWidget(self.row_group_variables)

        layout.addWidget(QLabel("Analysis Treatment Variable"))
        self.treatment_variable = VariableTokenEditor(
            "Select the treatment group variable"
        )
        self.treatment_variable.validation_error.connect(self.validation_error)
        layout.addWidget(self.treatment_variable)

        self.statistics_box = QGroupBox("Statistics (move rows to set display order)")
        stats_layout = QVBoxLayout(self.statistics_box)
        self.statistics = QListWidget()
        self.statistics.setMaximumHeight(170)
        for key, label in PROC_MEANS_STATISTICS:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, key)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(
                Qt.Checked
                if key in {"n", "mean", "std", "median", "q1", "q3", "min", "max"}
                else Qt.Unchecked
            )
            self.statistics.addItem(item)
        stats_layout.addWidget(self.statistics)
        buttons = QHBoxLayout()
        self.stat_up = QPushButton("Up")
        self.stat_down = QPushButton("Down")
        self.stat_up.clicked.connect(lambda: self._move_statistic(-1))
        self.stat_down.clicked.connect(lambda: self._move_statistic(1))
        buttons.addWidget(self.stat_up)
        buttons.addWidget(self.stat_down)
        buttons.addStretch(1)
        stats_layout.addLayout(buttons)
        layout.addWidget(self.statistics_box)

        self.include_total = QCheckBox("Include Total")
        self.include_total.setChecked(True)
        layout.addWidget(self.include_total)

        layout.addWidget(QLabel("Item Labels (one variable=label per line)"))
        self.item_labels = QPlainTextEdit()
        self.item_labels.setMaximumHeight(82)
        self.item_labels.setPlaceholderText("AVAL = Total Cholesterol (mmol/L)")
        layout.addWidget(self.item_labels)

        layout.addWidget(QLabel("Analysis Filter"))
        self.filter_editor = FilterTextEditor()
        self.filter_editor.setMaximumHeight(68)
        self.filter_editor.setPlaceholderText(
            'Optional WHERE condition, e.g. SAFFL = "Y"'
        )
        layout.addWidget(self.filter_editor)

        layout.addWidget(QLabel("Population Dataset (for header N)"))
        self.population_selector = AnalysisSourceSelector()
        self.population_selector.source_selected.connect(self.population_selected)
        layout.addWidget(self.population_selector)
        self.population_label = QLabel(
            "If not selected, current source data will be used with a warning."
        )
        self.population_label.setWordWrap(True)
        layout.addWidget(self.population_label)

        layout.addWidget(QLabel("Population Treatment Variable"))
        self.population_treatment_variable = VariableTokenEditor(
            "Select the population treatment variable"
        )
        self.population_treatment_variable.validation_error.connect(
            self.validation_error
        )
        layout.addWidget(self.population_treatment_variable)
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
        top_action_row = QHBoxLayout()
        self.sas_code_button = QPushButton("SAS Code Generator…")
        self.settings_button = QPushButton("Settings…")
        self.clear_button = QPushButton("Clear")
        self.run_button = QPushButton("Run")
        self.sas_code_button.clicked.connect(self._generate_sas_code)
        self.settings_button.clicked.connect(self.settings_requested)
        self.clear_button.clicked.connect(self.clear)
        self.run_button.clicked.connect(self._run)
        top_action_row.addWidget(self.settings_button)
        top_action_row.addWidget(self.clear_button)
        layout.addLayout(top_action_row)
        bottom_action_row = QHBoxLayout()
        bottom_action_row.addWidget(self.sas_code_button)
        bottom_action_row.addWidget(self.run_button)
        layout.addLayout(bottom_action_row)
        self.set_dataset(None, "")

    def set_source_datasets(self, datasets, selected=None) -> None:
        self.source_selector.set_sources(datasets, selected)

    def set_population_datasets(self, datasets, selected=None) -> None:
        self.population_selector.set_sources(datasets, selected)

    def set_dataset(
        self,
        metadata: DatasetMetadata | None,
        source_text: str,
        filter_text: str = "",
        source_kind: str = "sas",
    ) -> None:
        previous = bool(self._source_text)
        self._metadata = metadata
        self._source_text = source_text
        self._source_kind = source_kind
        self.source_label.setText(
            f"Source: {source_text}"
            if metadata
            else "Select a fully loaded source dataset."
        )
        for editor in (
            self.analysis_variables,
            self.group_variables,
            self.row_group_variables,
            self.treatment_variable,
        ):
            editor.set_metadata(metadata, preserve=previous)
        if metadata is not None and not previous:
            self.filter_editor.setText(filter_text)
        self._update_enabled()

    def _refresh_base_decimals(self) -> None:
        previous = {
            self.base_decimal_table.item(row, 0)
            .text()
            .casefold(): self.base_decimal_table.cellWidget(row, 1).currentData()
            for row in range(self.base_decimal_table.rowCount())
            if self.base_decimal_table.item(row, 0) is not None
        }
        self.base_decimal_table.setRowCount(0)
        for variable in self.analysis_variables.selected_variables():
            row = self.base_decimal_table.rowCount()
            self.base_decimal_table.insertRow(row)
            self.base_decimal_table.setItem(row, 0, QTableWidgetItem(variable))
            combo = ClickOnlyComboBox()
            combo.addItem("Auto", None)
            for decimals in range(5):
                combo.addItem(str(decimals), decimals)
            old = previous.get(variable.casefold())
            combo.setCurrentIndex(0 if old is None else old + 1)
            self.base_decimal_table.setCellWidget(row, 1, combo)

    def set_population_dataset(
        self, metadata: DatasetMetadata | None, source_text: str, filter_text: str = ""
    ) -> None:
        previous = bool(self._population_source_text)
        self._population_metadata = metadata
        self._population_source_text = source_text
        self.population_label.setText(
            f"Population: {source_text}"
            if metadata
            else "If not selected, current source data will be used with a warning."
        )
        self.population_treatment_variable.set_metadata(metadata, preserve=previous)
        if metadata is not None and not previous:
            self.population_filter_editor.setText(filter_text)

    def _move_statistic(self, delta: int) -> None:
        row = self.statistics.currentRow()
        target = row + delta
        if row < 0 or target < 0 or target >= self.statistics.count():
            return
        item = self.statistics.takeItem(row)
        self.statistics.insertItem(target, item)
        self.statistics.setCurrentRow(target)

    def _selected_statistics(self) -> tuple[str, ...]:
        return tuple(
            self.statistics.item(index).data(Qt.UserRole)
            for index in range(self.statistics.count())
            if self.statistics.item(index).checkState() == Qt.Checked
        )

    def set_default_statistics(self, statistics: list[str]) -> None:
        selected = set(statistics)
        items = []
        while self.statistics.count():
            items.append(self.statistics.takeItem(0))
        order = {key: position for position, key in enumerate(statistics)}
        items.sort(key=lambda item: order.get(item.data(Qt.UserRole), len(order)))
        for item in items:
            item.setCheckState(
                Qt.Checked if item.data(Qt.UserRole) in selected else Qt.Unchecked
            )
            self.statistics.addItem(item)

    def selection(self) -> ProcMeansVerticalBuilderSelection | None:
        analysis = self.analysis_variables.selected_variables()
        groups = self.group_variables.selected_variables()
        row_groups = self.row_group_variables.selected_variables()
        treatment = self.treatment_variable.selected_variables()
        population_treatment = self.population_treatment_variable.selected_variables()
        statistics = self._selected_statistics()
        if not analysis:
            self.validation_error.emit("Select at least one Analysis Variable.")
            return None
        if not statistics:
            self.validation_error.emit("Select at least one statistic.")
            return None
        base_decimals = tuple(
            (
                self.base_decimal_table.item(row, 0).text(),
                self.base_decimal_table.cellWidget(row, 1).currentData(),
            )
            for row in range(self.base_decimal_table.rowCount())
        )
        return ProcMeansVerticalBuilderSelection(
            analysis,
            groups,
            treatment[0] if treatment else "",
            population_treatment[0] if population_treatment else "",
            statistics,
            base_decimals,
            self.filter_editor.text().strip(),
            self.population_filter_editor.text().strip(),
            self.item_labels.toPlainText().strip(),
            self.include_total.isChecked(),
            "rows" if self.groups_as_rows.isChecked() else "columns",
            row_groups,
        )

    def _update_layout_controls(self) -> None:
        rows = self.groups_as_rows.isChecked()
        self.row_group_variables.setEnabled(
            rows and self._metadata is not None and not self._busy
        )
        codegen_available = (
            self._metadata is not None
            and not self._busy
            and self._source_kind != "merge"
        )
        self.sas_code_button.setEnabled(codegen_available)
        if self._source_kind == "merge":
            self.sas_code_button.setToolTip(
                "SAS code generation for merged sources is not available yet."
            )
        else:
            self.sas_code_button.setToolTip("")

    def _run(self) -> None:
        selection = self.selection()
        if selection is not None:
            self.run_requested.emit(selection)

    def _generate_sas_code(self) -> None:
        selection = self.selection()
        if selection is not None:
            self.sas_code_requested.emit(selection)

    def clear(self) -> None:
        self.analysis_variables.clear()
        self.base_decimal_table.setRowCount(0)
        self.group_variables.clear()
        self.row_group_variables.clear()
        self.treatment_variable.clear()
        self.population_treatment_variable.clear()
        self.filter_editor.clear()
        self.population_filter_editor.clear()
        self.item_labels.clear()
        self._source_text = ""
        self._source_kind = "sas"
        self._population_source_text = ""
        self.groups_as_columns.setChecked(True)
        self.status.clear()
        self.cleared.emit()

    def set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self._update_enabled()
        if message:
            self.status.setText(message)

    def _update_enabled(self) -> None:
        enabled = self._metadata is not None and not self._busy
        self.run_button.setEnabled(enabled)
        codegen_available = enabled and self._source_kind != "merge"
        self.sas_code_button.setEnabled(codegen_available)
        self.sas_code_button.setToolTip(
            "SAS code generation for merged sources is not available yet."
            if self._source_kind == "merge"
            else ""
        )
        self.settings_button.setEnabled(not self._busy)
        for editor in (
            self.analysis_variables,
            self.group_variables,
            self.row_group_variables,
            self.treatment_variable,
            self.population_treatment_variable,
        ):
            editor.setEnabled(enabled)
        self.statistics.setEnabled(enabled)
        self.stat_up.setEnabled(enabled)
        self.stat_down.setEnabled(enabled)
        self.filter_editor.setEnabled(enabled)
        self.population_filter_editor.setEnabled(enabled)
        self.item_labels.setEnabled(enabled)
        self.include_total.setEnabled(enabled)
        self._update_layout_controls()
