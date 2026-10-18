# -*- coding: utf-8 -*-
"""
編制表（PySide6）

一個群組的編制逐列列成表格：本專案有、聲部、聲部組（以樂器為單位，旁邊寫出會跟著改的其他編號）。
聲部的增刪與順序只在這張表：拖動列時分譜跟著聲部走，雙擊改名時分譜留在原位，取消勾選「本專案有」時
那份分譜移到未分組，表格下方寫出移了哪幾份；與另一列是同一聲部（舊專案留下的重名）的列標紅。
另有載入預設樂器表、從檔名擷取、匯出編制表與編輯人數（EnsembleDialog）。

元件不依賴主視窗：帶入專案後以 show_group 顯示任何一個群組；修改一律經專案的編輯操作立即寫入，
再發出 instruments_changed。上方的 header 版面預留給連線與時長。

使用範例：
    table = InstrumentationTable(project)
    table.instruments_changed.connect(refresh_parts)
    table.show_group(group)
"""
from collections import OrderedDict
from typing import Dict, List, Optional, TYPE_CHECKING
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QComboBox, QLabel, QMessageBox,
    QAbstractItemView, QMenu, QDialog, QTableWidget, QTableWidgetItem, QSpinBox, QHeaderView,
)
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from core.constants import DEFAULT_HEADCOUNT, INSTRUMENT_PRESETS, SECTION_NAMES_EN, TEXT_EXTENSION
from core.locale import is_english, localized, t
from core.models import DuplicateVoiceError, FileInfo
from core.voice_names import distinct_voices, duplicate_rows
from services.file_service import FileService
from ui.widgets import ask_save_path

if TYPE_CHECKING:
    from core.models import Group, Project

# 表格的欄，依顯示順序；加欄時在這裡插入，再於 _fill_row 填值、_HEADERS 給標題
COLUMNS = ("present", "voice", "section", "follows")
_HEADERS = {
    "present": "instrumentation.present",
    "voice": "instrumentation.voice",
    "section": "instrumentation.section",
    "follows": "",
}
# 重名的列與本專案沒有的列的文字顏色
_DUPLICATE_COLOR = QColor("#e74c3c")
_ABSENT_COLOR = QColor("#888888")


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


