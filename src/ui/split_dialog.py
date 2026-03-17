# -*- coding: utf-8 -*-
"""
PDF 分割對話框（PySide6）

存根版本 - 完整功能將逐步遷移。
"""
from typing import Callable, Optional
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel
from core.locale import t


class SplitPdfDialog(QDialog):
    """PDF 分割對話框"""

    def __init__(self, project, on_split_complete=None, initial_group=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("split.title"))
        self.resize(1100, 720)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("PDF Split - migrating to PySide6..."))
