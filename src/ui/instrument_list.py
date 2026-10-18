# -*- coding: utf-8 -*-
"""
樂器表編輯器（PySide6）

使用 QListWidget 內建拖拉排序，效能遠優於逐一建立元件。
支援雙擊直接編輯樂器名稱、匯出編制表、編輯建議人數與聲部組（EnsembleDialog，聲部組以樂器為單位）。
顯示目前群組的樂器表；使用者的修改立即透過專案的編輯操作寫入，再發出 instruments_changed。
"""
from collections import OrderedDict
from typing import Dict, List, Optional, TYPE_CHECKING
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidgetItem,
    QLineEdit, QPushButton, QComboBox, QLabel, QMessageBox,
    QAbstractItemView, QMenu, QDialog, QTableWidget,
    QTableWidgetItem, QSpinBox, QHeaderView,
)
from services.file_service import FileService
from ui.widgets import DragListWidget, ask_save_path
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from core.constants import DEFAULT_HEADCOUNT, INSTRUMENT_PRESETS, SECTION_NAMES_EN, TEXT_EXTENSION
from core.locale import is_english, localized, t
from core.models import DuplicateVoiceError
from core.voice_names import distinct_voices, same_instrument

if TYPE_CHECKING:
    from core.models import Group, Project


class InstrumentListEditor(QWidget):
    """樂器表編輯面板"""

    instruments_changed = Signal(list)

    def __init__(
        self, project: Optional["Project"] = None, parent=None, file_service: Optional[FileService] = None,
    ):
        """
        Args:
            project: 目前的專案（編制的建議人數與聲部組）
            parent: 父元件
            file_service: 檔案服務（寫出匯出的編制表）；None 時自建
        """
        super().__init__(parent)
        self._project = project
        self._file_service = file_service or FileService()
        self._group = None
        self._editing = False
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        title = QLabel(t("instrument.title"))
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(title)
        self._preset_combo = QComboBox()
        self._preset_combo.addItem(t("instrument.load_preset"))
        self._preset_combo.addItems(self._build_preset_options())
        self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        layout.addWidget(self._preset_combo)
        self._list = DragListWidget()
        self._list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._list.model().rowsMoved.connect(self._on_rows_moved)
        self._list.setContextMenuPolicy(Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        self._list.itemChanged.connect(self._on_item_edited)
        layout.addWidget(self._list)
        QShortcut(QKeySequence("Delete"), self._list, self._remove_selected)
        QShortcut(QKeySequence("Ctrl+A"), self._list, self._select_all)
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
        del_btn = QPushButton(t("instrument.auto_extract"))
        del_btn.clicked.connect(self._auto_extract)
        btn_row.addWidget(del_btn)
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

    def _build_preset_options(self) -> List[str]:
        return [localized(preset.name, preset.name_en) for preset in INSTRUMENT_PRESETS]

    def _on_preset_selected(self, index: int):
        if index <= 0:
            return
        preset_idx = index - 1
        if preset_idx < len(INSTRUMENT_PRESETS):
            self._fill(INSTRUMENT_PRESETS[preset_idx].instruments)
            self._commit()
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
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        self._list.addItem(item)
        self._entry.clear()
        self.instruments_changed.emit(self.get_instruments())

    def _explain_duplicate(self, name: str, error: DuplicateVoiceError):
        """說明新名稱與樂器表的哪一列是同一聲部"""
        QMessageBox.warning(
            self, t("dialog.warning"), t("instrument.duplicate", name=name, existing=error.existing),
        )

    def _remove_selected(self):
        items = self._list.selectedItems()
        if not items:
            return
        for item in items:
            self._list.takeItem(self._list.row(item))
        self._commit()

    def _select_all(self):
        self._list.selectAll()

    def _show_context_menu(self, pos):
        item = self._list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        remove_action = menu.addAction(t("instrument.remove_context"))
        action = menu.exec(self._list.mapToGlobal(pos))
        if action == remove_action:
            self._list.takeItem(self._list.row(item))
            self._commit()

    def _on_item_edited(self, item):
        """雙擊編輯樂器名稱後觸發：清空即移除；與其他列同一聲部時說明原因並還原名稱"""
        if self._editing:
            return
        new_text = item.text().strip()
        if not new_text:
            self._editing = True
            self._list.takeItem(self._list.row(item))
            self._editing = False
            self._commit()
            return
        if self._project is None or self._group is None:
            return
        row = self._list.row(item)
        try:
            self._project.rename_instrument(self._group, row, new_text)
        except DuplicateVoiceError as e:
            self._set_item_text(item, self._group.instruments[row])
            self._explain_duplicate(new_text, e)
            return
        self._set_item_text(item, new_text)
        self.instruments_changed.emit(self.get_instruments())

    def _set_item_text(self, item: QListWidgetItem, text: str):
        """由程式改寫一列的名稱，不再觸發改名"""
        self._editing = True
        item.setText(text)
        self._editing = False

    def _on_rows_moved(self):
        self._commit()

    def _auto_extract(self):
        if not self._group:
            QMessageBox.information(self, t("dialog.info"), t("instrument.auto_extract.empty"))
            return
        all_filenames = [f.display_name for f in self._group.files]
        if not all_filenames:
            QMessageBox.information(self, t("dialog.info"), t("instrument.auto_extract.empty"))
            return
        from core.template_engine import extract_instruments_from_filenames
        unique = distinct_voices(extract_instruments_from_filenames(all_filenames))
        if not unique:
            QMessageBox.information(self, t("dialog.info"), t("instrument.auto_extract.empty"))
            return
        preview = "\n".join(f"  {i+1}. {name}" for i, name in enumerate(unique))
        result = QMessageBox.question(
            self, t("instrument.auto_extract.title"),
            t("instrument.auto_extract.confirm", instruments=preview),
        )
        if result == QMessageBox.Yes:
            self._fill(unique)
            self._commit()

    def _edit_headcount(self):
        """開啟編輯人數與聲部組的對話框；按下儲存後寫入專案"""
        if not self.get_instruments() or self._project is None or self._group is None:
            return
        dialog = EnsembleDialog(self._project, self._group, self)
        if dialog.exec() == QDialog.Accepted:
            dialog.apply()

    def _export_instruments(self):
        """匯出編制表為文字檔；以 \n 分行，寫檔時換成這個平台的換行"""
        instruments = self.get_instruments()
        if not instruments:
            return
        path = ask_save_path(
            self, t("filedialog.export_instruments"), "", t("filedialog.text_files"), TEXT_EXTENSION,
            self._file_service,
        )
        if not path:
            return
        headcounts = self._project.instrument_headcounts if self._project else {}
        grouped: Dict[str, List[tuple]] = OrderedDict()
        for inst in instruments:
            section = _section_label(self._project.section_of(inst)) if self._project else ""
            if section not in grouped:
                grouped[section] = []
            count = headcounts.get(inst, DEFAULT_HEADCOUNT)
            grouped[section].append((inst, count))
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
        QMessageBox.information(
            self, t("dialog.info"),
            t("instrument.export_done", path=path),
        )

    def get_instruments(self) -> List[str]:
        return [self._list.item(i).text() for i in range(self._list.count())]

    def set_project(self, project: "Project"):
        """切換到另一個專案（開啟或新增專案時）"""
        self._project = project

    def show_group(self, group: Optional["Group"]):
        """顯示群組的樂器表；不是群組時清空並停用，輸入的樂器才不會沒有地方寫入。只顯示，不寫入專案"""
        self._group = group
        self.setEnabled(group is not None)
        self._fill(group.instruments if group else [])

    def _fill(self, instruments):
        """由程式填入清單，不寫入專案、不發出 instruments_changed"""
        self._editing = True
        self._list.clear()
        for name in instruments:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            self._list.addItem(item)
        self._editing = False

    def _commit(self):
        """使用者修改樂器表後寫入目前群組，並發出 instruments_changed"""
        instruments = self.get_instruments()
        if self._project and self._group is not None:
            self._project.set_instruments(self._group, instruments)
        self.instruments_changed.emit(instruments)


def _section_label(section: str) -> str:
    """聲部組在介面上的名稱：介面為英文時換成英文名稱，自訂的值原樣顯示"""
    return SECTION_NAMES_EN.get(section, section) if is_english() else section


class EnsembleDialog(QDialog):
    """編輯一個群組各聲部的建議人數與聲部組

    聲部組以樂器為單位：改一列時，同一樂器的其他編號跟著改，選單旁寫出會跟著改的編號。
    選項是專案樂團類型的那一套；已存的值不在選項裡（例如舊專案的自訂值）時另外列出。
    """

    def __init__(self, project: "Project", group: "Group", parent=None):
        """
        Args:
            project: 目前的專案
            group: 要編輯的群組
            parent: 父元件
        """
        super().__init__(parent)
        self._project = project
        self._group = group
        self._voices = list(group.instruments)
        self.setWindowTitle(t("instrument.headcount_title"))
        self.resize(560, 400)
        layout = QVBoxLayout(self)
        self._table = QTableWidget(len(self._voices), 4)
        self._table.setHorizontalHeaderLabels([
            t("instrument.headcount_instrument"), t("instrument.headcount_section"), "",
            t("instrument.headcount_column"),
        ])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._combos: List[QComboBox] = []
        self._spins: List[QSpinBox] = []
        for row, voice in enumerate(self._voices):
            self._table.setItem(row, 0, self._read_only_item(voice))
            self._table.setCellWidget(row, 1, self._section_combo(row, voice))
            follows = project.same_instrument_voices(group, voice)
            note = t("instrument.section_follows", voices="、".join(follows)) if follows else ""
            self._table.setItem(row, 2, self._read_only_item(note))
            spin = QSpinBox()
            spin.setRange(0, 99)
            spin.setValue(project.instrument_headcounts.get(voice, DEFAULT_HEADCOUNT))
            self._table.setCellWidget(row, 3, spin)
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

    @staticmethod
    def _read_only_item(text: str) -> QTableWidgetItem:
        """不能編輯的儲存格"""
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        return item

    def _section_combo(self, row: int, voice: str) -> QComboBox:
        """一列的聲部組選單：顯示介面語言的名稱，資料是專案存的中文名稱"""
        combo = QComboBox()
        current = self._project.section_of(voice)
        options = list(self._project.section_options())
        if current not in options:
            options.append(current)
        for section in options:
            combo.addItem(_section_label(section), section)
        combo.setCurrentIndex(options.index(current))
        combo.currentIndexChanged.connect(lambda _index, r=row: self._follow(r))
        self._combos.append(combo)
        return combo

    def _follow(self, row: int):
        """改了一列的聲部組後，同一樂器的其他列跟著改"""
        section = self._combos[row].currentData()
        for other, voice in enumerate(self._voices):
            if other != row and same_instrument(voice, self._voices[row]):
                combo = self._combos[other]
                combo.blockSignals(True)
                combo.setCurrentIndex(combo.findData(section))
                combo.blockSignals(False)

    def apply(self):
        """把對話框的內容寫進專案：人數逐聲部寫入，聲部組只寫改過的樂器"""
        self._project.update_ensemble(
            headcounts={voice: spin.value() for voice, spin in zip(self._voices, self._spins)},
        )
        for voice, combo in zip(self._voices, self._combos):
            if combo.currentData() != self._project.section_of(voice):
                self._project.set_section(self._group, voice, combo.currentData())
