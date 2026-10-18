# -*- coding: utf-8 -*-
"""
清除本機檔案的預覽（原型 #65 清除的變體 C）

上方是群組總表，每種歸類一欄（可清除、跟譜庫不一樣、還沒存入、譜庫裡沒有、可釋放），群組前面勾選；
沒有可清除檔案的群組不能勾，已清除的群組是灰色一列「已清除，只在譜庫」。點一列在下方顯示這個群組的檔案明細，
預設只看不能清除的。底部寫出勾了幾個群組、會永久刪除幾份、多大；專案有未存檔的改動時上方紅色提示，按不下去。
只顯示計畫（core/clear_local.py 的 ClearPlan），按「清除本機檔案」關閉對話框，確認框與清除由主視窗處理。

使用範例：
    dialog = ClearDialog(plan, unsaved=project.is_modified(), parent=window)
    if dialog.exec() == ClearDialog.Accepted:
        group_ids = dialog.chosen_ids()
"""
import os
from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.clear_local import ClearItem, ClearPlan, ClearStatus, GroupClear
from core.constants import ALERT_BANNER_STYLE
from core.locale import t
from ui.compress_pdf import size_text

# 群組總表裡依序列份數的歸類（接在群組欄之後，最後一欄是可釋放）
_COUNTED = (ClearStatus.CLEARABLE, ClearStatus.DIFFERENT, ClearStatus.NOT_ARCHIVED, ClearStatus.NOT_IN_CATALOG)
# 明細狀態欄的顏色（可清除綠、跟譜庫不一樣黃、其餘灰）
_STATUS_COLORS = {
    ClearStatus.CLEARABLE: "#7ddc9a",
    ClearStatus.DIFFERENT: "#f2c14e",
    ClearStatus.NOT_ARCHIVED: "#7a7f93",
    ClearStatus.NOT_IN_CATALOG: "#7a7f93",
    ClearStatus.CATALOG_ONLY: "#7a7f93",
    ClearStatus.MISSING: "#ff8a8a",
}
_CLEARED_COLOR = "#7a7f93"


