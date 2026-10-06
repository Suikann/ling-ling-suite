# -*- coding: utf-8 -*-
"""
共用增強元件與 UI 輔助函式
"""
import os
import threading
from typing import Callable, List, Optional, Tuple
from PySide6.QtWidgets import QListWidget, QAbstractItemView, QMessageBox, QWidget
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtCore import Qt, QRect, QObject, Signal
from core.constants import PDF_EXTENSION
from core.locale import t
from core.models import Group, Project


def ensure_file_exists(parent: QWidget, path: str) -> bool:
    """確認檔案存在；不存在時顯示「找不到檔案」錯誤訊息

    Args:
        parent: 錯誤訊息框的父元件
        path: 要確認的檔案路徑

    Returns:
        檔案存在時為 True
    """
    if os.path.isfile(path):
        return True
    QMessageBox.critical(parent, t("dialog.error"), t("dialog.error.file_not_found", path=path))
    return False


def pdf_file_choices(
    project: Optional[Project], group: Optional[Group],
) -> List[Tuple[str, str, Optional[Group]]]:
    """分割與旋轉對話框的選檔清單（只列 PDF）

    Args:
        project: 目前專案，可為 None
        group: 要列出的群組（總譜在前、分譜在後）；None 表示只列未分組檔案

    Returns:
        （顯示名稱、檔案路徑、所屬群組）清單
    """
    if not project:
        return []
    return [
        (ref.file.display_name, ref.file.original_path, ref.group)
        for ref in project.file_refs()
        if ref.group is group and ref.file.original_path.lower().endswith(PDF_EXTENSION)
    ]


class ThumbnailLoader(QObject):
    """在背景執行緒算繪 PDF 縮圖，完成時回到主執行緒通知

    只通知最近一次 load 的結果：換選其他檔案後，前一個檔案晚到的縮圖或錯誤直接捨棄；
    cancel 之後（對話框關閉時）也不再通知。
    """

    _rendered = Signal(int, list)
    _failed = Signal(int, str)

    def __init__(self, render: Callable[[str], list], on_ready: Callable[[list], None],
                 on_error: Callable[[str], None]):
        """建立縮圖載入器

        Args:
            render: 在背景執行緒呼叫的算繪函式，接收 PDF 路徑、回傳各頁縮圖
            on_ready: 縮圖算繪完成時在主執行緒呼叫，參數為各頁縮圖
            on_error: 算繪失敗時在主執行緒呼叫，參數為錯誤訊息
        """
        super().__init__()
        self._render = render
        self._on_ready = on_ready
        self._on_error = on_error
        self._latest = 0
        self._rendered.connect(self._deliver_ready)
        self._failed.connect(self._deliver_error)

    def load(self, path: str):
        """開始在背景算繪 path 的縮圖；先前還沒完成的載入作廢"""
        self._latest += 1
        threading.Thread(target=self._run, args=(self._latest, path), daemon=True).start()

    def cancel(self):
        """作廢還沒完成的載入"""
        self._latest += 1

    def _run(self, ticket: int, path: str):
        try:
            images = self._render(path)
        except Exception as e:
            self._failed.emit(ticket, str(e))
            return
        self._rendered.emit(ticket, images)

    def _deliver_ready(self, ticket: int, images: list):
        if ticket == self._latest:
            self._on_ready(images)

    def _deliver_error(self, ticket: int, message: str):
        if ticket == self._latest:
            self._on_error(message)


class DragListWidget(QListWidget):
    """增強版清單元件：均勻間距 + 彩色拖拉指示線"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setSpacing(1)
        self.setUniformItemSizes(True)
        self._drop_color = QColor("#7c6ddf")

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
