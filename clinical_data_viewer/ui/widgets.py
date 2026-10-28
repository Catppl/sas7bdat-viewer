from __future__ import annotations

from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QComboBox, QSpinBox


class ClickOnlyComboBox(QComboBox):
    """A combo box whose current value changes only through an explicit click."""

    def wheelEvent(self, event) -> None:
        event.ignore()


class MouseOnlyComboBox(ClickOnlyComboBox):
    """A selector whose value can only be changed with a mouse click."""

    def keyPressEvent(self, event: QKeyEvent) -> None:
        event.ignore()


class MouseOnlySpinBox(QSpinBox):
    """A spin box whose value can only be changed with an explicit click."""

    def wheelEvent(self, event) -> None:
        event.ignore()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        event.ignore()


__all__ = ["ClickOnlyComboBox", "MouseOnlyComboBox", "MouseOnlySpinBox"]
