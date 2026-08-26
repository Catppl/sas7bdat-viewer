from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..categorical import CategoricalItem
from ..domain import DatasetMetadata
from .proc_means_builder import VariableTokenEditor


@dataclass(frozen=True, slots=True)
class CategoricalBuilderSelection:
    items: tuple[CategoricalItem, ...]
    numerator_filter_text: str
    treatment_variable: str
    subject_id_variable: str
    count_type: str
    denominator_type: str
    analysis_value_variable: str
    population_tab: object | None
    population_filter_text: str
    baseline_filter_text: str
    postbaseline_filter_text: str
    include_total: bool
    percent_digits: int


class CategoricalItemEditor(QWidget):
    """Compact per-item editor; context and missing choices follow selection."""

    changed = Signal()
    validation_error = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._metadata: DatasetMetadata | None = None
        self._configs: dict[str, CategoricalItem] = {}
        self._updating = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        row = QHBoxLayout()
        self.editor = QLineEdit()
        self.editor.setPlaceholderText("Type a categorical variable and press Enter")
        self.editor.returnPressed.connect(self._add)
        row.addWidget(self.editor, 1)
        remove = QPushButton("Remove")
        remove.clicked.connect(self._remove)
        row.addWidget(remove)
        layout.addLayout(row)
        self.items = QListWidget()
        self.items.setMaximumHeight(75)
        self.items.currentItemChanged.connect(self._load_current)
        layout.addWidget(self.items)
        self.label_editor = QLineEdit()
        self.label_editor.setPlaceholderText("Item label (optional)")
        self.label_editor.editingFinished.connect(self._save_current)
        layout.addWidget(self.label_editor)
        layout.addWidget(QLabel("Context / group variables for selected Item"))
        self.contexts = VariableTokenEditor("Type a context variable and press Enter")
        self.contexts.setMaximumHeight(96)
        self.contexts.changed.connect(self._save_current)
        self.contexts.validation_error.connect(self.validation_error)
        layout.addWidget(self.contexts)
        self.include_missing = QCheckBox("Include missing level")
        self.include_missing.toggled.connect(lambda _checked: self._save_current())
        layout.addWidget(self.include_missing)

    def set_metadata(self, metadata: DatasetMetadata | None) -> None:
        if metadata is self._metadata:
            return
        self._metadata = metadata
        self._configs.clear()
        self.items.clear()
        self.editor.clear()
        self.contexts.set_metadata(metadata)
        self.setEnabled(metadata is not None)

    def selected_items(self) -> tuple[CategoricalItem, ...]:
        self._save_current()
        return tuple(
            self._configs[self.items.item(index).text()]
            for index in range(self.items.count())
        )

    def _add(self) -> None:
        requested = self.editor.text().strip()
        if not requested or self._metadata is None:
            return
        variables = {
            variable.name.casefold(): variable for variable in self._metadata.variables
        }
        variable = variables.get(requested.casefold())
        if variable is None:
            self.validation_error.emit(
                f'Variable "{requested}" does not exist in the current dataset.'
            )
            return
        if variable.name not in self._configs:
            self._configs[variable.name] = CategoricalItem(variable.name, variable.label)
            self.items.addItem(variable.name)
            self.items.setCurrentRow(self.items.count() - 1)
        self.editor.clear()
        self.changed.emit()

    def _remove(self) -> None:
        current = self.items.currentItem()
        if current is None:
            return
        name = current.text()
        row = self.items.row(current)
        self._configs.pop(name, None)
        self.items.takeItem(row)
        self.changed.emit()

    def _load_current(self, current: QListWidgetItem | None, _previous) -> None:
        self._updating = True
        if current is None:
            self.label_editor.clear()
            self.contexts.set_variables(())
            self.include_missing.setChecked(False)
        else:
            item = self._configs[current.text()]
            self.label_editor.setText(item.label)
            self.contexts.set_variables(item.context_variables)
            self.include_missing.setChecked(item.include_missing_level)
        self._updating = False

    def _save_current(self) -> None:
        if self._updating:
            return
        current = self.items.currentItem()
        if current is None:
            return
        existing = self._configs[current.text()]
        self._configs[current.text()] = CategoricalItem(
            existing.variable,
            self.label_editor.text().strip(),
            self.contexts.selected_variables(),
            self.include_missing.isChecked(),
            existing.level_order,
        )
        self.changed.emit()


