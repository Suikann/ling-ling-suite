# -*- coding: utf-8 -*-
"""
譜庫視窗的版本頁（原型 #60 版本頁的變體 A）

兩欄：左邊是編制表（與主視窗同一個元件 ui/instrumentation_table.py，以版本頁模式顯示），每個聲部寫出推算的狀態
（分譜列登記了 Drive 檔案就是有，沒有就是缺，不能手改）與登記的檔，最上面固定一列總譜；
右邊是「資料夾裡沒有登記的檔」，選一個檔、再選左邊的聲部或總譜，按「指定」。
指定給已登記了檔的聲部時先問：按儲存後改登記新的那份，原本那份丟進 Drive 垃圾桶（30 天內可救回）。

編制的改動與指定都只記在這一頁，按「儲存」才寫進譜庫：編制走存檔時寫回譜庫的同一套比對
（core/catalog_sync.py，編制寫入紀錄的來源記譜庫視窗，衝突時開衝突畫面選邊），寫完再登記指定的檔
（CatalogService.register_files）。按「放棄改動」或換版本、樂章時重新讀取，沒儲存的改動丟掉。

編制表以一個只在這一頁的專案顯示：一個連到這一處的群組，快照就是讀到的那處，所以編制表的改動就是待寫入的改動。
讀寫都經譜庫視窗在背景執行（讀不到、寫不進去時視窗頂端顯示提示列）。

使用範例：
    page = EditionPagePanel(detail, window._read, window._write, lambda: email, showing, voice_candidates)
    page.actions.addWidget(rename_button)   # 版本頁的其他入口（套用命名格式、下載、存入紀錄）
    pdfs = page.folder_pdfs()               # 版本資料夾裡全部的 PDF（一般下載）
"""
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QSplitter, QVBoxLayout, QWidget,
)

from core.catalog_models import Edition, EditionPage, FolderPdf, PieceDetail
from core.catalog_sync import WriteSource, place_writes
from core.constants import DEFAULT_ORCHESTRA_TYPE
from core.locale import t
from core.models import Project
from core.voice_names import voice_key
from core.voice_suggestions import VoiceCandidates
from services.catalog_service import CatalogService, PendingRegistration
from ui.instrumentation_table import SCORE_TARGET, InstrumentationTable

# 介面上的樂章選單裡，整首的資料
_WHOLE = ""


