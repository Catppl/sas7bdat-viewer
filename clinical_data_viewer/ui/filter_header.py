from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter
from PySide6.QtWidgets import QHeaderView, QStyle, QStyleOptionHeader


class FilterHeaderView(QHeaderView):
    filter_requested = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self._filtered_sections: set[int] = set()
        self.setSectionsClickable(True)

    def set_filtered_sections(self, sections: set[int]) -> None:
        self._filtered_sections = set(sections)
        self.viewport().update()

    def _button_rect(self, section: int) -> QRect:
        return QRect(
            self.sectionViewportPosition(section) + self.sectionSize(section) - 22,
            0,
            22,
            self.height(),
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        section = self.logicalIndexAt(event.position().toPoint())
        if (
            event.button() == Qt.LeftButton
            and section >= 0
            and self._button_rect(section).contains(event.position().toPoint())
        ):
            self.filter_requested.emit(section)
            event.accept()
            return
        super().mousePressEvent(event)

    def paintSection(self, painter: QPainter, rect: QRect, logical_index: int) -> None:
        if not rect.isValid():
            return
        option = QStyleOptionHeader()
        self.initStyleOption(option)
        option.rect = rect
        option.section = logical_index
        option.text = str(
            self.model().headerData(logical_index, Qt.Horizontal, Qt.DisplayRole) or ""
        )
        option.textAlignment = Qt.AlignLeft | Qt.AlignVCenter
        self.style().drawControl(QStyle.CE_Header, option, painter, self)
        button = QRect(rect.right() - 21, rect.top(), 21, rect.height())
        active = logical_index in self._filtered_sections
        painter.save()
        painter.setPen(QColor("#0878c9" if active else "#627d98"))
        painter.drawText(button, Qt.AlignCenter, "▼")
        if active:
            painter.setBrush(QColor("#0878c9"))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(button.right() - 6, button.top() + 4, 4, 4)
        painter.restore()
