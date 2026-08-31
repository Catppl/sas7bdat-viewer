from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPalette
from PySide6.QtWidgets import QHeaderView, QStyle, QStyleOptionHeader


class FilterHeaderView(QHeaderView):
    filter_requested = Signal(int)
    FILTER_BUTTON_WIDTH = 22
    RESIZE_GUTTER_WIDTH = 10

    def __init__(self, parent=None) -> None:
        super().__init__(Qt.Horizontal, parent)
        self._filtered_sections: set[int] = set()
        self.setSectionsClickable(True)

    def set_filtered_sections(self, sections: set[int]) -> None:
        self._filtered_sections = set(sections)
        self.viewport().update()

    def _button_rect(self, section: int) -> QRect:
        section_right = self.sectionViewportPosition(section) + self.sectionSize(section)
        return QRect(
            section_right - self.RESIZE_GUTTER_WIDTH - self.FILTER_BUTTON_WIDTH,
            0,
            self.FILTER_BUTTON_WIDTH,
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
        background = self.model().headerData(
            logical_index, Qt.Horizontal, Qt.BackgroundRole
        )
        if isinstance(background, QColor):
            option.palette.setColor(QPalette.Button, background)
            option.palette.setColor(QPalette.Window, background)
            painter.fillRect(rect, background)
            painter.setPen(QColor("#ceddea"))
            painter.drawLine(rect.topRight(), rect.bottomRight())
            painter.drawLine(rect.bottomLeft(), rect.bottomRight())
            painter.setPen(QColor("#243b53"))
            font = painter.font()
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(
                rect.adjusted(
                    6,
                    0,
                    -(self.FILTER_BUTTON_WIDTH + self.RESIZE_GUTTER_WIDTH),
                    0,
                ),
                Qt.AlignLeft | Qt.AlignVCenter,
                option.text,
            )
        else:
            self.style().drawControl(QStyle.CE_Header, option, painter, self)
        button = QRect(
            rect.right()
            - self.RESIZE_GUTTER_WIDTH
            - self.FILTER_BUTTON_WIDTH
            + 1,
            rect.top(),
            self.FILTER_BUTTON_WIDTH,
            rect.height(),
        )
        active = logical_index in self._filtered_sections
        painter.save()
        painter.setPen(QColor("#0878c9" if active else "#627d98"))
        painter.drawText(button, Qt.AlignCenter, "▼")
        if active:
            painter.setBrush(QColor("#0878c9"))
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(button.right() - 6, button.top() + 4, 4, 4)
        painter.restore()
