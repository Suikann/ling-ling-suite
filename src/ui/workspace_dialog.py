# -*- coding: utf-8 -*-
"""
工作區清理對話框（PySide6）

列出工作區內各來源的分割輸出及其引用狀態，讓使用者勾選要移至資源回收桶的項目。
"""
import os
from datetime import datetime
from typing import Callable, List
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QAbstractItemView,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from core.constants import WorkspaceStatus
from core.locale import t
from core.models import WorkspaceEntry
from services.workspace_service import WorkspaceScan, WorkspaceService

_STATUS_KEYS = {
    WorkspaceStatus.IN_USE.value: "workspace.status.in_use",
    WorkspaceStatus.OWNED_BY_OTHER.value: "workspace.status.owned_by_other",
    WorkspaceStatus.OWNER_UNREADABLE.value: "workspace.status.owner_unreadable",
    WorkspaceStatus.ORPHAN.value: "workspace.status.orphan",
    WorkspaceStatus.UNKNOWN_SOURCE.value: "workspace.status.unknown_source",
}
_STATUS_COLORS = {
    WorkspaceStatus.IN_USE.value: "#7fb37f",
    WorkspaceStatus.OWNED_BY_OTHER.value: "#e0b060",
    WorkspaceStatus.OWNER_UNREADABLE.value: "#e0b060",
    WorkspaceStatus.ORPHAN.value: "#c0c4d4",
    WorkspaceStatus.UNKNOWN_SOURCE.value: "#c0c4d4",
}


def _format_size(num_bytes: int) -> str:
    """把位元組數格式化為易讀字串"""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


class WorkspaceCleanupDialog(QDialog):
    """工作區清理對話框"""

    def __init__(
        self,
        workspace_service: WorkspaceService,
        scan_callback: Callable[[], WorkspaceScan],
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(t("workspace.title"))
        self.resize(820, 480)
        self._workspace = workspace_service
        self._scan_callback = scan_callback
        self._entries: List[WorkspaceEntry] = []
        self._build_ui()
        self._reload()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("workspace.intro")))
        self._notice_label = QLabel("")
        self._notice_label.setStyleSheet("color: #e0b060;")
        self._notice_label.setWordWrap(True)
        self._notice_label.setVisible(False)
        layout.addWidget(self._notice_label)
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels([
            t("workspace.col.source"), t("workspace.col.files"), t("workspace.col.size"),
            t("workspace.col.modified"), t("workspace.col.status"),
        ])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 5):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionMode(QAbstractItemView.NoSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.itemChanged.connect(self._update_summary)
        layout.addWidget(self._table, stretch=1)
        self._summary_label = QLabel("")
        layout.addWidget(self._summary_label)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton(t("workspace.close"))
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        self._cleanup_btn = QPushButton(t("workspace.cleanup"))
        self._cleanup_btn.setStyleSheet("font-weight: bold;")
        self._cleanup_btn.clicked.connect(self._cleanup)
        btn_row.addWidget(self._cleanup_btn)
        layout.addLayout(btn_row)

    def _reload(self):
        scan = self._scan_callback()
        self._entries = scan.entries
        if scan.unreadable_projects:
            self._notice_label.setText(t(
                "workspace.unreadable_notice",
                count=len(scan.unreadable_projects),
                files="\n".join(scan.unreadable_projects),
            ))
            self._notice_label.setVisible(True)
        else:
            self._notice_label.setVisible(False)
        self._render_table()

    def _render_table(self):
        self._table.blockSignals(True)
        self._table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            source_item = QTableWidgetItem(entry.source_name or os.path.basename(entry.folder))
            source_item.setToolTip(entry.source_path or entry.folder)
            source_item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            checkable = entry.status != WorkspaceStatus.IN_USE.value
            if not checkable:
                source_item.setFlags(Qt.ItemIsUserCheckable)
            default_checked = entry.status == WorkspaceStatus.ORPHAN.value
            source_item.setCheckState(Qt.Checked if default_checked else Qt.Unchecked)
            self._table.setItem(row, 0, source_item)
            self._table.setItem(row, 1, self._readonly_item(str(entry.file_count)))
            self._table.setItem(row, 2, self._readonly_item(_format_size(entry.total_bytes)))
            modified = datetime.fromtimestamp(entry.modified_at).strftime("%Y-%m-%d %H:%M")
            self._table.setItem(row, 3, self._readonly_item(modified))
            status_item = self._readonly_item(self._status_text(entry))
            status_item.setForeground(QColor(_STATUS_COLORS.get(entry.status, "#c0c4d4")))
            status_item.setToolTip(entry.project_path)
            self._table.setItem(row, 4, status_item)
        self._table.blockSignals(False)
        self._update_summary()

    @staticmethod
    def _readonly_item(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemIsEnabled)
        return item

    @staticmethod
    def _status_text(entry: WorkspaceEntry) -> str:
        key = _STATUS_KEYS.get(entry.status, "workspace.status.unknown_source")
        project = os.path.basename(entry.project_path) if entry.project_path else ""
        return t(key, project=project)

    def _checked_entries(self) -> List[WorkspaceEntry]:
        checked = []
        for row, entry in enumerate(self._entries):
            item = self._table.item(row, 0)
            if item and item.checkState() == Qt.Checked:
                checked.append(entry)
        return checked

    def _update_summary(self, *_args):
        if not self._entries:
            self._summary_label.setText(t("workspace.empty"))
            self._cleanup_btn.setEnabled(False)
            return
        checked = self._checked_entries()
        total = sum(e.total_bytes for e in checked)
        self._summary_label.setText(t("workspace.summary", count=len(checked), size=_format_size(total)))
        self._cleanup_btn.setEnabled(bool(checked))

    def _cleanup(self):
        checked = self._checked_entries()
        if not checked:
            return
        try:
            count = self._workspace.cleanup(checked)
        except Exception as e:
            QMessageBox.critical(self, t("dialog.error"), str(e))
            return
        QMessageBox.information(self, t("dialog.complete"), t("workspace.cleanup_done", count=count))
        self._reload()
