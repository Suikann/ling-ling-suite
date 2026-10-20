# -*- coding: utf-8 -*-
"""
連線的合併差異表

群組與譜庫那處都已有編制時，連線對話框開這張表：每個聲部一列（Project.merge_rows），列出兩邊的人數、差異、
聲部組（社團的值蓋掉專案的值時兩個都寫），最後一欄是用譜庫的時每個聲部配到的分譜。整張二選一，預設用譜庫的；
選了就發出 chosen（LinkChoice），由呼叫端交給專案的連線操作。取消時什麼都不發。

使用範例：
    table = CatalogMergeDialog(project.merge_rows(group, place), group.name, parent=dialog)
    table.chosen.connect(lambda choice: project.link_group(group, place, choice))
    table.open()
"""
from typing import List, Optional

from PySide6.QtCore import Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget, QTableWidgetItem,
    QVBoxLayout,
)

from core.locale import t
from core.models import Headcount, LinkChoice, MergeDifference, MergeRow
from core.sections import section_label

# 各種差異的列底色；兩邊相同的列不上色
_DIFFERENCE_COLORS = {
    MergeDifference.HEADCOUNT: QColor("#fff3b0"),
    MergeDifference.GROUP_ONLY: QColor("#d8f5d0"),
    MergeDifference.CATALOG_ONLY: QColor("#d6e6fb"),
}
# 各種差異在「差異」欄的介面字串鍵
_DIFFERENCE_KEYS = {
    MergeDifference.SAME: "catalog.merge.same",
    MergeDifference.HEADCOUNT: "catalog.merge.headcount",
    MergeDifference.GROUP_ONLY: "catalog.merge.group_only",
    MergeDifference.CATALOG_ONLY: "catalog.merge.catalog_only",
}
_NONE_TEXT = "—"


class CatalogMergeDialog(QDialog):
    """合併差異表：整張選用譜庫的或群組的，選了發出 chosen（LinkChoice）"""

    chosen = Signal(object)

    def __init__(self, rows: List[MergeRow], group_name: str, parent=None):
        """
        Args:
            rows: 合併差異表的每一列（Project.merge_rows）
            group_name: 群組名稱，寫在標題
            parent: 父元件
        """
        super().__init__(parent)
        self.setWindowTitle(t("catalog.link.title"))
        self.setMinimumWidth(820)
        layout = QVBoxLayout(self)
        heading = QLabel(t("catalog.merge.title", group=group_name))
        heading.setStyleSheet("font-weight: bold;")
        layout.addWidget(heading)
        intro = QLabel(t("catalog.merge.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        layout.addWidget(self._table(rows))
        # 依原型照「取消、用群組的、用譜庫的」排列，不交給平台的按鈕順序
        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel = QPushButton(t("dialog.cancel_btn"))
        use_group = QPushButton(t("catalog.merge.use_group"))
        use_catalog = QPushButton(t("catalog.merge.use_catalog"))
        for button in (cancel, use_group, use_catalog):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        cancel.clicked.connect(self.reject)
        use_group.clicked.connect(lambda: self._choose(LinkChoice.GROUP))
        use_catalog.clicked.connect(lambda: self._choose(LinkChoice.CATALOG))
        use_catalog.setDefault(True)
        use_catalog.setFocus()

    def _choose(self, choice: LinkChoice):
        """選了一邊：關閉並發出 chosen"""
        self.accept()
        self.chosen.emit(choice)

    def _table(self, rows: List[MergeRow]) -> QTableWidget:
        """排出差異表：聲部、兩邊的人數、差異、聲部組、用譜庫的時配到的分譜"""
        columns = ("voice", "group", "catalog", "difference", "section", "part")
        table = QTableWidget(len(rows), len(columns))
        table.setHorizontalHeaderLabels([t(f"catalog.merge.column.{name}") for name in columns])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        for index, row in enumerate(rows):
            cells = [
                _voice_text(row), _headcount_text(row.group_headcount), _headcount_text(row.catalog_headcount),
                t(_DIFFERENCE_KEYS[row.difference]), _section_text(row), _part_text(row),
            ]
            color = _DIFFERENCE_COLORS.get(row.difference)
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if color is not None:
                    item.setBackground(QBrush(color))
                table.setItem(index, column, item)
        return table


def _voice_text(row: MergeRow) -> str:
    """聲部欄：譜庫的寫法（只在群組時為群組的）；兩邊寫法不同時一併寫出群組的寫法"""
    if not row.catalog_voice:
        return row.group_voice
    if row.group_voice and row.group_voice != row.catalog_voice:
        return t("catalog.merge.spelled", voice=row.catalog_voice, group=row.group_voice)
    return row.catalog_voice


def _headcount_text(headcount: Optional[Headcount]) -> str:
    """人數欄：這一邊沒有這個聲部時為破折號"""
    return _NONE_TEXT if headcount is None else headcount.text()


def _section_text(row: MergeRow) -> str:
    """聲部組欄：連線後的聲部組；社團的值蓋掉專案的值時寫出原本的"""
    if row.replaced_section:
        return t(
            "catalog.merge.section_replaced", section=section_label(row.section),
            own=section_label(row.replaced_section),
        )
    return section_label(row.section)


def _part_text(row: MergeRow) -> str:
    """配到的分譜欄：只在群組的聲部寫分譜移到未分組；用譜庫的時本專案沒有的聲部寫出來"""
    if not row.catalog_voice:
        return t("catalog.merge.to_ungrouped", file=row.part.display_name) if row.part else _NONE_TEXT
    if row.part is not None:
        return row.part.display_name
    return _NONE_TEXT if row.present else t("catalog.merge.absent")
