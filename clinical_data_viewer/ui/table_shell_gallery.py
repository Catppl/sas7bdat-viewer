from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..resources import resource_path


@dataclass(frozen=True, slots=True)
class TableShell:
    key: str
    title: str
    description: str
    category: str
    preview_path: str | None = None


TABLE_SHELLS = (
    TableShell(
        "proc_means_vertical",
        "Descriptive Statistics",
        "Continuous variable summary by treatment and group",
        "Summary",
        "clinical_data_viewer/assets/shells/proc_means_vertical.png",
    ),
    TableShell(
        "change_from_baseline",
        "Change from Baseline",
        "Change scores across visits and treatment groups",
        "Summary",
        "clinical_data_viewer/assets/shells/change_from_baseline_horizontal.png",
    ),
    TableShell(
        "categorical",
        "Categorical Summary",
        "Frequency and percentage by category",
        "Summary",
    ),
    TableShell(
        "rule_based",
        "Rule-based Table",
        "Configurable clinical summary from row-level rules",
        "Summary",
    ),
    TableShell(
        "ae_table",
        "Adverse Events",
        "AE summary by SOC and preferred term",
        "Safety",
    ),
)


def _placeholder_pixmap(width: int = 760, height: int = 420) -> QPixmap:
    pixmap = QPixmap(width, height)
    pixmap.fill(QColor("#f5f7fa"))
    painter = QPainter(pixmap)
    painter.setPen(QColor("#d8dee6"))
    left, top, right, bottom = 38, 44, width - 38, height - 44
    painter.drawRect(left, top, right - left, bottom - top)
    painter.setPen(QColor("#c4ccd5"))
    painter.drawLine(left, top + 42, right, top + 42)
    painter.drawLine(left, bottom - 42, right, bottom - 42)
    for row in range(4):
        y = top + 78 + row * 48
        painter.drawLine(left + 10, y, right - 10, y)
    for column in range(1, 4):
        x = left + column * (right - left) // 4
        painter.drawLine(x, top + 42, x, bottom - 42)
    painter.end()
    return pixmap


def _shell_pixmap(shell: TableShell) -> QPixmap:
    if shell.preview_path:
        path = resource_path(shell.preview_path)
        if path.exists():
            return QPixmap(str(path))
    return _placeholder_pixmap()


class _ShellCard(QFrame):
    preview_requested = Signal(object)

    def __init__(self, shell: TableShell, parent=None) -> None:
        super().__init__(parent)
        self.shell = shell
        self.setObjectName("tableShellCard")
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(8)

        self.preview = QLabel()
        self.preview.setObjectName("tableShellThumbnail")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(150)
        self.preview.setStyleSheet(
            "QLabel#tableShellThumbnail { background: #f5f7fa; border-radius: 5px; }"
        )
        self._set_thumbnail()
        layout.addWidget(self.preview)

        title_row = QHBoxLayout()
        title = QLabel(shell.title)
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        arrow = QLabel("↗")
        arrow.setStyleSheet("color: #667085; font-size: 16px;")
        title_row.addWidget(arrow)
        layout.addLayout(title_row)

        description = QLabel(shell.description)
        description.setWordWrap(True)
        description.setStyleSheet("color: #667085;")
        layout.addWidget(description)

        category = QLabel(shell.category)
        category.setStyleSheet("color: #667085; font-size: 11px;")
        layout.addWidget(category)

    def _set_thumbnail(self) -> None:
        pixmap = _shell_pixmap(self.shell)
        self.preview.setPixmap(
            pixmap.scaled(560, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.preview_requested.emit(self.shell)
        super().mousePressEvent(event)


class _ShellPreviewDialog(QDialog):
    builder_requested = Signal(str)

    def __init__(self, shell: TableShell, parent=None) -> None:
        super().__init__(parent)
        self.shell = shell
        self.setWindowTitle(f"Preview: {shell.title}")
        self.resize(980, 720)
        layout = QVBoxLayout(self)
        title = QLabel(shell.title)
        title.setStyleSheet("font-size: 18px; font-weight: 700;")
        layout.addWidget(title)
        description = QLabel(shell.description)
        description.setStyleSheet("color: #667085;")
        layout.addWidget(description)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        image = QLabel()
        image.setAlignment(Qt.AlignCenter)
        image.setPixmap(_shell_pixmap(shell))
        scroll.setWidget(image)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        open_button = buttons.addButton("Open Builder", QDialogButtonBox.AcceptRole)
        open_button.clicked.connect(self._open_builder)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _open_builder(self) -> None:
        self.builder_requested.emit(self.shell.key)
        self.accept()


class TableShellGalleryDialog(QDialog):
    """Large shell browser; cards preview first, then open an existing Builder."""

    builder_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Table Shell Gallery")
        self.resize(1080, 780)
        self._category = "All"
        self._cards: list[_ShellCard] = []
        self._retired_cards: list[_ShellCard] = []
        self._preview_dialog: _ShellPreviewDialog | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 16)
        outer.setSpacing(9)
        title = QLabel("Table Shell Gallery")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        outer.addWidget(title)
        subtitle = QLabel("Choose a table layout to preview and open its builder")
        subtitle.setStyleSheet("color: #667085;")
        outer.addWidget(subtitle)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search shell by name or keyword…")
        self.search.textChanged.connect(self._refresh_cards)
        outer.addWidget(self.search)

        categories = QHBoxLayout()
        categories.setSpacing(6)
        self.category_buttons: dict[str, QPushButton] = {}
        for category in ("All", "Summary", "Safety"):
            button = QPushButton(category)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked, value=category: self._select_category(value))
            categories.addWidget(button)
            self.category_buttons[category] = button
        categories.addStretch(1)
        outer.addLayout(categories)
        self.category_buttons["All"].setChecked(True)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        self.grid = QGridLayout(content)
        self.grid.setContentsMargins(0, 2, 0, 2)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(10)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 1)
        self._refresh_cards()

    def _select_category(self, category: str) -> None:
        self._category = category
        for name, button in self.category_buttons.items():
            button.setChecked(name == category)
        self._refresh_cards()

    def _refresh_cards(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Keep retired cards parented until the dialog is destroyed.
                # This avoids deleting Qt child widgets while a filter change
                # is still being delivered to the gallery.
                widget.hide()
                self._retired_cards.append(widget)
        self._cards.clear()
        query = self.search.text().strip().casefold()
        visible = []
        for shell in TABLE_SHELLS:
            text = " ".join((shell.title, shell.description, shell.category)).casefold()
            if self._category != "All" and shell.category != self._category:
                continue
            if query and query not in text:
                continue
            visible.append(shell)
        for index, shell in enumerate(visible):
            card = _ShellCard(shell)
            card.preview_requested.connect(self._show_preview)
            self._cards.append(card)
            self.grid.addWidget(card, index // 2, index % 2)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 1)

    def _show_preview(self, shell: TableShell) -> None:
        preview = _ShellPreviewDialog(shell, self)
        preview.builder_requested.connect(self.builder_requested)
        preview.finished.connect(lambda _result, dialog=preview: self._preview_finished(dialog))
        self._preview_dialog = preview
        preview.setModal(True)
        preview.open()

    def _preview_finished(self, dialog: _ShellPreviewDialog) -> None:
        if self._preview_dialog is dialog:
            self._preview_dialog = None

    def visible_shell_keys(self) -> tuple[str, ...]:
        return tuple(card.shell.key for card in self._cards)


__all__ = ["TABLE_SHELLS", "TableShell", "TableShellGalleryDialog"]
