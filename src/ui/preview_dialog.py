# -*- coding: utf-8 -*-
"""
預覽對話框（PySide6）
"""
from typing import Callable, Dict, List
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget,
)
from core.locale import t
from core.models import RenameEntry


class PreviewDialog(QDialog):
    """預覽重新命名對話框"""

    def __init__(self, plan: List[RenameEntry], conflicts: Dict, on_execute: Callable, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("preview.title"))
        self.resize(700, 500)
        self._plan = plan
        self._conflicts = conflicts
        self._on_execute = on_execute
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        if self._conflicts:
            warn = QLabel(t("preview.conflict_warning", count=len(self._conflicts)))
            warn.setStyleSheet("color: #e74c3c; font-weight: bold;")
            layout.addWidget(warn)
        layout.addWidget(QLabel(t("preview.file_count", count=len(self._plan))))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        cl = QVBoxLayout(container)
        for entry in self._plan:
            row = QLabel(f"{entry.original_path}\n  \u2192 {entry.new_path}")
            row.setWordWrap(True)
            if entry.new_path.lower() in {k.lower() for k in self._conflicts}:
                row.setStyleSheet("color: #e74c3c;")
            cl.addWidget(row)
        cl.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, stretch=1)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton(t("preview.cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        if self._conflicts:
            exec_btn = QPushButton(t("preview.execute_with_suffix"))
            exec_btn.clicked.connect(self._execute_with_suffix)
        else:
            exec_btn = QPushButton(t("preview.execute"))
            exec_btn.clicked.connect(self._execute)
        exec_btn.setStyleSheet("font-weight: bold;")
        btn_row.addWidget(exec_btn)
        layout.addLayout(btn_row)

    def _execute(self):
        self._on_execute(self._plan)
        self.accept()

    def _execute_with_suffix(self):
        from services.rename_service import RenameService
        from services.file_service import FileService
        plan = RenameService(FileService()).apply_auto_suffix(self._plan)
        self._on_execute(plan)
        self.accept()
