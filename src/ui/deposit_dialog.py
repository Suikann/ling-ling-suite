# -*- coding: utf-8 -*-
"""
存入譜庫的預覽（原型 #65 存入的變體 C）

左邊是群組清單，每個群組寫出各動作的份數，不存入的群組寫原因，沒連到譜庫的附「連到譜庫…」；
右邊是所選群組的「聲部／本機 → 譜庫（Drive）／動作」對照表，預設只看要上傳或搬移的。
上方是未存檔時的紅色提示（「存入譜庫」按不下去）與上次中斷的提示（DepositPlan.interrupted，從存入紀錄推算），底部是合計（上傳幾份、多大、搬移幾份）。
只顯示計畫（core/deposit.py 的 DepositPlan），按「存入譜庫」關閉對話框，存入由主視窗在背景執行。

使用範例：
    dialog = DepositDialog(plan, unsaved=project.is_modified(), parent=window)
    dialog.link_requested.connect(link_group)
    if dialog.exec() == DepositDialog.Accepted:
        ...
"""
from typing import Dict, Optional, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.deposit import DepositAction, DepositItem, DepositPlan, GroupDeposit, PlaceFiles, folder_key
from core.locale import t
from ui.compress_pdf import size_text
from ui.instrumentation_table import link_text

# 動作欄的顏色（同原型：新上傳綠、更新與搬移藍、跳過灰、不存入黃）
_ACTION_COLORS = {
    DepositAction.NEW: "#7ddc9a",
    DepositAction.UPDATE: "#8fb8ff",
    DepositAction.MOVE: "#8fb8ff",
    DepositAction.REGISTER: "#8fb8ff",
    DepositAction.SKIP: "#7a7f93",
    DepositAction.UNLISTED: "#f2c14e",
}
# 「只看要上傳或搬移的」勾著時藏起來的動作
_QUIET = frozenset({DepositAction.SKIP, DepositAction.REGISTER})
# 群組摘要依這個順序列份數
_SUMMARY_ORDER = (
    DepositAction.NEW, DepositAction.UPDATE, DepositAction.MOVE, DepositAction.REGISTER, DepositAction.SKIP,
    DepositAction.UNLISTED,
)
_UNSAVED_STYLE = "background-color: #5a2129; color: #ff8a8a; border-radius: 5px; padding: 6px 10px;"
_INTERRUPTED_STYLE = "background-color: #5b4613; color: #f2c14e; border-radius: 5px; padding: 6px 10px;"


def group_summary(group: GroupDeposit) -> str:
    """群組清單上的一行摘要：各動作的份數，或不存入的原因"""
    if group.block is not None:
        return t(f"deposit.block.{group.block.value}")
    counts = group.counts()
    return "・".join(
        f"{t(f'deposit.action.{action.value}')} {counts[action]}" for action in _SUMMARY_ORDER if counts.get(action)
    )


