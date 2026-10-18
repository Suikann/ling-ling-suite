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
- 「檔案」：每個群組列出要發的分譜與總譜、會被取代的那份（同一個聲部底下、泠靈發的，publish_service.plan_files）
  與同聲部別人放的檔（不動）。本機找不到的檔跳過並寫出來，只在譜庫的旁邊放「從譜庫下載回來」（關掉預覽，
  交給主視窗下載回原位）。超過大小上限的檔第一次進這一步時在背景逐份壓縮（壓縮檔在壓縮服務的暫存資料夾，
  原檔不動），底部顯示進度與「取消壓縮」；每份顯示壓縮狀態與「開啟」「比較」（共用的比較畫面）「不發這份」。
- 「公演」：複選本學期、還沒結束的公演（PublishReadiness.concerts），逐場說明掛上後的影響；可以不掛。
- 「確認」：每個群組列出曲目、要新建與覆寫的聲部、要傳的檔數與大小、要取代的份數與不發的檔，加上公演與合計。
  每個群組要寫進 HuaXia 的全部內容由 publish_plan() 交出（publish_service.group_publish），確認步的文字也由它算。
  超過上限的檔都壓縮好（或選了不發）、壓縮做完，「發譜」才按得下去。

關閉預覽時停下還在跑的壓縮並丟掉暫存的壓縮檔。

使用範例：
    dialog = PublishPreviewDialog(project, check_groups(project), readiness, parent, compressor=compressor,
                                  opener=opener, download_back=download_back)
    dialog.exec()
