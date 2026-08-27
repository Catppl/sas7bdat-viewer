from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
    QVBoxLayout, QWidget,
)

from ..domain import DatasetMetadata


@dataclass(frozen=True, slots=True)
class AeTableBuilderSelection:
    dataset_filter_text: str
    treatment_variable: str
    soc_variable: str
    pt_variable: str
    denominator_type: str
    population_tab: object | None
    population_filter_text: str
    include_any_ae: bool
    any_ae_label: str
    include_total: bool
    percent_digits: int
    hierarchy_missing_policy: str


class AeTableBuilder(QWidget):
    run_requested = Signal(object)
    sas_code_requested = Signal(object)
    validation_error = Signal(str)
    browse_adsl_requested = Signal()
    cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._metadata = None
        self._source_filter_snapshot = ""
        self._filter_text = ""
        self._busy = False
        self._source_kind = "sas"
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content); layout.setContentsMargins(6, 6, 6, 6)
        outer.addWidget(scroll, 1)
        source = QGroupBox("Source"); form = QFormLayout(source)
        self.source_label = QLabel("Select a fully loaded source dataset."); self.source_label.setWordWrap(True)
        form.addRow(self.source_label)
        self.treatment = QComboBox(); form.addRow("Treatment variable", self.treatment)
        subject = QLabel("USUBJID"); subject.setTextInteractionFlags(Qt.TextSelectableByMouse); form.addRow("Subject ID", subject)
        self.soc = QComboBox(); form.addRow("SOC variable", self.soc)
        self.pt = QComboBox(); form.addRow("PT variable", self.pt)
        self.missing_policy = QComboBox()
        self.missing_policy.addItem("Exclude", "exclude")
        self.missing_policy.addItem('Show as "Uncoded"', "uncoded")
        form.addRow("Missing SOC / PT", self.missing_policy)
        layout.addWidget(source)
        filters = QGroupBox("Dataset Filter"); f = QVBoxLayout(filters)
        self.dataset_filter = QPlainTextEdit(); self.dataset_filter.setPlaceholderText('e.g. TRTEMFL = "Y"'); self.dataset_filter.setMaximumHeight(78); self.dataset_filter.textChanged.connect(lambda: setattr(self, "_filter_text", self.dataset_filter.toPlainText().strip())); f.addWidget(self.dataset_filter); layout.addWidget(filters)
        denom = QGroupBox("Denominator"); df = QFormLayout(denom)
        self.denominator_type = QComboBox(); self.denominator_type.addItem("Population N (ADSL)", "population"); self.denominator_type.addItem("Same-universe N", "same_universe"); self.denominator_type.currentIndexChanged.connect(self._sync_denominator); df.addRow("Type", self.denominator_type)
        self.adsl = QComboBox(); row = QHBoxLayout(); row.addWidget(self.adsl, 1); browse = QPushButton("Browse…"); browse.clicked.connect(self.browse_adsl_requested); row.addWidget(browse); df.addRow("ADSL dataset", row)
        self.population_where = QLineEdit(); self.population_where.setPlaceholderText('e.g. SAFFL = "Y"'); df.addRow("Population WHERE", self.population_where)
        layout.addWidget(denom)
        options = QGroupBox("Display"); of = QFormLayout(options)
        self.include_any = QCheckBox("Include Any AE row"); self.include_any.setChecked(True); of.addRow(self.include_any)
        self.any_label = QLineEdit("Any AE"); of.addRow("Any AE label", self.any_label)
        self.include_total = QCheckBox("Include Total column"); self.include_total.setChecked(True); of.addRow(self.include_total)
        self.percent_digits = QSpinBox(); self.percent_digits.setRange(0, 4); self.percent_digits.setValue(1); of.addRow("Percent digits", self.percent_digits)
        layout.addWidget(options); layout.addStretch(1)
        self.status = QLabel(""); self.status.setWordWrap(True); layout.addWidget(self.status)
        scroll.setWidget(content)
        buttons = QHBoxLayout(); clear = QPushButton("Clear"); clear.clicked.connect(self.clear); buttons.addWidget(clear)
        self.sas_code_button = QPushButton("SAS Code Generator…"); self.sas_code_button.clicked.connect(self._generate_sas_code); buttons.addWidget(self.sas_code_button)
        self.run_button = QPushButton("Run AE Table"); self.run_button.setDefault(True); self.run_button.clicked.connect(self._run); buttons.addWidget(self.run_button, 1); outer.addLayout(buttons)
        self._sync_denominator(); self.set_dataset(None, "")

    def set_dataset(self, metadata: DatasetMetadata | None, source_text: str, filter_text: str = "", source_kind: str = "sas"):
        self._source_kind = source_kind
        if metadata is not None and metadata is not self._metadata:
            self._metadata = metadata; self._filter_text = filter_text.strip(); self._source_filter_snapshot = self._filter_text
            self.dataset_filter.setPlainText(self._filter_text)
            names = [v.name for v in metadata.variables]
            for combo, preferred in ((self.treatment, ("TRT01A", "TRT01P", "TRTA")), (self.soc, ("AEBODSYS",)), (self.pt, ("AEDECOD",))):
                combo.clear(); combo.addItems(names)
                for candidate in preferred:
                    index = combo.findText(candidate, Qt.MatchFixedString)
                    if index >= 0: combo.setCurrentIndex(index); break
        self.source_label.setText(f"Source: {source_text}" if metadata else "Select a fully loaded source dataset.")
        enabled = metadata is not None and not self._busy; self.setEnabled(enabled or self._busy); self.run_button.setEnabled(enabled)
        self.sas_code_button.setEnabled(enabled and source_kind == "sas")
        self.sas_code_button.setToolTip("SAS code generation for merged AE sources is not available yet." if source_kind == "merge" else "Generate reusable SAS code from the current AE configuration.")

    def inherit_current_filter(self, text: str):
        if self._filter_text == self._source_filter_snapshot:
            self._filter_text = text.strip(); self._source_filter_snapshot = self._filter_text; self.dataset_filter.setPlainText(self._filter_text)

    def current_filter_text(self): return self._filter_text
    def set_adsl_sources(self, datasets):
        current = self.adsl.currentData(); self.adsl.blockSignals(True); self.adsl.clear()
        for tab, label in datasets: self.adsl.addItem(label, tab)
        if current is not None and self.adsl.findData(current) >= 0:
            self.adsl.setCurrentIndex(self.adsl.findData(current))
        else:
            for index in range(self.adsl.count()):
                tab = self.adsl.itemData(index)
                if getattr(getattr(tab, "handle", None), "metadata", None) and tab.handle.metadata.name.casefold() == "adsl":
                    self.adsl.setCurrentIndex(index)
                    break
        self.adsl.blockSignals(False); self._sync_denominator()
    def select_adsl(self, tab):
        index = self.adsl.findData(tab)
        if index >= 0: self.adsl.setCurrentIndex(index)
    def _sync_denominator(self):
        population = self.denominator_type.currentData() == "population"; self.adsl.setEnabled(population and not self._busy); self.population_where.setEnabled(population and not self._busy)
    def set_busy(self, busy: bool, message: str = ""):
        self._busy = busy; available = not busy and self._metadata is not None; self.run_button.setEnabled(available); self.sas_code_button.setEnabled(available and self._source_kind == "sas"); self._sync_denominator(); self.status.setText(message)
    def clear(self):
        self.dataset_filter.clear(); self.population_where.clear(); self.status.clear()
        self.cleared.emit()
    def _selection(self):
        if self._metadata is None: return
        population = self.adsl.currentData() if self.denominator_type.currentData() == "population" else None
        return AeTableBuilderSelection(self._filter_text, self.treatment.currentText(), self.soc.currentText(), self.pt.currentText(), self.denominator_type.currentData(), population, self.population_where.text().strip(), self.include_any.isChecked(), self.any_label.text().strip() or "Any AE", self.include_total.isChecked(), self.percent_digits.value(), self.missing_policy.currentData())

    def _run(self):
        selection = self._selection()
        if selection is not None: self.run_requested.emit(selection)

    def _generate_sas_code(self):
        selection = self._selection()
        if selection is not None and self._source_kind == "sas" and not self._busy:
            self.sas_code_requested.emit(selection)
