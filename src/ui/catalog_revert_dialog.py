# -*- coding: utf-8 -*-
"""
撤掉原處編制的確認框與四欄表

改連完成後，若這個群組在原本那處寫過、之後沒人改過的編制（core/catalog_revert.py 的 revert_rows），連線對話框開這個確認框
（原型 #65 的變體 B）：標題問「要撤掉原本那處的 N 項編制嗎？」，下面是四欄表（聲部、那次寫的、譜庫現在、撤掉後），
按鈕「留著」「撤掉」。按撤掉為 Accepted、留著或關閉為 Rejected，由呼叫端決定要不要記下撤掉。
四欄表另以 revert_table 提供，撤銷存入的「一起撤掉編制」共用。

使用範例：
    dialog = CatalogRevertDialog(rows, "春江花月夜 › 彭修文編 › 江樓鐘鼓", parent=link_dialog)
    dialog.finished.connect(lambda result: ...)   # result == QDialog.Accepted 就是撤掉
    dialog.open()
"""
from typing import Optional, Sequence

from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from core.catalog_revert import KeepReason, RevertOutcome, RevertRow, revert_count
from core.locale import t
from core.models import Headcount

# 四欄表的欄位（介面字串鍵的結尾）
_COLUMNS = ("voice", "written", "current", "after")
# 各種不動的原因在「撤掉後」欄的介面字串鍵
_KEEP_KEYS = {
    KeepReason.CHANGED: "catalog.revert.keep_changed",
    KeepReason.EDITED: "catalog.revert.keep_edited",
    KeepReason.FILED: "catalog.revert.keep_filed",
}


class CatalogRevertDialog(QDialog):
    """要不要撤掉原本那處的編制：「撤掉」為 Accepted，「留著」為 Rejected"""

    def __init__(self, rows: Sequence[RevertRow], place: str, parent=None):
        """
        Args:
            rows: 這個群組在原處寫過的聲部（CatalogService.revert_rows）
            place: 原本那處的寫法（曲目 › 版本 › 樂章）
            parent: 父元件
        """
        super().__init__(parent)
        self.setWindowTitle(t("catalog.relink.title"))
        self.setMinimumWidth(640)
        layout = QVBoxLayout(self)
        heading = QLabel(t("catalog.revert.question", count=revert_count(rows)))
        heading.setStyleSheet("font-weight: bold;")
        layout.addWidget(heading)
        layout.addWidget(QLabel(place))
        layout.addWidget(revert_table(rows))
        buttons = QHBoxLayout()
        buttons.addStretch()
        keep = QPushButton(t("catalog.revert.keep"))
        revert = QPushButton(t("catalog.revert.revert"))
        buttons.addWidget(keep)
        buttons.addWidget(revert)
        layout.addLayout(buttons)
        keep.clicked.connect(self.reject)
        revert.clicked.connect(self.accept)
        keep.setDefault(True)


def revert_table(rows: Sequence[RevertRow]) -> QTableWidget:
    """四欄表：聲部、那次寫的、譜庫現在、撤掉後（刪掉這個聲部、改回原值，或不動並寫出原因）

    Args:
        rows: 這個群組在那處寫過的聲部

    Returns:
        唯讀的表格
    """
    table = QTableWidget(len(rows), len(_COLUMNS))
    table.setHorizontalHeaderLabels([t(f"catalog.revert.column.{name}") for name in _COLUMNS])
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setSelectionMode(QAbstractItemView.NoSelection)
    table.verticalHeader().setVisible(False)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    for index, row in enumerate(rows):
        cells = [row.voice, _written_text(row), _current_text(row.current), _after_text(row)]
        for column, text in enumerate(cells):
            table.setItem(index, column, QTableWidgetItem(text))
    return table


def _headcount_text(headcount: Headcount) -> str:
    """人數的寫法；未填時寫出未填"""
    return headcount.stored_text() or t("catalog.revert.blank")


def _written_text(row: RevertRow) -> str:
    """那次寫的：新增的聲部寫新增（連同人數），否則是寫的人數"""
    if row.added and row.written.stored_text():
        return t("catalog.revert.added", headcount=row.written.stored_text())
    if row.added:
        return t("catalog.revert.added_blank")
    return _headcount_text(row.written)


def _current_text(current: Optional[Headcount]) -> str:
    """譜庫現在：人數；譜庫已沒有這個聲部時寫出來"""
    return t("catalog.revert.gone") if current is None else _headcount_text(current)


def _after_text(row: RevertRow) -> str:
    """撤掉後：刪掉這個聲部、改回原值，或不動並寫出原因（之後誰在何時改過）"""
    if row.outcome is RevertOutcome.REMOVE:
        return t("catalog.revert.remove")
    if row.outcome is RevertOutcome.RESTORE:
        return t("catalog.revert.restore", headcount=_headcount_text(row.original))
    return t(_KEEP_KEYS[row.keep_reason], who=row.changed_by, when=row.changed_at)

