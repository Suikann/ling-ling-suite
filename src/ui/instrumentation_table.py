# -*- coding: utf-8 -*-
"""
編制表（PySide6）

一個群組的編制逐列列成表格：本專案有、聲部、人數（最少、最多、以上）、聲部組（以樂器為單位，
旁邊寫出會跟著改的其他編號）；上方是群組的時長（分、秒）。
聲部的增刪與順序只在這張表：拖動列時分譜跟著聲部走，雙擊改名時分譜留在原位，取消勾選「本專案有」時
那份分譜移到未分組，表格下方寫出移了哪幾份；與另一列是同一聲部（舊專案留下的重名）的列標紅。
輸入欄與改名的編輯欄有聲部名稱的自動完成（ui/voice_completer.py），候選來源由建立時傳入。
另有載入預設樂器表、從其他群組複製編制、從檔名擷取與匯出樂器表。

元件不依賴主視窗：帶入專案後以 show_group 顯示任何一個群組；修改一律經專案的編輯操作立即寫入，
只有改了聲部清單（增刪、改名、排序、本專案有）時再發出 instruments_changed。人數、時長、
複製編制與聲部組不影響配對，不發出：人數與時長只寫入不重畫，複製編制與聲部組只重畫這張表。
上方顯示群組連到譜庫的哪裡（曲目 › 版本 › 樂章）與旁邊的「改連…」，其下是群組對到的 HuaXia 曲目（發譜預覽的「曲目」步挑的）；
沒連線時是「連到譜庫…」按鈕。兩個按鈕都發出 link_requested，由主視窗開啟連線對話框，連上後以 show_linked 標出依位置猜的聲部。
連到譜庫的群組刪除聲部前先確認（等於從樂譜刪除），已有 Drive 檔案的聲部不刪；聲部組旁另寫出譜庫裡用到這種樂器的其他曲目數。
群組有未同步的值（存檔時寫不進譜庫）時，頂端的提示列寫出是哪些，人數與時長旁寫出譜庫現在的值。
譜庫視窗的版本頁（ui/edition_page.py）以 edition_page=True 沿用同一個元件：每列多了推算的狀態與登記的檔（show_files），
最上面固定一列總譜，選取的列以 selected_target 取得；沒有本專案有、從其他群組複製與從檔名擷取。
開啟專案時譜庫的聲部清單變了，頂端另有提示列與「套用譜庫的」「用專案的（存檔時寫回）」兩顆按鈕，
表格標出譜庫移除與換了順序的列，譜庫新增的聲部以不能編輯的列接在最後。

使用範例：
    table = InstrumentationTable(project)
    table.instruments_changed.connect(refresh_parts)
    table.show_group(group)
"""
from typing import Dict, List, Mapping, Optional, Set, Tuple, TYPE_CHECKING
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QComboBox, QLabel, QMessageBox,
    QAbstractItemView, QMenu, QTableWidget, QTableWidgetItem, QSpinBox, QHeaderView,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from core.constants import INSTRUMENT_PRESETS, TEXT_EXTENSION
from core.instrument_list import instrument_list_text
from core.locale import localized, t
from core.models import (
    CatalogChange, CatalogChangeKind, CatalogLink, CatalogListChange, DuplicateVoiceError, FiledVoicesError,
    FileInfo, Headcount, HuaXiaLink, LinkResult, format_duration,
)
from core.sections import section_label
from core.voice_names import VoiceKey, distinct_voices, duplicate_rows, instrument_name, voice_key
from core.voice_suggestions import VoiceCandidates
from services.file_service import FileService
from ui.voice_completer import VoiceNameDelegate, attach_voice_completer
from ui.widgets import ask_save_path

if TYPE_CHECKING:
    from core.models import Group, Project

# 表格的欄，依顯示順序；加欄時在這裡插入，再於 _fill_row 填值、_HEADERS 給標題
# 狀態與登記的檔只在譜庫視窗的版本頁顯示，本專案有只在主視窗顯示
COLUMNS = ("present", "voice", "minimum", "maximum", "open_ended", "section", "follows", "status", "file")
_EDITION_PAGE_ONLY = ("status", "file")
_PROJECT_ONLY = ("present",)
# 版本頁選了總譜列時 selected_target 的值（聲部名稱不會是空字串）
SCORE_TARGET = ""
_HEADERS = {
    "present": "instrumentation.present",
    "voice": "instrumentation.voice",
    "minimum": "instrumentation.minimum",
    "maximum": "instrumentation.maximum",
    "open_ended": "instrumentation.open_ended",
    "section": "instrumentation.section",
    "follows": "",
    "status": "catalog.page.status",
    "file": "catalog.page.file",
}
# 人數欄的上限（超過的樂譜要求不實際）
_MAX_HEADCOUNT = 999
# 時長的分鐘欄上限
_MAX_DURATION_MINUTES = 999
# 重名的列與本專案沒有的列的文字顏色
_DUPLICATE_COLOR = QColor("#e74c3c")
_ABSENT_COLOR = QColor("#888888")
# 連線時依位置猜的聲部的底色
_GUESSED_COLOR = QColor("#fff3b0")
# 未同步的提示與值的文字顏色
_UNSYNCED_COLOR = QColor("#e67e22")
# 版本頁的狀態：有、缺、指定了檔還沒儲存
_PRESENT_COLOR = QColor("#27ae60")
_MISSING_COLOR = QColor("#e74c3c")
_PENDING_COLOR = QColor("#2980b9")
# 開啟專案時譜庫的聲部清單變了：譜庫新增、移除、換了順序的列的底色與提示列的文字顏色
_ADDED_COLOR = QColor("#d5f5e3")
_REMOVED_COLOR = QColor("#fadbd8")
_MOVED_COLOR = QColor("#d6eaf8")
_LIST_CHANGE_COLOR = QColor("#2471a3")


