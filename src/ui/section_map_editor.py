# -*- coding: utf-8 -*-
"""
聲部組對照的編輯（原型 #60 聲部組對照的變體 B）

譜庫視窗裡編輯全社團共用的聲部組對照：每個聲部組一欄（最前面是「未歸類」，社團對照還沒有的樂器），
以樂器為單位在欄與欄之間拖拉歸類；每個樂器標出譜庫裡用到幾首，欄標題寫出幾種樂器。
樂團類型選單切換國樂團、管弦樂團兩套，搜尋框篩選每一欄。這裡只收集編輯結果：
按「儲存」發出 save_requested，由譜庫視窗經譜庫服務寫入（CatalogService.sync_section_map）；
改過的樂器怎麼算在 core/catalog_sync.py 的 section_map_items。

使用範例：
    editor = SectionMapEditor(catalog.section_map(), OrchestraType.CHINESE)
    editor.save_requested.connect(lambda: save(editor.changes()))
"""
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from core.catalog_models import SectionMapEntry
from core.catalog_sync import SyncItem, section_map_items
from core.constants import OrchestraType
from core.locale import t
from core.sections import section_label, section_options


class SectionMapEditor(QWidget):
    """聲部組對照的編輯器：每個聲部組一欄，拖拉樂器歸類，按儲存發出 save_requested"""

    save_requested = Signal()

    def __init__(
        self, entries: Mapping[OrchestraType, Sequence[SectionMapEntry]], orchestra_type: OrchestraType,
        parent: Optional[QWidget] = None,
    ):
        """
        Args:
            entries: 讀到的對照，每個樂團類型一套（CatalogService.section_map）
            orchestra_type: 一開始顯示哪一套
            parent: 父元件
        """
        super().__init__(parent)
        self._entries = {orchestra: list(entries.get(orchestra, [])) for orchestra in OrchestraType}
        # 每一套的欄：聲部組（未歸類為空字串）、外框（標題）與樂器清單
        self._columns: Dict[OrchestraType, List[Tuple[str, QGroupBox, QListWidget]]] = {}
        self._build_ui()
        self._orchestra.setCurrentIndex(self._orchestra.findData(orchestra_type.value))
        self._refresh()

    def orchestra_type(self) -> OrchestraType:
        """目前顯示的樂團類型"""
        return OrchestraType(self._orchestra.currentData())

    def changes(self) -> Dict[OrchestraType, List[SyncItem]]:
        """編輯後改過的樂器：樂團類型 → 聲部組的格子；沒有改動的樂團類型不列"""
        changed = {}
        for orchestra, columns in self._columns.items():
            chosen = {
                column.item(row).data(Qt.UserRole): section
                for section, _, column in columns for row in range(column.count())
            }
            items = section_map_items(self._entries[orchestra], chosen)
            if items:
                changed[orchestra] = items
        return changed

    def _build_ui(self) -> None:
        """排出說明、樂團類型選單、搜尋框與儲存按鈕，下面是每一套的欄（一次顯示一套）"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        intro = QLabel(t("catalog.sections.intro"))
        intro.setWordWrap(True)
        layout.addWidget(intro)
        top = QHBoxLayout()
        top.addWidget(QLabel(t("catalog.sections.orchestra")))
        self._orchestra = QComboBox()
        for orchestra in OrchestraType:
            self._orchestra.addItem(t(f"orchestra.{orchestra.value}"), orchestra.value)
        top.addWidget(self._orchestra)
        self._search = QLineEdit()
        self._search.setPlaceholderText(t("catalog.sections.search"))
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._refresh)
        top.addWidget(self._search)
        top.addStretch()
        self._save = QPushButton(t("catalog.sections.save"))
        self._save.clicked.connect(self.save_requested.emit)
        top.addWidget(self._save)
        layout.addLayout(top)
        self._pages = QStackedWidget()
        for orchestra in OrchestraType:
            self._pages.addWidget(self._build_page(orchestra))
        self._orchestra.currentIndexChanged.connect(self._pages.setCurrentIndex)
        layout.addWidget(self._pages, stretch=1)

    def _build_page(self, orchestra: OrchestraType) -> QWidget:
        """一套的欄：未歸類、這個樂團類型的聲部組，以及對照裡存了、不在選項裡的值（例如舊的自訂文字）"""
        page = QWidget()
        row = QHBoxLayout(page)
        row.setContentsMargins(0, 0, 0, 0)
        entries = self._entries[orchestra]
        sections = ["", *section_options(orchestra)]
        sections += list(dict.fromkeys(e.section for e in entries if e.section and e.section not in sections))
        columns = []
        for section in sections:
            box = QGroupBox()
            box_layout = QVBoxLayout(box)
            column = QListWidget()
            column.setDragDropMode(QAbstractItemView.DragDrop)
            column.setDefaultDropAction(Qt.MoveAction)
            for entry in entries:
                if entry.section == section:
                    item = QListWidgetItem(t("catalog.sections.item", instrument=entry.instrument, count=entry.pieces))
                    item.setData(Qt.UserRole, entry.instrument)
                    column.addItem(item)
            column.model().rowsInserted.connect(self._refresh)
            column.model().rowsRemoved.connect(self._refresh)
            box_layout.addWidget(column)
            row.addWidget(box)
            columns.append((section, box, column))
        self._columns[orchestra] = columns
        return page

    def _refresh(self, *_) -> None:
        """樂器移動或搜尋字改了：更新各欄標題的樂器數、依搜尋字隱藏不符的樂器、有改動才能按儲存"""
        query = self._search.text().strip().casefold()
        for columns in self._columns.values():
            for section, box, column in columns:
                name = section_label(section) if section else t("catalog.sections.unclassified")
                box.setTitle(t("catalog.sections.column", section=name, count=column.count()))
                for row in range(column.count()):
                    item = column.item(row)
                    item.setHidden(bool(query) and query not in str(item.data(Qt.UserRole)).casefold())
        self._save.setEnabled(bool(self.changes()))
