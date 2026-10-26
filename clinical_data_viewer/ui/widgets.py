from __future__ import annotations

from PySide6.QtWidgets import QComboBox


class ClickOnlyComboBox(QComboBox):
    """A combo box whose current value changes only through an explicit click."""

    def wheelEvent(self, event) -> None:
        event.ignore()


__all__ = ["ClickOnlyComboBox"]
