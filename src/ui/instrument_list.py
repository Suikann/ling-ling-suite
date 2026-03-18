# -*- coding: utf-8 -*-
"""
樂器表編輯器（PySide6）

使用 QListWidget 內建拖拉排序，效能遠優於逐一建立元件。
"""
from typing import List, Optional, TYPE_CHECKING
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidgetItem,
    QLineEdit, QPushButton, QComboBox, QLabel, QMessageBox,
    QAbstractItemView, QMenu,
)
from ui.widgets import DragListWidget
from PySide6.QtCore import Signal
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
        self._list.setSelectionMode(QAbstractItemView.SingleSelection)
        self._list.model().rowsMoved.connect(self._on_rows_moved)
        from PySide6.QtCore import Qt as _Qt
        self._list.setContextMenuPolicy(_Qt.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self._list)
        QShortcut(QKeySequence("Delete"), self._list, self._remove_selected)
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
        remove_btn.setStyleSheet("background: #6b3020; color: #eed8d0;")
        remove_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(remove_btn)
        layout.addLayout(btn_row)

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
        self._list.addItem(name)
        self._entry.clear()
        self._notify()

    def _remove_selected(self):
        items = self._list.selectedItems()
        if not items:
            return
        for item in items:
            self._list.takeItem(self._list.row(item))
        self._notify()

    def _show_context_menu(self, pos):
        item = self._list.itemAt(pos)
        if not item:
            return
        menu = QMenu(self)
        remove_action = menu.addAction(t("instrument.remove"))
        action = menu.exec(self._list.mapToGlobal(pos))
        if action == remove_action:
            self._list.takeItem(self._list.row(item))
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

    def get_instruments(self) -> List[str]:
        return [self._list.item(i).text() for i in range(self._list.count())]

    def set_instruments(self, instruments: List[str]):
        self._list.clear()
        self._list.addItems(instruments)
        self._notify()

    def _notify(self):
        self.instruments_changed.emit(self.get_instruments())