class VoiceRowsTable(QTableWidget):
    """可拖動整列的表格：放下時不自己搬動儲存格，改發出 rows_dropped，由編制表經編輯操作重排"""

    # 拖動的列（從 0 起算，依原順序）、放到第幾列之前（列數表示最後）
    rows_dropped = Signal(list, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDragDropOverwriteMode(False)
        self.setDropIndicatorShown(True)

    def dropEvent(self, event):
        """放下拖動的列：算出放到哪一列之前，發出 rows_dropped；儲存格不在這裡搬動"""
        if event.source() is not self:
            event.ignore()
            return
        rows = sorted({index.row() for index in self.selectedIndexes()})
        target = self._drop_row(event.position().toPoint())
        event.setDropAction(Qt.IgnoreAction)
        event.accept()
        if rows:
            self.rows_dropped.emit(rows, target)

    def _drop_row(self, pos) -> int:
        """放下的位置在哪一列之前：列的下半部算下一列之前，空白處算最後"""
        index = self.indexAt(pos)
        if not index.isValid():
            return self.rowCount()
        rect = self.visualRect(index)
        return index.row() + (1 if pos.y() > rect.center().y() else 0)


class _RefreshingComboBox(QComboBox):
    """展開選單前先發出 about_to_popup，讓選項依專案目前的內容重列"""

    about_to_popup = Signal()

    def showPopup(self):
        """展開選單：先重列選項"""
        self.about_to_popup.emit()
        super().showPopup()


class InstrumentationTable(QWidget):
    """編制表面板：顯示一個群組的編制，修改經專案的編輯操作寫入"""

    instruments_changed = Signal()
    # 按了「連到譜庫…」（目前顯示的群組）
    link_requested = Signal()

    def __init__(
        self, project: Optional["Project"] = None, parent=None, file_service: Optional[FileService] = None,
        voice_candidates: Optional[VoiceCandidates] = None, edition_page: bool = False,
    ):
        """
        Args:
            project: 目前的專案（編輯操作與聲部組）
            parent: 父元件
            file_service: 檔案服務（寫出匯出的樂器表）；None 時自建
            voice_candidates: 輸入聲部名稱時自動完成的候選來源；None 時自建（沒有譜庫的名稱）
            edition_page: 是否用在譜庫視窗的版本頁：每列多了狀態與登記的檔（show_files），最上面固定一列總譜，
                沒有本專案有、從其他群組複製與從檔名擷取
        """
        super().__init__(parent)
        self._edition_page = edition_page
        # 版本頁各聲部登記的檔與指定了還沒儲存的檔（以 voice_key 為鍵），以及總譜列的
        self._registered: Dict[object, str] = {}
        self._assigned: Dict[object, str] = {}
        self._score_files: Tuple[str, str] = ("", "")
        self._project = project
        self._file_service = file_service or FileService()
        self._voice_candidates = voice_candidates or VoiceCandidates()
        self._group: Optional["Group"] = None
        self._editable = True
        self._filling = False
        self._copy_sources: List["Group"] = []
        # 連線時依位置猜的聲部（標黃），換顯示別的群組時清掉
        self._guessed: Set[str] = set()
        # 人數未同步的聲部（同一聲部的鍵 → 譜庫現在的人數），重畫時更新
        self._unsynced_headcounts: Dict[VoiceKey, Optional[Headcount]] = {}
        # 開啟專案時讀到、跟群組不同的譜庫聲部清單（沒有時為 None），重畫時更新
        self._list_change: Optional[CatalogListChange] = None
        self._build_ui()

    @staticmethod
    def column(name: str) -> int:
        """欄的位置（COLUMNS 裡的名稱）"""
        return COLUMNS.index(name)

    def _build_ui(self):
        """排出標題、連線的預留版面、時長、預設樂器表與複製編制選單、表格、提示、輸入欄與按鈕"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        title = QLabel(t("instrumentation.title"))
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)
        self.header = QVBoxLayout()
        self.header.addLayout(self._build_link_row())
        self._huaxia_label = QLabel()
        self._huaxia_label.setObjectName("huaxia_piece")
        self._huaxia_label.setWordWrap(True)
        self._huaxia_label.hide()
        self.header.addWidget(self._huaxia_label)
        self._unsynced_bar = QLabel()
        self._unsynced_bar.setObjectName("unsynced_bar")
        self._unsynced_bar.setWordWrap(True)
        self._unsynced_bar.setStyleSheet(f"color: {_UNSYNCED_COLOR.name()};")
        self._unsynced_bar.hide()
        self.header.addWidget(self._unsynced_bar)
        self.header.addWidget(self._build_list_bar())
        self._keep_note = QLabel(t("catalog.refresh.keep_note"))
        self._keep_note.setObjectName("catalog_keep_note")
        self._keep_note.setWordWrap(True)
        self._keep_note.setStyleSheet(f"color: {_LIST_CHANGE_COLOR.name()};")
        self._keep_note.hide()
        self.header.addWidget(self._keep_note)
        layout.addLayout(self.header)
        layout.addLayout(self._build_duration_row())
        self._preset_combo = QComboBox()
        self._preset_combo.addItem(t("instrument.load_preset"))
        self._preset_combo.addItems([localized(preset.name, preset.name_en) for preset in INSTRUMENT_PRESETS])
        self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        layout.addWidget(self._preset_combo)
        self._copy_combo = _RefreshingComboBox()
        self._copy_combo.about_to_popup.connect(self._list_copy_sources)
        self._copy_combo.currentIndexChanged.connect(self._on_copy_source_chosen)
        layout.addWidget(self._copy_combo)
        self._copy_combo.setVisible(not self._edition_page)
        if self._edition_page:
            layout.addWidget(self._build_score_table())
        self._table = VoiceRowsTable()
        self._table.setColumnCount(len(COLUMNS))
        self._table.setHorizontalHeaderLabels([t(_HEADERS[name]) if _HEADERS[name] else "" for name in COLUMNS])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        header = self._table.horizontalHeader()
        for index in range(len(COLUMNS)):
            header.setSectionResizeMode(index, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.column("voice"), QHeaderView.Stretch)
        for name in _PROJECT_ONLY if self._edition_page else _EDITION_PAGE_ONLY:
            self._table.setColumnHidden(self.column(name), True)
        self._table.setItemDelegateForColumn(self.column("voice"), VoiceNameDelegate(self._candidates, self._table))
        self._table.rows_dropped.connect(self._on_rows_dropped)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self._table)
        if self._edition_page:
            self._link_score_table()
        QShortcut(QKeySequence("Delete"), self._table, self._remove_selected)
        self._notice = QLabel()
        self._notice.setWordWrap(True)
        self._notice.setStyleSheet("color: #e67e22;")
        self._notice.hide()
        layout.addWidget(self._notice)
        input_row = QHBoxLayout()
        self._entry = QLineEdit()
        self._entry.setPlaceholderText(t("instrument.placeholder"))
        self._entry.returnPressed.connect(self._add_instrument)
        attach_voice_completer(self._entry, self._candidates)
        input_row.addWidget(self._entry)
        add_btn = QPushButton(t("instrument.add"))
        add_btn.setFixedWidth(60)
        add_btn.clicked.connect(self._add_instrument)
        input_row.addWidget(add_btn)
        layout.addLayout(input_row)
        btn_row = QHBoxLayout()
        extract_btn = QPushButton(t("instrument.auto_extract"))
        extract_btn.clicked.connect(self._auto_extract)
        extract_btn.setVisible(not self._edition_page)
        btn_row.addWidget(extract_btn)
        remove_btn = QPushButton(t("instrument.remove"))
        remove_btn.setStyleSheet("background: #6b2030; color: #f0d8d8;")
        remove_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(remove_btn)
        layout.addLayout(btn_row)
        export_btn = QPushButton(t("instrument.export"))
        export_btn.clicked.connect(self._export_instruments)
        layout.addWidget(export_btn)

    def _build_score_table(self) -> QTableWidget:
        """版本頁最上面固定的總譜列：只有名稱、狀態與登記的檔，不能拖動、改名，沒有人數與聲部組"""
        self._score_table = QTableWidget(1, len(COLUMNS))
        self._score_table.setObjectName("score_row")
        self._score_table.horizontalHeader().setVisible(False)
        self._score_table.verticalHeader().setVisible(False)
        self._score_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._score_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._score_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._score_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._score_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._score_table.setFixedHeight(self._score_table.rowHeight(0) + 2 * self._score_table.frameWidth())
        for name in _PROJECT_ONLY:
            self._score_table.setColumnHidden(self.column(name), True)
        for column in range(len(COLUMNS)):
            self._score_table.setItem(0, column, _read_only_item(""))
        self._score_table.item(0, self.column("voice")).setText(t("catalog.page.score"))
        return self._score_table

    def _link_score_table(self):
        """總譜列跟著編制表的欄寬；兩張表同時只有一列被選取"""
        header = self._table.horizontalHeader()
        header.sectionResized.connect(lambda index, _old, size: self._score_table.setColumnWidth(index, size))
        self._score_table.itemSelectionChanged.connect(
            lambda: self._score_table.selectedItems() and self._table.clearSelection(),
        )
        self._table.itemSelectionChanged.connect(
            lambda: self._table.selectedItems() and self._score_table.clearSelection(),
        )

    def _build_link_row(self) -> QHBoxLayout:
        """連線一列：連到哪裡（曲目 › 版本 › 樂章）與「改連…」，沒連線時是「連到譜庫…」；兩個按鈕都發出 link_requested"""
        row = QHBoxLayout()
        self._link_label = QLabel()
        self._link_label.setWordWrap(True)
        row.addWidget(self._link_label, stretch=1)
        self._link_button = QPushButton(t("catalog.link.button"))
        self._relink_button = QPushButton(t("catalog.relink.button"))
        for button in (self._link_button, self._relink_button):
            button.clicked.connect(self.link_requested.emit)
            row.addWidget(button)
        return row

    def _build_list_bar(self) -> QWidget:
        """「譜庫的聲部清單變了」的提示列：說明與「套用譜庫的」「用專案的（存檔時寫回）」兩顆按鈕；先收起"""
        self._list_bar = QWidget()
        self._list_bar.setObjectName("catalog_list_bar")
        bar = QVBoxLayout(self._list_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        text = QLabel(t("catalog.refresh.bar"))
        text.setWordWrap(True)
        text.setStyleSheet(f"color: {_LIST_CHANGE_COLOR.name()};")
        bar.addWidget(text)
        buttons = QHBoxLayout()
        take = QPushButton(t("catalog.refresh.take"))
        take.clicked.connect(self._take_catalog_list)
        buttons.addWidget(take)
        keep = QPushButton(t("catalog.refresh.keep"))
        keep.clicked.connect(self._keep_project_list)
        buttons.addWidget(keep)
        buttons.addStretch()
        bar.addLayout(buttons)
        self._list_bar.hide()
        return self._list_bar

    def _build_duration_row(self) -> QHBoxLayout:
        """時長一列：分、秒兩個欄位"""
        row = QHBoxLayout()
        row.addWidget(QLabel(t("instrumentation.duration")))
        self._duration_minutes = _count_spin(_MAX_DURATION_MINUTES, blank_zero=False)
        self._duration_minutes.setObjectName("duration_minutes")
        self._duration_seconds = _count_spin(59, blank_zero=False)
        self._duration_seconds.setObjectName("duration_seconds")
        for spin, unit in ((self._duration_minutes, "instrumentation.minutes"),
                           (self._duration_seconds, "instrumentation.seconds")):
            spin.valueChanged.connect(self._on_duration_edited)
            row.addWidget(spin)
            row.addWidget(QLabel(t(unit)))
        self._duration_catalog_now = QLabel()
        self._duration_catalog_now.setObjectName("duration_catalog_now")
        self._duration_catalog_now.setStyleSheet(f"color: {_UNSYNCED_COLOR.name()};")
        self._duration_catalog_now.hide()
        row.addWidget(self._duration_catalog_now)
        row.addStretch()
        return row

    def _candidates(self) -> List[str]:
        """在目前群組輸入聲部名稱時的自動完成候選"""
        return self._voice_candidates.for_group(self._project, self._group)

    # --- 顯示 ---

    def set_project(self, project: "Project"):
        """切換到另一個專案（開啟或新增專案時）"""
        self._project = project

    def set_editable(self, editable: bool):
        """能不能修改（專案唯讀時停用）；不能修改時整張表停用，切換群組也不恢復"""
        self._editable = editable
        self.setEnabled(self._group is not None and editable)

    def show_group(self, group: Optional["Group"]):
        """顯示群組的編制；不是群組時清空並停用，輸入的聲部才不會沒有地方寫入。只顯示，不寫入專案"""
        self._group = group
        self._guessed = set()
        self.setEnabled(group is not None and self._editable)
        self._show_moved([])
        self._fill()

    def show_files(
        self, registered: Mapping[str, str], assigned: Mapping[str, str], score_registered: str = "",
        score_assigned: str = "",
    ):
        """版本頁：每列寫出推算的狀態與登記的檔（聲部以同一聲部的規則比對）

        登記了檔就是有、沒有就是缺；指定了還沒儲存的檔寫成待儲存。只顯示，不寫入專案。

        Args:
            registered: 聲部與它在譜庫登記的檔名；沒登記的聲部不在裡面
            assigned: 聲部與指定給它、還沒儲存的檔名
            score_registered: 總譜列登記的檔名；沒有時為空字串
            score_assigned: 指定給總譜列、還沒儲存的檔名；沒有時為空字串
        """
        self._registered = {voice_key(name): file for name, file in registered.items()}
        self._assigned = {voice_key(name): file for name, file in assigned.items()}
        self._score_files = (score_registered, score_assigned)
        self._fill()

    def selected_target(self) -> Optional[str]:
        """版本頁選取的列：聲部名稱，選了總譜列時為 SCORE_TARGET，都沒選時為 None"""
        if self._edition_page and self._score_table.selectedItems():
            return SCORE_TARGET
        rows = self._selected_rows()
        return self._group.instruments[rows[0]] if rows and self._group is not None else None

    def show_linked(self, result: LinkResult):
        """目前的群組剛連到譜庫：重畫、標黃依位置猜的聲部、寫出移到未分組的分譜，發出 instruments_changed"""
        self._guessed = set(result.guessed)
        self._changed(result.moved)

    def refresh(self):
        """依專案重畫目前群組的編制（在別處改了專案設定，例如樂團類型之後）"""
        self._fill()

    def _fill(self):
        """依群組重畫整張表與時長（由程式填值，不觸發編輯操作）"""
        self._filling = True
        self._show_link()
        unsynced = self._project.unsynced_changes(self._group) if self._group else []
        self._show_unsynced(unsynced)
        self._unsynced_headcounts = {
            voice_key(change.voice): change.old for change in unsynced if change.kind is CatalogChangeKind.HEADCOUNT
        }
        self._list_change = self._project.catalog_list_change(self._group) if self._group else None
        self._list_bar.setVisible(self._list_change is not None)
        link = self._group.catalog_link if self._group else None
        self._keep_note.setVisible(link is not None and link.confirm_voices)
        self._table.clearContents()
        voices = list(self._group.instruments) if self._group else []
        added = self._list_change.added if self._list_change else []
        self._table.setRowCount(len(voices) + len(added))
        duplicates = set(duplicate_rows(voices))
        for row, voice in enumerate(voices):
            self._fill_row(row, voice, row in duplicates)
            self._mark_list_change(row, voice)
        for row, voice in enumerate(added, start=len(voices)):
            self._fill_added_row(row, voice)
        if self._edition_page:
            self._fill_files(self._score_table, 0, *self._score_files)
        minutes, seconds = divmod(self._group.duration_seconds or 0, 60) if self._group else (0, 0)
        self._duration_minutes.setValue(minutes)
        self._duration_seconds.setValue(seconds)
        self._filling = False
        self._list_copy_sources()

    def _show_link(self):
        """顯示群組連到譜庫的哪裡、「改連…」與對到的 HuaXia 曲目；沒連線時顯示「連到譜庫…」按鈕，不是群組時都收起"""
        link = self._group.catalog_link if self._group else None
        self._link_label.setText(link_text(link) if link else "")
        self._link_label.setVisible(link is not None)
        self._relink_button.setVisible(link is not None and not self._edition_page)
        self._link_button.setVisible(self._group is not None and link is None)
        huaxia = self._group.huaxia_link if self._group else None
        self._huaxia_label.setText(_huaxia_text(huaxia) if huaxia else "")
        self._huaxia_label.setVisible(huaxia is not None)

    def _show_unsynced(self, changes: List[CatalogChange]):
        """群組頂端寫出未同步的值，時長未同步時旁邊寫出譜庫現在的值；沒有時收起"""
        items = "、".join(catalog_change_text(change) for change in changes)
        self._unsynced_bar.setText(t("catalog.unsynced.bar", items=items) if changes else "")
        self._unsynced_bar.setVisible(bool(changes))
        duration = next((change for change in changes if change.kind is CatalogChangeKind.DURATION), None)
        self._duration_catalog_now.setText(
            t("catalog.unsynced.catalog_now", value=_catalog_value(format_duration(duration.old))) if duration else "",
        )
        self._duration_catalog_now.setVisible(duration is not None)

    def _fill_row(self, row: int, voice: str, duplicate: bool):
        """填一列：本專案有的勾選框、聲部名稱（重名標紅、本專案沒有的變灰、依位置猜的標黃）、聲部組選單與影響範圍"""
        present = self._group.present[row]
        check = QTableWidgetItem()
        check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
        check.setCheckState(Qt.Checked if present else Qt.Unchecked)
        check.setToolTip(t("instrumentation.present_tip"))
        self._table.setItem(row, self.column("present"), check)
        name = QTableWidgetItem(voice)
        name.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsEditable | Qt.ItemIsDragEnabled)
        if duplicate:
            name.setForeground(QBrush(_DUPLICATE_COLOR))
            name.setToolTip(t("instrumentation.duplicate_row"))
        elif not present:
            name.setForeground(QBrush(_ABSENT_COLOR))
        if voice in self._guessed:
            name.setBackground(QBrush(_GUESSED_COLOR))
            if not duplicate:
                name.setToolTip(t("catalog.link.guessed"))
        unsynced = voice_key(voice) in self._unsynced_headcounts
        catalog_now = ""
        if unsynced:
            old = self._unsynced_headcounts[voice_key(voice)]
            catalog_now = t("catalog.unsynced.catalog_now", value=_catalog_value(old.text() if old else ""))
            if not duplicate:
                name.setForeground(QBrush(_UNSYNCED_COLOR))
                name.setToolTip(catalog_now)
        self._table.setItem(row, self.column("voice"), name)
        for column, tip in (("minimum", "instrumentation.minimum_tip"), ("maximum", "instrumentation.maximum_tip")):
            spin = _count_spin(_MAX_HEADCOUNT)
            spin.setToolTip(t(tip) + (f"\n{catalog_now}" if unsynced else ""))
            spin.valueChanged.connect(lambda _value, r=row: self._on_headcount_edited(r))
            self._table.setCellWidget(row, self.column(column), spin)
        open_ended = QTableWidgetItem()
        open_ended.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
        open_ended.setToolTip(t("instrumentation.open_ended_tip"))
        self._table.setItem(row, self.column("open_ended"), open_ended)
        self._show_headcount(row)
        self._table.setCellWidget(row, self.column("section"), self._section_combo(voice))
        note = QTableWidgetItem(self._section_impact(voice))
        note.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
        note.setForeground(QBrush(_ABSENT_COLOR))
        self._table.setItem(row, self.column("follows"), note)
        if self._edition_page:
            key = voice_key(voice)
            self._fill_files(self._table, row, self._registered.get(key, ""), self._assigned.get(key, ""))

    def _fill_files(self, table: QTableWidget, row: int, registered: str, assigned: str):
        """版本頁一列的狀態與登記的檔：指定了檔就是待儲存，否則登記了檔就是有、沒有就是缺（唯讀，不能手改）"""
        if assigned:
            status, file, color = t("catalog.page.pending"), t("catalog.page.assigned", name=assigned), _PENDING_COLOR
        elif registered:
            status, file, color = t("catalog.page.present"), registered, _PRESENT_COLOR
        else:
            status, file, color = t("catalog.page.missing"), t("catalog.page.no_file"), _MISSING_COLOR
        status_item = _read_only_item(status)
        status_item.setForeground(QBrush(color))
        table.setItem(row, self.column("status"), status_item)
        file_item = _read_only_item(file)
        if not registered and not assigned:
            file_item.setForeground(QBrush(_ABSENT_COLOR))
        table.setItem(row, self.column("file"), file_item)

    def _mark_list_change(self, row: int, voice: str):
        """譜庫的聲部清單變了時，標出這一列：譜庫已移除的標紅底、換了順序的標藍底，提示寫出原因"""
        change = self._list_change
        if change is None:
            return
        name = self._table.item(row, self.column("voice"))
        if voice in change.removed:
            name.setBackground(QBrush(_REMOVED_COLOR))
            name.setToolTip(t("catalog.refresh.removed"))
        elif voice in change.moved:
            position = 1 + [voice_key(n) for n, _ in change.voices].index(voice_key(voice))
            name.setBackground(QBrush(_MOVED_COLOR))
            name.setToolTip(t("catalog.refresh.moved", position=position))

    def _fill_added_row(self, row: int, voice: str):
        """接在最後、譜庫新增的聲部：標綠底，不能勾選、編輯、選取或拖動（選了「套用譜庫的」才加進群組）"""
        for column in range(len(COLUMNS)):
            cell = QTableWidgetItem(t("catalog.refresh.added", voice=voice) if column == self.column("voice") else "")
            cell.setFlags(Qt.ItemIsEnabled)
            cell.setBackground(QBrush(_ADDED_COLOR))
            self._table.setItem(row, column, cell)

    def _section_impact(self, voice: str) -> str:
        """聲部組旁的影響範圍：連到譜庫時譜庫裡用到這種樂器的其他曲目，以及專案裡同一樂器的其他編號"""
        notes = []
        link = self._group.catalog_link
        count = link.other_pieces_using(voice) if link is not None else 0
        if count:
            notes.append(t("catalog.sync.other_pieces", count=count, instrument=instrument_name(voice)))
        follows = self._project.same_instrument_voices(voice)
        if follows:
            notes.append(t("instrument.section_follows", voices="、".join(follows)))
        return "；".join(notes)

    def _section_combo(self, voice: str) -> QComboBox:
        """一列的聲部組選單：選項是專案樂團類型的那一套（已存的值不在其中時另外列出），資料是中文名稱"""
        combo = QComboBox()
        current = self._project.section_of(voice)
        options = list(self._project.section_options())
        if current not in options:
            options.append(current)
        for section in options:
            combo.addItem(section_label(section), section)
        combo.setCurrentIndex(options.index(current))
        combo.currentIndexChanged.connect(lambda _index, v=voice, c=combo: self._on_section_chosen(v, c))
        return combo

    def _show_headcount(self, row: int):
        """把群組裡那一列的人數填進最少、最多、以上三欄（留空顯示空白；以上時停用最多）"""
        headcount = self._group.headcounts[row]
        filling, self._filling = self._filling, True
        self._table.cellWidget(row, self.column("minimum")).setValue(headcount.minimum or 0)
        maximum = self._table.cellWidget(row, self.column("maximum"))
        maximum.setValue(headcount.maximum or 0)
        maximum.setEnabled(not headcount.open_ended)
        self._table.item(row, self.column("open_ended")).setCheckState(
            Qt.Checked if headcount.open_ended else Qt.Unchecked,
        )
        self._filling = filling

    def _list_copy_sources(self):
        """重列「從其他群組複製編制」的選項：專案裡目前群組以外的群組"""
        groups = self._project.groups if self._project is not None else []
        self._copy_sources = [g for g in groups if g is not self._group]
        self._copy_combo.blockSignals(True)
        self._copy_combo.clear()
        self._copy_combo.addItem(t("instrumentation.copy_from"))
        self._copy_combo.addItems([g.name for g in self._copy_sources])
        self._copy_combo.setCurrentIndex(0)
        self._copy_combo.blockSignals(False)
        self._copy_combo.setEnabled(bool(self._copy_sources))

    def _show_moved(self, moved: List[FileInfo]):
        """在表格下方寫出移到未分組的分譜；沒有時收起"""
        self._show_notice(
            t("instrumentation.moved_to_ungrouped", files="、".join(f.display_name for f in moved)) if moved else "",
        )

    def _show_notice(self, text: str):
        """在表格下方寫出提示；空字串時收起"""
        self._notice.setText(text)
        self._notice.setVisible(bool(text))

    def _changed(self, moved: Optional[List[FileInfo]] = None):
        """改了聲部清單的編輯操作完成後：重畫、寫出移到未分組的分譜、發出 instruments_changed"""
        self._fill()
        self._show_moved(moved or [])
        self.instruments_changed.emit()

    # --- 使用者的修改 ---

    def _on_rows_dropped(self, rows: List[int], target: int):
        """拖動列：聲部連同分譜一起移動；放在譜庫新增的列（接在最後）上時算放到最後"""
        if self._group is None:
            return
        count = len(self._group.instruments)
        rows = [row for row in rows if row < count]
        if not rows:
            return
        self._project.move_instruments(self._group, rows, min(target, count))
        self._changed()

    def _take_catalog_list(self):
        """按了「套用譜庫的」：換成譜庫的聲部清單，分譜依名稱配對，寫出移到未分組的分譜"""
        if self._group is None:
            return
        self._changed(self._project.take_catalog_list(self._group))

    def _keep_project_list(self):
        """按了「用專案的（存檔時寫回）」：群組的清單不變，存檔時確認後寫回譜庫"""
        if self._group is None:
            return
        self._project.keep_project_list(self._group)
        self._fill()

    def _on_item_changed(self, item: QTableWidgetItem):
        """勾選「本專案有」或改完聲部名稱"""
        if self._filling or self._group is None:
            return
        if item.column() == self.column("present"):
            moved = self._project.set_present(self._group, item.row(), item.checkState() == Qt.Checked)
            self._changed(moved)
        elif item.column() == self.column("open_ended"):
            self._on_headcount_edited(item.row())
        elif item.column() == self.column("voice"):
            self._rename(item.row(), item.text().strip())

    def _rename(self, row: int, name: str):
        """改聲部名稱：清空即移除；與其他列同一聲部、或連到譜庫而已有 Drive 檔案時說明原因並還原名稱"""
        if not name:
            if not self._remove_rows([row]):
                self._fill()
            return
        try:
            self._project.rename_instrument(self._group, row, name)
        except DuplicateVoiceError as e:
            self._fill()
            self._explain_duplicate(name, e)
            return
        except FiledVoicesError as e:
            self._fill()
            self._explain_filed(e.voices)
            return
        self._changed()

    def _on_headcount_edited(self, row: int):
        """改了最少、最多或以上：整列寫進人數範圍，再顯示整理後的值（不重畫整張表，輸入中的欄位才不會被換掉）"""
        if self._filling or self._group is None:
            return
        self._project.set_headcount(self._group, row, Headcount(
            self._table.cellWidget(row, self.column("minimum")).value(),
            self._table.cellWidget(row, self.column("maximum")).value(),
            self._table.item(row, self.column("open_ended")).checkState() == Qt.Checked,
        ))
        self._show_headcount(row)

    def _on_duration_edited(self):
        """改了時長的分或秒：以秒寫進群組"""
        if self._filling or self._group is None:
            return
        self._project.set_duration(self._group, self._duration_minutes.value() * 60 + self._duration_seconds.value())

    def _on_copy_source_chosen(self, index: int):
        """選了複製來源的群組：依同一聲部複製人數，寫出對應不到、已留空的聲部"""
        if index <= 0 or self._group is None or index > len(self._copy_sources):
            return
        unmatched = self._project.copy_instrumentation(self._group, self._copy_sources[index - 1])
        self._fill()
        self._show_notice(t("instrumentation.copy_unmatched", voices="、".join(unmatched)) if unmatched else "")

    def _on_section_chosen(self, voice: str, combo: QComboBox):
        """改聲部組：專案裡同一樂器的其他編號一起改"""
        if self._filling or self._group is None:
            return
        self._project.set_section(voice, combo.currentData())
        self._fill()

    def _on_preset_selected(self, index: int):
        """選了預設樂器表：整份換掉目前群組的樂器表，選單回到提示列"""
        if index <= 0:
            return
        preset_idx = index - 1
        if preset_idx < len(INSTRUMENT_PRESETS) and self._group is not None:
            self._project.set_instruments(self._group, INSTRUMENT_PRESETS[preset_idx].instruments)
            self._changed()
        self._preset_combo.blockSignals(True)
        self._preset_combo.setCurrentIndex(0)
        self._preset_combo.blockSignals(False)

    def _add_instrument(self):
        """把輸入欄的名稱加到樂器表最後；與其他列同一聲部時說明原因，樂器表與輸入欄不變"""
        name = self._entry.text().strip()
        if not name or self._project is None or self._group is None:
            return
        try:
            self._project.add_instrument(self._group, name)
        except DuplicateVoiceError as e:
            self._explain_duplicate(name, e)
            return
        self._entry.clear()
        self._changed()

    def _explain_duplicate(self, name: str, error: DuplicateVoiceError):
        """說明新名稱與樂器表的哪一列是同一聲部"""
        QMessageBox.warning(
            self, t("dialog.warning"), t("instrument.duplicate", name=name, existing=error.existing),
        )

    def _selected_rows(self) -> List[int]:
        """選取的列（從 0 起算，依順序）"""
        return sorted({index.row() for index in self._table.selectedIndexes()})

    def _remove_selected(self):
        """移除選取的列；它們的分譜移到未分組"""
        rows = self._selected_rows()
        if not rows or self._group is None:
            return
        self._remove_rows(rows)

    def _remove_rows(self, rows: List[int]) -> bool:
        """移除幾列，回傳是否移除了

        連到譜庫的群組：已有 Drive 檔案的聲部說明要到譜庫視窗處理、不移除；其餘先確認，因為這等於從樂譜刪除。
        """
        filed = self._project.filed_voices(self._group, rows)
        if filed:
            self._explain_filed(filed)
            return False
        if self._group.catalog_link is not None:
            voices = "、".join(self._group.instruments[i] for i in rows)
            answer = QMessageBox.question(self, t("dialog.warning"), t("catalog.sync.delete_voices", voices=voices))
            if answer != QMessageBox.Yes:
                return False
        self._changed(self._project.remove_instruments(self._group, rows))
        return True

    def _explain_filed(self, voices: List[str]):
        """說明這些聲部在譜庫已有 Drive 檔案，不能從群組刪除"""
        QMessageBox.warning(self, t("dialog.warning"), t("catalog.sync.filed_voices", voices="、".join(voices)))

    def _show_context_menu(self, pos):
        """在列上按右鍵：選單提供移除選取的列"""
        if self._table.itemAt(pos) is None:
            return
        menu = QMenu(self)
        remove_action = menu.addAction(t("instrument.remove_context"))
        if menu.exec(self._table.viewport().mapToGlobal(pos)) == remove_action:
            self._remove_selected()

    def _auto_extract(self):
        """從群組分譜的檔名擷取聲部，確認後整份換掉樂器表；擷取不到時說明"""
        if not self._group or not self._group.files:
            QMessageBox.information(self, t("dialog.info"), t("instrument.auto_extract.empty"))
            return
        from core.template_engine import extract_instruments_from_filenames
        unique = distinct_voices(extract_instruments_from_filenames([f.display_name for f in self._group.files]))
        if not unique:
            QMessageBox.information(self, t("dialog.info"), t("instrument.auto_extract.empty"))
            return
        preview = "\n".join(f"  {i+1}. {name}" for i, name in enumerate(unique))
        result = QMessageBox.question(
            self, t("instrument.auto_extract.title"),
            t("instrument.auto_extract.confirm", instruments=preview),
        )
        if result == QMessageBox.Yes:
            self._project.set_instruments(self._group, unique)
            self._changed()

    def _export_instruments(self):
        """匯出樂器表（連同人數與時長）為文字檔；以 \\n 分行，寫檔時換成這個平台的換行"""
        if not self._group or not self._group.instruments:
            return
        path = ask_save_path(
            self, t("filedialog.export_instruments"), "", t("filedialog.text_files"), TEXT_EXTENSION,
            self._file_service,
        )
        if not path:
            return
        try:
            self._file_service.write_text_atomic(path, instrument_list_text(self._project, self._group))
        except OSError as e:
            QMessageBox.critical(self, t("dialog.error"), str(e))
            return
        QMessageBox.information(self, t("dialog.info"), t("instrument.export_done", path=path))


def _catalog_value(text: str) -> str:
    """「譜庫現在是…」裡的值：空的寫「空白」"""
    return text or t("catalog.unsynced.blank")


def catalog_change_text(change: CatalogChange) -> str:
    """未同步提示列裡一項改動的寫法（與衝突畫面的項目同一套；發譜預覽標出未同步的值也用它）"""
    if change.kind is CatalogChangeKind.VOICES:
        return t("catalog.unsynced.voices")
    if change.kind is CatalogChangeKind.HEADCOUNT:
        return t("catalog.sync.headcount", voice=change.voice)
    if change.kind is CatalogChangeKind.DURATION:
        return t("catalog.sync.duration")
    return t("catalog.sync.section", instrument=instrument_name(change.voice))


def _read_only_item(text: str) -> QTableWidgetItem:
    """不能編輯、可以選取與拖動整列的儲存格"""
    item = QTableWidgetItem(text)
    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
    return item


def link_text(link: CatalogLink) -> str:
    """連線的顯示：曲目 › 版本 › 樂章（整首時寫整首；版本沒有標示時寫未命名版本）"""
    return place_text(link.piece_title, link.edition_label, link.movement_id, link.movement_name)


def place_text(piece_title: str, edition_label: str, movement_id: str, movement_name: str) -> str:
    """譜庫裡一處的顯示：曲目 › 版本 › 樂章（整首時寫整首；版本沒有標示時寫未命名版本）"""
    return t(
        "catalog.link.path", piece=piece_title, edition=edition_label or t("catalog.link.unnamed_edition"),
        movement=movement_name if movement_id else t("catalog.link.whole"),
    )


def _huaxia_text(link: HuaXiaLink) -> str:
    """對到的 HuaXia 曲目的顯示：曲名；發譜時才新建的寫明新建"""
    return t("publish.huaxia_new_piece" if link.new_piece else "publish.huaxia_piece", title=link.piece_title)


def _count_spin(maximum: int, blank_zero: bool = True) -> QSpinBox:
    """人數或時長用的數字欄：0 到 maximum；打完（按 Enter 或離開欄位）才發出 valueChanged

    Args:
        maximum: 上限
        blank_zero: 0 是否顯示空白（人數的留空）
    """
    spin = QSpinBox()
    spin.setRange(0, maximum)
    if blank_zero:
        # 空字串會被 Qt 當成沒有特殊值，所以用一個空格
        spin.setSpecialValueText(" ")
    spin.setKeyboardTracking(False)
    return spin
