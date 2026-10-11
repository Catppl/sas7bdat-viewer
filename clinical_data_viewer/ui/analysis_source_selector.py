from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QLabel, QVBoxLayout, QWidget


class AnalysisSourceSelector(QWidget):
    """Select one already-open analysis dataset without following the active tab."""

    source_selected = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._selection_changed)
        layout.addWidget(self.combo)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color: #536b80;")
        layout.addWidget(self.hint)
        self.set_sources((), None)

    def set_sources(self, datasets, selected=None) -> None:
        self.combo.blockSignals(True)
        try:
            self.combo.clear()
            self.combo.addItem("Select source dataset…", None)
            for tab, label in datasets:
                self.combo.addItem(label, tab)
            index = self.combo.findData(selected) if selected is not None else -1
            self.combo.setCurrentIndex(max(index, 0))
        finally:
            self.combo.blockSignals(False)
        if datasets:
            self.hint.setText(
                "Select an opened dataset. If the dataset you need is not listed, "
                "open it first."
            )
        else:
            self.hint.setText(
                "No datasets are open. Open a dataset before using this Builder."
            )

    def set_selected(self, tab) -> None:
        self.combo.blockSignals(True)
        try:
            index = self.combo.findData(tab) if tab is not None else 0
            self.combo.setCurrentIndex(max(index, 0))
        finally:
            self.combo.blockSignals(False)

    def selected_source(self):
        return self.combo.currentData()

    def _selection_changed(self, _index: int) -> None:
        self.source_selected.emit(self.combo.currentData())
