# -*- coding: utf-8 -*-
"""
共用增強元件
"""
from PySide6.QtWidgets import QListWidget, QAbstractItemView
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtCore import Qt, QRect


class DragListWidget(QListWidget):
    """增強版清單元件：均勻間距 + 彩色拖拉指示線"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setSpacing(1)
        self.setUniformItemSizes(True)
        self._drop_color = QColor("#c89530")

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.state() != QAbstractItemView.DraggingState:
            return
        drop_pos = self.dropIndicatorPosition()
        if drop_pos == QAbstractItemView.BelowItem:
            index = self.currentIndex()
            rect = self.visualRect(index)
            self._draw_indicator(rect.bottom() + 1)
        elif drop_pos == QAbstractItemView.AboveItem:
            index = self.currentIndex()
            rect = self.visualRect(index)
            self._draw_indicator(rect.top())
        elif drop_pos == QAbstractItemView.OnViewport:
            pass

    def _draw_indicator(self, y: int):
        painter = QPainter(self.viewport())
        pen = QPen(self._drop_color, 3, Qt.SolidLine)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        w = self.viewport().width()
        painter.drawLine(8, y, w - 8, y)
        painter.setBrush(self._drop_color)
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(4, y - 4, 8, 8)
        painter.drawEllipse(w - 12, y - 4, 8, 8)
        painter.end()

    def wheelEvent(self, event):
        super().wheelEvent(event)
        if self.state() == QAbstractItemView.DraggingState:
            self.viewport().update()
