# -*- coding: utf-8 -*-
"""
發譜預覽（PySide6）

照原型 #63 發譜預覽的變體 B：分步驟「曲目 › 聲部 › 檔案 › 公演 › 確認」，每一步列出全部群組，擋下的群組寫出原因
（發譜前的檢查見 services/publish_service.py）。目前做好「曲目」步：每個群組挑一首 HuaXia 的現有曲目（已封存的不列）
或新建一首，新建的曲名、作曲家、時長（分鐘）與合奏／絲竹帶入預設值（NewHuaXiaPiece.for_group）、都能改；
每次選擇經 Project.set_huaxia_link 立即寫進專案。有未同步值的群組標出哪些值還沒進譜庫。
之後的步驟由後續工單以 add_step 接上；下一步還沒接上時「下一步」停用。

使用範例：
    dialog = PublishPreviewDialog(project, check_groups(project), readiness, parent)
    dialog.exec()
"""
from typing import Callable, Dict, List, Optional

from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QRadioButton, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from core.constants import HUAXIA_DURATION_STEP_MINUTES, HuaXiaEnsemble
from core.locale import t
from core.models import Group, HuaXiaLink, NewHuaXiaPiece, Project
from services.huaxia_client import HuaXiaPiece
from services.publish_service import GroupCheck, PublishReadiness
from ui.instrumentation_table import catalog_change_text

# 步驟的字串鍵，依順序
STEPS = ("publish.step.piece", "publish.step.voices", "publish.step.files", "publish.step.events",
         "publish.step.confirm")
# 時長欄位的上限（分鐘）
_MAX_DURATION_MINUTES = 999
# 擋下的原因、未同步與說明的文字顏色
_BLOCKED_COLOR = "#e74c3c"
_UNSYNCED_COLOR = "#e67e22"
_NOTE_COLOR = "#888888"


def blocked_text(check: GroupCheck) -> str:
    """群組被擋下的原因，一項一行；沒擋下時為空字串"""
    reasons = []
    if check.not_renamed:
        reasons.append(t("publish.group.not_renamed"))
    if check.unfilled:
        reasons.append(t("publish.group.unfilled", voices="、".join(check.unfilled)))
    if check.duplicate_rows:
        rows = "、".join(
            t("publish.group.duplicate_row", row=row + 1, voice=check.group.instruments[row])
            for row in check.duplicate_rows
        )
        reasons.append(t("publish.group.duplicates", rows=rows))
    return "\n".join(reasons)


def _piece_text(piece: HuaXiaPiece) -> str:
    """現有曲目選單的一項：曲名，有作曲家時附在後面"""
    return t("publish.piece.with_composer", title=piece.title, composer=piece.composer) if piece.composer \
        else piece.title


