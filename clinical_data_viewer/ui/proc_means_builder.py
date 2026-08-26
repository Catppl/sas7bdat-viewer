from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QCompleter,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..domain import DatasetMetadata
from ..settings import PROC_MEANS_STATISTICS


@dataclass(frozen=True, slots=True)
class ProcMeansBuilderSelection:
    analysis_variables: tuple[str, ...]
    by_variables: tuple[str, ...]
    class_variables: tuple[str, ...]
    statistics: tuple[str, ...]
    decimal_group_variables: tuple[str, ...]


class VariableTokenEditor(QWidget):
    changed = Signal()
    validation_error = Signal(str)

    def __init__(self, placeholder: str, *, numeric_only: bool = False, parent=None):
        super().__init__(parent)
        self.numeric_only = numeric_only
        self._metadata: DatasetMetadata | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        row = QHBoxLayout()
        self.editor = QLineEdit()
        self.editor.setPlaceholderText(placeholder)
        self.editor.returnPressed.connect(self._add_from_editor)
        row.addWidget(self.editor, 1)
        remove = QPushButton("Remove")
        remove.setMinimumWidth(64)
        remove.clicked.connect(self._remove_selected)
        row.addWidget(remove)
        layout.addLayout(row)
        self.values = QListWidget()
        self.values.setMaximumHeight(62)
        self.values.itemDoubleClicked.connect(lambda _item: self._remove_selected())
        layout.addWidget(self.values)

    def set_metadata(
        self, metadata: DatasetMetadata | None, *, preserve: bool = False
    ) -> None:
        selected = self.selected_variables() if preserve else ()
        self._metadata = metadata
        names = (
            [
                variable.name
                for variable in metadata.variables
                if not self.numeric_only or variable.kind == "numeric"
            ]
            if metadata
            else []
        )
        completer = QCompleter(names, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setFilterMode(Qt.MatchContains)
        self.editor.setCompleter(completer)
        self.clear()
        if preserve:
            self.set_variables(selected)
        self.setEnabled(metadata is not None)

    def selected_variables(self) -> tuple[str, ...]:
        return tuple(
            self.values.item(index).text() for index in range(self.values.count())
        )

    def set_variables(self, variables: tuple[str, ...] | list[str]) -> None:
        self.values.clear()
        for variable in variables:
            self.values.addItem(variable)
        self.changed.emit()

    def clear(self) -> None:
        self.editor.clear()
        self.values.clear()
        self.changed.emit()

    def _add_from_editor(self) -> None:
        text = self.editor.text().strip()
        if not text or self._metadata is None:
            return
        by_fold = {
            variable.name.casefold(): variable for variable in self._metadata.variables
        }
        for requested in (part.strip() for part in text.split(",")):
            if not requested:
                continue
            variable = by_fold.get(requested.casefold())
            if variable is None:
                self.validation_error.emit(
                    f'Variable "{requested}" does not exist in the current dataset.'
                )
                return
            if self.numeric_only and variable.kind != "numeric":
                self.validation_error.emit(
                    f'Analysis Variable "{variable.name}" must be numeric.'
                )
                return
            selected = {name.casefold() for name in self.selected_variables()}
            if variable.name.casefold() not in selected:
                self.values.addItem(variable.name)
        self.editor.clear()
        self.changed.emit()

    def _remove_selected(self) -> None:
        rows = sorted(
            {self.values.row(item) for item in self.values.selectedItems()},
            reverse=True,
        )
        for row in rows:
            self.values.takeItem(row)
        if rows:
            self.changed.emit()


class ProcMeansBuilder(QWidget):
    run_requested = Signal(object)
    sas_code_requested = Signal(object)
    r_code_requested = Signal(object)
    validation_error = Signal(str)
    settings_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("procMeansBuilder")
        self._metadata: DatasetMetadata | None = None
        self._filter_text = ""
        self._busy = False
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        content.setObjectName("procMeansBuilderContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll, 1)
        self.source_label = QLabel("Select a fully loaded source dataset.")
        self.source_label.setWordWrap(True)
        layout.addWidget(self.source_label)

        layout.addWidget(QLabel("Analysis Variables"))
        self.analysis_variables = VariableTokenEditor(
            "Type a numeric variable and press Enter", numeric_only=True
        )
        layout.addWidget(self.analysis_variables)
        layout.addWidget(QLabel("BY Variables"))
        self.by_variables = VariableTokenEditor("Type a variable and press Enter")
        layout.addWidget(self.by_variables)
        layout.addWidget(QLabel("CLASS Variables"))
        self.class_variables = VariableTokenEditor("Type a variable and press Enter")
        layout.addWidget(self.class_variables)
        for editor in (
            self.analysis_variables,
            self.by_variables,
            self.class_variables,
        ):
            editor.validation_error.connect(self.validation_error)
        self.by_variables.changed.connect(self._refresh_decimal_groups)
        self.class_variables.changed.connect(self._refresh_decimal_groups)

        layout.addWidget(QLabel("Decimal Group Variables"))
        self.decimal_groups = QListWidget()
        self.decimal_groups.setMaximumHeight(72)
        self.decimal_groups.setToolTip(
            "Check zero or more variables already selected as BY or CLASS Variables."
        )
        layout.addWidget(self.decimal_groups)

        statistics_box = QGroupBox("Statistics")
        statistics_layout = QGridLayout(statistics_box)
        self.statistics: dict[str, QCheckBox] = {}
        for index, (key, label) in enumerate(PROC_MEANS_STATISTICS):
            checkbox = QCheckBox(label)
            self.statistics[key] = checkbox
            statistics_layout.addWidget(checkbox, index // 2, index % 2)
        layout.addWidget(statistics_box)

        filter_title = QLabel("Filter")
        filter_title.setObjectName("panelTitle")
        layout.addWidget(filter_title)
        self.filter_label = QLabel("All rows")
        self.filter_label.setWordWrap(True)
        self.filter_label.setObjectName("filterNotice")
        layout.addWidget(self.filter_label)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QGridLayout()
        buttons.setContentsMargins(6, 0, 6, 6)
        settings = QPushButton("Settings…")
        settings.clicked.connect(self.settings_requested)
        buttons.addWidget(settings, 0, 0)
        clear = QPushButton("Clear")
        clear.clicked.connect(self.clear)
        buttons.addWidget(clear, 0, 1)
        self.sas_code_button = QPushButton("SAS Code Generator…")
        self.sas_code_button.clicked.connect(self._generate_sas_code)
        buttons.addWidget(self.sas_code_button, 1, 0, 1, 1)
        self.r_code_button = QPushButton("R Code Generator…")
        self.r_code_button.clicked.connect(self._generate_r_code)
        buttons.addWidget(self.r_code_button, 1, 1, 1, 1)
        self.run_button = QPushButton("Run")
        self.run_button.setDefault(True)
        self.run_button.clicked.connect(self._run)
        buttons.addWidget(self.run_button, 0, 2, 2, 1)
        buttons.setColumnStretch(2, 1)
        outer_layout.addLayout(buttons)
        self.set_dataset(None, "")

    def set_dataset(
        self, metadata: DatasetMetadata | None, source_text: str, filter_text: str = ""
    ) -> None:
        if metadata is not None and metadata is not self._metadata:
            self._metadata = metadata
            self._filter_text = self._normalized_filter_text(filter_text)
            for editor in (
                self.analysis_variables,
                self.by_variables,
                self.class_variables,
            ):
                editor.set_metadata(metadata)
        self.source_label.setText(
            f"Source: {source_text}"
            if metadata
            else "Select a fully loaded source dataset."
        )
        self.filter_label.setText(
            f"WHERE {self._filter_text}" if self._filter_text else "All rows"
        )
        available = metadata is not None and not self._busy
        self.run_button.setEnabled(available)
        self.sas_code_button.setEnabled(available)
        self.r_code_button.setEnabled(available)
        for editor in (
            self.analysis_variables,
            self.by_variables,
            self.class_variables,
        ):
            editor.setEnabled(available)
        self.decimal_groups.setEnabled(available)

    def current_filter_text(self) -> str:
        """Return the Builder's saved source-filter snapshot."""
        return self._filter_text

    def apply_current_filter(self, filter_text: str) -> None:
        """Replace the saved snapshot after the user explicitly confirms it."""
        self._filter_text = self._normalized_filter_text(filter_text)
        self.filter_label.setText(
            f"WHERE {self._filter_text}" if self._filter_text else "All rows"
        )

    @staticmethod
    def _normalized_filter_text(filter_text: str) -> str:
        text = filter_text.strip()
        return "" if text.casefold() == "all rows" else text

    def set_default_statistics(self, statistics: list[str]) -> None:
        selected = set(statistics)
        for key, checkbox in self.statistics.items():
            checkbox.setChecked(key in selected)

    def prefill_analysis_variable(self, variable: str) -> None:
        if self._metadata is not None:
            self.analysis_variables.set_variables((variable,))

    def clear(self) -> None:
        self.analysis_variables.clear()
        self.by_variables.clear()
        self.class_variables.clear()
        self.status.clear()

    def set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.run_button.setEnabled(not busy and self._metadata is not None)
        self.sas_code_button.setEnabled(not busy and self._metadata is not None)
        self.r_code_button.setEnabled(not busy and self._metadata is not None)
        for editor in (
            self.analysis_variables,
            self.by_variables,
            self.class_variables,
        ):
            editor.setEnabled(not busy)
        self.decimal_groups.setEnabled(not busy)
        if message:
            self.status.setText(message)

    def _refresh_decimal_groups(self) -> None:
        previous = set(self.selected_decimal_groups())
        values = (
            *self.by_variables.selected_variables(),
            *self.class_variables.selected_variables(),
        )
        self.decimal_groups.clear()
        for variable in values:
            item = QListWidgetItem(variable)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Checked if variable in previous else Qt.Unchecked)
            self.decimal_groups.addItem(item)

    def selected_decimal_groups(self) -> tuple[str, ...]:
        return tuple(
            self.decimal_groups.item(index).text()
            for index in range(self.decimal_groups.count())
            if self.decimal_groups.item(index).checkState() == Qt.Checked
        )

    def _selection(self) -> ProcMeansBuilderSelection | None:
        selection = ProcMeansBuilderSelection(
            self.analysis_variables.selected_variables(),
            self.by_variables.selected_variables(),
            self.class_variables.selected_variables(),
            tuple(
                key for key, checkbox in self.statistics.items() if checkbox.isChecked()
            ),
            self.selected_decimal_groups(),
        )
        if not selection.analysis_variables:
            self.validation_error.emit("Select at least one Analysis Variable.")
            return None
        if not selection.statistics:
            self.validation_error.emit("Select at least one statistic.")
            return None
        return selection

    def _run(self) -> None:
        selection = self._selection()
        if selection is None:
            return
        self.run_requested.emit(selection)

    def _generate_sas_code(self) -> None:
        selection = self._selection()
        if selection is None:
            return
        self.sas_code_requested.emit(selection)

    def _generate_r_code(self) -> None:
        selection = self._selection()
        if selection is None:
            return
        self.r_code_requested.emit(selection)
