# -*- coding: utf-8 -*-
"""
發譜紀錄與撤回的對話框（原型 #63 撤回的變體 D）

PublishLogPanel 是譜庫視窗右側的發譜紀錄列表：每次發譜與撤回一列（新的在前），選一次發譜按「撤回…」。
RetractDialog 是撤回的預覽：左邊勾要撤回的群組（只選到群組；沒發成、沒有結果或沒有東西可撤的不能勾），
右邊是所選群組逐聲部的「現在 HuaXia 上 → 撤回後 → 原因」對照表（依結果分類上色、上方列出各類數量，最後一列是場次），
那次新建了曲目時一列提示撤回後留下空曲目；底部提示勾選的群組撤回後有幾個聲部沒有譜、要重新發譜。
只顯示預覽（services/retract_service.py 的 RetractPreview），按「撤回」關閉對話框，撤回由譜庫視窗執行。

使用範例：
    panel = PublishLogPanel(publish_entries(records))
    panel.retract_requested.connect(start_retract)
    dialog = RetractDialog(preview, parent=window)
    if dialog.exec() == QDialog.Accepted:
        group_ids = dialog.chosen()
    QMessageBox.information(window, t("retract.title_short"), result_text(result))
"""
from typing import Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSplitter,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.constants import ALERT_BANNER_STYLE, INFO_BANNER_STYLE
from core.locale import t
from core.publish_log import PublishAction, PublishLogEntry
from services.huaxia_client import HuaXiaScore
from services.retract_service import RetractEffect, RetractGroup, RetractPreview, RetractReason, RetractResult, RetractRow

# 各類的（字色，底色），同原型：沒有譜與刪掉聲部紅、聲部留著但沒有譜黃、只撤這次的藍、不動灰
_EFFECT_COLORS = {
    RetractEffect.NO_SCORE: ("#ff8a8a", "#5a2129"),
    RetractEffect.DELETE_PART: ("#ff8a8a", "#5a2129"),
    RetractEffect.KEEP_PART: ("#f2c14e", "#5b4613"),
    RetractEffect.PARTIAL: ("#8fb8ff", "#253c63"),
    RetractEffect.UNTOUCHED: ("#7a7f93", "#24262f"),
}
_GRAY = "#7a7f93"
# 發譜紀錄列表的欄位（介面字串鍵的結尾）
_LOG_COLUMNS = ("time", "operator", "action", "groups", "note")
# 撤回預覽對照表的欄位（介面字串鍵的結尾）
_ROW_COLUMNS = ("voice", "now", "after", "reason")
# 撤回不改回的欄位：（發譜紀錄 parts_updated 的欄位名稱，介面字串鍵的結尾）；needed_min 跟著人數，不另列
_KEPT_TEXTS = (("needed", "needed"), ("section", "section"))


def retractable(entry: PublishLogEntry) -> bool:
    """這一項是有結果、有群組發成了的發譜（可以按「撤回…」）"""
    return entry.action is PublishAction.START and entry.finished and any(
        record.failure is None for record in entry.groups
    )


def entry_cells(entry: PublishLogEntry, times: Dict[str, str]) -> Tuple[str, str, str, str, str]:
    """發譜紀錄列表的一列：時間、操作者、動作、群組 → HuaXia 曲目、備註

    Args:
        entry: 列表的一項
        times: 發譜編號對應那次發譜的時間（撤回的備註寫出撤回的是哪一次）
    """
    groups = "、".join(f"{record.group} → {record.piece_title}" for record in entry.groups)
    if entry.action is PublishAction.RETRACT:
        stopped = any(record.failure for record in entry.groups)
        note = t("publish_log.note.retract", time=times.get(entry.publish_id, entry.publish_id))
        if stopped:
            note = t("publish_log.note.stopped", note=note)
        return entry.time, entry.operator, t("publish_log.action.retract"), groups, note
    failed = sum(1 for record in entry.groups if record.failure is not None)
    if not entry.finished:
        note = t("publish_log.note.unfinished")
    elif failed:
        note = t("publish_log.note.failed", count=failed)
    else:
        note = ""
    return entry.time, entry.operator, t("publish_log.action.publish"), groups, note