"""
import os
from typing import Callable, Dict, List, Optional, Sequence, Set

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup, QCheckBox, QComboBox, QDialog, QDoubleSpinBox, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMessageBox, QPushButton, QRadioButton, QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from core.constants import (
    HUAXIA_DURATION_STEP_MINUTES, MAX_DURATION_MINUTES, UNSYNCED_COLOR, CompressionStatus, HuaXiaEnsemble,
)
from core.locale import t
from core.models import Group, HuaXiaLink, NewHuaXiaPiece, Project
from core.sections import section_label
from services.compression_service import CompressionResult, CompressionService
from services.external_opener import ExternalOpener
from services.file_service import FileService
from services.huaxia_client import HuaXiaEvent, HuaXiaPart, HuaXiaPiece, HuaXiaScore
from services.publish_service import (
    FilePlan, GroupCheck, GroupPublish, PartMatch, PublishFile, PublishReadiness, VoiceDecisions, VoicePlan, VoiceRow,
    group_publish, oversized, plan_files, plan_voices,
)
from ui.background_task import BackgroundTask
from ui.compress_pdf import result_state, size_text
from ui.compression_compare import CompressionCompareDialog
from ui.instrumentation_table import catalog_change_text

# 步驟的字串鍵，依順序
STEPS = ("publish.step.piece", "publish.step.voices", "publish.step.files", "publish.step.events",
         "publish.step.confirm")
# 擋下的原因、說明、取代與新增的文字顏色（未同步的是 UNSYNCED_COLOR）
_BLOCKED_COLOR = "#e74c3c"
_NOTE_COLOR = "#888888"
_REPLACE_COLOR = "#3498db"
_NEW_COLOR = "#27ae60"
# 「檔案」步表格的欄數（聲部、這次發的檔、大小、取代、其他人放的檔、壓縮）
_FILE_COLUMNS = 6


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


def _score_titles(scores: List[HuaXiaScore]) -> str:
    """樂譜的名稱，以頓號分隔；沒有名稱的用 Storage 裡的檔名"""
    return "、".join(score.title or os.path.basename(score.file_path) for score in scores)


def _file_voice(file: PublishFile) -> str:
    """一份檔在表格裡的聲部欄：聲部名稱，總譜寫「總譜」"""
    return t("publish.files.score") if file.score else file.voice


def _event_text(event: HuaXiaEvent) -> str:
    """一場公演：名稱、開始時間（這台電腦的時區）、地點（有時）"""
    texts = [event.title, event.starts_at.astimezone().strftime("%Y-%m-%d %H:%M"), event.location]
    return "　".join(text for text in texts if text)


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
        self._duration.setRange(0, MAX_DURATION_MINUTES)
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
    """發譜預覽：分步驟列出全部群組，從挑選 HuaXia 曲目到確認所有會寫進 HuaXia 的變動"""

    def __init__(
        self, project: Project, checks: List[GroupCheck], readiness: PublishReadiness, parent=None,
        compressor: Optional[CompressionService] = None, file_service: Optional[FileService] = None,
        opener: Optional[ExternalOpener] = None, download_back: Optional[Callable[[Sequence[str]], None]] = None,
    ):
        """
        Args:
            project: 專案；「曲目」步的選擇經它的編輯操作寫入
            checks: 各群組發譜前的檢查結果（check_groups）
            readiness: 按下發譜時 HuaXia 那一側的檢查結果（可以挑的曲目、它們的聲部與樂譜、可以掛的公演、
                登入的 Discord 帳號）
            parent: 父元件
            compressor: 壓縮超過大小上限的檔（它的大小上限就是發譜的單檔上限）；省略時以預設的暫存資料夾建立
            file_service: 讀本機檔案大小；省略時建立一個
            opener: 以預設的 PDF 閱讀器開啟壓縮檔；省略時依目前的平台建立
            download_back: 「從譜庫下載回來」：關掉預覽後以群組 ID 呼叫；省略時不顯示這個按鈕
        """
        super().__init__(parent)
        self._project = project
        self._checks = checks
        self._readiness = readiness
        self._file_service = file_service or FileService()
        self._compressor = compressor or CompressionService(self._file_service)
        self._opener = opener or ExternalOpener()
        self._download_back = download_back
        # 壓縮：結果以本機檔的路徑為鍵；正在壓的那份；背景工作（第一次進「檔案」步時開始，一次壓完全部）
        self._compressed: Dict[str, CompressionResult] = {}
        self._compressing: Optional[str] = None
        self._compress_task: Optional[BackgroundTask] = None
        self._compress_started = False
        # 選了「不發這份」的本機檔路徑；勾選要掛的公演的編號
        self._dropped: Set[str] = set()
        self._chosen_events: Set[str] = set()
        self._file_boxes: List[QWidget] = []
        self._confirm_widgets: List[QWidget] = []
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
        self.add_step(2, self._build_files_step())
        self.add_step(3, self._build_events_step())
        self.add_step(4, self._build_confirm_step())
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
        self._compress_status = QLabel()
        self._compress_status.setObjectName("compression_status")
        self._compress_status.setVisible(False)
        buttons.addWidget(self._compress_status)
        self._compress_cancel = QPushButton(t("publish.files.cancel"))
        self._compress_cancel.clicked.connect(self._cancel_compression)
        self._compress_cancel.setVisible(False)
        buttons.addWidget(self._compress_cancel)
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
        self._publish = QPushButton(t("publish.publish"))
        self._publish.setDefault(True)
        self._publish.clicked.connect(self.accept)
        buttons.addWidget(self._publish)
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

    def file_plan(self, group: Group) -> FilePlan:
        """群組要發的檔與各自要取代的（依目前的聲部配對）；群組要已對到曲目"""
        link = group.huaxia_link
        scores = self._readiness.scores.get(link.piece_id, []) if link.new_piece is None else []
        return plan_files(group, self.voice_plan(group), scores, self._file_service)

    def publish_plan(self) -> List[GroupPublish]:
        """每個沒擋下的群組這次發譜要寫進 HuaXia 的全部內容（依預覽目前的選擇），依專案中的順序"""
        events = [event for event in self._readiness.concerts if event.id in self._chosen_events]
        return [
            group_publish(
                check.group, self.voice_plan(check.group), self.file_plan(check.group), self._compressed,
                self._dropped, events, self._compressor.size_limit,
            )
            for check in self._checks if not check.blocked
        ]

    def done(self, code: int):
        """關閉預覽：停下還在跑的壓縮（之後它不再碰這個對話框），丟掉暫存的壓縮檔"""
        if self._compress_task is not None:
            self._compress_task.cancel()
            self._compress_task.abandon()
            self._compress_task = None
        for result in self._compressed.values():
            self._compressor.discard(result)
        self._compressed = {}
        super().done(code)

    def _go(self, index: int):
        """切到第 index 步：步驟列標出目前這步，還沒接上頁面的步驟變灰；「聲部」「檔案」「確認」步依目前的選擇重排"""
        self._current = index
        if index == 1:
            self._show_voices()
        elif index == 2:
            self._start_compression()
            self._show_files()
        elif index == 4:
            self._show_confirm()
        self._stack.setCurrentWidget(self._pages[index])
        for step, label in enumerate(self._step_labels):
            if step == index:
                label.setStyleSheet("font-weight: bold;")
            else:
                label.setStyleSheet(f"color: {_NOTE_COLOR};")
        self._refresh_buttons()

    def _refresh_buttons(self):
        """「上一步」要有上一步的頁面；「下一步」還要這一步都決定好了；最後一步換成「發譜」"""
        last = self._current == len(STEPS) - 1
        self._back.setEnabled(self._current - 1 in self._pages)
        self._next.setVisible(not last)
        self._next.setEnabled(self._current + 1 in self._pages and self._step_ready(self._current))
        self._publish.setVisible(last)
        self._publish.setEnabled(last and self._can_publish())

    def _can_publish(self) -> bool:
        """前兩步都決定好、壓縮做完，有群組可以發，而且超過上限的檔都壓縮好或選了不發"""
        if self._compress_task is not None or not self._step_ready(0) or not self._step_ready(1):
            return False
        plans = self.publish_plan()
        return bool(plans) and not any(plan.too_large for plan in plans)

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
            unsynced.setStyleSheet(f"color: {UNSYNCED_COLOR};")
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


    # --- 「檔案」步 ---

    def _build_files_step(self) -> QWidget:
        """「檔案」步的頁面：每個群組一塊，切到這一步時才排（_show_files）"""
        scroll = QScrollArea()
        scroll.setObjectName("files_step")
        scroll.setWidgetResizable(True)
        content = QWidget()
        self._files_layout = QVBoxLayout(content)
        self._files_layout.addStretch()
        scroll.setWidget(content)
        return scroll

    def _show_files(self):
        """重排「檔案」步的每個群組"""
        for box in self._file_boxes:
            _discard(box)
        self._file_boxes = [self._files_box(check) for check in self._checks]
        for box in self._file_boxes:
            self._files_layout.insertWidget(self._files_layout.count() - 1, box)

    def _files_box(self, check: GroupCheck) -> QGroupBox:
        """一個群組在「檔案」步的那一塊：擋下的寫原因，其餘是要發的檔與跳過的檔"""
        box = QGroupBox(check.group.name)
        box.setObjectName("files_group")
        layout = QVBoxLayout(box)
        if check.blocked:
            reason = QLabel(blocked_text(check))
            reason.setObjectName("blocked_reason")
            reason.setStyleSheet(f"color: {_BLOCKED_COLOR};")
            layout.addWidget(reason)
            return box
        plan = self.file_plan(check.group)
        layout.addWidget(self._files_table(plan))
        if plan.skipped:
            layout.addLayout(self._skipped_row(check.group, plan))
        return box

    def _files_table(self, plan: FilePlan) -> QTableWidget:
        """要發的檔：聲部、檔名、大小、要取代的、其他人放的（不動）與壓縮；表格高度剛好放下所有列"""
        files = plan.publishing
        table = QTableWidget(len(files), _FILE_COLUMNS)
        table.setObjectName("files_table")
        table.setHorizontalHeaderLabels([
            t("publish.files.voice"), t("publish.files.file"), t("publish.files.size"), t("publish.files.replaces"),
            t("publish.files.others"),
            t("publish.files.compression", limit=size_text(self._compressor.size_limit)),
        ])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        too_large = set(id(file) for file in oversized(plan, self._compressor.size_limit))
        for row, file in enumerate(files):
            replaced = _score_titles(file.replaces) if file.replaces else t("publish.files.new")
            others = t("publish.files.untouched", titles=_score_titles(file.others)) if file.others else ""
            texts = (_file_voice(file), os.path.basename(file.path), size_text(file.size), replaced, others)
            for column, text in enumerate(texts):
                table.setItem(row, column, QTableWidgetItem(text))
            table.item(row, 3).setForeground(QColor(_REPLACE_COLOR if file.replaces else _NEW_COLOR))
            table.item(row, 4).setForeground(QColor(UNSYNCED_COLOR))
            if id(file) in too_large:
                table.item(row, 2).setForeground(QColor(_BLOCKED_COLOR))
                table.setCellWidget(row, 5, self._compression_cell(file))
        table.resizeRowsToContents()
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        height = table.horizontalHeader().sizeHint().height() + 2 * table.frameWidth()
        table.setFixedHeight(height + sum(table.rowHeight(r) for r in range(table.rowCount())))
        return table

    def _compression_cell(self, file: PublishFile) -> QWidget:
        """超過上限的一份檔的壓縮欄：狀態，壓縮好時加「開啟」「比較」，做完後都有「不發這份」"""
        cell = QWidget()
        cell.setObjectName("compression")
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(2, 0, 2, 0)
        result = self._compressed.get(file.path)
        if result is not None:
            text = result_state(result)
        elif file.path == self._compressing:
            text = t("publish.files.compressing")
        elif self._compress_task is not None:
            text = t("publish.files.waiting")
        else:
            text = t("publish.files.not_compressed")
        state = QLabel(text)
        state.setObjectName("compression_state")
        compressed = result is not None and result.status is CompressionStatus.COMPRESSED
        state.setStyleSheet(f"color: {_NEW_COLOR if compressed else UNSYNCED_COLOR};")
        layout.addWidget(state)
        if compressed:
            open_button = QPushButton(t("publish.files.open"))
            open_button.clicked.connect(lambda: self._open_compressed(result))
            layout.addWidget(open_button)
            compare = QPushButton(t("publish.files.compare"))
            compare.clicked.connect(lambda: self._compare(file, result))
            layout.addWidget(compare)
        if result is not None or self._compress_task is None:
            drop = QPushButton(t("publish.files.drop"))
            drop.setCheckable(True)
            drop.setChecked(file.path in self._dropped)
            drop.toggled.connect(lambda on: self._drop(file.path, on))
            layout.addWidget(drop)
        layout.addStretch()
        return cell

    def _skipped_row(self, group: Group, plan: FilePlan) -> QHBoxLayout:
        """跳過的檔：只在譜庫的（旁邊放「從譜庫下載回來」）與本機找不到的"""
        row = QHBoxLayout()
        catalog_only = [os.path.basename(f.path) for f in plan.skipped if f.catalog_only]
        missing = [os.path.basename(f.path) for f in plan.skipped if not f.catalog_only]
        lines = []
        if catalog_only:
            lines.append(t("publish.files.catalog_only", files="、".join(catalog_only)))
        if missing:
            lines.append(t("publish.files.missing", files="、".join(missing)))
        label = QLabel("\n".join(lines))
        label.setObjectName("files_skipped")
        label.setStyleSheet(f"color: {UNSYNCED_COLOR};")
        row.addWidget(label)
        if catalog_only and self._download_back is not None:
            download = QPushButton(t("download.back"))
            download.clicked.connect(lambda: self._download(group))
            row.addWidget(download)
        row.addStretch()
        return row

    def _download(self, group: Group):
        """「從譜庫下載回來」：關掉預覽，交給主視窗在背景下載回原位"""
        self.reject()
        self._download_back([group.id])

    def _drop(self, path: str, on: bool):
        """「不發這份」切換：on 為不發"""
        if on:
            self._dropped.add(path)
        else:
            self._dropped.discard(path)
        self._refresh_buttons()

    def _open_compressed(self, result: CompressionResult):
        """以預設的 PDF 閱讀器開啟壓縮檔"""
        if not self._opener.open(result.output_path):
            QMessageBox.warning(self, t("dialog.warning"), t("compare.open_failed"))

    def _compare(self, file: PublishFile, result: CompressionResult):
        """開共用的比較畫面比較這一份：確定時留下就要發，不要就不發這份"""
        dialog = CompressionCompareDialog(
            [(os.path.basename(file.path), result)], self._opener, self, accept_text=t("publish.files.compare_done"),
        )
        if dialog.exec() == QDialog.Accepted:
            self._drop(file.path, not dialog.kept())
            self._show_files()
        dialog.deleteLater()

    def _start_compression(self):
        """第一次進「檔案」步時，在背景逐份壓縮所有群組裡超過上限的檔"""
        if self._compress_started:
            return
        self._compress_started = True
        paths = [
            file.path for check in self._checks if not check.blocked
            for file in oversized(self.file_plan(check.group), self._compressor.size_limit)
        ]
        if not paths:
            return
        compressor = self._compressor

        def work(should_cancel, report):
            """一份一份壓；每份開始與壓好時回報，取消時丟掉剛壓好的那份"""
            for index, path in enumerate(paths, start=1):
                if should_cancel():
                    return
                report((path, index, None))
                result = compressor.compress(path, should_cancel)
                if should_cancel():
                    compressor.discard(result)
                    return
                report((path, index, result))

        task = BackgroundTask(work, self)
        self._compress_task = task

        def progressed(progress):
            """一份開始或壓好了：更新狀態與「檔案」步"""
            path, index, result = progress
            if result is None:
                self._compressing = path
                self._compress_status.setText(t(
                    "publish.files.status", name=os.path.basename(path), index=index, total=len(paths),
                ))
            else:
                self._compressed[path] = result
            self._compression_changed()

        def finished(_result):
            """壓完或取消了"""
            self._compress_task = None
            self._compressing = None
            task.deleteLater()
            self._compress_status.setVisible(False)
            self._compress_cancel.setVisible(False)
            self._compression_changed()

        def failed(message: str):
            """壓縮服務拋出例外：提示後當作做完"""
            finished(None)
            QMessageBox.warning(self, t("dialog.warning"), t("publish.files.compress_failed", error=message))

        task.progressed.connect(progressed)
        task.finished.connect(finished)
        task.failed.connect(failed)
        self._compress_status.setText(t("publish.files.waiting"))
        self._compress_status.setVisible(True)
        self._compress_cancel.setEnabled(True)
        self._compress_cancel.setVisible(True)
        task.start()

    def _cancel_compression(self):
        """「取消壓縮」：目前這份在頁與頁之間停下，之後的不壓"""
        if self._compress_task is not None:
            self._compress_task.cancel()
            self._compress_cancel.setEnabled(False)

    def _compression_changed(self):
        """壓縮有進展：重排目前顯示的「檔案」或「確認」步，更新「發譜」"""
        if self._current == 2:
            self._show_files()
        elif self._current == 4:
            self._show_confirm()
        self._refresh_buttons()

    # --- 「公演」步 ---

    def _build_events_step(self) -> QWidget:
        """「公演」步：每場候選公演一個勾選框與說明；沒有候選時寫明"""
        scroll = QScrollArea()
        scroll.setObjectName("events_step")
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        heading = QLabel(t("publish.events.heading"))
        heading.setStyleSheet("font-weight: bold;")
        layout.addWidget(heading)
        if not self._readiness.concerts:
            layout.addWidget(QLabel(t("publish.events.none")))
        for event in self._readiness.concerts:
            check = QCheckBox(_event_text(event))
            check.setObjectName("concert")
            check.toggled.connect(lambda on, e=event.id: self._choose_event(e, on))
            layout.addWidget(check)
            note = QLabel(t("publish.events.note"))
            note.setObjectName("concert_note")
            note.setWordWrap(True)
            note.setStyleSheet(f"color: {_NOTE_COLOR}; margin-left: 20px;")
            layout.addWidget(note)
        unattached = QLabel(t("publish.events.unattached"))
        unattached.setWordWrap(True)
        unattached.setStyleSheet(f"color: {_NOTE_COLOR};")
        layout.addWidget(unattached)
        layout.addStretch()
        scroll.setWidget(content)
        return scroll

    def _choose_event(self, event_id: str, on: bool):
        """勾選或取消一場公演"""
        if on:
            self._chosen_events.add(event_id)
        else:
            self._chosen_events.discard(event_id)

    # --- 「確認」步 ---

    def _build_confirm_step(self) -> QWidget:
        """「確認」步的頁面：切到這一步時才排（_show_confirm）"""
        scroll = QScrollArea()
        scroll.setObjectName("confirm_step")
        scroll.setWidgetResizable(True)
        content = QWidget()
        self._confirm_layout = QVBoxLayout(content)
        self._confirm_layout.addStretch()
        scroll.setWidget(content)
        return scroll

    def _show_confirm(self):
        """重排「確認」步：每個要發的群組一塊，接著公演、合計與這次不發的群組"""
        for widget in self._confirm_widgets:
            _discard(widget)
        plans = self.publish_plan()
        widgets: List[QWidget] = [self._confirm_box(plan) for plan in plans]
        events = [event.title for event in self._readiness.concerts if event.id in self._chosen_events]
        widgets.append(_named_label(
            "confirm_events",
            t("publish.confirm.events", events="、".join(events)) if events else t("publish.confirm.no_events"),
        ))
        total = sum(len(plan.uploads) for plan in plans)
        widgets.append(_named_label("confirm_total", t(
            "publish.confirm.total", groups=len(plans), count=total, size=size_text(sum(p.size for p in plans)),
        )))
        blocked = [check.group.name for check in self._checks if check.blocked]
        if blocked:
            widgets.append(_named_label("confirm_blocked", t("publish.confirm.blocked", groups="、".join(blocked))))
        for widget in widgets:
            self._confirm_layout.insertWidget(self._confirm_layout.count() - 1, widget)
        self._confirm_widgets = widgets

    def _confirm_box(self, plan: GroupPublish) -> QGroupBox:
        """一個群組會寫進 HuaXia 的變動：曲目、聲部、要傳的檔與不發的檔"""
        box = QGroupBox(plan.group.name)
        box.setObjectName("confirm_group")
        layout = QVBoxLayout(box)
        link = plan.link
        piece = t("publish.confirm.new_piece", title=link.new_piece.title) if link.new_piece is not None \
            else t("publish.confirm.piece", title=link.piece_title)
        layout.addWidget(_named_label("confirm_piece", piece))
        voices = []
        if plan.voices.creates:
            voices.append(t("publish.confirm.creates", voices="、".join(c.name for c in plan.voices.creates)))
        if plan.voices.updates:
            voices.append(t("publish.confirm.updates", voices="、".join(u.name for u in plan.voices.updates)))
        layout.addWidget(_named_label("confirm_voices", "\n".join(voices) or t("publish.confirm.voices_unchanged")))
        layout.addWidget(_named_label("confirm_files", t(
            "publish.confirm.files", count=len(plan.uploads), size=size_text(plan.size), replaced=len(plan.replaces),
        )))
        not_sent = [os.path.basename(f.path) for f in plan.skipped + plan.dropped]
        if not_sent:
            layout.addWidget(_named_label("confirm_not_sent", t("publish.confirm.not_sent", files="、".join(not_sent))))
        if plan.too_large:
            waiting = "、".join(os.path.basename(f.path) for f in plan.too_large)
            label = _named_label("confirm_too_large", t("publish.confirm.too_large", files=waiting))
            label.setStyleSheet(f"color: {_BLOCKED_COLOR};")
            layout.addWidget(label)
        return box


def _named_label(name: str, text: str) -> QLabel:
    """有 objectName、可換行的標籤"""
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(True)
    return label


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
