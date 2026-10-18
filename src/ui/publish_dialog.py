# -*- coding: utf-8 -*-
"""
發譜預覽（PySide6）

照原型 #63 發譜預覽的變體 B：分步驟「曲目 › 聲部 › 檔案 › 公演 › 確認」，每一步列出全部群組，擋下的群組寫出原因
（發譜前的檢查見 services/publish_service.py）。目前做好前兩步：

- 「曲目」：每個群組挑一首 HuaXia 的現有曲目（已封存的不列）或新建一首，新建的曲名、作曲家、時長（分鐘）與
  合奏／絲竹帶入預設值（NewHuaXiaPiece.for_group）、都能改；每次選擇經 Project.set_huaxia_link 立即寫進專案。
  有未同步值的群組標出哪些值還沒進譜庫。每個沒擋下的群組都對到曲目（新建的要有曲名）才能按「下一步」。
- 「聲部」：每個群組列出樂器表每一列對到 HuaXia 的哪個聲部（publish_service.plan_voices）、HuaXia 現在的值、
  變動與發譜後的值，以及只在 HuaXia 的聲部（不動）。本專案有的聲部可以改選新建或還沒被配對的 HuaXia 聲部
  （名稱規則對到多個時只列那幾個），挑了就經 Project.choose_huaxia_part 記進專案；人數或聲部組不同時「覆寫」
  預設勾選；本專案沒有、HuaXia 也沒有的聲部預設不建，可以勾「在 HuaXia 建」。有衝突、還沒挑或要建的聲部
  沒填最少人數時寫出原因，「下一步」按不下去。覆寫與建立的勾選只在這次預覽，不記在專案。

之後的步驟由後續工單以 add_step 接上；下一步還沒接上時「下一步」停用。

使用範例：
    dialog = PublishPreviewDialog(project, check_groups(project), readiness, parent)
    dialog.exec()
"""
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPushButton, QRadioButton, QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from core.constants import HUAXIA_DURATION_STEP_MINUTES, HuaXiaEnsemble
from core.locale import t
from core.models import Group, HuaXiaLink, NewHuaXiaPiece, Project
from core.sections import section_label
from services.huaxia_client import HuaXiaPart, HuaXiaPiece
from services.publish_service import (
    GroupCheck, PartMatch, PublishReadiness, VoiceDecisions, VoicePlan, VoiceRow, plan_voices,
)
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


def voices_problem_text(plan: VoicePlan) -> str:
    """群組在「聲部」步還不能發的原因，一項一行；沒有時為空字串"""
    reasons = [
        t("publish.voices.conflict", part=part.name, voices="、".join(voices)) for part, voices in plan.conflicts
    ]
    if plan.undecided:
        reasons.append(t("publish.voices.undecided", voices="、".join(plan.undecided)))
    if plan.unfilled:
        reasons.append(t("publish.voices.unfilled", voices="、".join(plan.unfilled)))
    return "\n".join(reasons)


def _headcount_section(needed: str, section: str) -> str:
    """人數記法與聲部組的一格：「6-8 人・拉弦」；人數空白時寫「？」，沒有聲部組時寫明"""
    return t(
        "publish.voices.headcount_section", needed=needed or "？",
        section=section_label(section) if section else t("publish.voices.no_section"),
    )


def _part_text(part: HuaXiaPart) -> str:
    """HuaXia 聲部現在的值：名稱、人數記法、聲部組"""
    return t("publish.voices.part", name=part.name, needed=part.needed,
             section=part.section or t("publish.voices.no_section"))