class EditionPagePanel(QWidget):
    """版本頁：編制表加沒登記的檔，按儲存才寫進譜庫"""

    def __init__(
        self, detail: PieceDetail, read: Callable, write: Callable, operator: Callable[[], str],
        showing: Callable[[], bool], voice_candidates: Optional[VoiceCandidates] = None, parent=None,
    ):
        """
        Args:
            detail: 曲目完整資訊（版本與樂章的選單）
            read: 譜庫視窗的背景讀取（CatalogWindow._read 的形式：讀取函式、完成、讀不到、重試、曲名）
            write: 譜庫視窗的背景寫入（CatalogWindow._write 的形式：動作名稱、寫入函式、完成、曲名）
            operator: 操作者（登入的 email），寫進編制寫入紀錄
            showing: 這一頁是否還在顯示（背景讀寫交回時頁面已換掉就不再填回）
            voice_candidates: 編制表輸入聲部名稱時自動完成的候選來源
            parent: 父元件
        """
        super().__init__(parent)
        self._detail = detail
        self._read = read
        self._write = write
        self._operator = operator
        self._showing = showing
        self._page: Optional[EditionPage] = None
        self._project = Project()
        # 指定了、還沒儲存的檔：聲部（群組裡的寫法）→ 檔，以及總譜列的
        self._assigned: Dict[str, FolderPdf] = {}
        self._score_assigned: Optional[FolderPdf] = None
        self._build_ui(voice_candidates)
        self._load()

    def _build_ui(self, voice_candidates: Optional[VoiceCandidates]):
        """排出版本與樂章選單、其他入口、左邊的編制表、右邊沒登記的檔，以及底下的儲存列"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        selector = QHBoxLayout()
        selector.addWidget(QLabel(t("catalog.page.edition")))
        self._edition_combo = QComboBox()
        for edition in self._detail.editions:
            self._edition_combo.addItem(edition.edition_label or edition.publisher or edition.id, edition.id)
        active = self._edition_combo.findData(self._detail.piece.active_edition_id)
        self._edition_combo.setCurrentIndex(max(active, 0))
        self._edition_combo.currentIndexChanged.connect(lambda _index: self._load())
        selector.addWidget(self._edition_combo)
        selector.addWidget(QLabel(t("catalog.page.movement")))
        self._movement_combo = QComboBox()
        self._movement_combo.addItem(t("catalog.link.whole"), _WHOLE)
        for movement in self._detail.movements:
            self._movement_combo.addItem(
                " ".join(part for part in (movement.movement_number, movement.name) if part), movement.id,
            )
        self._movement_combo.currentIndexChanged.connect(lambda _index: self._load())
        selector.addWidget(self._movement_combo)
        selector.addStretch()
        layout.addLayout(selector)
        # 版本頁的其他入口（套用命名格式，以及之後的一般下載、存入紀錄）
        self.actions = QHBoxLayout()
        layout.addLayout(self.actions)
        splitter = QSplitter(Qt.Horizontal)
        self._table = InstrumentationTable(self._project, voice_candidates=voice_candidates, edition_page=True)
        self._table.instruments_changed.connect(self._on_instruments_changed)
        splitter.addWidget(self._table)
        splitter.addWidget(self._build_files_box())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, stretch=1)
        save_row = QHBoxLayout()
        self._unsaved = QLabel()
        self._unsaved.setStyleSheet("color: #2980b9;")
        save_row.addWidget(self._unsaved)
        save_row.addStretch()
        discard = QPushButton(t("catalog.page.discard"))
        discard.clicked.connect(self._load)
        save_row.addWidget(discard)
        self._save_button = QPushButton(t("catalog.save"))
        self._save_button.clicked.connect(lambda: self._save({}))
        save_row.addWidget(self._save_button)
        layout.addLayout(save_row)

    def _build_files_box(self) -> QGroupBox:
        """右欄：資料夾裡沒有登記的檔、提示與「指定」按鈕"""
        self._files_box = QGroupBox(t("catalog.page.unregistered", count=0))
        box = QVBoxLayout(self._files_box)
        self._files = QListWidget()
        box.addWidget(self._files, stretch=1)
        self._hint = QLabel(t("catalog.page.assign_hint"))
        self._hint.setStyleSheet("color: gray;")
        self._hint.setWordWrap(True)
        box.addWidget(self._hint)
        assign = QPushButton(t("catalog.page.assign"))
        assign.clicked.connect(self._assign)
        box.addWidget(assign)
        return self._files_box

    # --- 讀取與顯示 ---

    def selected_edition(self) -> Optional[Edition]:
        """選單選的版本；曲目沒有版本時為 None"""
        edition_id = self._edition_combo.currentData()
        return next((edition for edition in self._detail.editions if edition.id == edition_id), None)

    def folder_pdfs(self) -> List[FolderPdf]:
        """版本資料夾（含子資料夾）裡全部的 PDF（一般下載從這裡挑）；還沒讀到時是空的"""
        return list(self._page.pdfs) if self._page is not None else []

    def _place_ids(self):
        """選單選的版本與樂章（整首為空字串）"""
        return self._edition_combo.currentData() or "", self._movement_combo.currentData() or _WHOLE

    def _load(self):
        """經譜庫服務在背景讀這一處的版本頁；沒儲存的改動丟掉"""
        edition_id, movement_id = self._place_ids()
        self._page = None
        self._assigned = {}
        self._score_assigned = None
        self._table.setEnabled(False)
        self._save_button.setEnabled(False)
        if not edition_id:
            return

        def done(page: EditionPage):
            """讀到了：換成這一處"""
            if self._showing() and self._place_ids() == (edition_id, movement_id):
                self._show_page(page)

        def unreadable():
            """讀不到：清空沒登記的檔，編制表維持停用"""
            if self._showing():
                self._files.clear()

        self._read(
            lambda catalog: catalog.edition_page(edition_id, movement_id, DEFAULT_ORCHESTRA_TYPE),
            done, unreadable, self._load, self._detail.piece.title,
        )

    def _show_page(self, page: EditionPage):
        """以讀到的那一處建出只在這一頁的專案（一個連到這處的群組），顯示編制表與沒登記的檔"""
        self._page = page
        self._project = Project(orchestra_type=DEFAULT_ORCHESTRA_TYPE)
        group = self._project.add_group(page.place.piece_title or self._detail.piece.title, t("group.score_label"))
        self._project.link_group(group, page.place)
        self._project.mark_saved()
        self._project.subscribe(self._show_unsaved)
        self._table.set_project(self._project)
        self._table.show_group(group)
        self._save_button.setEnabled(True)
        self._show_files()

    def _group(self):
        """這一頁的群組"""
        return self._project.groups[0] if self._project.groups else None

    def _show_files(self):
        """編制表寫出各列的狀態與登記的檔，右欄列出還沒指定的沒登記的檔"""
        page = self._page
        if page is None:
            return
        self._table.show_files(
            {name: registration.file_name or registration.file_id for name, registration in page.registrations.items()},
            {voice: pdf.path for voice, pdf in self._assigned.items()},
            score_registered=(page.score.file_name or page.score.file_id) if page.score else "",
            score_assigned=self._score_assigned.path if self._score_assigned else "",
        )
        taken = {pdf.file.id for pdf in self._pending_files()}
        self._files.clear()
        for pdf in page.unregistered:
            if pdf.file.id in taken:
                continue
            item = QListWidgetItem(pdf.path)
            item.setData(Qt.UserRole, pdf)
            self._files.addItem(item)
        self._files_box.setTitle(t("catalog.page.unregistered", count=self._files.count()))
        self._show_unsaved()

    def _pending_files(self):
        """指定了、還沒儲存的檔"""
        return list(self._assigned.values()) + ([self._score_assigned] if self._score_assigned else [])

    def _show_unsaved(self):
        """編制改了或有指定了的檔時，儲存列寫出還沒儲存"""
        unsaved = self._project.is_modified() or bool(self._pending_files())
        self._unsaved.setText(t("catalog.page.unsaved") if unsaved else "")

    def _on_instruments_changed(self):
        """編制表改了聲部清單：指定給已不在清單裡的聲部的檔放回右欄"""
        group = self._group()
        keys = {voice_key(name) for name in group.instruments} if group else set()
        self._assigned = {voice: pdf for voice, pdf in self._assigned.items() if voice_key(voice) in keys}
        self._show_files()

    # --- 指定與儲存 ---

    def _assign(self):
        """把右欄選的檔指定給左邊選取的聲部或總譜列；那裡已登記了檔時先問要不要覆蓋"""
        item = self._files.currentItem()
        target = self._table.selected_target()
        if self._page is None or item is None or target is None:
            self._hint.setText(t("catalog.page.pick_both"))
            return
        pdf: FolderPdf = item.data(Qt.UserRole)
        registration = self._page.score if target == SCORE_TARGET else self._page.registration(target)
        if registration is not None:
            name = t("catalog.page.score") if target == SCORE_TARGET else target
            answer = QMessageBox.question(self, t("catalog.page.overwrite_title"), t(
                "catalog.page.overwrite", voice=name, old=registration.file_name or registration.file_id,
                new=pdf.path,
            ))
            if answer != QMessageBox.Yes:
                return
        if target == SCORE_TARGET:
            self._score_assigned = pdf
        else:
            self._assigned = {v: p for v, p in self._assigned.items() if voice_key(v) != voice_key(target)}
            self._assigned[target] = pdf
        self._hint.setText(t("catalog.page.assign_hint"))
        self._show_files()

    def _save(self, decisions: dict):
        """按儲存：編制的改動走寫回譜庫的比對（衝突時開衝突畫面選邊再寫），寫完再登記指定的檔，最後重新讀取

        Args:
            decisions: 衝突的選擇（群組 ID → 各格的選擇）；第一次按儲存時為空
        """
        if self._page is None:
            return
        edition_id, movement_id = self._place_ids()
        writes = place_writes(self._project)
        pending = PendingRegistration(
            edition_id, movement_id, {voice: pdf.file for voice, pdf in self._assigned.items()},
            score=self._score_assigned.file if self._score_assigned else None,
        ) if self._pending_files() else None
        operator = self._operator()

        def save(catalog: CatalogService):
            """寫回編制，沒有待選邊的衝突才登記指定的檔"""
            outcome = None
            if writes:
                outcome = catalog.sync_instrumentation(
                    writes, self._project.orchestra_type, operator, WriteSource.CATALOG_WINDOW, decisions,
                )
                if not outcome.written:
                    return outcome
            if pending is not None:
                catalog.register_files(pending)
            return outcome

        def done(outcome):
            """寫好了就重新讀取；有衝突時選邊後再儲存一次"""
            if not self._showing():
                return
            if outcome is not None and not outcome.written:
                from ui.catalog_conflict_dialog import CatalogConflictDialog
                dialog = CatalogConflictDialog(outcome, self)
                if dialog.exec() != QDialog.Accepted:
                    return
                merged = {group: dict(chosen) for group, chosen in decisions.items()}
                for group_id, chosen in dialog.decisions().items():
                    merged[group_id] = {**merged.get(group_id, {}), **chosen}
                self._save(merged)
                return
            self._load()

        self._write(t("catalog.save"), save, done, self._detail.piece.title)