class InstrumentationTable(QWidget):
    """編制表面板：顯示一個群組的編制，修改經專案的編輯操作寫入"""

    instruments_changed = Signal()

    def __init__(
        self, project: Optional["Project"] = None, parent=None, file_service: Optional[FileService] = None,
    ):
        """
        Args:
            project: 目前的專案（編輯操作、聲部組與建議人數）
            parent: 父元件
            file_service: 檔案服務（寫出匯出的編制表）；None 時自建
        """
        super().__init__(parent)
        self._project = project
        self._file_service = file_service or FileService()
        self._group: Optional["Group"] = None
        self._filling = False
        self._build_ui()

    @staticmethod
    def column(name: str) -> int:
        """欄的位置（COLUMNS 裡的名稱）"""
        return COLUMNS.index(name)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        title = QLabel(t("instrumentation.title"))
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)
        # 預留給連線（曲目 › 版本 › 樂章）與時長
        self.header = QVBoxLayout()
        layout.addLayout(self.header)
        self._preset_combo = QComboBox()
        self._preset_combo.addItem(t("instrument.load_preset"))
        self._preset_combo.addItems([localized(preset.name, preset.name_en) for preset in INSTRUMENT_PRESETS])
        self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        layout.addWidget(self._preset_combo)
        self._table = VoiceRowsTable()
        self._table.setColumnCount(len(COLUMNS))
        self._table.setHorizontalHeaderLabels([t(_HEADERS[name]) if _HEADERS[name] else "" for name in COLUMNS])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        header = self._table.horizontalHeader()
        for index in range(len(COLUMNS)):
            header.setSectionResizeMode(index, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.column("voice"), QHeaderView.Stretch)
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
        extra_row = QHBoxLayout()
        headcount_btn = QPushButton(t("instrument.headcount"))
        headcount_btn.clicked.connect(self._edit_headcount)
        extra_row.addWidget(headcount_btn)
        export_btn = QPushButton(t("instrument.export"))
        export_btn.clicked.connect(self._export_instruments)
        extra_row.addWidget(export_btn)
        layout.addLayout(extra_row)

    # --- 顯示 ---

    def set_project(self, project: "Project"):
        """切換到另一個專案（開啟或新增專案時）"""
        self._project = project

    def show_group(self, group: Optional["Group"]):
        """顯示群組的編制；不是群組時清空並停用，輸入的聲部才不會沒有地方寫入。只顯示，不寫入專案"""
        self._group = group
        self.setEnabled(group is not None)
        self._show_moved([])
        self._fill()

    def refresh(self):
        """依專案重畫目前群組的編制（在別處改了專案設定，例如樂團類型之後）"""
        self._fill()

    def _fill(self):
        """依群組重畫整張表（由程式填值，不觸發編輯操作）"""
        self._filling = True
        self._table.clearContents()
        voices = list(self._group.instruments) if self._group else []
        self._table.setRowCount(len(voices))
        duplicates = set(duplicate_rows(voices))
        for row, voice in enumerate(voices):
            self._fill_row(row, voice, row in duplicates)
        self._filling = False

    def _fill_row(self, row: int, voice: str, duplicate: bool):
        """填一列：本專案有的勾選框、聲部名稱（重名標紅、本專案沒有的變灰）、聲部組選單與影響範圍"""
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
        self._table.setItem(row, self.column("voice"), name)
        self._table.setCellWidget(row, self.column("section"), self._section_combo(voice))
        follows = self._project.same_instrument_voices(self._group, voice)
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
            combo.addItem(_section_label(section), section)
        combo.setCurrentIndex(options.index(current))
        combo.currentIndexChanged.connect(lambda _index, v=voice, c=combo: self._on_section_chosen(v, c))
        return combo

    def _show_moved(self, moved: List[FileInfo]):
        """在表格下方寫出移到未分組的分譜；沒有時收起"""
        self._notice.setText(
            t("instrumentation.moved_to_ungrouped", files="、".join(f.display_name for f in moved)) if moved else "",
        )
        self._notice.setVisible(bool(moved))

    def _changed(self, moved: Optional[List[FileInfo]] = None):
        """編輯操作完成後：重畫、寫出移到未分組的分譜、發出 instruments_changed"""
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

    def _on_section_chosen(self, voice: str, combo: QComboBox):
        """改聲部組：同一樂器的其他編號一起改"""
        if self._filling or self._group is None:
            return
        self._project.set_section(self._group, voice, combo.currentData())
        self._changed()

    def _on_preset_selected(self, index: int):
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
        return sorted({index.row() for index in self._table.selectedIndexes()})

    def _remove_selected(self):
        """移除選取的列；它們的分譜移到未分組"""
        rows = self._selected_rows()
        if not rows or self._group is None:
            return
        self._changed(self._project.remove_instruments(self._group, rows))

    def _show_context_menu(self, pos):
        if self._table.itemAt(pos) is None:
            return
        menu = QMenu(self)
        remove_action = menu.addAction(t("instrument.remove_context"))
        if menu.exec(self._table.viewport().mapToGlobal(pos)) == remove_action:
            self._remove_selected()

    def _auto_extract(self):
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

    def _edit_headcount(self):
        """開啟編輯人數的對話框；按下儲存後寫入專案"""
        if self._project is None or self._group is None or not self._group.instruments:
            return
        dialog = EnsembleDialog(self._project, self._group, self)
        if dialog.exec() == QDialog.Accepted:
            dialog.apply()

    def _export_instruments(self):
        """匯出編制表為文字檔；以 \\n 分行，寫檔時換成這個平台的換行"""
        instruments = list(self._group.instruments) if self._group else []
        if not instruments:
            return
        path = ask_save_path(
            self, t("filedialog.export_instruments"), "", t("filedialog.text_files"), TEXT_EXTENSION,
            self._file_service,
        )
        if not path:
            return
        headcounts = self._project.instrument_headcounts
        grouped: Dict[str, List[tuple]] = OrderedDict()
        for inst in instruments:
            section = _section_label(self._project.section_of(inst))
            grouped.setdefault(section, []).append((inst, headcounts.get(inst, DEFAULT_HEADCOUNT)))
        lines = [t("instrument.title"), "=" * 40, ""]
        total_parts = 0
        total_people = 0
        for section, items in grouped.items():
            lines.append(f"{section}：")
            for inst, count in items:
                lines.append(f"  {inst:<30s} x {count}")
                total_parts += 1
                total_people += count
            lines.append("")
        lines.append("=" * 40)
        lines.append(f"{total_parts} parts / {total_people} people")
        try:
            self._file_service.write_text_atomic(path, "\n".join(lines))
        except OSError as e:
            QMessageBox.critical(self, t("dialog.error"), str(e))
            return
        QMessageBox.information(self, t("dialog.info"), t("instrument.export_done", path=path))


def _section_label(section: str) -> str:
    """聲部組在介面上的名稱：介面為英文時換成英文名稱，自訂的值原樣顯示"""
    return SECTION_NAMES_EN.get(section, section) if is_english() else section


class EnsembleDialog(QDialog):
    """編輯一個群組各聲部的建議人數（聲部組在編制表裡改）"""

    def __init__(self, project: "Project", group: "Group", parent=None):
        """
        Args:
            project: 目前的專案
            group: 要編輯的群組
            parent: 父元件
        """
        super().__init__(parent)
        self._project = project
        self._voices = list(group.instruments)
        self.setWindowTitle(t("instrument.headcount_title"))
        self.resize(420, 400)
        layout = QVBoxLayout(self)
        self._table = QTableWidget(len(self._voices), 2)
        self._table.setHorizontalHeaderLabels([t("instrument.headcount_instrument"), t("instrument.headcount_column")])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._spins: List[QSpinBox] = []
        for row, voice in enumerate(self._voices):
            item = QTableWidgetItem(voice)
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            self._table.setItem(row, 0, item)
            spin = QSpinBox()
            spin.setRange(0, 99)
            spin.setValue(project.instrument_headcounts.get(voice, DEFAULT_HEADCOUNT))
            self._table.setCellWidget(row, 1, spin)
            self._spins.append(spin)
        layout.addWidget(self._table)
        buttons = QHBoxLayout()
        buttons.addStretch()
        ok_btn = QPushButton(t("catalog.save"))
        ok_btn.clicked.connect(self.accept)
        buttons.addWidget(ok_btn)
        cancel_btn = QPushButton(t("catalog.cancel"))
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        layout.addLayout(buttons)

    def apply(self):
        """把對話框的人數逐聲部寫進專案"""
        self._project.update_ensemble(
            headcounts={voice: spin.value() for voice, spin in zip(self._voices, self._spins)},
        )
