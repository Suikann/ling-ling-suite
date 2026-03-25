# -*- coding: utf-8 -*-
"""
樂器表編輯器（PySide6）

使用 QListWidget 內建拖拉排序，效能遠優於逐一建立元件。
支援雙擊直接編輯樂器名稱、匯出編制表、編輯建議人數。
"""
import os
from collections import OrderedDict
from typing import Dict, List, Optional, TYPE_CHECKING
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidgetItem,
    QLineEdit, QPushButton, QComboBox, QLabel, QMessageBox,
    QAbstractItemView, QMenu, QDialog, QTableWidget,
    QTableWidgetItem, QSpinBox, QHeaderView, QFileDialog,
)
from ui.widgets import DragListWidget
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from core.locale import t

if TYPE_CHECKING:
    from core.models import Project


class InstrumentListEditor(QWidget):
    """樂器表編輯面板"""

    instruments_changed = Signal(list)

    def __init__(self, project: Optional["Project"] = None, parent=None):
        super().__init__(parent)
        self._project = project
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
        from core.constants import INSTRUMENT_PRESETS
        from core.locale import get_locale
        locale = get_locale()
        return [
            preset.name_en if locale == "en" else preset.name
            for preset in INSTRUMENT_PRESETS
        ]

    def _on_preset_selected(self, index: int):
        if index <= 0:
            return
        from core.constants import INSTRUMENT_PRESETS
        preset_idx = index - 1
        if preset_idx < len(INSTRUMENT_PRESETS):
            instruments = list(INSTRUMENT_PRESETS[preset_idx].instruments)
            self.set_instruments(instruments)
        self._preset_combo.blockSignals(True)
        self._preset_combo.setCurrentIndex(0)
        self._preset_combo.blockSignals(False)

    def _add_instrument(self):
        name = self._entry.text().strip()
        if not name:
            return
        item = QListWidgetItem(name)
        item.setFlags(item.flags() | Qt.ItemIsEditable)
        self._list.addItem(item)
        self._entry.clear()
        self._notify()

    def _remove_selected(self):
        items = self._list.selectedItems()
        if not items:
            return
        for item in items:
            self._list.takeItem(self._list.row(item))
        self._notify()

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
            self._notify()

    def _on_item_edited(self, item):
        """雙擊編輯樂器名稱後觸發"""
        if self._editing:
            return
        new_text = item.text().strip()
        if not new_text:
            self._editing = True
            self._list.takeItem(self._list.row(item))
            self._editing = False
        self._notify()

    def _on_rows_moved(self):
        self._notify()

    def _auto_extract(self):
        if not self._group:
            QMessageBox.information(self, t("dialog.info"), t("instrument.auto_extract.empty"))
            return
        all_filenames = [f.display_name for f in self._group.files]
        if not all_filenames:
            QMessageBox.information(self, t("dialog.info"), t("instrument.auto_extract.empty"))
            return
        from core.template_engine import extract_instruments_from_filenames
        extracted = extract_instruments_from_filenames(all_filenames)
        seen = set()
        unique = []
        for inst in extracted:
            if inst not in seen:
                seen.add(inst)
                unique.append(inst)
        if not unique:
            QMessageBox.information(self, t("dialog.info"), t("instrument.auto_extract.empty"))
            return
        preview = "\n".join(f"  {i+1}. {name}" for i, name in enumerate(unique))
        result = QMessageBox.question(
            self, t("instrument.auto_extract.title"),
            t("instrument.auto_extract.confirm", instruments=preview),
        )
        if result == QMessageBox.Yes:
            self.set_instruments(unique)

    def _edit_headcount(self):
        """開啟建議人數編輯對話框"""
        instruments = self.get_instruments()
        if not instruments:
            return
        from core.constants import detect_instrument_section
        headcounts = {}
        sections = {}
        if self._project:
            headcounts = dict(self._project.instrument_headcounts)
            sections = dict(self._project.instrument_sections)
        dlg = QDialog(self)
        dlg.setWindowTitle(t("instrument.headcount_title"))
        dlg.resize(500, 400)
        lay = QVBoxLayout(dlg)
        table = QTableWidget(len(instruments), 3)
        table.setHorizontalHeaderLabels([
            t("instrument.headcount_instrument"),
            t("instrument.headcount_section"),
            t("instrument.headcount_column"),
        ])
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)
        spinboxes = []
        section_items = []
        for row, inst in enumerate(instruments):
            name_item = QTableWidgetItem(inst)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 0, name_item)
            section = sections.get(inst, detect_instrument_section(inst))
            section_item = QTableWidgetItem(section)
            table.setItem(row, 1, section_item)
            section_items.append(section_item)
            spin = QSpinBox()
            spin.setMinimum(0)
            spin.setMaximum(99)
            spin.setValue(headcounts.get(inst, 1))
            table.setCellWidget(row, 2, spin)
            spinboxes.append(spin)
        lay.addWidget(table)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton(t("catalog.save"))
        ok_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton(t("catalog.cancel"))
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)
        if dlg.exec() != QDialog.Accepted:
            return
        if self._project:
            for row, inst in enumerate(instruments):
                self._project.instrument_headcounts[inst] = spinboxes[row].value()
                self._project.instrument_sections[inst] = section_items[row].text().strip()

    def _export_instruments(self):
        """匯出編制表為文字檔"""
        instruments = self.get_instruments()
        if not instruments:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, t("filedialog.export_instruments"), "",
            f"{t('filedialog.text_files')} (*.txt)",
        )
        if not path:
            return
        from core.constants import detect_instrument_section, SECTION_NAMES_EN
        from core.locale import get_locale
        headcounts = {}
        sections_map = {}
        if self._project:
            headcounts = self._project.instrument_headcounts
            sections_map = self._project.instrument_sections
        grouped: Dict[str, List[tuple]] = OrderedDict()
        for inst in instruments:
            section = sections_map.get(inst, detect_instrument_section(inst))
            if get_locale() == "en":
                section = SECTION_NAMES_EN.get(section, section)
            if section not in grouped:
                grouped[section] = []
            count = headcounts.get(inst, 1)
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
        with open(path, "w", encoding="utf-8") as f:
            f.write("\r\n".join(lines))
        QMessageBox.information(
            self, t("dialog.info"),
            t("instrument.export_done", path=path),
        )

    def get_instruments(self) -> List[str]:
        return [self._list.item(i).text() for i in range(self._list.count())]

    def set_instruments(self, instruments: List[str]):
        self._editing = True
        self._list.clear()
        for name in instruments:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemIsEditable)
            self._list.addItem(item)
        self._editing = False
        self._notify()

    def _notify(self):
        self.instruments_changed.emit(self.get_instruments())
