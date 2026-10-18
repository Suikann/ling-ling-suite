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
上方顯示群組連到譜庫的哪裡（曲目 › 版本 › 樂章）；沒連線時是「連到譜庫…」按鈕，按下發出 link_requested，
由主視窗開啟連線對話框，連上後以 show_linked 標出依位置猜的聲部。

使用範例：
    table = InstrumentationTable(project)
    table.instruments_changed.connect(refresh_parts)
    table.show_group(group)
"""
from typing import List, Optional, Set, TYPE_CHECKING
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QComboBox, QLabel, QMessageBox,
    QAbstractItemView, QMenu, QTableWidget, QTableWidgetItem, QSpinBox, QHeaderView,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from core.constants import INSTRUMENT_PRESETS, TEXT_EXTENSION
from core.instrument_list import instrument_list_text
from core.locale import localized, t
from core.models import CatalogLink, DuplicateVoiceError, FileInfo, Headcount, LinkResult
from core.sections import section_label
from core.voice_names import distinct_voices, duplicate_rows
from core.voice_suggestions import VoiceCandidates
from services.file_service import FileService
from ui.voice_completer import VoiceNameDelegate, attach_voice_completer
from ui.widgets import ask_save_path

if TYPE_CHECKING:
    from core.models import Group, Project

# 表格的欄，依顯示順序；加欄時在這裡插入，再於 _fill_row 填值、_HEADERS 給標題
COLUMNS = ("present", "voice", "minimum", "maximum", "open_ended", "section", "follows")
_HEADERS = {
    "present": "instrumentation.present",
    "voice": "instrumentation.voice",
    "minimum": "instrumentation.minimum",
    "maximum": "instrumentation.maximum",
    "open_ended": "instrumentation.open_ended",
    "section": "instrumentation.section",
    "follows": "",
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
        voice_candidates: Optional[VoiceCandidates] = None,
    ):
        """
        Args:
            project: 目前的專案（編輯操作與聲部組）
            parent: 父元件
            file_service: 檔案服務（寫出匯出的樂器表）；None 時自建
            voice_candidates: 輸入聲部名稱時自動完成的候選來源；None 時自建（沒有譜庫的名稱）
        """
        super().__init__(parent)
        self._project = project
        self._file_service = file_service or FileService()
        self._voice_candidates = voice_candidates or VoiceCandidates()
        self._group: Optional["Group"] = None
        self._editable = True
        self._filling = False
        self._copy_sources: List["Group"] = []
        # 連線時依位置猜的聲部（標黃），換顯示別的群組時清掉
        self._guessed: Set[str] = set()
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
        self._table = VoiceRowsTable()
        self._table.setColumnCount(len(COLUMNS))
        self._table.setHorizontalHeaderLabels([t(_HEADERS[name]) if _HEADERS[name] else "" for name in COLUMNS])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        header = self._table.horizontalHeader()
        for index in range(len(COLUMNS)):
            header.setSectionResizeMode(index, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.column("voice"), QHeaderView.Stretch)
        self._table.setItemDelegateForColumn(self.column("voice"), VoiceNameDelegate(self._candidates, self._table))
        self._table.rows_dropped.connect(self._on_rows_dropped)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self._table)
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
        btn_row.addWidget(extract_btn)
        remove_btn = QPushButton(t("instrument.remove"))
        remove_btn.setStyleSheet("background: #6b2030; color: #f0d8d8;")
        remove_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(remove_btn)
        layout.addLayout(btn_row)
        export_btn = QPushButton(t("instrument.export"))
        export_btn.clicked.connect(self._export_instruments)
        layout.addWidget(export_btn)

    def _build_link_row(self) -> QHBoxLayout:
        """連線一列：連到哪裡（曲目 › 版本 › 樂章），沒連線時是「連到譜庫…」按鈕"""
        row = QHBoxLayout()
        self._link_label = QLabel()
        self._link_label.setWordWrap(True)
        row.addWidget(self._link_label, stretch=1)
        self._link_button = QPushButton(t("catalog.link.button"))
        self._link_button.clicked.connect(self.link_requested.emit)
        row.addWidget(self._link_button)
        return row

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
        self._table.clearContents()
        voices = list(self._group.instruments) if self._group else []
        self._table.setRowCount(len(voices))
        duplicates = set(duplicate_rows(voices))
        for row, voice in enumerate(voices):
            self._fill_row(row, voice, row in duplicates)
        minutes, seconds = divmod(self._group.duration_seconds or 0, 60) if self._group else (0, 0)
        self._duration_minutes.setValue(minutes)
        self._duration_seconds.setValue(seconds)
        self._filling = False
        self._list_copy_sources()

    def _show_link(self):
        """顯示群組連到譜庫的哪裡；沒連線時顯示「連到譜庫…」按鈕，不是群組時兩者都收起"""
        link = self._group.catalog_link if self._group else None
        self._link_label.setText(_link_text(link) if link else "")
        self._link_label.setVisible(link is not None)
        self._link_button.setVisible(self._group is not None and link is None)

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
        self._table.setItem(row, self.column("voice"), name)
        for column, tip in (("minimum", "instrumentation.minimum_tip"), ("maximum", "instrumentation.maximum_tip")):
            spin = _count_spin(_MAX_HEADCOUNT)
            spin.setToolTip(t(tip))
            spin.valueChanged.connect(lambda _value, r=row: self._on_headcount_edited(r))
            self._table.setCellWidget(row, self.column(column), spin)
        open_ended = QTableWidgetItem()
        open_ended.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
        open_ended.setToolTip(t("instrumentation.open_ended_tip"))
        self._table.setItem(row, self.column("open_ended"), open_ended)
        self._show_headcount(row)
        self._table.setCellWidget(row, self.column("section"), self._section_combo(voice))
        follows = self._project.same_instrument_voices(voice)
        note = QTableWidgetItem(t("instrument.section_follows", voices="、".join(follows)) if follows else "")
        note.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled)
        note.setForeground(QBrush(_ABSENT_COLOR))
        self._table.setItem(row, self.column("follows"), note)

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
        """拖動列：聲部連同分譜一起移動"""
        if self._group is None:
            return
        self._project.move_instruments(self._group, rows, target)
        self._changed()

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
        """改聲部名稱：清空即移除；與其他列同一聲部時說明原因並還原名稱"""
        if not name:
            self._changed(self._project.remove_instruments(self._group, [row]))
            return
        try:
            self._project.rename_instrument(self._group, row, name)
        except DuplicateVoiceError as e:
            self._fill()
            self._explain_duplicate(name, e)
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
        self._changed(self._project.remove_instruments(self._group, rows))

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


def _link_text(link: CatalogLink) -> str:
    """連線的顯示：曲目 › 版本 › 樂章（整首時寫整首；版本沒有標示時寫未命名版本）"""
    return t(
        "catalog.link.path", piece=link.piece_title, edition=link.edition_label or t("catalog.link.unnamed_edition"),
        movement=link.movement_name if link.movement_id else t("catalog.link.whole"),
    )


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
