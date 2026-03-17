# -*- coding: utf-8 -*-
"""
檔案清單元件（PySide6）

使用 QListWidget 提供拖拉排序的檔案清單。
"""
from typing import Callable, List, Optional
from PySide6.QtWidgets import QWidget, QVBoxLayout, QListWidget, QAbstractItemView
from core.locale import t
from core.models import FileInfo


class FileListWidget(QWidget):
    """可排序的檔案清單元件"""

    def __init__(self, on_changed: Optional[Callable] = None, parent=None):
        super().__init__(parent)
        self._files: List[FileInfo] = []
        self._on_changed = on_changed
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._list = QListWidget()
        self._list.setDragDropMode(QAbstractItemView.InternalMove)
        self._list.model().rowsMoved.connect(self._on_reordered)
        layout.addWidget(self._list)

    def set_files(self, files: List[FileInfo]):
        self._files = files
        self._list.clear()
        for f in files:
            self._list.addItem(f.display_name)

    def get_files(self) -> List[FileInfo]:
        return list(self._files)

    def _on_reordered(self):
        if self._on_changed:
            self._on_changed()