class PublishLogPanel(QWidget):
    """譜庫視窗的發譜紀錄：每次發譜與撤回一列，選一次發譜按「撤回…」時送出 retract_requested(那一項)"""

    retract_requested = Signal(object)

    def __init__(self, entries: Sequence[PublishLogEntry], parent: Optional[QWidget] = None):
        """
        Args:
            entries: 發譜紀錄列表的項，新的在前（core/publish_log.py 的 publish_entries）
            parent: 父元件
        """
        super().__init__(parent)
        self._entries = list(entries)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(t("publish_log.title"))
        heading.setStyleSheet("font-size: 16px; font-weight: bold; padding: 4px;")
        layout.addWidget(heading)
        if not self._entries:
            layout.addWidget(QLabel(t("publish_log.empty")))
        times = {e.publish_id: e.time for e in self._entries if e.action is PublishAction.START}
        self.table = QTableWidget(len(self._entries), len(_LOG_COLUMNS))
        self.table.setHorizontalHeaderLabels([t(f"publish_log.column.{name}") for name in _LOG_COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, entry in enumerate(self._entries):
            for column, text in enumerate(entry_cells(entry, times)):
                item = QTableWidgetItem(text)
                if entry.action is not PublishAction.START:
                    item.setForeground(QColor(_GRAY))
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table, stretch=1)
        self.actions = QHBoxLayout()
        self.actions.addStretch()
        self.retract_button = QPushButton(t("retract.button"))
        self.retract_button.setEnabled(False)
        self.retract_button.clicked.connect(lambda: self.retract_requested.emit(self.chosen()))
        self.actions.addWidget(self.retract_button)
        layout.addLayout(self.actions)
        self.table.itemSelectionChanged.connect(self._on_selected)

    def chosen(self) -> Optional[PublishLogEntry]:
        """選的那一項；沒選時為 None"""
        rows = {index.row() for index in self.table.selectedIndexes()}
        return self._entries[rows.pop()] if len(rows) == 1 else None

    def _on_selected(self) -> None:
        """選了可以撤回的發譜才能按「撤回…」"""
        entry = self.chosen()
        self.retract_button.setEnabled(entry is not None and retractable(entry))


def _score_names(scores: Sequence[HuaXiaScore]) -> str:
    """幾份樂譜的名稱（HuaXia 上的樂譜名稱，沒有名稱時寫 Storage 路徑的檔名）"""
    return "、".join(t("retract.score_name", name=s.title or s.file_path.rsplit("/", 1)[-1]) for s in scores)


def row_cells(row: RetractRow) -> Tuple[str, str, str, str]:
    """對照表的一列：聲部、現在 HuaXia 上、撤回後、原因"""
    name = row.name or t("retract.full_score")
    now_parts = []
    if row.ours:
        now_parts.append(t("retract.now.ours", names=_score_names(row.ours)))
    if row.others:
        now_parts.append(_score_names(row.others))
    if row.effect is RetractEffect.DELETE_PART:
        now_parts.insert(0, t("retract.now.new_unassigned"))
    elif row.reason is RetractReason.ASSIGNED:
        now_parts.insert(0, t("retract.now.new_assigned"))
    now = "；".join(now_parts) or t("retract.now.empty")
    if row.effect is RetractEffect.PARTIAL:
        after = t("retract.after.partial", names=_score_names(row.others))
    else:
        after = t(f"retract.after.{row.effect.value}")
    reasons = [t(f"retract.reason.{row.reason.value}")]
    for field, key in _KEPT_TEXTS:
        if field in row.kept:
            before = row.kept[field][0]
            reasons.append(t(f"retract.kept.{key}", before=before if before not in (None, "") else t("retract.blank")))
    return name, now, after, "；".join(reasons)


def events_cells(group: RetractGroup) -> Tuple[str, str, str, str]:
    """對照表最後一列：那次掛上的場次（撤回不動）"""
    if group.events:
        now = t("retract.events.attached", events="、".join(
            f"{event.get('title', '')}（{str(event.get('ends_at', ''))[:10]}）" for event in group.events
        ))
    else:
        now = t("retract.events.none")
    return t("retract.events"), now, t("retract.after.untouched"), t("retract.events.reason")


def block_text(group: RetractGroup) -> str:
    """群組不能勾的原因；可以勾時為空字串"""
    if group.unfinished:
        return t("retract.block.unfinished")
    if group.failed:
        return t("retract.block.failed")
    if not group.actionable:
        return t("retract.block.nothing")
    return ""


class RetractGroupPage(QWidget):
    """撤回預覽右邊一個群組的頁面：各類的數量、殘留的空曲目、逐聲部的對照表與最後一列場次"""

    def __init__(self, group: RetractGroup, parent: Optional[QWidget] = None):
        """
        Args:
            group: 預覽裡的群組
            parent: 父元件
        """
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(t("retract.heading", group=group.name, piece=group.piece_title))
        heading.setStyleSheet("font-weight: bold;")
        layout.addWidget(heading)
        self.notice: Optional[QLabel] = None
        if block_text(group):
            self.notice = QLabel(block_text(group))
            self.notice.setWordWrap(True)
            self.notice.setStyleSheet(ALERT_BANNER_STYLE)
            layout.addWidget(self.notice)
        strip = QHBoxLayout()
        self.counts: List[QLabel] = []
        counts = group.counts()
        for effect in RetractEffect:
            label = QLabel(t("retract.count", kind=t(f"retract.effect.{effect.value}"), count=counts[effect]))
            fg, bg = _EFFECT_COLORS[effect]
            label.setStyleSheet(f"color: {fg}; background-color: {bg}; border-radius: 4px; padding: 3px 8px;")
            self.counts.append(label)
            strip.addWidget(label)
        strip.addStretch()
        layout.addLayout(strip)
        self.left_piece: Optional[QLabel] = None
        if group.new_piece and not group.failed:
            self.left_piece = QLabel(t("retract.left_piece", piece=group.piece_title))
            self.left_piece.setWordWrap(True)
            self.left_piece.setStyleSheet(INFO_BANNER_STYLE)
            layout.addWidget(self.left_piece)
        self.table = self._row_table(group)
        layout.addWidget(self.table, stretch=1)

    @staticmethod
    def _row_table(group: RetractGroup) -> QTableWidget:
        """每個聲部一列（第一欄的 UserRole 是結果分類），最後一列是場次；依結果上色，不能勾也不能改"""
        rows = [(row.effect, row_cells(row)) for row in group.rows] if group.rows else []
        if not group.failed and not group.unfinished:
            rows.append((None, events_cells(group)))
        table = QTableWidget(len(rows), len(_ROW_COLUMNS))
        table.setHorizontalHeaderLabels([t(f"retract.column.{name}") for name in _ROW_COLUMNS])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        for index, (effect, cells) in enumerate(rows):
            fg, bg = _EFFECT_COLORS[effect or RetractEffect.UNTOUCHED]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsEnabled)
                if column == 0:
                    item.setData(Qt.UserRole, effect)
                if column == 2:
                    item.setForeground(QColor(fg))
                    item.setBackground(QColor(bg))
                elif column == 3:
                    item.setForeground(QColor(_GRAY))
                table.setItem(index, column, item)
        table.resizeColumnsToContents()
        return table


