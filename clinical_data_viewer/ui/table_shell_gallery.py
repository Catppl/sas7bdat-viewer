from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
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
    tags: tuple[str, ...] = ()
    preview_paths: tuple[str, ...] = ()

    @property
    def all_tags(self) -> tuple[str, ...]:
        return (self.category, *self.tags)

    @property
    def all_preview_paths(self) -> tuple[str, ...]:
        if self.preview_paths:
            return self.preview_paths
        return (self.preview_path,) if self.preview_path else ()


TABLE_SHELLS = (
    TableShell(
        "proc_means_vertical",
        "Descriptive Statistics",
        "Continuous variable summary by treatment and group",
        "Summary",
        "clinical_data_viewer/assets/shells/proc_means_vertical.png",
        ("Continuous",),
        (
            "clinical_data_viewer/assets/shells/proc_means_vertical.png",
            "clinical_data_viewer/assets/shells/proc_means_rows.png",
        ),
    ),
    TableShell(
        "change_from_baseline",
        "Change from Baseline",
        "Change scores across visits and treatment groups",
        "Summary",
        "clinical_data_viewer/assets/shells/change_from_baseline_horizontal.png",
        ("Continuous",),
    ),
    TableShell(
        "categorical",
        "Categorical Summary",
        "Frequency and percentage by category",
        "Summary",
        tags=("Categorical",),
    ),
    TableShell(
        "rule_based",
        "Rule-based Table",
        "Configurable clinical summary from row-level rules",
        "Summary",
        tags=("Categorical",),
    ),
    TableShell(
        "ae_table",
        "Adverse Events",
        "AE summary by SOC and preferred term",
        "Safety",
        tags=("Categorical",),
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
            return _trim_whitespace(QPixmap(str(path)))
    return _placeholder_pixmap()


def _shell_pixmaps(shell: TableShell) -> tuple[QPixmap, ...]:
    pixmaps = []
    for preview_path in shell.all_preview_paths:
        path = resource_path(preview_path)
        if path.exists():
            pixmap = _trim_whitespace(QPixmap(str(path)))
            if not pixmap.isNull():
                pixmaps.append(pixmap)
    return tuple(pixmaps) or (_placeholder_pixmap(),)


def _trim_whitespace(pixmap: QPixmap, margin: int = 16) -> QPixmap:
    """Remove blank white margins without resampling the shell image."""
    if pixmap.isNull():
        return pixmap
    image = pixmap.toImage().convertToFormat(QImage.Format_RGB32)
    mask = image.createMaskFromColor(QColor("white").rgba(), Qt.MaskOutColor)
    min_x, min_y = image.width(), image.height()
    max_x = max_y = -1
    for y in range(image.height()):
        for x in range(image.width()):
            if mask.pixel(x, y) & 0x00FFFFFF:
                min_x = min(min_x, x)
                max_x = max(max_x, x)
                min_y = min(min_y, y)
                max_y = max(max_y, y)
    if max_x < min_x or max_y < min_y:
        return pixmap
    content = image.rect().intersected(
        image.rect().adjusted(
            min_x - margin,
            min_y - margin,
            -(image.width() - max_x - 1 - margin),
            -(image.height() - max_y - 1 - margin),
        )
    )
    return pixmap.copy(content)


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

        self._source_pixmaps = _shell_pixmaps(shell)
        self._page_index = 0
        self._source_pixmap = self._source_pixmaps[0]
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(False)
        self._scroll.setAlignment(Qt.AlignCenter)
        self._image = QLabel()
        self._image.setAlignment(Qt.AlignCenter)
        self._scroll.setWidget(self._image)
        layout.addWidget(self._scroll, 1)

        navigation = QHBoxLayout()
        self._previous_button = QPushButton("Previous")
        self._previous_button.clicked.connect(lambda: self._set_page(self._page_index - 1))
        navigation.addWidget(self._previous_button)
        navigation.addStretch(1)
        self._page_label = QLabel()
        self._page_label.setAlignment(Qt.AlignCenter)
        navigation.addWidget(self._page_label)
        navigation.addStretch(1)
        self._next_button = QPushButton("Next")
        self._next_button.clicked.connect(lambda: self._set_page(self._page_index + 1))
        navigation.addWidget(self._next_button)
        layout.addLayout(navigation)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        open_button = buttons.addButton("Open Builder", QDialogButtonBox.AcceptRole)
        open_button.clicked.connect(self._open_builder)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_page_controls()
        self._fit_image_to_viewport()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit_image_to_viewport()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_image_to_viewport()

    def _fit_image_to_viewport(self) -> None:
        """Fit only the display copy; keep the original Pixmap untouched."""
        if self._source_pixmap.isNull():
            self._image.clear()
            return
        viewport = self._scroll.viewport().size()
        if viewport.width() <= 0 or viewport.height() <= 0:
            return
        display = self._source_pixmap
        if display.width() > viewport.width() or display.height() > viewport.height():
            display = display.scaled(
                viewport,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation,
            )
        self._image.setPixmap(display)
        self._image.setFixedSize(display.size())

    def _set_page(self, index: int) -> None:
        if not 0 <= index < len(self._source_pixmaps):
            return
        self._page_index = index
        self._source_pixmap = self._source_pixmaps[index]
        self._update_page_controls()
        self._fit_image_to_viewport()

    def _update_page_controls(self) -> None:
        page_count = len(self._source_pixmaps)
        self._page_label.setText(f"Page {self._page_index + 1} of {page_count}")
        self._previous_button.setEnabled(self._page_index > 0)
        self._next_button.setEnabled(self._page_index < page_count - 1)

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
        for category in ("All", "Summary", "Safety", "Categorical", "Continuous"):
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
            text = f"{shell.title} {shell.description} {' '.join(shell.all_tags)}".casefold()
            if self._category != "All" and self._category not in shell.all_tags:
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
