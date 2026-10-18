# -*- coding: utf-8 -*-
"""
存入紀錄與撤銷存入的對話框（原型 #65 撤銷存入的變體 A）

DepositLogDialog 列出動到這個版本的每次存入與撤銷存入（新的在前），選一次存入按「撤銷存入…」。
DepositRevertDialog 是撤銷的預覽：左邊勾群組（寫出連到哪裡；撤銷會毀掉唯一一份的群組不能勾），右邊是所選群組的頁面——
不撤銷時一列紅色說明原因，上方各下場的份數
（丟進 Drive 垃圾桶、還原存入前的內容、移除登記、無法還原、不動），本機已清除時一列藍色提示寫明會先下載回原位並核對；
中間是逐聲部的「現在譜庫裡 → 撤銷後 → 原因」；下面是「一起撤掉這個群組寫進某處的編制」勾選框加四欄編制清單
（ui/catalog_revert_dialog.py 的 revert_table，與改連共用），預設不勾；底部註明移除的檔放進 Drive 垃圾桶、30 天內可救回。
只顯示預覽（core/deposit_revert.py 的 RevertPreview），按「撤銷存入」關閉對話框，撤銷由主視窗在背景執行。

使用範例：
    log = DepositLogDialog(deposits(entries, edition_id), parent=window)
    if log.exec() == QDialog.Accepted:
        deposit = log.chosen()
    dialog = DepositRevertDialog(preview, parent=window)
    if dialog.exec() == QDialog.Accepted:
        choices = dialog.choices()
    QMessageBox.warning(window, title, skipped_text(outcome))   # 撤銷後，有沒撤銷的群組時
"""
import os
from typing import List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QSplitter,
    QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.catalog_errors import CATALOG_ERROR_RULES
from core.catalog_revert import revert_count
from core.deposit_revert import (
    Deposit, RevertAction, RevertBlock, RevertChoice, RevertFile, RevertGroup, RevertPreview,
)
from core.locale import t
from ui.catalog_revert_dialog import revert_table
from services.deposit_revert_service import RevertOutcome
from ui.instrumentation_table import place_text
from ui.widgets import not_downloaded_text

# 各下場的（字色，底色），同原型：丟進垃圾桶紅、還原與移除登記藍、無法還原黃、不動灰
_ACTION_COLORS = {
    RevertAction.TRASH: ("#ff8a8a", "#5a2129"),
    RevertAction.RESTORE: ("#8fb8ff", "#253c63"),
    RevertAction.UNREGISTER: ("#8fb8ff", "#253c63"),
    RevertAction.LOST: ("#f2c14e", "#5b4613"),
    RevertAction.KEEP: ("#7a7f93", "#24262f"),
}
_GRAY = "#7a7f93"
_DOWNLOAD_STYLE = "background-color: #253c63; color: #8fb8ff; border-radius: 5px; padding: 6px 10px;"
_BLOCK_STYLE = "background-color: #5a2129; color: #ff8a8a; border-radius: 5px; padding: 6px 10px;"
# 存入紀錄表的欄位（介面字串鍵的結尾）
_LOG_COLUMNS = ("time", "operator", "kind", "groups", "count")
# 撤銷預覽對照表的欄位（介面字串鍵的結尾）
_FILE_COLUMNS = ("voice", "now", "after", "reason")


def group_place(group: RevertGroup) -> str:
    """群組連到哪裡（曲目 › 版本 › 樂章）；那處已不在譜庫時寫出來"""
    if group.place is None:
        return t("revert.place_gone")
    place = group.place
    return place_text(place.piece_title, place.edition_label, place.movement_id, place.movement_name)


def count_texts(group: RevertGroup) -> List[Tuple[RevertAction, str]]:
    """群組頁上方的份數：依下場的順序，沒有的不列"""
    counts = group.counts()
    return [
        (action, t("revert.count", kind=t(f"revert.action.{action.value}"), count=counts[action]))
        for action in RevertAction if counts.get(action)
    ]


def block_text(group: RevertGroup) -> str:
    """群組不撤銷的原因；可以撤銷時為空字串"""
    if group.block is RevertBlock.NOT_OPEN:
        return t("revert.block.not_open")
    if group.block is RevertBlock.NO_LOCAL_COPY:
        return t("revert.block.no_local_copy", files="、".join(_names(group.uncovered)))
    return ""


def _names(files: Sequence[RevertFile]) -> List[str]:
    """檔的本機檔名"""
    return [os.path.basename(file.entry.local_path) for file in files]


