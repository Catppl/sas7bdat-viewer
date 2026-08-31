from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
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

from ..domain import DatasetMetadata
from ..listing.models import ListingColumn, ListingMergeAdsl


@dataclass(frozen=True, slots=True)
class ListingBuilderSelection:
    data_filter_text: str
    columns: tuple[ListingColumn, ...]
    merge_adsl: ListingMergeAdsl
    adsl_tab: object | None


class ListingBuilder(QWidget):
    run_requested = Signal(object)
    sas_code_requested = Signal(object)
    browse_adsl_requested = Signal()
    cleared = Signal()

    headers = (
        "#",
        "Expression",
        "Output",
        "Label",
        "Format",
        "Sort",
        "Dir",
        "Report",
        "In Report",
        "Post",
        "Actions",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._metadata: DatasetMetadata | None = None
        self._filter_text = ""
        self._filter_snapshot = ""
        self._source_kind = "sas"
        self._busy = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 6, 6)
        source_box = QGroupBox("Source")
        source_form = QFormLayout(source_box)
        self.source_label = QLabel("Select a fully loaded source dataset.")
        self.source_label.setWordWrap(True)
        source_form.addRow(self.source_label)
        layout.addWidget(source_box)
        merge = QGroupBox("Optional ADSL Merge")
        form = QFormLayout(merge)
        self.merge_enabled = QCheckBox("Merge ADSL")
        self.merge_enabled.toggled.connect(self._merge_toggled)
        form.addRow(self.merge_enabled)
        self.adsl = QComboBox()
        self.adsl.currentIndexChanged.connect(lambda _index: self._refresh_rename_map())
        adsl_row = QHBoxLayout()
        adsl_row.addWidget(self.adsl, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self.browse_adsl_requested)
        adsl_row.addWidget(browse)
        form.addRow("ADSL dataset", adsl_row)
        self.by = QLineEdit("USUBJID")
        self.by.textChanged.connect(self._refresh_rename_map)
        form.addRow("BY variable", self.by)
        self.keep = QLineEdit()
        self.keep.setPlaceholderText("e.g. TRT01A SAFFL AGE")
        self.keep.textChanged.connect(self._refresh_rename_map)
        form.addRow("Keep ADSL Vars", self.keep)
        self.drop = QLineEdit()
        self.drop.setPlaceholderText("e.g. AGE, SEX")
        self.drop.textChanged.connect(self._refresh_rename_map)
        form.addRow("Drop ADSL Vars", self.drop)
        self.duplicate_policy = QComboBox()
        self.duplicate_policy.addItem("Ignore duplicates", "ignore")
        self.duplicate_policy.addItem("Rename ADSL duplicates", "rename")
        self.duplicate_policy.currentIndexChanged.connect(
            lambda _index: self._refresh_rename_map()
        )
        form.addRow("Duplicates", self.duplicate_policy)
        self.rename_map = QLineEdit()
        self.rename_map.setPlaceholderText("e.g. AGE=AGE_ADSL, SEX=SEX_ADSL")
        self._rename_map_user_edited = False
        self._rename_map_syncing = False
        self.rename_map.textChanged.connect(self._rename_map_changed)
        form.addRow("Rename map", self.rename_map)
        layout.addWidget(merge)
        filter_box = QGroupBox("Data Filter")
        filter_layout = QVBoxLayout(filter_box)
        self.data_filter = QPlainTextEdit()
        self.data_filter.setMaximumHeight(70)
        self.data_filter.setPlaceholderText('e.g. SAFFL = "Y" and TRTEMFL = "Y"')
        self.data_filter.textChanged.connect(
            lambda: setattr(
                self, "_filter_text", self.data_filter.toPlainText().strip()
            )
        )
        filter_layout.addWidget(self.data_filter)
        layout.addWidget(filter_box)
        columns_box = QGroupBox("Columns")
        columns_layout = QVBoxLayout(columns_box)
        add_row = QHBoxLayout()
        self.variable_picker = QComboBox()
        add_row.addWidget(self.variable_picker, 1)
        add_variable = QPushButton("+ Add Variable")
        add_variable.clicked.connect(self._add_variable)
        add_row.addWidget(add_variable)
        add_expression = QPushButton("+ Add Expression")
        add_expression.clicked.connect(self._add_expression)
        add_row.addWidget(add_expression)
        columns_layout.addLayout(add_row)
        self.table = QTableWidget(0, len(self.headers))
        self.table.setHorizontalHeaderLabels(self.headers)
        self.table.verticalHeader().hide()
        self.table.setMinimumHeight(190)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        for index, width in {
            0: 28,
            2: 88,
            4: 68,
            5: 72,
            6: 58,
            7: 82,
            8: 66,
            9: 48,
            10: 150,
        }.items():
            self.table.setColumnWidth(index, width)
        columns_layout.addWidget(self.table)
        layout.addWidget(columns_box, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        buttons = QHBoxLayout()
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        buttons.addWidget(clear)
        self.sas_code = QPushButton("SAS Code Generator…")
        self.sas_code.clicked.connect(self._generate)
        buttons.addWidget(self.sas_code)
        self.run = QPushButton("Run Listing")
        self.run.setDefault(True)
        self.run.clicked.connect(self._run)
        buttons.addWidget(self.run, 1)
        outer.addLayout(buttons)
        self._sync_merge()
        self.set_dataset(None, "")

    def set_dataset(
        self,
        metadata: DatasetMetadata | None,
        source_text: str,
        filter_text: str = "",
        source_kind: str = "sas",
    ):
        self._source_kind = source_kind
        if metadata is None:
            self.source_label.setText("Select a fully loaded source dataset.")
            self.run.setEnabled(False)
            self.sas_code.setEnabled(False)
            return
        if metadata is not None and metadata is not self._metadata:
            initial_source = self._metadata is None
            self._metadata = metadata
            self.variable_picker.clear()
            self.variable_picker.addItems(
                variable.name for variable in metadata.variables
            )
            if initial_source:
                self._filter_text = filter_text.strip()
                self._filter_snapshot = self._filter_text
                self.data_filter.setPlainText(self._filter_text)
            self._refresh_rename_map()
        self.source_label.setText(f"Source: {source_text}")
        available = metadata is not None and not self._busy
        self.run.setEnabled(available)
        self.sas_code.setEnabled(available and source_kind == "sas")
        self.sas_code.setToolTip(
            "SAS code generation for merged Listing sources is not available yet."
            if source_kind == "merge"
            else "Generate reusable SAS code from the current Listing configuration."
        )

    def set_adsl_sources(self, datasets):
        current = self.adsl.currentData()
        self.adsl.blockSignals(True)
        self.adsl.clear()
        for tab, label in datasets:
            self.adsl.addItem(label, tab)
        if current is not None and self.adsl.findData(current) >= 0:
            self.adsl.setCurrentIndex(self.adsl.findData(current))
        self.adsl.blockSignals(False)
        self._refresh_rename_map()

    def select_adsl(self, tab):
        index = self.adsl.findData(tab)
        if index >= 0:
            self.adsl.setCurrentIndex(index)

    def inherit_current_filter(self, text: str):
        if self._filter_text == self._filter_snapshot:
            self._filter_text = text.strip()
            self._filter_snapshot = self._filter_text
            self.data_filter.setPlainText(self._filter_text)

    @staticmethod
    def _names(text: str) -> tuple[str, ...]:
        return tuple(item for item in text.replace(",", " ").split() if item)

    def _rename_map_changed(self, _text: str = "") -> None:
        if not self._rename_map_syncing:
            self._rename_map_user_edited = True

    def _set_automatic_rename_map(self, text: str) -> None:
        self._rename_map_syncing = True
        try:
            self.rename_map.setText(text)
        finally:
            self._rename_map_syncing = False

    def _refresh_rename_map(self):
        if self.duplicate_policy.currentData() != "rename" or self._metadata is None:
            if not self._rename_map_user_edited:
                self._set_automatic_rename_map("")
            return
        tab = self.adsl.currentData()
        metadata = getattr(getattr(tab, "handle", None), "metadata", None)
        if metadata is None:
            if not self._rename_map_user_edited:
                self._set_automatic_rename_map("")
            return
        source = {variable.name.casefold() for variable in self._metadata.variables}
        by = self.by.text().strip().casefold()
        selected = {name.casefold() for name in self._names(self.keep.text())} or {
            variable.name.casefold() for variable in metadata.variables
        }
        selected -= {name.casefold() for name in self._names(self.drop.text())}
        duplicates = [
            variable.name
            for variable in metadata.variables
            if variable.name.casefold() in source
            and variable.name.casefold() != by
            and variable.name.casefold() in selected
        ]
        if not self._rename_map_user_edited:
            self._set_automatic_rename_map(
                ", ".join(f"{name}={name}_ADSL" for name in duplicates)
            )

    def _sync_merge(self):
        enabled = self.merge_enabled.isChecked() and not self._busy
        for widget in (
            self.adsl,
            self.by,
            self.keep,
            self.drop,
            self.duplicate_policy,
            self.rename_map,
        ):
            widget.setEnabled(enabled)

    def _merge_toggled(self, checked: bool):
        self._sync_merge()
        metadata = getattr(
            getattr(self.adsl.currentData(), "handle", None), "metadata", None
        )
        if checked and (metadata is None or metadata.name.casefold() != "adsl"):
            self.browse_adsl_requested.emit()

    def _add_variable(self):
        if self._metadata is None:
            return
        name = self.variable_picker.currentText()
        variable = next(
            (item for item in self._metadata.variables if item.name == name), None
        )
        if variable is None:
            return
        self._add_row(variable.name, variable.name, variable.label, variable.format)

    def _add_expression(self):
        self._add_row("", f"COL{self.table.rowCount() + 1}", "", "")

    def _add_row(self, expression: str, output: str, label: str, format_text: str):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        for column, text in (
            (1, expression),
            (2, output),
            (3, label),
            (4, format_text),
        ):
            self.table.setCellWidget(row, column, QLineEdit(text))
        sort = self._sort_editor()
        self.table.setCellWidget(row, 5, sort)
        direction = QComboBox()
        direction.addItems(("ASC", "DESC"))
        self.table.setCellWidget(row, 6, direction)
        report = QComboBox()
        report.addItems(("DISPLAY", "ORDER", "GROUP"))
        self.table.setCellWidget(row, 7, report)
        include = QCheckBox()
        include.setChecked(True)
        include.setToolTip("Include this column in PROC REPORT")
        self.table.setCellWidget(row, 8, include)
        post = QCheckBox()
        post.setToolTip("Division by zero → Missing")
        self.table.setCellWidget(row, 9, post)
        self.table.setCellWidget(row, 10, self._action_buttons(row))

    @staticmethod
    def _sort_editor(value: int = 0) -> QLineEdit:
        editor = QLineEdit(str(value) if value else "")
        editor.setValidator(QIntValidator(1, 999, editor))
        editor.setPlaceholderText("1")
        editor.setToolTip("Sort priority: enter 1, 2, 3, ...; leave blank for no sort")
        editor.setMinimumWidth(64)
        return editor

    def _action_buttons(self, row: int) -> QWidget:
        actions = QWidget()
        layout = QHBoxLayout(actions)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        for title, tooltip, width, callback in (
            ("Up", "Move this column up", 36, lambda: self._move_row(row, -1)),
            ("Down", "Move this column down", 44, lambda: self._move_row(row, 1)),
            ("Remove", "Remove this column", 58, lambda: self._delete_row(row)),
        ):
            button = QPushButton(title)
            button.setToolTip(tooltip)
            button.setStyleSheet("min-width: 0; padding: 3px 4px;")
            button.setFixedWidth(width)
            button.clicked.connect(callback)
            layout.addWidget(button)
        return actions

    @staticmethod
    def _sort_value(editor: QLineEdit) -> int:
        text = editor.text().strip()
        return int(text) if text else 0

    def _delete_row(self, row):
        values = [
            self._column_values(index)
            for index in range(self.table.rowCount())
            if index != row
        ]
        self._rebuild_rows(values)

    def _move_row(self, row, delta):
        target = row + delta
        if not 0 <= target < self.table.rowCount():
            return
        values = [self._column_values(index) for index in range(self.table.rowCount())]
        values[row], values[target] = values[target], values[row]
        self._rebuild_rows(values)

    def _rebuild_rows(self, values) -> None:
        self.table.setRowCount(0)
        for row_values in values:
            self._insert_values(self.table.rowCount(), row_values)

    def _column_values(self, row):
        return (
            self.table.cellWidget(row, 1).text(),
            self.table.cellWidget(row, 2).text(),
            self.table.cellWidget(row, 3).text(),
            self.table.cellWidget(row, 4).text(),
            self._sort_value(self.table.cellWidget(row, 5)),
            self.table.cellWidget(row, 6).currentText(),
            self.table.cellWidget(row, 7).currentText(),
            self.table.cellWidget(row, 8).isChecked(),
            self.table.cellWidget(row, 9).isChecked(),
        )

    def _insert_values(self, row, values):
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
        for column, text in (
            (1, values[0]),
            (2, values[1]),
            (3, values[2]),
            (4, values[3]),
        ):
            self.table.setCellWidget(row, column, QLineEdit(text))
        sort = self._sort_editor(values[4])
        self.table.setCellWidget(row, 5, sort)
        direction = QComboBox()
        direction.addItems(("ASC", "DESC"))
        direction.setCurrentText(values[5])
        self.table.setCellWidget(row, 6, direction)
        report = QComboBox()
        report.addItems(("DISPLAY", "ORDER", "GROUP"))
        report.setCurrentText(values[6])
        self.table.setCellWidget(row, 7, report)
        include = QCheckBox()
        include.setChecked(values[7])
        self.table.setCellWidget(row, 8, include)
        post = QCheckBox()
        post.setChecked(values[8])
        self.table.setCellWidget(row, 9, post)
        self.table.setCellWidget(row, 10, self._action_buttons(row))

    def _renumber(self):
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setText(str(row + 1))

    def _selection(self):
        if self._metadata is None:
            return None
        columns = []
        for row in range(self.table.rowCount()):
            columns.append(
                ListingColumn(
                    self.table.cellWidget(row, 1).text().strip(),
                    self.table.cellWidget(row, 2).text().strip(),
                    self.table.cellWidget(row, 3).text().strip(),
                    self.table.cellWidget(row, 4).text().strip(),
                    self._sort_value(self.table.cellWidget(row, 5)) or None,
                    self.table.cellWidget(row, 6).currentText(),
                    self.table.cellWidget(row, 7).currentText(),
                    self.table.cellWidget(row, 8).isChecked(),
                    self.table.cellWidget(row, 9).isChecked(),
                )
            )
        rename = []
        for item in self.rename_map.text().split(","):
            if "=" in item:
                old, new = (part.strip() for part in item.split("=", 1))
                rename.append((old, new))
        merge = ListingMergeAdsl(
            self.merge_enabled.isChecked(),
            self.by.text().strip() or "USUBJID",
            self._names(self.keep.text()),
            self._names(self.drop.text()),
            self.duplicate_policy.currentData(),
            tuple(rename),
        )
        return ListingBuilderSelection(
            self._filter_text,
            tuple(columns),
            merge,
            self.adsl.currentData() if merge.enabled else None,
        )

    def set_busy(self, busy: bool, message: str = ""):
        self._busy = busy
        available = self._metadata is not None and not busy
        self.run.setEnabled(available)
        self.sas_code.setEnabled(available and self._source_kind == "sas")
        self._sync_merge()
        self.status.setText(message)

    def clear(self):
        self.data_filter.clear()
        self.table.setRowCount(0)
        self.merge_enabled.setChecked(False)
        self.keep.clear()
        self.drop.clear()
        self._rename_map_user_edited = False
        self._set_automatic_rename_map("")
        self.status.clear()
        self._metadata = None
        self.variable_picker.clear()
        self.cleared.emit()

    def _run(self):
        if (selection := self._selection()) is not None:
            self.run_requested.emit(selection)

    def _generate(self):
        if (
            (selection := self._selection()) is not None
            and self._source_kind == "sas"
            and not self._busy
        ):
            self.sas_code_requested.emit(selection)