class ClearDialog(QDialog):
    """清除本機檔案的預覽；按「清除本機檔案」時 accept，勾了的群組由 chosen_ids 交出"""

    def __init__(self, plan: ClearPlan, unsaved: bool, parent: Optional[QWidget] = None):
        """
        Args:
            plan: 清除計畫
            unsaved: 專案有沒有未存檔的改動（有時不能清除）
            parent: 父元件
        """
        super().__init__(parent)
        self._plan = plan
        self._unsaved = unsaved
        self.setWindowTitle(t("clear.title"))
        self.resize(1000, 680)
        layout = QVBoxLayout(self)
        banner = QLabel(t("clear.unsaved"))
        banner.setStyleSheet(ALERT_BANNER_STYLE)
        banner.setVisible(unsaved)
        layout.addWidget(banner)
        self._groups = self._group_table()
        layout.addWidget(self._groups, stretch=1)
        header = QHBoxLayout()
        self._detail_title = QLabel("")
        header.addWidget(self._detail_title)
        header.addStretch()
        self._only_problems = QCheckBox(t("clear.only_problems"))
        self._only_problems.setChecked(True)
        self._only_problems.toggled.connect(self._show_details)
        header.addWidget(self._only_problems)
        layout.addLayout(header)
        self._details = QTableWidget(0, 3)
        self._details.setHorizontalHeaderLabels([
            t("clear.column.voice"), t("clear.column.local"), t("clear.column.status"),
        ])
        self._details.setEditTriggers(QTableWidget.NoEditTriggers)
        self._details.verticalHeader().setVisible(False)
        self._details.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._details, stretch=1)
        bottom = QHBoxLayout()
        self._totals = QLabel("")
        bottom.addWidget(self._totals)
        bottom.addStretch()
        cancel = QPushButton(t("dialog.cancel_btn"))
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        self._confirm = QPushButton(t("clear.confirm"))
        self._confirm.clicked.connect(self.accept)
        bottom.addWidget(self._confirm)
        layout.addLayout(bottom)
        self._groups.itemChanged.connect(lambda _item: self._show_totals())
        self._groups.currentCellChanged.connect(lambda *_: self._show_details())
        self._show_totals()
        if plan.groups:
            self._groups.setCurrentCell(0, 0)

    def chosen_ids(self) -> List[str]:
        """勾了的群組 ID（依專案的順序）"""
        return [
            group.group_id for row, group in enumerate(self._plan.groups)
            if self._groups.item(row, 0).checkState() == Qt.Checked
        ]

    def _group_table(self) -> QTableWidget:
        """群組總表：每個群組一列，能勾的預設勾著；已清除的群組灰色一列"""
        columns = 2 + len(_COUNTED)
        table = QTableWidget(len(self._plan.groups), columns)
        table.setHorizontalHeaderLabels(
            [t("clear.column.group")] + [t(f"clear.column.{status.value}") for status in _COUNTED]
            + [t("clear.column.freed")],
        )
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        for row, group in enumerate(self._plan.groups):
            name = QTableWidgetItem(group.sources.group_name)
            flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled
            name.setFlags(flags | Qt.ItemIsUserCheckable if group.choosable else flags)
            name.setCheckState(Qt.Checked if group.choosable else Qt.Unchecked)
            table.setItem(row, 0, name)
            if group.cleared:
                name.setForeground(QColor(_CLEARED_COLOR))
                cleared = QTableWidgetItem(t("clear.cleared_group"))
                cleared.setForeground(QColor(_CLEARED_COLOR))
                table.setItem(row, 1, cleared)
                table.setSpan(row, 1, 1, columns - 1)
                continue
            counts = group.counts()
            for column, status in enumerate(_COUNTED, start=1):
                table.setItem(row, column, QTableWidgetItem(str(counts.get(status, 0))))
            table.setItem(row, columns - 1, QTableWidgetItem(size_text(group.freed_size())))
        table.resizeColumnsToContents()
        return table

    def _show_totals(self) -> None:
        """底部的合計，連同「清除本機檔案」按不按得下去"""
        chosen = self._plan.chosen(self.chosen_ids())
        self._totals.setText(t(
            "clear.totals", groups=len(self.chosen_ids()), count=len(chosen),
            size=size_text(sum(item.size for item in chosen)),
        ))
        self._confirm.setEnabled(not self._unsaved and bool(chosen))

    def _show_details(self) -> None:
        """下方顯示目前那一列群組的檔案明細；勾著「只看不能清除的」時藏起可清除的"""
        row = self._groups.currentRow()
        if not 0 <= row < len(self._plan.groups):
            self._details.setRowCount(0)
            self._detail_title.setText("")
            return
        group = self._plan.groups[row]
        self._detail_title.setText(group.sources.group_name)
        self._details.setRowCount(len(group.items))
        for index, item in enumerate(group.items):
            status = QTableWidgetItem(t(f"clear.status.{item.status.value}"))
            status.setForeground(QColor(_STATUS_COLORS[item.status]))
            local = QTableWidgetItem(os.path.basename(item.path))
            local.setToolTip(item.path)
            for column, cell in enumerate((QTableWidgetItem(_voice_text(group, item)), local, status)):
                self._details.setItem(index, column, cell)
            self._details.setRowHidden(
                index, self._only_problems.isChecked() and item.status is ClearStatus.CLEARABLE,
            )
        self._details.resizeColumnsToContents()


def _voice_text(group: GroupClear, item: ClearItem) -> str:
    """明細的聲部欄：總譜寫總譜標籤，沒配到聲部的分譜留空"""
    voice = item.source.voice
    if voice is None:
        return ""
    return voice or group.sources.score_label
