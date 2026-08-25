from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..compare_engine import CompareConfig, MatchVariable


class DatasetComparePanel(QWidget):
    browse_requested = Signal(str)
    compare_requested = Signal(object, object, object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("datasetComparePanel")
        self._last_pair: tuple[object | None, object | None] = (None, None)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        title = QLabel("Dataset Compare")
        title.setObjectName("panelTitle")
        layout.addWidget(title)
        description = QLabel(
            "Choose Main and QC datasets, then configure grouping and matching. "
            "Current dataset WHERE conditions are ignored."
        )
        description.setWordWrap(True)
        layout.addWidget(description)

        for side, label in (("main", "Main Dataset"), ("qc", "QC Dataset")):
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            combo = QComboBox()
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(16)
            combo.currentIndexChanged.connect(self._datasets_changed)
            setattr(self, f"{side}_dataset", combo)
            row.addWidget(combo, 1)
            browse = QPushButton("Browse…")
            browse.setMinimumWidth(72)
            browse.clicked.connect(
                lambda _checked=False, selected_side=side: self.browse_requested.emit(
                    selected_side
                )
            )
            row.addWidget(browse)
            layout.addLayout(row)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter common variables")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_rows)
        layout.addWidget(self.search)

        self.variables = QTableWidget(0, 6)
        self.variables.setHorizontalHeaderLabels(
            ("Variable", "Group", "Match", "Key", "Weight", "Tolerance")
        )
        self.variables.verticalHeader().hide()
        self.variables.setAlternatingRowColors(True)
        self.variables.setSelectionBehavior(QTableWidget.SelectRows)
        self.variables.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for column in range(1, 6):
            self.variables.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeToContents
            )
        self.variables.itemChanged.connect(self._item_changed)
        layout.addWidget(self.variables, 1)

        settings = QFormLayout()
        self.threshold = QDoubleSpinBox()
        self.threshold.setRange(0, 1)
        self.threshold.setSingleStep(0.05)
        self.threshold.setDecimals(2)
        self.threshold.setValue(0.5)
        settings.addRow("Match threshold", self.threshold)
        self.ambiguity = QDoubleSpinBox()
        self.ambiguity.setRange(0, 1)
        self.ambiguity.setSingleStep(0.01)
        self.ambiguity.setDecimals(2)
        self.ambiguity.setValue(0.05)
        settings.addRow("Ambiguity margin", self.ambiguity)
        layout.addLayout(settings)

        self.status = QLabel("Select two datasets.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.compare_button = QPushButton("Compare")
        self.compare_button.setDefault(True)
        self.compare_button.clicked.connect(self._start_compare)
        button_row.addWidget(self.compare_button)
        layout.addLayout(button_row)
        self._update_enabled()

    def set_datasets(self, datasets: list[tuple[object, str, bool]]) -> None:
        previous_main = self.main_dataset.currentData()
        previous_qc = self.qc_dataset.currentData()
        for combo, previous in (
            (self.main_dataset, previous_main),
            (self.qc_dataset, previous_qc),
        ):
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("Select dataset…", None)
            for owner, label, complete in datasets:
                combo.addItem(label if complete else f"{label} (loading…)", owner)
                combo.setItemData(combo.count() - 1, complete, Qt.UserRole + 1)
            index = combo.findData(previous)
            combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)
        self._datasets_changed()

    def select_dataset(self, side: str, dataset: object) -> None:
        combo = self.main_dataset if side == "main" else self.qc_dataset
        index = combo.findData(dataset)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _datasets_changed(self) -> None:
        pair = (self.main_dataset.currentData(), self.qc_dataset.currentData())
        if pair != self._last_pair:
            self._last_pair = pair
            self._rebuild_variables()
        self._update_enabled()

    def _rebuild_variables(self) -> None:
        main = self.main_dataset.currentData()
        qc = self.qc_dataset.currentData()
        self.variables.blockSignals(True)
        self.variables.setRowCount(0)
        if main is None or qc is None or main is qc:
            self.variables.blockSignals(False)
            return
        qc_by_name = {
            variable.name.casefold(): variable
            for variable in qc.handle.metadata.variables
        }
        common = [
            variable
            for variable in main.handle.metadata.variables
            if variable.name.casefold() in qc_by_name
        ]
        group_defaults = {
            variable.name
            for variable in common
            if variable.name.casefold() in {"usubjid", "paramcd"}
        }
        for row, variable in enumerate(common):
            self.variables.insertRow(row)
            name = QTableWidgetItem(variable.name)
            name.setData(Qt.UserRole, variable.kind)
            self.variables.setItem(row, 0, name)
            for column in (1, 2, 3):
                item = QTableWidgetItem()
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                default_group = column == 1 and variable.name in group_defaults
                default_match = column == 2 and variable.name not in group_defaults
                item.setCheckState(
                    Qt.Checked if default_group or default_match else Qt.Unchecked
                )
                self.variables.setItem(row, column, item)
            weight = QDoubleSpinBox()
            weight.setRange(0.1, 100)
            weight.setValue(1)
            weight.setDecimals(1)
            self.variables.setCellWidget(row, 4, weight)
            tolerance = QDoubleSpinBox()
            tolerance.setRange(0, 1_000_000_000)
            tolerance.setDecimals(6)
            tolerance.setValue(0)
            tolerance.setEnabled(variable.kind == "numeric")
            self.variables.setCellWidget(row, 5, tolerance)
        self.variables.blockSignals(False)
        for row in range(self.variables.rowCount()):
            self._sync_match_controls(row)
        self._filter_rows(self.search.text())

    def _item_changed(self, item: QTableWidgetItem) -> None:
        if item.column() == 2:
            self._sync_match_controls(item.row())
        self._update_enabled()

    def _sync_match_controls(self, row: int) -> None:
        checked = self.variables.item(row, 2).checkState() == Qt.Checked
        self.variables.cellWidget(row, 4).setEnabled(checked)
        kind = self.variables.item(row, 0).data(Qt.UserRole)
        self.variables.cellWidget(row, 5).setEnabled(checked and kind == "numeric")

    def _filter_rows(self, text: str) -> None:
        needle = text.strip().casefold()
        for row in range(self.variables.rowCount()):
            self.variables.setRowHidden(
                row, needle not in self.variables.item(row, 0).text().casefold()
            )

    def _selected(self, column: int) -> tuple[str, ...]:
        return tuple(
            self.variables.item(row, 0).text()
            for row in range(self.variables.rowCount())
            if self.variables.item(row, column).checkState() == Qt.Checked
        )

    def config(self) -> CompareConfig:
        matches = []
        for row in range(self.variables.rowCount()):
            if self.variables.item(row, 2).checkState() != Qt.Checked:
                continue
            matches.append(
                MatchVariable(
                    self.variables.item(row, 0).text(),
                    self.variables.item(row, 0).data(Qt.UserRole),
                    self.variables.cellWidget(row, 4).value(),
                    self.variables.cellWidget(row, 5).value(),
                )
            )
        config = CompareConfig(
            self._selected(1),
            tuple(matches),
            self._selected(3),
            self.threshold.value(),
            self.ambiguity.value(),
        )
        config.validate()
        return config

    def _update_enabled(self) -> None:
        main = self.main_dataset.currentData()
        qc = self.qc_dataset.currentData()
        complete = (
            main is not None
            and qc is not None
            and main is not qc
            and bool(self.main_dataset.currentData(Qt.UserRole + 1))
            and bool(self.qc_dataset.currentData(Qt.UserRole + 1))
        )
        self.compare_button.setEnabled(complete and self.variables.rowCount() > 0)
        if main is qc and main is not None:
            self.status.setText("Main and QC must be different datasets.")
        elif not complete:
            self.status.setText("Select two fully loaded datasets.")
        else:
            self.status.setText(
                f"{self.variables.rowCount()} common variables. Current WHERE is ignored."
            )

    def _start_compare(self) -> None:
        try:
            config = self.config()
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.compare_requested.emit(
            self.main_dataset.currentData(), self.qc_dataset.currentData(), config
        )

    def set_busy(self, busy: bool, message: str = "") -> None:
        self.compare_button.setEnabled(not busy)
        self.main_dataset.setEnabled(not busy)
        self.qc_dataset.setEnabled(not busy)
        self.variables.setEnabled(not busy)
        if message:
            self.status.setText(message)
