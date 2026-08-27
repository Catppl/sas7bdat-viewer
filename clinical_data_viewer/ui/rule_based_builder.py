from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..domain import DatasetMetadata


@dataclass(frozen=True, slots=True)
class RuleBasedRowSelection:
    row_id: str
    item: str
    filter_text: str
    indent: int


@dataclass(frozen=True, slots=True)
class RuleBasedBuilderSelection:
    rows: tuple[RuleBasedRowSelection, ...]
    dataset_filter_text: str
    treatment_variable: str
    denominator_type: str
    population_tab: object | None
    population_treatment_variable: str
    population_filter_text: str
    nonmissing_value_variable: str
    include_total: bool


class RuleBasedBuilder(QWidget):
    run_requested = Signal(object)
    sas_code_requested = Signal(object)
    validation_error = Signal(str)
    browse_adsl_requested = Signal()
    cleared = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ruleBasedBuilder")
        self._metadata: DatasetMetadata | None = None
        self._source_kind = "sas"
        self._filter_text = ""
        self._source_filter_snapshot = ""
        self._busy = False
        self._row_counter = 0
        self._adsl_user_selected = False
        self._population_treatment_user_selected = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        content.setObjectName("ruleBasedBuilderContent")
        # Keep the full layout width/height as the scroll area's scrollable
        # extent.  Without an explicit min/max layout constraint, some Qt
        # styles can resize the child to the viewport and clip its right edge
        # even after the horizontal bar reaches its maximum.
        content.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)
        layout.setSizeConstraint(QLayout.SetMinAndMaxSize)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self.source_label = QLabel("Select a fully loaded source dataset.")
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)

        setup = QGroupBox("Table Setup")
        setup_layout = QFormLayout(setup)
        self.treatment = QComboBox()
        self.subject_label = QLabel("USUBJID")
        self.subject_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.include_total = QCheckBox("Include Total column")
        self.include_total.setChecked(True)
        setup_layout.addRow("Treatment variable", self.treatment)
        setup_layout.addRow("Subject ID", self.subject_label)
        setup_layout.addRow("", self.include_total)
        layout.addWidget(setup)

        source_group = QGroupBox("Source Filter")
        source_layout = QVBoxLayout(source_group)
        source_layout.addWidget(QLabel("Dataset-level Filter"))
        self.dataset_filter = QPlainTextEdit()
        self.dataset_filter.setPlaceholderText('e.g. SAFFL = "Y" and TRTEMFL = "Y"')
        self.dataset_filter.setMaximumHeight(78)
        self.dataset_filter.textChanged.connect(self._dataset_filter_changed)
        source_layout.addWidget(self.dataset_filter)
        layout.addWidget(source_group)

        denominator = QGroupBox("Denominator")
        denominator_layout = QVBoxLayout(denominator)
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Type"))
        self.denominator_type = QComboBox()
        self.denominator_type.addItem("Population N (ADSL)", "population")
        self.denominator_type.addItem("Non-missing N", "nonmissing")
        self.denominator_type.addItem("Same-universe N", "same_universe")
        self.denominator_type.currentIndexChanged.connect(self._sync_denominator_page)
        type_row.addWidget(self.denominator_type, 1)
        denominator_layout.addLayout(type_row)
        self.denominator_stack = QStackedWidget()

        population_page = QWidget()
        population_layout = QFormLayout(population_page)
        adsl_row = QHBoxLayout()
        self.adsl = QComboBox()
        self.adsl.currentIndexChanged.connect(self._mark_adsl_user_selection)
        adsl_row.addWidget(self.adsl, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse_adsl_requested)
        adsl_row.addWidget(browse)
        population_layout.addRow("ADSL dataset", adsl_row)
        self.population_treatment = QComboBox()
        self.population_treatment.currentIndexChanged.connect(
            self._mark_population_treatment_user_selection
        )
        population_layout.addRow("ADSL treatment variable", self.population_treatment)
        self.population_where = QLineEdit()
        self.population_where.setPlaceholderText('e.g. SAFFL = "Y"')
        population_layout.addRow("Population WHERE", self.population_where)
        self.denominator_stack.addWidget(population_page)

        nonmissing_page = QWidget()
        nonmissing_layout = QFormLayout(nonmissing_page)
        self.nonmissing_value = QComboBox()
        nonmissing_layout.addRow("Analysis value", self.nonmissing_value)
        self.denominator_stack.addWidget(nonmissing_page)

        same_page = QWidget()
        same_layout = QVBoxLayout(same_page)
        same_layout.addWidget(
            QLabel("Denominator uses the source Dataset Filter universe by treatment.")
        )
        same_layout.addStretch(1)
        self.denominator_stack.addWidget(same_page)
        denominator_layout.addWidget(self.denominator_stack)
        layout.addWidget(denominator)

        rows_group = QGroupBox("Rows")
        rows_layout = QVBoxLayout(rows_group)
        self.rows_table = QTableWidget(0, 4)
        self.rows_table.setHorizontalHeaderLabels(["Item", "Filter", "Count", "Indent"])
        self.rows_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.rows_table.setSelectionMode(QTableWidget.SingleSelection)
        self.rows_table.verticalHeader().setVisible(False)
        self.rows_table.setMinimumHeight(150)
        self.rows_table.horizontalHeader().setStretchLastSection(False)
        self.rows_table.setColumnWidth(0, 135)
        self.rows_table.setColumnWidth(1, 220)
        self.rows_table.setColumnWidth(2, 135)
        self.rows_table.setColumnWidth(3, 60)
        rows_layout.addWidget(self.rows_table)
        row_buttons = QHBoxLayout()
        add = QPushButton("Add Rows")
        add.clicked.connect(self.add_row)
        remove = QPushButton("Remove")
        remove.clicked.connect(self.remove_row)
        up = QPushButton("Move Up")
        up.clicked.connect(lambda: self.move_row(-1))
        down = QPushButton("Move Down")
        down.clicked.connect(lambda: self.move_row(1))
        for button in (add, remove, up, down):
            row_buttons.addWidget(button)
        row_buttons.addStretch(1)
        rows_layout.addLayout(row_buttons)
        layout.addWidget(rows_group, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        buttons.addWidget(clear)
        self.sas_code_button = QPushButton("SAS Code Generator…")
        self.sas_code_button.clicked.connect(self._generate_sas_code)
        buttons.addWidget(self.sas_code_button)
        self.run_button = QPushButton("Run Rule-based Table")
        self.run_button.setDefault(True)
        self.run_button.clicked.connect(self._run)
        buttons.addWidget(self.run_button, 1)
        outer.addLayout(buttons)
        self._sync_denominator_page()
        self.set_dataset(None, "")

    def set_dataset(
        self,
        metadata: DatasetMetadata | None,
        source_text: str,
        filter_text: str = "",
        source_kind: str = "sas",
    ) -> None:
        self._source_kind = source_kind
        if metadata is not None and metadata is not self._metadata:
            self._metadata = metadata
            self._filter_text = filter_text.strip()
            self._source_filter_snapshot = self._filter_text
            self._set_dataset_filter(self._filter_text)
            names = [variable.name for variable in metadata.variables]
            self.treatment.clear()
            self.treatment.addItems(names)
            self.nonmissing_value.clear()
            self.nonmissing_value.addItems(names)
            self.rows_table.setRowCount(0)
            self._row_counter = 0
            self._refresh_population_treatment_variables()
        self.source_label.setText(
            f"Source: {source_text}"
            if metadata
            else "Select a fully loaded source dataset."
        )
        enabled = metadata is not None and not self._busy
        self.setEnabled(enabled or self._busy)
        self.run_button.setEnabled(enabled)
        codegen_available = enabled and source_kind == "sas"
        self.sas_code_button.setEnabled(codegen_available)
        self.sas_code_button.setToolTip(
            "SAS code generation for merged Rule-based sources is not available yet."
            if source_kind == "merge"
            else ""
        )

    def current_filter_text(self) -> str:
        return self._filter_text

    def apply_current_filter(self, text: str) -> None:
        self._filter_text = text.strip()
        self._source_filter_snapshot = self._filter_text
        self._set_dataset_filter(self._filter_text)

    def inherit_current_filter(self, text: str) -> None:
        if self._filter_text != self._source_filter_snapshot:
            return
        self._filter_text = text.strip()
        self._source_filter_snapshot = self._filter_text
        self._set_dataset_filter(self._filter_text)

    def _set_dataset_filter(self, text: str) -> None:
        self.dataset_filter.blockSignals(True)
        self.dataset_filter.setPlainText(text)
        self.dataset_filter.blockSignals(False)

    def _dataset_filter_changed(self) -> None:
        self._filter_text = self.dataset_filter.toPlainText().strip()

    def set_adsl_sources(self, datasets: list[tuple[object, str]]) -> None:
        current = self.adsl.currentData()
        self.adsl.blockSignals(True)
        self.adsl.clear()
        for tab, label in datasets:
            self.adsl.addItem(label, tab)
        if current is not None:
            index = self.adsl.findData(current)
            if index >= 0:
                self.adsl.setCurrentIndex(index)
        if datasets and not self._adsl_user_selected:
            preferred = next(
                (
                    index
                    for index, (_tab, label) in enumerate(datasets)
                    if label.split(" — ", 1)[0].casefold() == "adsl"
                ),
                0,
            )
            self.adsl.setCurrentIndex(preferred)
        self.adsl.blockSignals(False)
        self._refresh_population_treatment_variables()

    def select_adsl(self, tab: object) -> None:
        index = self.adsl.findData(tab)
        if index >= 0:
            self._adsl_user_selected = True
            self.adsl.setCurrentIndex(index)

    def _mark_adsl_user_selection(self, _index: int) -> None:
        if not self.adsl.signalsBlocked():
            self._adsl_user_selected = True
            self._refresh_population_treatment_variables()

    def _mark_population_treatment_user_selection(self, _index: int) -> None:
        if not self.population_treatment.signalsBlocked():
            self._population_treatment_user_selected = True

    def _refresh_population_treatment_variables(self) -> None:
        """Populate the independent ADSL treatment variable selector."""
        selected = (
            self.population_treatment.currentText()
            if self._population_treatment_user_selected
            else ""
        )
        tab = self.adsl.currentData()
        metadata = getattr(getattr(tab, "handle", None), "metadata", None)
        names = [variable.name for variable in metadata.variables] if metadata else []
        self.population_treatment.blockSignals(True)
        self.population_treatment.clear()
        self.population_treatment.addItems(names)
        source_treatment = self.treatment.currentText()
        preferred = (
            selected
            if selected in names
            else source_treatment
            if source_treatment.casefold().startswith("trt")
            else ""
        )
        index = self.population_treatment.findText(preferred, Qt.MatchFixedString)
        if index < 0:
            # ADSL often has TRT01AN while the source has TRTAN.  Prefer a
            # treatment-looking variable over an arbitrary first column, but
            # always leave the final choice visible and editable to the user.
            index = next(
                (
                    position
                    for position, name in enumerate(names)
                    if name.casefold().startswith("trt")
                ),
                -1,
            )
        if index >= 0:
            self.population_treatment.setCurrentIndex(index)
        self.population_treatment.blockSignals(False)

    def add_row(self) -> None:
        self._row_counter += 1
        row = self.rows_table.rowCount()
        self.rows_table.insertRow(row)
        self.rows_table.setCellWidget(row, 0, QLineEdit())
        self.rows_table.setCellWidget(row, 1, QLineEdit())
        count = QLabel("Distinct USUBJID")
        count.setAlignment(Qt.AlignCenter)
        count.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.rows_table.setCellWidget(row, 2, count)
        indent = QSpinBox()
        indent.setRange(0, 8)
        indent.setValue(0)
        self.rows_table.setCellWidget(row, 3, indent)
        self.rows_table.selectRow(row)

    def remove_row(self) -> None:
        row = self.rows_table.currentRow()
        if row >= 0:
            self.rows_table.removeRow(row)

    def move_row(self, delta: int) -> None:
        current = self.rows_table.currentRow()
        target = current + delta
        if current < 0 or target < 0 or target >= self.rows_table.rowCount():
            return
        values = [self._row_values(current), self._row_values(target)]
        self._set_row_values(current, values[1])
        self._set_row_values(target, values[0])
        self.rows_table.selectRow(target)

    def _row_values(self, row: int) -> tuple[str, str, int]:
        item = self.rows_table.cellWidget(row, 0)
        filter_editor = self.rows_table.cellWidget(row, 1)
        indent = self.rows_table.cellWidget(row, 3)
        return (
            item.text().strip() if isinstance(item, QLineEdit) else "",
            filter_editor.text().strip()
            if isinstance(filter_editor, QLineEdit)
            else "",
            indent.value() if isinstance(indent, QSpinBox) else 0,
        )

    def _set_row_values(self, row: int, values: tuple[str, str, int]) -> None:
        item = self.rows_table.cellWidget(row, 0)
        filter_editor = self.rows_table.cellWidget(row, 1)
        indent = self.rows_table.cellWidget(row, 3)
        if isinstance(item, QLineEdit):
            item.setText(values[0])
        if isinstance(filter_editor, QLineEdit):
            filter_editor.setText(values[1])
        if isinstance(indent, QSpinBox):
            indent.setValue(values[2])

    def set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.run_button.setEnabled(self._metadata is not None and not busy)
        self.sas_code_button.setEnabled(
            self._metadata is not None and not busy and self._source_kind == "sas"
        )
        if message:
            self.status.setText(message)

    def clear(self) -> None:
        self.rows_table.setRowCount(0)
        self.status.clear()
        self.cleared.emit()

    def _sync_denominator_page(self) -> None:
        self.denominator_stack.setCurrentIndex(self.denominator_type.currentIndex())
        self.population_treatment.setEnabled(
            self.denominator_type.currentData() == "population" and not self._busy
        )

    def _selection(self) -> RuleBasedBuilderSelection | None:
        if self._metadata is None:
            self.validation_error.emit("Select a fully loaded source dataset.")
            return None
        if not self.treatment.currentText():
            self.validation_error.emit("Select a Treatment variable.")
            return None
        rows = []
        for index in range(self.rows_table.rowCount()):
            item, filter_text, indent = self._row_values(index)
            rows.append(
                RuleBasedRowSelection(f"row_{index + 1:03d}", item, filter_text, indent)
            )
        if not rows:
            self.validation_error.emit("Add at least one Rule-based Table row.")
            return None
        return RuleBasedBuilderSelection(
            tuple(rows),
            self.current_filter_text(),
            self.treatment.currentText(),
            str(self.denominator_type.currentData()),
            self.adsl.currentData(),
            self.population_treatment.currentText(),
            self.population_where.text().strip(),
            self.nonmissing_value.currentText(),
            self.include_total.isChecked(),
        )

    def _run(self) -> None:
        selection = self._selection()
        if selection is None:
            return
        self.run_requested.emit(selection)

    def _generate_sas_code(self) -> None:
        if self._source_kind != "sas":
            return
        selection = self._selection()
        if selection is not None:
            self.sas_code_requested.emit(selection)
