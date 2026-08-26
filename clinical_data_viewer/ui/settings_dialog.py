from __future__ import annotations

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPainter, QPolygon
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QStyle,
    QStyleOptionSpinBox,
    QVBoxLayout,
)

from ..settings import PROC_MEANS_COUNT_STATISTICS, PROC_MEANS_STATISTICS, AppSettings


class DecimalSpinBox(QSpinBox):
    """A spin box whose increment/decrement arrows remain visible with QSS."""

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        painter = QPainter(self)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#35698f"))
        for subcontrol, points in (
            (QStyle.SC_SpinBoxUp, ((0, 3), (-4, -3), (4, -3))),
            (QStyle.SC_SpinBoxDown, ((0, -3), (-4, 3), (4, 3))),
        ):
            rect = self.style().subControlRect(QStyle.CC_SpinBox, option, subcontrol)
            center = rect.center()
            painter.drawPolygon(
                QPolygon(
                    [QPoint(center.x() + x, center.y() + y) for x, y in points]
                )
            )
        painter.end()


class SettingsDialog(QDialog):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("settingsDialog")
        self.settings = settings
        self.setWindowTitle("Settings")
        self.resize(470, 610)
        layout = QVBoxLayout(self)
        title = QLabel("PROC MEANS")
        title.setObjectName("panelTitle")
        layout.addWidget(title)
        form = QFormLayout()
        self.confidence = QDoubleSpinBox()
        self.confidence.setRange(50.0, 99.9)
        self.confidence.setDecimals(1)
        self.confidence.setSuffix("%")
        self.confidence.setValue(settings.proc_means_confidence * 100)
        form.addRow("Mean confidence level:", self.confidence)
        layout.addLayout(form)
        box = QGroupBox("Displayed statistics and additional decimal places")
        box_layout = QGridLayout(box)
        box_layout.addWidget(QLabel("Show"), 0, 0)
        box_layout.addWidget(QLabel("Statistic"), 0, 1)
        box_layout.addWidget(QLabel("Add decimals"), 0, 2)
        selected = set(settings.proc_means_statistics)
        self.statistics: dict[str, QCheckBox] = {}
        self.statistic_decimals: dict[str, QSpinBox] = {}
        for row, (key, label) in enumerate(PROC_MEANS_STATISTICS, start=1):
            check = QCheckBox()
            check.setChecked(key in selected)
            self.statistics[key] = check
            box_layout.addWidget(check, row, 0)
            box_layout.addWidget(QLabel(label), row, 1)
            if key in PROC_MEANS_COUNT_STATISTICS:
                integer = QLabel("Integer")
                integer.setEnabled(False)
                box_layout.addWidget(integer, row, 2)
            else:
                decimals = DecimalSpinBox()
                decimals.setRange(0, 4)
                decimals.setPrefix("+")
                decimals.setButtonSymbols(QAbstractSpinBox.UpDownArrows)
                decimals.setMinimumWidth(72)
                decimals.setValue(settings.proc_means_decimal_offsets.get(key, 0))
                self.statistic_decimals[key] = decimals
                box_layout.addWidget(decimals, row, 2)
        box_layout.setColumnStretch(1, 1)
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
        self.confidence.setValue(defaults.proc_means_confidence * 100)
        selected = set(defaults.proc_means_statistics)
        for key, check in self.statistics.items():
            check.setChecked(key in selected)
        for key, decimals in self.statistic_decimals.items():
            decimals.setValue(defaults.proc_means_decimal_offsets[key])

    def _save(self) -> None:
        selected = [
            key
            for key, _label in PROC_MEANS_STATISTICS
            if self.statistics[key].isChecked()
        ]
        if not selected:
            self.statistics["mean"].setChecked(True)
            return
        self.settings.proc_means_decimal_offsets = {
            key: decimals.value() for key, decimals in self.statistic_decimals.items()
        }
        self.settings.proc_means_confidence = self.confidence.value() / 100
        self.settings.proc_means_statistics = selected
        self.settings.save()
        self.accept()