class _PieceEditor(QWidget):
    """一個群組對到哪首 HuaXia 曲目：挑選現有的或新建；每次改動經 on_change 交出新的對應"""

    def __init__(
        self, group: Group, pieces: List[HuaXiaPiece], on_change: Callable[[Optional[HuaXiaLink]], None],
        parent=None,
    ):
        """
        Args:
            group: 群組（顯示它目前的對應）
            pieces: 可以挑的現有曲目
            on_change: 選擇改變時呼叫，帶入新的對應
        """
        super().__init__(parent)
        self._group = group
        self._pieces = pieces
        self._on_change = on_change
        self._filling = True
        self._build_ui()
        self._show(group.huaxia_link)
        self._filling = False

    def _build_ui(self):
        """排出「挑選現有」「新建」、現有曲目選單與新建的欄位"""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        self._existing = QRadioButton(t("publish.piece.existing"))
        self._new = QRadioButton(t("publish.piece.new"))
        choice = QButtonGroup(self)
        for button in (self._existing, self._new):
            choice.addButton(button)
            button.clicked.connect(self._changed)
            layout.addWidget(button)
        self._piece_combo = QComboBox()
        self._piece_combo.setObjectName("existing_pieces")
        for piece in self._pieces:
            self._piece_combo.addItem(_piece_text(piece), piece.id)
        self._piece_combo.currentIndexChanged.connect(self._changed)
        layout.addWidget(self._piece_combo)
        self._new_fields = QWidget()
        fields = QHBoxLayout(self._new_fields)
        fields.setContentsMargins(0, 0, 0, 0)
        self._title = QLineEdit()
        self._composer = QLineEdit()
        self._duration = QDoubleSpinBox()
        self._duration.setRange(0, _MAX_DURATION_MINUTES)
        self._duration.setDecimals(1)
        self._duration.setSingleStep(HUAXIA_DURATION_STEP_MINUTES)
        self._duration.setSpecialValueText(" ")
        self._duration.setKeyboardTracking(False)
        self._ensemble = QComboBox()
        for ensemble in HuaXiaEnsemble:
            self._ensemble.addItem(t(f"huaxia.ensemble.{ensemble.value}"), ensemble.value)
        for label, widget in (("publish.piece.title", self._title), ("publish.piece.composer", self._composer),
                              ("publish.piece.duration", self._duration), ("publish.piece.ensemble", self._ensemble)):
            fields.addWidget(QLabel(t(label)))
            fields.addWidget(widget)
        self._title.editingFinished.connect(self._changed)
        self._composer.editingFinished.connect(self._changed)
        self._duration.valueChanged.connect(self._changed)
        self._ensemble.currentIndexChanged.connect(self._changed)
        layout.addWidget(self._new_fields)
        layout.addStretch()

    def _show(self, link: Optional[HuaXiaLink]):
        """顯示目前的對應：新建的填欄位，現有的選到那首（已不在可挑的清單時不選），沒對到時兩者都不選"""
        new = (link.new_piece if link else None) or NewHuaXiaPiece.for_group(self._group)
        self._title.setText(new.title)
        self._composer.setText(new.composer)
        self._duration.setValue(new.duration_min or 0)
        self._ensemble.setCurrentIndex(self._ensemble.findData(new.ensemble.value))
        index = self._piece_combo.findData(link.piece_id) if link and not link.new_piece else -1
        self._piece_combo.setCurrentIndex(index)
        self._existing.setChecked(link is not None and link.new_piece is None)
        self._new.setChecked(link is not None and link.new_piece is not None)
        self._existing.setEnabled(bool(self._pieces))
        self._show_fields()

    def _show_fields(self):
        """只顯示選了的那一種的欄位"""
        self._piece_combo.setVisible(not self._new.isChecked())
        self._new_fields.setVisible(self._new.isChecked())

    def _changed(self, *_args):
        """選擇或欄位改了：交出新的對應"""
        if self._filling:
            return
        self._show_fields()
        if self._new.isChecked():
            piece = NewHuaXiaPiece(
                self._title.text().strip(), self._composer.text().strip(), self._duration.value() or None,
                HuaXiaEnsemble(self._ensemble.currentData()),
            )
            self._on_change(HuaXiaLink(piece_title=piece.title, new_piece=piece))
        elif self._existing.isChecked() and self._piece_combo.currentIndex() >= 0:
            piece = self._pieces[self._piece_combo.currentIndex()]
            self._on_change(HuaXiaLink(piece_id=piece.id, piece_title=piece.title))