class RetractDialog(QDialog):
    """撤回的預覽；按「撤回」時 accept，chosen 是勾了的群組"""

    def __init__(self, preview: RetractPreview, parent: Optional[QWidget] = None):
        """
        Args:
            preview: 撤回的預覽（RetractService.preview）
            parent: 父元件
        """
        super().__init__(parent)
        self._preview = preview
        entry = preview.entry
        self.setWindowTitle(t("retract.title", time=entry.time, operator=entry.operator))
        self.resize(1200, 720)
        layout = QVBoxLayout(self)
        intro = QLabel(t("retract.intro"))
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {_GRAY};")
        layout.addWidget(intro)
        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel(t("retract.groups")))
        self.groups = QListWidget()
        left_layout.addWidget(self.groups)
        pick = QLabel(t("retract.pick"))
        pick.setWordWrap(True)
        pick.setStyleSheet(f"color: {_GRAY};")
        left_layout.addWidget(pick)
        left.setMaximumWidth(320)
        self._pages = QStackedWidget()
        self.pages: List[RetractGroupPage] = []
        open_groups = [group for group in preview.groups if not block_text(group)]
        for group in preview.groups:
            item = QListWidgetItem(f"{group.name}\n→ {group.piece_title}", self.groups)
            if block_text(group):
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
                item.setForeground(QColor(_GRAY))
            else:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if len(open_groups) == 1 else Qt.Unchecked)
            page = RetractGroupPage(group)
            self.pages.append(page)
            self._pages.addWidget(page)
        self.groups.currentRowChanged.connect(self._pages.setCurrentIndex)
        self.groups.itemChanged.connect(self._update)
        splitter.addWidget(left)
        splitter.addWidget(self._pages)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, stretch=1)
        self.missing = QLabel()
        self.missing.setWordWrap(True)
        self.missing.setStyleSheet(ALERT_BANNER_STYLE)
        layout.addWidget(self.missing)
        bottom = QHBoxLayout()
        bottom.addStretch()
        cancel = QPushButton(t("dialog.cancel_btn"))
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        self.confirm = QPushButton()
        self.confirm.clicked.connect(self.accept)
        bottom.addWidget(self.confirm)
        layout.addLayout(bottom)
        if preview.groups:
            self.groups.setCurrentRow(0)
        self._update()

    def chosen(self) -> List[str]:
        """勾了的群組 ID（依預覽的順序）"""
        return [group.group_id for group in self._checked()]

    def _checked(self) -> List[RetractGroup]:
        """勾了的群組"""
        return [
            group for row, group in enumerate(self._preview.groups)
            if not block_text(group) and self.groups.item(row).checkState() == Qt.Checked
        ]

    def _update(self, *_args) -> None:
        """依勾了的群組更新底部的提示與「撤回」按鈕（沒勾就按不下去）"""
        checked = self._checked()
        missing = sum(group.missing for group in checked)
        self.missing.setText(t("retract.missing", count=missing))
        self.missing.setVisible(missing > 0)
        self.confirm.setText(t("retract.confirm", count=len(checked)))
        self.confirm.setEnabled(bool(checked))


def result_text(result: RetractResult) -> str:
    """撤回結束後的說明：做完了或中途停下、撤回後沒有譜的聲部、留下的空曲目、「撤回」寫不進譜庫"""
    if result.error is not None:
        parts = [t("retract.stopped", error=result.error.detail or str(result.error))]
    else:
        parts = [t("retract.done", count=len(result.groups))]
    if result.missing:
        parts.append(t("retract.missing", count=result.missing))
    for piece in result.left_pieces:
        parts.append(t("retract.left_piece", piece=piece))
    if result.log_error is not None:
        parts.append(t("retract.not_logged"))
    return "\n\n".join(parts)
