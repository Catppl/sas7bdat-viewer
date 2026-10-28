from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
)

from ..shell_assets import load_change_from_baseline_shell_preview


class ShellPreviewDialog(QDialog):
    """Display a packaged shell preview without exposing the DOCX viewer."""

    def __init__(self, parent=None, image_bytes: bytes | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Change from Baseline Shell Preview")
        self.resize(900, 700)
        self._fit_to_window = True
        self._pixmap = QPixmap()

        try:
            payload = (
                image_bytes
                if image_bytes is not None
                else load_change_from_baseline_shell_preview()
            )
            self._pixmap.loadFromData(payload)
        except (OSError, ValueError):
            pass

        self._image = QLabel()
        self._image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image.setText(
            "Shell preview is unavailable." if self._pixmap.isNull() else ""
        )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._image)
        self._scroll = scroll

        fit = QPushButton("Fit to Window")
        fit.clicked.connect(self._fit)
        actual = QPushButton("Actual Size")
        actual.clicked.connect(self._actual_size)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        button_layout = buttons.layout()
        if button_layout is not None:
            button_layout.insertWidget(0, fit)
            button_layout.insertWidget(1, actual)

        layout = QVBoxLayout(self)
        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)
        self._render()

    def _fit(self) -> None:
        self._fit_to_window = True
        self._render()

    def _actual_size(self) -> None:
        self._fit_to_window = False
        self._render()

    def _render(self) -> None:
        if self._pixmap.isNull():
            return
        if not self._fit_to_window:
            self._image.setPixmap(self._pixmap)
            self._image.adjustSize()
            return
        viewport = self._scroll.viewport().size()
        if viewport.width() <= 0 or viewport.height() <= 0:
            self._image.setPixmap(self._pixmap)
            return
        scaled = self._pixmap.scaled(
            viewport,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self._image.setPixmap(scaled)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render()


__all__ = ["ShellPreviewDialog"]
