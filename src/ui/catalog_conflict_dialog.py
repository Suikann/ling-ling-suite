# -*- coding: utf-8 -*-
"""
存檔衝突畫面（原型 #60 存檔衝突的變體 C）

存檔時譜庫裡有格子被別人改過，列出這次會寫進譜庫的全部改動：群組、項目、原本（快照）、改成、譜庫現在，
以及要怎麼做。衝突的列標紅，要逐項選「用我的」或「用譜庫的」，全部選完才能按「寫入並存檔」；
其餘的列寫出照寫、譜庫已採用或無法寫回（已有 Drive 檔案的聲部，要到譜庫視窗處理）。
比對與選擇的意義見 core/catalog_sync.py；這裡只顯示比對結果、收集選擇。

使用範例：
    dialog = CatalogConflictDialog(outcome, parent)
    if dialog.exec() == CatalogConflictDialog.Accepted:
        decisions = dialog.decisions()
"""
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QHeaderView, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.catalog_sync import (
    Choice, Decision, ItemReview, PlaceReview, SyncField, SyncOutcome, Verdict, display_text,
)
from core.locale import t
from core.voice_names import instrument_name

# 衝突的列的文字顏色
_CONFLICT_COLOR = QColor("#e74c3c")
# 表格的欄標題
_HEADERS = (
    "catalog.sync.group", "catalog.sync.item", "catalog.sync.old", "catalog.sync.new", "catalog.sync.theirs",
    "catalog.sync.choice",
)
# 不是衝突的列在「怎麼做」一欄的寫法
_VERDICT_TEXT = {
    Verdict.WRITE: "catalog.sync.will_write",
    Verdict.CATALOG: "catalog.sync.use_catalog",
    Verdict.BLOCKED: "catalog.sync.blocked",
}


class CatalogConflictDialog(QDialog):
    """存檔衝突畫面：列出這次會寫進譜庫的全部改動，衝突的標紅、逐項選邊"""

    def __init__(self, outcome: SyncOutcome, parent: Optional[QWidget] = None):
        """
        Args:
            outcome: 有衝突、還沒寫入的比對結果（CatalogService.sync_instrumentation）
            parent: 父元件
        """
        super().__init__(parent)
        self.setWindowTitle(t("catalog.sync.title"))
        self.resize(760, 420)
        # 衝突的列：所在的那處、比對結果與選擇的選單
        self._choices: List[Tuple[PlaceReview, ItemReview, QComboBox]] = []
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(t("catalog.sync.intro")))
        self._table = QTableWidget(0, len(_HEADERS))
        self._table.setHorizontalHeaderLabels([t(key) for key in _HEADERS])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._table)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self._confirm = QPushButton(t("catalog.sync.confirm"))
        buttons.addButton(self._confirm, QDialogButtonBox.AcceptRole)
        self._confirm.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        for place in outcome.places:
            for review in place.items:
                if review.verdict is not Verdict.DONE:
                    self._add_row(place, review)
        self._update_confirm()

    def decisions(self) -> Dict[str, Dict[Tuple[SyncField, Any], Decision]]:
        """衝突的選擇：群組 ID → （SyncItem.key → Decision），連同當時看到的譜庫值"""
        chosen: Dict[str, Dict[Tuple[SyncField, Any], Decision]] = {}
        for place, review, combo in self._choices:
            choice = combo.currentData()
            if choice is not None:
                chosen.setdefault(place.write.group_id, {})[review.item.key] = Decision(choice, review.theirs)
        return chosen

    def _add_row(self, place: PlaceReview, review: ItemReview) -> None:
        """加一列改動；衝突的列標紅並放選邊的選單"""
        row = self._table.rowCount()
        self._table.insertRow(row)
        item = review.item
        texts = (
            place.write.group_name, _item_text(item.field, item.voice, item.new),
            _value_text(item.field, item.old), _value_text(item.field, item.new), _theirs_text(review),
        )
        conflict = review.verdict is Verdict.CONFLICT
        for column, text in enumerate(texts):
            cell = QTableWidgetItem(text)
            if conflict:
                cell.setForeground(QBrush(_CONFLICT_COLOR))
            self._table.setItem(row, column, cell)
        if conflict:
            combo = QComboBox()
            combo.addItem(t("catalog.sync.choose"), None)
            combo.addItem(t("catalog.sync.use_mine"), Choice.MINE)
            combo.addItem(t("catalog.sync.use_catalog"), Choice.CATALOG)
            combo.currentIndexChanged.connect(self._update_confirm)
            self._table.setCellWidget(row, len(_HEADERS) - 1, combo)
            self._choices.append((place, review, combo))
        else:
            self._table.setItem(row, len(_HEADERS) - 1, QTableWidgetItem(t(_VERDICT_TEXT[review.verdict])))

    def _update_confirm(self) -> None:
        """每個衝突都選了邊才能按「寫入並存檔」"""
        self._confirm.setEnabled(all(combo.currentData() is not None for _, _, combo in self._choices))


def _item_text(sync_field: SyncField, voice: str, new: Any) -> str:
    """一格改動的項目名稱，例如「琵琶 人數」「新增聲部 中阮」"""
    if sync_field is SyncField.VOICE:
        return t("catalog.sync.add_voice" if new else "catalog.sync.remove_voice", voice=voice)
    if sync_field is SyncField.ORDER:
        return t("catalog.sync.order")
    if sync_field is SyncField.HEADCOUNT:
        return t("catalog.sync.headcount", voice=voice)
    if sync_field is SyncField.DURATION:
        return t("catalog.sync.duration")
    return t("catalog.sync.section", instrument=instrument_name(voice))


def _value_text(sync_field: SyncField, value: Any) -> str:
    """一格的值在畫面上的寫法；聲部的增刪寫「有」「沒有」"""
    if sync_field is SyncField.VOICE:
        return t("catalog.sync.present" if value else "catalog.sync.absent")
    return display_text(sync_field, value)


def _theirs_text(review: ItemReview) -> str:
    """譜庫現在的值；人數那一格的聲部在譜庫已被刪掉時寫明"""
    if review.item.field is SyncField.HEADCOUNT and review.theirs is None:
        gone = review.verdict in (Verdict.CONFLICT, Verdict.CATALOG)
        return t("catalog.sync.voice_gone") if gone else ""
    return _value_text(review.item.field, review.theirs)