def _voice_texts(row: VoiceRow) -> List[str]:
    """樂器表一列在「聲部」步的前四欄：聲部、HuaXia 現在、變動、發譜後"""
    if row.part is not None:
        now = _part_text(row.part)
    else:
        now = t("publish.voices.several") if row.match is PartMatch.AMBIGUOUS else t("publish.voices.none")
    if not row.present:
        change = t("publish.voices.absent")
    elif row.match is PartMatch.AMBIGUOUS and row.part is None:
        change = t("publish.voices.several")
    elif row.part is None:
        change = t("publish.voices.missing")
    elif row.headcount_differs and row.section_differs:
        change = t("publish.voices.headcount_and_section_differ")
    elif row.headcount_differs:
        change = t("publish.voices.headcount_differs")
    elif row.section_differs:
        change = t("publish.voices.section_differs")
    else:
        change = t("publish.voices.same")
    if row.create:
        after = t("publish.voices.create", needed=row.headcount.text() or "？", section=section_label(row.section))
    elif not row.present:
        after = t("publish.voices.untouched") if row.part else t("publish.voices.not_created")
    elif row.part is None:
        after = ""
    elif row.overwrite:
        after = _headcount_section(
            row.headcount.text() if row.headcount_differs else row.part.needed,
            row.section if row.section_differs else row.part.section,
        )
    else:
        after = t("publish.voices.unchanged")
    return [row.voice, now, change, after]


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
        self._current = 0
        # 開啟預覽時各群組對到的曲目：改選別首再選回來時找回手動挑的聲部配對
        self._opened_links = {check.group.id: check.group.huaxia_link for check in checks}
        # 各群組這次在「聲部」步的決定，以群組 ID 為鍵；改選曲目時清掉
        self._decisions: Dict[str, VoiceDecisions] = {}
        self._voice_boxes: Dict[str, QGroupBox] = {}
        self.setWindowTitle(t("publish.preview.title", count=len(checks)))
        self.resize(1100, 600)
        self._build_ui()
        self.add_step(0, self._build_piece_step())
        self.add_step(1, self._build_voices_step())
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
        self._refresh_buttons()

    def voice_plan(self, group: Group) -> VoicePlan:
        """群組目前的聲部配對與要寫的內容（依「曲目」步對到的曲目與「聲部」步的選擇）；群組要已對到曲目"""
        link = group.huaxia_link
        parts = self._readiness.parts.get(link.piece_id, []) if link.new_piece is None else []
        return plan_voices(self._project, group, parts, self._decisions.setdefault(group.id, VoiceDecisions()))

    def _go(self, index: int):
        """切到第 index 步：步驟列標出目前這步，還沒接上頁面的步驟變灰；「聲部」步依目前對到的曲目重排"""
        self._current = index
        if index == 1:
            self._show_voices()
        self._stack.setCurrentWidget(self._pages[index])
        for step, label in enumerate(self._step_labels):
            if step == index:
                label.setStyleSheet("font-weight: bold;")
            else:
                label.setStyleSheet(f"color: {_NOTE_COLOR};")
        self._refresh_buttons()

    def _refresh_buttons(self):
        """「上一步」要有上一步的頁面；「下一步」還要這一步都決定好了"""
        self._back.setEnabled(self._current - 1 in self._pages)
        self._next.setEnabled(self._current + 1 in self._pages and self._step_ready(self._current))

    def _step_ready(self, index: int) -> bool:
        """第 index 步是否都決定好了：「曲目」要每個沒擋下的群組都對到曲目，「聲部」要沒有群組還不能發"""
        groups = [check.group for check in self._checks if not check.blocked]
        if index == 0:
            return all(self._has_piece(group) for group in groups)
        if index == 1:
            return not any(self.voice_plan(group).blocked for group in groups)
        return True

    def _has_piece(self, group: Group) -> bool:
        """群組已對到曲目：可以挑的現有曲目，或有曲名的新建曲目"""
        link = group.huaxia_link
        if link is None:
            return False
        if link.new_piece is not None:
            return bool(link.new_piece.title.strip())
        return any(piece.id == link.piece_id for piece in self._readiness.pieces)

    def _set_piece(self, group: Group, link: Optional[HuaXiaLink]):
        """「曲目」步的選擇改了：寫進專案；選回同一首現有曲目時沿用手動挑的聲部配對，換了曲目就清掉這次的聲部決定"""
        previous = group.huaxia_link
        if link is not None and link.new_piece is None:
            for before in (previous, self._opened_links.get(group.id)):
                if before is not None and before.new_piece is None and before.piece_id == link.piece_id:
                    link.part_ids = dict(before.part_ids)
                    break
        if _piece_identity(previous) != _piece_identity(link):
            self._decisions.pop(group.id, None)
        self._project.set_huaxia_link(group, link)
        self._refresh_buttons()

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
                group, self._readiness.pieces, lambda link, g=group: self._set_piece(g, link),
            ))
        if check.unsynced:
            items = "、".join(catalog_change_text(change) for change in check.unsynced)
            unsynced = QLabel(t("publish.group.unsynced", items=items))
            unsynced.setObjectName("unsynced")
            unsynced.setStyleSheet(f"color: {_UNSYNCED_COLOR};")
            layout.addWidget(unsynced)
        return cell

    def _build_voices_step(self) -> QWidget:
        """「聲部」步的頁面：每個群組一塊，切到這一步時才排（_show_voices）"""
        scroll = QScrollArea()
        scroll.setObjectName("voices_step")
        scroll.setWidgetResizable(True)
        content = QWidget()
        self._voices_layout = QVBoxLayout(content)
        self._voices_layout.addStretch()
        scroll.setWidget(content)
        return scroll

    def _show_voices(self):
        """重排「聲部」步的每個群組"""
        for box in self._voice_boxes.values():
            _discard(box)
        self._voice_boxes = {}
        for check in self._checks:
            box = self._voices_box(check)
            self._voice_boxes[check.group.id] = box
            self._voices_layout.insertWidget(self._voices_layout.count() - 1, box)

    def _refresh_voices(self, check: GroupCheck):
        """一個群組在「聲部」步的選擇改了：重排那一塊，更新「下一步」"""
        old = self._voice_boxes[check.group.id]
        box = self._voices_box(check)
        self._voices_layout.insertWidget(self._voices_layout.indexOf(old), box)
        self._voice_boxes[check.group.id] = box
        _discard(old)
        self._refresh_buttons()

    def _voices_box(self, check: GroupCheck) -> QGroupBox:
        """一個群組在「聲部」步的那一塊：擋下的寫原因，其餘是還不能發的原因與聲部對照表"""
        box = QGroupBox(check.group.name)
        box.setObjectName("voices_group")
        layout = QVBoxLayout(box)
        if check.blocked:
            reason = QLabel(blocked_text(check))
            reason.setObjectName("blocked_reason")
            reason.setStyleSheet(f"color: {_BLOCKED_COLOR};")
            layout.addWidget(reason)
            return box
        plan = self.voice_plan(check.group)
        problem = QLabel(voices_problem_text(plan))
        problem.setObjectName("voices_problem")
        problem.setStyleSheet(f"color: {_BLOCKED_COLOR};")
        problem.setVisible(plan.blocked)
        layout.addWidget(problem)
        layout.addWidget(self._voices_table(check, plan))
        return box

    def _voices_table(self, check: GroupCheck, plan: VoicePlan) -> QTableWidget:
        """聲部對照表：樂器表每一列，接著只在 HuaXia 的聲部（灰色、不動）；表格高度剛好放下所有列"""
        table = QTableWidget(len(plan.rows) + len(plan.huaxia_only), 5)
        table.setObjectName("voices_table")
        table.setHorizontalHeaderLabels([
            t("publish.voices.voice"), t("publish.voices.now"), t("publish.voices.change"),
            t("publish.voices.after"), t("publish.voices.decision"),
        ])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        for index, row in enumerate(plan.rows):
            for column, text in enumerate(_voice_texts(row)):
                table.setItem(index, column, QTableWidgetItem(text))
            decision = self._decision_cell(check, row)
            if decision is not None:
                table.setCellWidget(index, 4, decision)
        for index, part in enumerate(plan.huaxia_only, start=len(plan.rows)):
            texts = (part.name, _part_text(part), t("publish.voices.huaxia_only"), t("publish.voices.untouched"))
            for column, text in enumerate(texts):
                item = QTableWidgetItem(text)
                item.setForeground(QColor(_NOTE_COLOR))
                table.setItem(index, column, item)
        table.resizeRowsToContents()
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        height = table.horizontalHeader().sizeHint().height() + 2 * table.frameWidth()
        table.setFixedHeight(height + sum(table.rowHeight(r) for r in range(table.rowCount())))
        return table

    def _decision_cell(self, check: GroupCheck, row: VoiceRow) -> Optional[QWidget]:
        """一列的「決定」欄：本專案有的挑對到哪個聲部（有差異時加「覆寫」），本專案沒有、HuaXia 也沒有的問要不要建"""
        cell = QWidget()
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(2, 0, 2, 0)
        if row.present:
            combo = QComboBox()
            combo.setObjectName("part_choice")
            combo.setPlaceholderText(t("publish.voices.pick"))
            combo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
            if row.can_create:
                combo.addItem(t("publish.voices.new", voice=row.voice), None)
            for part in row.options:
                combo.addItem(part.name, part.id)
            combo.setCurrentIndex(combo.findData(row.part.id) if row.part else (0 if row.can_create else -1))
            combo.currentIndexChanged.connect(
                lambda i, c=combo: self._choose_part(check, row, c.itemData(i)),
            )
            layout.addWidget(combo)
            if row.headcount_differs or row.section_differs:
                overwrite = QCheckBox(t("publish.voices.overwrite"))
                overwrite.setObjectName("overwrite")
                overwrite.setChecked(row.overwrite)
                overwrite.toggled.connect(lambda on: self._decide(check, row.index, "keep", not on))
                layout.addWidget(overwrite)
        elif row.part is None and row.match is not PartMatch.AMBIGUOUS:
            create = QCheckBox(t("publish.voices.create_absent"))
            create.setObjectName("create_absent")
            create.setChecked(row.create)
            create.toggled.connect(lambda on: self._decide(check, row.index, "create_absent", on))
            layout.addWidget(create)
        else:
            return None
        layout.addStretch()
        return cell

    def _choose_part(self, check: GroupCheck, row: VoiceRow, part_id: Optional[str]):
        """一個聲部改選了新建或某個 HuaXia 聲部：記進專案（新建是忘掉記過的配對、這次不套名稱規則）"""
        decisions = self._decisions.setdefault(check.group.id, VoiceDecisions())
        if part_id is None:
            decisions.new.add(row.index)
        else:
            decisions.new.discard(row.index)
        self._project.choose_huaxia_part(check.group, row.voice, part_id)
        self._refresh_voices(check)

    def _decide(self, check: GroupCheck, index: int, kind: str, on: bool):
        """勾選改了：kind 是 VoiceDecisions 的欄位（keep、create_absent），on 為加入這一列"""
        rows = getattr(self._decisions.setdefault(check.group.id, VoiceDecisions()), kind)
        if on:
            rows.add(index)
        else:
            rows.discard(index)
        self._refresh_voices(check)


def _piece_identity(link: Optional[HuaXiaLink]):
    """群組對到哪首曲目（新建的都算同一首）；沒對到時為 None"""
    if link is None:
        return None
    return "new" if link.new_piece is not None else link.piece_id


def _discard(widget: QWidget):
    """把元件從畫面拿掉並在之後刪除（可能正在處理它裡面元件的訊號，所以不立即刪除）"""
    widget.hide()
    widget.setParent(None)
    widget.deleteLater()
