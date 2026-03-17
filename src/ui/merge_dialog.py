# -*- coding: utf-8 -*-
"""
連結樂章對話框（PySide6）
"""
from typing import Callable, List
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QScrollArea, QWidget, QMessageBox,
)
from core.locale import t
from core.models import Group


class LinkMovementsDialog(QDialog):
    """連結群組為樂章的對話框"""

    def __init__(self, groups: List[Group], on_confirm: Callable, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("group.link_movements.title"))
        self.resize(500, 420)
        self._groups = groups
        self._on_confirm = on_confirm
        self._checks: list = []
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("group.link_movements.select")))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        cl = QVBoxLayout(container)
        for group in self._groups:
            cb = QCheckBox(group.name or group.id[:8])
            cl.addWidget(cb)
            self._checks.append((group, cb))
        cl.addStretch()
        scroll.setWidget(container)
        layout.addWidget(scroll, stretch=1)
        name_row = QHBoxLayout()
        name_row.addWidget(QLabel(t("group.link_movements.piece_name")))
        self._piece_entry = QLineEdit()
        name_row.addWidget(self._piece_entry, stretch=1)
        layout.addLayout(name_row)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton(t("group.link_movements.cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        confirm_btn = QPushButton(t("group.link_movements.confirm"))
        confirm_btn.setStyleSheet("font-weight: bold;")
        confirm_btn.clicked.connect(self._confirm)
        btn_row.addWidget(confirm_btn)
        layout.addLayout(btn_row)

    def _confirm(self):
        selected = [g for g, cb in self._checks if cb.isChecked()]
        if len(selected) < 2:
            QMessageBox.information(self, t("dialog.info"), t("group.link_movements.need_two"))
            return
        piece_name = self._piece_entry.text().strip()
        if not piece_name:
            return
        self._on_confirm(selected, piece_name)
        self.accept()