def file_cells(file: RevertFile) -> Tuple[str, str, str, str]:
    """對照表的一列：聲部、現在譜庫裡、撤銷後、原因（下場：為什麼）"""
    name = file.file.name if file.file is not None else ""
    now = name or t("revert.now.missing")
    if file.action is RevertAction.TRASH:
        after = t("revert.after.trash")
    elif file.action is RevertAction.RESTORE:
        after = t("revert.after.restore", name=name)
    elif file.action is RevertAction.UNREGISTER:
        after = t("revert.after.unregister", name=name)
    else:
        after = t("revert.after.keep")
    if file.action is RevertAction.KEEP:
        why = t(f"revert.reason.{file.kept.value}", when=file.later.time if file.later else "")
    else:
        why = t(f"revert.reason.{file.action.value}")
    return file.entry.voice, now, after, t("revert.reason", kind=t(f"revert.action.{file.action.value}"), why=why)


class DepositLogDialog(QDialog):
    """存入紀錄：選一次存入按「撤銷存入…」時 accept，chosen 是選的那次"""

    def __init__(self, history: Sequence[Deposit], parent: Optional[QWidget] = None):
        """
        Args:
            history: 動到這個版本的每次存入與撤銷存入，新的在前（core/deposit_revert.py 的 deposits）
            parent: 父元件
        """
        super().__init__(parent)
        self._history = list(history)
        self.setWindowTitle(t("deposit_log.title"))
        self.resize(900, 480)
        layout = QVBoxLayout(self)
        if not self._history:
            layout.addWidget(QLabel(t("deposit_log.empty")))
        times = {deposit.deposit_id: deposit.time for deposit in self._history}
        self.table = QTableWidget(len(self._history), len(_LOG_COLUMNS))
        self.table.setHorizontalHeaderLabels([t(f"deposit_log.column.{name}") for name in _LOG_COLUMNS])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        for row, deposit in enumerate(self._history):
            kind = (
                t("deposit_log.revert", time=times.get(deposit.reverts, deposit.reverts)) if deposit.reverts
                else t("deposit_log.deposit")
            )
            cells = [deposit.time, deposit.operator, kind, "、".join(deposit.groups), str(len(deposit.entries))]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if deposit.reverts:
                    item.setForeground(QColor(_GRAY))
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()
        layout.addWidget(self.table, stretch=1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        close = QPushButton(t("deposit_log.close"))
        close.clicked.connect(self.reject)
        buttons.addWidget(close)
        self.revert_button = QPushButton(t("revert.button"))
        self.revert_button.setEnabled(False)
        self.revert_button.clicked.connect(self.accept)
        buttons.addWidget(self.revert_button)
        layout.addLayout(buttons)
        self.table.itemSelectionChanged.connect(self._on_selected)

    def chosen(self) -> Optional[Deposit]:
        """選的那次存入；沒選或選的是撤銷存入時為 None"""
        rows = {index.row() for index in self.table.selectedIndexes()}
        if len(rows) != 1:
            return None
        deposit = self._history[rows.pop()]
        return None if deposit.reverts else deposit

    def _on_selected(self) -> None:
        """選了存入才能按「撤銷存入…」（撤銷存入本身不能再撤）"""
        self.revert_button.setEnabled(self.chosen() is not None)


class RevertGroupPage(QWidget):
    """撤銷預覽右邊一個群組的頁面：各下場的份數、要先下載的提示、對照表與一起撤掉編制"""

    def __init__(self, group: RevertGroup, parent: Optional[QWidget] = None):
        """
        Args:
            group: 預覽裡的群組
            parent: 父元件
        """
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        heading = QLabel(f"{group.name} → {group_place(group)}")
        heading.setStyleSheet("font-weight: bold;")
        layout.addWidget(heading)
        self.block_notice: Optional[QLabel] = None
        if group.block is not None:
            self.block_notice = QLabel(block_text(group))
            self.block_notice.setWordWrap(True)
            self.block_notice.setStyleSheet(_BLOCK_STYLE)
            layout.addWidget(self.block_notice)
        strip = QHBoxLayout()
        self.counts: List[QLabel] = []
        for action, text in count_texts(group):
            label = QLabel(text)
            fg, bg = _ACTION_COLORS[action]
            label.setStyleSheet(f"color: {fg}; background-color: {bg}; border-radius: 4px; padding: 3px 8px;")
            self.counts.append(label)
            strip.addWidget(label)
        strip.addStretch()
        layout.addLayout(strip)
        downloads = group.downloads()
        self.download_notice: Optional[QLabel] = None
        if downloads:
            self.download_notice = QLabel(t("revert.download", group=group.name, count=len(downloads)))
            self.download_notice.setWordWrap(True)
            self.download_notice.setStyleSheet(_DOWNLOAD_STYLE)
            layout.addWidget(self.download_notice)
        self.table = self._file_table(group)
        layout.addWidget(self.table, stretch=1)
        place = (group.place.movement_name or group.place.piece_title) if group.place else group_place(group)
        count = revert_count(group.instrumentation)
        self.instrumentation = QCheckBox(t("revert.instrumentation", place=place, count=count))
        self.instrumentation.setEnabled(count > 0)
        layout.addWidget(self.instrumentation)
        self.instrumentation_table = revert_table(group.instrumentation)
        self.instrumentation_table.setEnabled(False)
        self.instrumentation_table.setVisible(bool(group.instrumentation))
        self.instrumentation.toggled.connect(self.instrumentation_table.setEnabled)
        layout.addWidget(self.instrumentation_table)

    @staticmethod
    def _file_table(group: RevertGroup) -> QTableWidget:
        """每份檔一列：聲部、現在譜庫裡、撤銷後、原因（依下場上色）"""
        table = QTableWidget(len(group.files), len(_FILE_COLUMNS))
        table.setHorizontalHeaderLabels([t(f"revert.column.{name}") for name in _FILE_COLUMNS])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.NoSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        for row, file in enumerate(group.files):
            fg, _ = _ACTION_COLORS[file.action]
            for column, text in enumerate(file_cells(file)):
                item = QTableWidgetItem(text)
                if column >= 2:
                    item.setForeground(QColor(fg))
                table.setItem(row, column, item)
        table.resizeColumnsToContents()
        return table


class DepositRevertDialog(QDialog):
    """撤銷存入的預覽；按「撤銷存入」時 accept，choices 是勾了的群組與要不要一起撤掉編制"""

    def __init__(self, preview: RevertPreview, parent: Optional[QWidget] = None):
        """
        Args:
            preview: 撤銷的預覽（DepositRevertService.preview）
            parent: 父元件
        """
        super().__init__(parent)
        self._preview = preview
        deposit = preview.deposit
        self.setWindowTitle(t("revert.title", time=deposit.time, operator=deposit.operator))
        self.resize(1100, 720)
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        pick = QLabel(t("revert.pick"))
        pick.setStyleSheet(f"color: {_GRAY};")
        left_layout.addWidget(pick)
        self.groups = QListWidget()
        left_layout.addWidget(self.groups)
        left.setMaximumWidth(340)
        self._pages = QStackedWidget()
        self.pages: List[RevertGroupPage] = []
        only = len(preview.groups) == 1
        for group in preview.groups:
            item = QListWidgetItem(f"{group.name}\n{group_place(group)}", self.groups)
            if group.block is None:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if only else Qt.Unchecked)
            else:
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
                item.setForeground(QColor(_GRAY))
            page = RevertGroupPage(group)
            self.pages.append(page)
            self._pages.addWidget(page)
        self.groups.currentRowChanged.connect(self._pages.setCurrentIndex)
        self.groups.itemChanged.connect(self._update_confirm)
        splitter.addWidget(left)
        splitter.addWidget(self._pages)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, stretch=1)
        bottom = QHBoxLayout()
        note = QLabel(t("revert.trash_note"))
        note.setStyleSheet(f"color: {_GRAY};")
        bottom.addWidget(note)
        bottom.addStretch()
        cancel = QPushButton(t("dialog.cancel_btn"))
        cancel.clicked.connect(self.reject)
        bottom.addWidget(cancel)
        self.confirm = QPushButton(t("revert.confirm"))
        self.confirm.clicked.connect(self.accept)
        bottom.addWidget(self.confirm)
        layout.addLayout(bottom)
        if preview.groups:
            self.groups.setCurrentRow(0)
        self._update_confirm()

    def choices(self) -> List[RevertChoice]:
        """勾了的群組，與各自要不要一起撤掉編制（依預覽的順序）"""
        return [
            RevertChoice(group, page.instrumentation.isChecked())
            for row, (group, page) in enumerate(zip(self._preview.groups, self.pages))
            if group.block is None and self.groups.item(row).checkState() == Qt.Checked
        ]

    def _update_confirm(self, *_args) -> None:
        """勾了群組才能按「撤銷存入」"""
        self.confirm.setEnabled(bool(self.choices()))


def skipped_text(outcome: RevertOutcome) -> str:
    """沒撤銷的群組與檔、原因（本機已清除的檔沒有全部下載回來、會毀掉唯一的一份、預覽之後在 Drive 上改過）；
    都撤銷了時為空字串"""
    parts = [
        t("revert.refused", group=group.name, reason=block_text(group) if group.block else t(
            "revert.block.no_local_copy", files="、".join(_names(group.kept_locally())),
        ))
        for group, _ in outcome.refused
    ]
    for group, download in outcome.skipped:
        detail = not_downloaded_text(download)
        if not detail and download.error is not None:
            rule = CATALOG_ERROR_RULES[download.error.kind]
            detail = f"{t(rule.title_key)}：{t(rule.advice_key)}"
        parts.append("\n".join(text for text in (t("revert.skipped", group=group.name), detail) if text))
    if outcome.changed:
        parts.append(t("revert.changed", files="、".join(_names(outcome.changed))))
    return "\n\n".join(parts)
