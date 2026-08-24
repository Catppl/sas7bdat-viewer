from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from ..settings import PROC_MEANS_STATISTICS, AppSettings


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.resize(430, 560)
        layout = QVBoxLayout(self)
        title = QLabel("PROC MEANS")
        title.setObjectName("panelTitle")
        layout.addWidget(title)
        form = QFormLayout()
        self.decimals = QSpinBox()
        self.decimals.setRange(0, 10)
        self.decimals.setValue(settings.proc_means_decimals)
        form.addRow("Decimal places:", self.decimals)
        self.confidence = QDoubleSpinBox()
        self.confidence.setRange(50.0, 99.9)
        self.confidence.setDecimals(1)
        self.confidence.setSuffix("%")
        self.confidence.setValue(settings.proc_means_confidence * 100)
        form.addRow("Mean confidence level:", self.confidence)
        layout.addLayout(form)
        box = QGroupBox("Displayed statistics")
        box_layout = QVBoxLayout(box)
        selected = set(settings.proc_means_statistics)
        self.statistics: dict[str, QCheckBox] = {}
        for key, label in PROC_MEANS_STATISTICS:
            check = QCheckBox(label)
            check.setChecked(key in selected)
            self.statistics[key] = check
            box_layout.addWidget(check)
        layout.addWidget(box)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save
            | QDialogButtonBox.Cancel
            | QDialogButtonBox.RestoreDefaults
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.RestoreDefaults).clicked.connect(
            self._restore_defaults
        )
        layout.addWidget(buttons)

    def _restore_defaults(self) -> None:
        defaults = AppSettings()
        self.decimals.setValue(defaults.proc_means_decimals)
        self.confidence.setValue(defaults.proc_means_confidence * 100)
        selected = set(defaults.proc_means_statistics)
        for key, check in self.statistics.items():
            check.setChecked(key in selected)

    def _save(self) -> None:
        selected = [
            key
            for key, _label in PROC_MEANS_STATISTICS
            if self.statistics[key].isChecked()
        ]
        if not selected:
            self.statistics["mean"].setChecked(True)
            return
        self.settings.proc_means_decimals = self.decimals.value()
        self.settings.proc_means_confidence = self.confidence.value() / 100
        self.settings.proc_means_statistics = selected
        self.settings.save()
        self.accept()