class CategoricalBuilder(QWidget):
    run_requested = Signal(object)
    validation_error = Signal(str)
    browse_adsl_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("categoricalBuilder")
        self._metadata: DatasetMetadata | None = None
        # Kept as _filter_text for compatibility with the existing Builder
        # API; in this module it always means Numerator WHERE.
        self._filter_text = ""
        self._source_filter_snapshot = ""
        self._busy = False
        self._adsl_user_selected = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        content.setObjectName("categoricalBuilderContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self.source_label = QLabel("Select a fully loaded source dataset.")
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)
        layout.addWidget(QLabel("Categorical Items"))
        self.items = CategoricalItemEditor()
        self.items.validation_error.connect(self.validation_error)
        layout.addWidget(self.items)

        setup = QGroupBox("Table Setup")
        setup_layout = QFormLayout(setup)
        self.treatment = QComboBox()
        self.subject = QComboBox()
        self.count_type = QComboBox()
        self.count_type.addItem("Distinct subjects", "distinct_subject")
        self.count_type.addItem("Record count", "record")
        self.percent_digits = QSpinBox()
        self.percent_digits.setRange(0, 4)
        self.percent_digits.setValue(1)
        self.include_total = QCheckBox("Include Total column")
        self.include_total.setChecked(True)
        setup_layout.addRow("Treatment variable", self.treatment)
        setup_layout.addRow("Subject ID variable", self.subject)
        setup_layout.addRow("Count", self.count_type)
        setup_layout.addRow("Percent decimal digits", self.percent_digits)
        setup_layout.addRow("", self.include_total)
        layout.addWidget(setup)

        layout.addWidget(QLabel("Numerator WHERE"))
        self.numerator_where = QPlainTextEdit()
        self.numerator_where.setObjectName("categoricalNumeratorWhere")
        self.numerator_where.setPlaceholderText(
            'e.g. TRTEMFL = "Y" and AOCC01FL = "Y"'
        )
        self.numerator_where.setMaximumHeight(78)
        self.numerator_where.textChanged.connect(self._numerator_where_changed)
        layout.addWidget(self.numerator_where)

        denominator = QGroupBox("Denominator")
        denominator_layout = QVBoxLayout(denominator)
        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Type"))
        self.denominator_type = QComboBox()
        self.denominator_type.addItem("Population N (ADSL)", "population")
        self.denominator_type.addItem("Non-missing N", "nonmissing")
        self.denominator_type.addItem("Baseline + Postbaseline n1", "baseline_postbaseline")
        self.denominator_type.currentIndexChanged.connect(self._sync_denominator_page)
        type_row.addWidget(self.denominator_type, 1)
        denominator_layout.addLayout(type_row)
        self.denominator_stack = QStackedWidget()
        self.population_page = QWidget()
        population_layout = QFormLayout(self.population_page)
        adsl_row = QHBoxLayout()
        self.adsl = QComboBox()
        self.adsl.currentIndexChanged.connect(self._mark_adsl_user_selection)
        adsl_row.addWidget(self.adsl, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse_adsl_requested)
        adsl_row.addWidget(browse)
        population_layout.addRow("ADSL dataset", adsl_row)
        self.population_where = QLineEdit()
        self.population_where.setPlaceholderText('e.g. SAFFL = "Y"')
        population_layout.addRow("Population WHERE", self.population_where)
        self.denominator_stack.addWidget(self.population_page)
        self.nonmissing_page = QWidget()
        nonmissing_layout = QFormLayout(self.nonmissing_page)
        self.nonmissing_value = QComboBox()
        nonmissing_layout.addRow("Analysis value", self.nonmissing_value)
        self.denominator_stack.addWidget(self.nonmissing_page)
        self.n1_page = QWidget()
        n1_layout = QFormLayout(self.n1_page)
        self.n1_value = QComboBox()
        self.baseline_where = QLineEdit()
        self.baseline_where.setPlaceholderText('e.g. ABLFL = "Y"')
        self.postbaseline_where = QLineEdit()
        self.postbaseline_where.setPlaceholderText('e.g. ABLFL != "Y"')
        n1_layout.addRow("Analysis value", self.n1_value)
        n1_layout.addRow("Baseline WHERE", self.baseline_where)
        n1_layout.addRow("Postbaseline WHERE", self.postbaseline_where)
        self.denominator_stack.addWidget(self.n1_page)
        denominator_layout.addWidget(self.denominator_stack)
        layout.addWidget(denominator)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        buttons.addWidget(clear)
        self.run_button = QPushButton("Run Categorical Table")
        self.run_button.setDefault(True)
        self.run_button.clicked.connect(self._run)
        buttons.addWidget(self.run_button, 1)
        outer.addLayout(buttons)
        self._sync_denominator_page()
        self.set_dataset(None, "")

    def set_dataset(
        self, metadata: DatasetMetadata | None, source_text: str, filter_text: str = ""
    ) -> None:
        if metadata is not None and metadata is not self._metadata:
            self._metadata = metadata
            self._filter_text = filter_text.strip()
            self._source_filter_snapshot = self._filter_text
            self._set_numerator_where(self._filter_text)
            names = [variable.name for variable in metadata.variables]
            numeric = [variable.name for variable in metadata.variables if variable.kind == "numeric"]
            for combo, values in (
                (self.treatment, names),
                (self.subject, names),
                (self.nonmissing_value, names),
                (self.n1_value, names),
            ):
                combo.clear()
                combo.addItems(values)
            self.items.set_metadata(metadata)
        self.source_label.setText(
            f"Source: {source_text}" if metadata else "Select a fully loaded source dataset."
        )
        enabled = metadata is not None and not self._busy
        self.setEnabled(enabled or self._busy)
        self.run_button.setEnabled(enabled)

    def set_adsl_sources(self, datasets: list[tuple[object, str]]) -> None:
        current = self.adsl.currentData()
        self.adsl.blockSignals(True)
        self.adsl.clear()
        for tab, label in datasets:
            self.adsl.addItem(label, tab)
        current_index = -1
        if current is not None:
            index = self.adsl.findData(current)
            if index >= 0:
                self.adsl.setCurrentIndex(index)
                current_index = index
        if datasets and not self._adsl_user_selected:
            # Prefer an already-open dataset explicitly named ADSL, while still
            # allowing Browse/manual selection for studies with another filename.
            adsl_index = next(
                (
                    index
                    for index, (_tab, label) in enumerate(datasets)
                    if label.split(" — ", 1)[0].casefold() == "adsl"
                ),
                current_index if current_index >= 0 else 0,
            )
            self.adsl.setCurrentIndex(adsl_index)
        self.adsl.blockSignals(False)

    def select_adsl(self, tab: object) -> None:
        index = self.adsl.findData(tab)
        if index >= 0:
            self._adsl_user_selected = True
            self.adsl.setCurrentIndex(index)

    def _mark_adsl_user_selection(self, _index: int) -> None:
        if not self.adsl.signalsBlocked():
            self._adsl_user_selected = True

    def current_filter_text(self) -> str:
        return self._filter_text

    def apply_current_filter(self, text: str) -> None:
        self._filter_text = text.strip()
        self._source_filter_snapshot = self._filter_text
        self._set_numerator_where(self._filter_text)

    def inherit_current_filter(self, text: str) -> None:
        """Refresh the default Numerator WHERE without overwriting edits.

        The Builder is often opened after a user applies a WHERE on the
        source tab.  If the Builder still contains the previous inherited
        value, it is safe to refresh it; an explicitly edited Numerator WHERE
        remains untouched.
        """

        if self._filter_text != self._source_filter_snapshot:
            return
        self._filter_text = text.strip()
        self._source_filter_snapshot = self._filter_text
        self._set_numerator_where(self._filter_text)

    def _set_numerator_where(self, text: str) -> None:
        self.numerator_where.blockSignals(True)
        self.numerator_where.setPlainText(text)
        self.numerator_where.blockSignals(False)

    def _numerator_where_changed(self) -> None:
        """Keep the Builder's numerator filter independent from the source tab."""

        self._filter_text = self.numerator_where.toPlainText().strip()

    def set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.run_button.setEnabled(self._metadata is not None and not busy)
        if message:
            self.status.setText(message)

    def clear(self) -> None:
        self.items.set_metadata(None)
        self.items.set_metadata(self._metadata)
        self.population_where.clear()
        self.baseline_where.clear()
        self.postbaseline_where.clear()
        self.status.clear()

    def _sync_denominator_page(self) -> None:
        is_n1 = self.denominator_type.currentData() == "baseline_postbaseline"
        self.denominator_stack.setCurrentIndex(self.denominator_type.currentIndex())
        if is_n1:
            record_index = self.count_type.findData("record")
            self.count_type.setCurrentIndex(record_index)
        self.count_type.setEnabled(not is_n1)

    def _run(self) -> None:
        items = self.items.selected_items()
        if not items:
            self.validation_error.emit("Select at least one categorical Item.")
            return
        if not self.treatment.currentText():
            self.validation_error.emit("Select a Treatment variable.")
            return
        selection = CategoricalBuilderSelection(
            items,
            self.current_filter_text(),
            self.treatment.currentText(),
            self.subject.currentText(),
            str(self.count_type.currentData()),
            str(self.denominator_type.currentData()),
            self.nonmissing_value.currentText()
            if self.denominator_type.currentData() == "nonmissing"
            else self.n1_value.currentText(),
            self.adsl.currentData(),
            self.population_where.text().strip(),
            self.baseline_where.text().strip(),
            self.postbaseline_where.text().strip(),
            self.include_total.isChecked(),
            self.percent_digits.value(),
        )
        self.run_requested.emit(selection)