class DepositDialog(QDialog):
    """存入譜庫的預覽；按「存入譜庫」時 accept"""

    link_requested = Signal(str)

    def __init__(self, plan: DepositPlan, unsaved: bool, parent: Optional[QWidget] = None):
        """
        Args:
            plan: 存入計畫（上次中斷時 interrupted 是那次做了幾份）
            unsaved: 專案有沒有未存檔的改動（有時不能存入）
            parent: 父元件
        """
        super().__init__(parent)
        self._plan = plan
        self.setWindowTitle(t("deposit.title"))
        self.resize(1100, 700)
        layout = QVBoxLayout(self)
        self._unsaved = QLabel(t("deposit.unsaved"))
        self._unsaved.setStyleSheet(_UNSAVED_STYLE)
        self._unsaved.setVisible(unsaved)
        layout.addWidget(self._unsaved)
        self.interrupted_notice: Optional[QLabel] = None
        if plan.interrupted is not None:
            notice = self.interrupted_notice = QLabel(t("deposit.interrupted", count=plan.interrupted))
            notice.setStyleSheet(_INTERRUPTED_STYLE)
            layout.addWidget(notice)
        splitter = QSplitter(Qt.Horizontal)
        self._groups = QListWidget()
        self._groups.setMaximumWidth(320)
        self._pages = QStackedWidget()
        for group in plan.groups:
            QListWidgetItem(f"{group.sources.group_name}\n{group_summary(group)}", self._groups)
            self._pages.addWidget(self._group_page(group))
        self._groups.currentRowChanged.connect(self._pages.setCurrentIndex)
        splitter.addWidget(self._groups)
        splitter.addWidget(self._pages)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, stretch=1)
        bottom = QHBoxLayout()
        bottom.addWidget(QLabel(t(
            "deposit.totals", count=plan.upload_count(), size=size_text(plan.upload_size()), moved=plan.move_count(),
        )))
        bottom.addStretch()
        cancel = QPushButton(t("dialog.cancel_btn"))
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        self._confirm = QPushButton(t("deposit.confirm"))
        self._confirm.setDefault(True)
        self._confirm.setEnabled(not unsaved and bool(plan.work()))
        self._confirm.clicked.connect(self.accept)
        bottom.addWidget(self._confirm)
        layout.addLayout(bottom)
        if plan.groups:
            self._groups.setCurrentRow(0)

    def _group_page(self, group: GroupDeposit) -> QWidget:
        """右邊一個群組的頁面：不存入時寫原因（沒連到譜庫的附「連到譜庫…」），否則是對照表"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        if group.block is not None:
            layout.addWidget(QLabel(group_summary(group)))
            if group.sources.link is None:
                link = QPushButton(t("deposit.link"))
                link.clicked.connect(lambda: self._link(group.sources.group_id))
                row = QHBoxLayout()
                row.addWidget(link)
                row.addStretch()
                layout.addLayout(row)
            layout.addStretch()
            return page
        header = QHBoxLayout()
        header.addWidget(QLabel(link_text(group.sources.link)))
        header.addStretch()
        only_changes = QCheckBox(t("deposit.only_changes"))
        only_changes.setChecked(True)
        header.addWidget(only_changes)
        layout.addLayout(header)
        table = self._table(group)
        layout.addWidget(table)

        def filter_rows(only: bool) -> None:
            """勾著時藏起跳過與直接登記的列"""
            for row, item in enumerate(group.items):
                table.setRowHidden(row, only and item.action in _QUIET)

        only_changes.toggled.connect(filter_rows)
        filter_rows(True)
        return page

    def _table(self, group: GroupDeposit) -> QTableWidget:
        """群組每份檔一列：聲部、本機、譜庫（Drive）現在的位置、動作"""
        table = QTableWidget(len(group.items), 4)
        table.setHorizontalHeaderLabels([
            t("deposit.column.voice"), t("deposit.column.local"), t("deposit.column.drive"), t("deposit.column.action"),
        ])
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        paths = _folder_paths(group.place)
        for row, item in enumerate(group.items):
            local = QTableWidgetItem(item.source.drive_path())
            local.setToolTip(item.source.local_path)
            action = QTableWidgetItem(_action_text(item))
            action.setForeground(QColor(_ACTION_COLORS[item.action]))
            cells = [
                QTableWidgetItem(item.source.voice or group.sources.score_label), local,
                QTableWidgetItem(_drive_text(item, paths)), action,
            ]
            for column, cell in enumerate(cells):
                table.setItem(row, column, cell)
        table.resizeColumnsToContents()
        return table

    def _link(self, group_id: str) -> None:
        """「連到譜庫…」：關閉預覽，交給主視窗連線"""
        self.reject()
        self.link_requested.emit(group_id)


def _folder_paths(place: Optional[PlaceFiles]) -> Dict[str, Tuple[str, ...]]:
    """版本資料夾與底下子資料夾的 ID → 在版本資料夾裡的路徑（比對鍵，名稱已轉小寫）"""
    return {folder_id: key for key, folder_id in place.folders.items()} if place else {}


def _drive_text(item: DepositItem, paths: Dict[str, Tuple[str, ...]]) -> str:
    """譜庫（Drive）欄：要動的那份現在在哪裡；新上傳寫要放的位置"""
    if item.target is None:
        return item.source.drive_path() if item.action is DepositAction.NEW else ""
    folder = next((paths[parent] for parent in item.target.parents if parent in paths), None)
    if folder is None:
        return item.target.name
    if folder == folder_key(item.source.folders):
        folder = item.source.folders
    return "/".join((*folder, item.target.name))


def _action_text(item: DepositItem) -> str:
    """動作欄：動作，連同原因（改名、內容相同或不同、譜庫沒有這個聲部）"""
    action = t(f"deposit.action.{item.action.value}")
    if item.action is DepositAction.UNLISTED:
        reason = t("deposit.reason.unlisted")
    elif item.renamed:
        reason = t("deposit.reason.renamed", old=item.target.name, new=item.source.file_name)
    elif item.action is DepositAction.UPDATE:
        reason = t("deposit.reason.changed")
    elif item.action in (DepositAction.SKIP, DepositAction.REGISTER, DepositAction.MOVE):
        reason = t("deposit.reason.same")
    else:
        reason = ""
    return f"{action}（{reason}）" if reason else action