class PublishPreviewDialog(QDialog):
    """發譜預覽：分步驟列出全部群組，「曲目」步挑選或新建 HuaXia 曲目"""

    def __init__(
        self, project: Project, checks: List[GroupCheck], readiness: PublishReadiness, parent=None,
    ):
        """
        Args:
            project: 專案；「曲目」步的選擇經它的編輯操作寫入
            checks: 各群組發譜前的檢查結果（check_groups）
            readiness: 按下發譜時 HuaXia 那一側的檢查結果（可以挑的曲目、登入的 Discord 帳號）
            parent: 父元件
        """
        super().__init__(parent)
        self._project = project
        self._checks = checks
        self._readiness = readiness
        self._pages: Dict[int, QWidget] = {}
        self.setWindowTitle(t("publish.preview.title", count=len(checks)))
        self.resize(1100, 600)
        self._build_ui()
        self.add_step(0, self._build_piece_step())
        self._go(0)

    def _build_ui(self):
        """排出檢查結果的一行、步驟列、各步的頁面與底部按鈕"""
        layout = QVBoxLayout(self)
        checked = QLabel(t("publish.preview.checked", name=self._readiness.discord_name))
        checked.setStyleSheet(f"color: {_NOTE_COLOR};")
        layout.addWidget(checked)
        steps = QHBoxLayout()
        self._step_labels = []
        for index, key in enumerate(STEPS):
            label = QLabel(t("publish.step.numbered", number=index + 1, name=t(key)))
            self._step_labels.append(label)
            steps.addWidget(label)
            if index < len(STEPS) - 1:
                steps.addWidget(QLabel("›"))
        steps.addStretch()
        layout.addLayout(steps)
        self._stack = QStackedWidget()
        layout.addWidget(self._stack, stretch=1)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self._back = QPushButton(t("publish.back"))
        self._back.clicked.connect(lambda: self._go(self._current - 1))
        buttons.addWidget(self._back)
        self._next = QPushButton(t("publish.next"))
        self._next.clicked.connect(lambda: self._go(self._current + 1))
        buttons.addWidget(self._next)
        cancel = QPushButton(t("dialog.cancel_btn"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    def add_step(self, index: int, page: QWidget):
        """接上第 index 步（STEPS 的順序）的頁面"""
        self._pages[index] = page
        self._stack.addWidget(page)

    def _go(self, index: int):
        """切到第 index 步：步驟列標出目前這步，還沒接上頁面的步驟變灰"""
        self._current = index
        self._stack.setCurrentWidget(self._pages[index])
        for step, label in enumerate(self._step_labels):
            if step == index:
                label.setStyleSheet("font-weight: bold;")
            else:
                label.setStyleSheet(f"color: {_NOTE_COLOR};")
        self._back.setEnabled(index - 1 in self._pages)
        self._next.setEnabled(index + 1 in self._pages)

    def _build_piece_step(self) -> QWidget:
        """「曲目」步：每個群組一列，擋下的寫出原因，其餘挑選現有或新建；有未同步的值時標出"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        table = QTableWidget(len(self._checks), 2)
        table.setObjectName("piece_step")
        table.setHorizontalHeaderLabels([t("publish.piece.group"), t("publish.piece.huaxia")])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        for row, check in enumerate(self._checks):
            table.setItem(row, 0, QTableWidgetItem(check.group.name))
            table.setCellWidget(row, 1, self._piece_cell(check))
        table.resizeRowsToContents()
        layout.addWidget(table)
        note = QLabel(t("publish.piece.new_note"))
        note.setStyleSheet(f"color: {_NOTE_COLOR};")
        layout.addWidget(note)
        return page

    def _piece_cell(self, check: GroupCheck) -> QWidget:
        """一個群組那一列的右欄：擋下的原因或曲目的選擇，加上未同步的值"""
        cell = QWidget()
        layout = QVBoxLayout(cell)
        layout.setContentsMargins(2, 2, 2, 2)
        if check.blocked:
            reason = QLabel(blocked_text(check))
            reason.setObjectName("blocked_reason")
            reason.setStyleSheet(f"color: {_BLOCKED_COLOR};")
            layout.addWidget(reason)
        else:
            group = check.group
            layout.addWidget(_PieceEditor(
                group, self._readiness.pieces, lambda link, g=group: self._project.set_huaxia_link(g, link),
            ))
        if check.unsynced:
            items = "、".join(catalog_change_text(change) for change in check.unsynced)
            unsynced = QLabel(t("publish.group.unsynced", items=items))
            unsynced.setObjectName("unsynced")
            unsynced.setStyleSheet(f"color: {_UNSYNCED_COLOR};")
            layout.addWidget(unsynced)
        return cell
