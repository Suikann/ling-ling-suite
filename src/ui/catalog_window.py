# -*- coding: utf-8 -*-
"""
譜庫瀏覽器視窗

以 Drive 資料夾內容為主體，使用者可為資料夾加上元資料（曲目資訊、演出紀錄等）。
"""
from dataclasses import replace
from typing import Dict, List, Optional
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QTreeWidget, QTreeWidgetItem, QLabel, QLineEdit, QPushButton,
    QToolBar, QStatusBar, QMessageBox, QComboBox, QFormLayout,
    QGroupBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QDialog, QDialogButtonBox, QInputDialog,
    QListWidget, QListWidgetItem,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QBrush
from core.locale import t
from core.catalog_constants import PartStatus, PerformanceStatus
from core.catalog_models import (
    Composer, Piece, Edition, Movement, Part, Performance, PieceDetail,
)
from services.sheets_service import SheetsService
from services.drive_service import DriveService
from services.google_auth_service import GoogleAuthService
from services.preferences_service import PreferencesService


_STATUS_COLORS = {
    PartStatus.AVAILABLE.value: QColor("#2ecc71"),
    PartStatus.MISSING.value: QColor("#e74c3c"),
    PartStatus.DAMAGED.value: QColor("#f39c12"),
    PartStatus.UNKNOWN.value: QColor("#95a5a6"),
}

_FOLDER_ICON = "\U0001f4c1 "
_PDF_ICON = "\U0001f4c4 "


class CatalogWindow(QMainWindow):
    """譜庫管理主視窗"""

    _FOLDER_MIME = "application/vnd.google-apps.folder"
    _PDF_MIME = "application/pdf"

    def __init__(
        self, auth_service: GoogleAuthService,
        preferences: PreferencesService, parent=None,
    ):
        super().__init__(parent)
        self._auth = auth_service
        self._prefs = preferences
        self._sheets: Optional[SheetsService] = None
        self._drive: Optional[DriveService] = None
        self._pieces: List[Piece] = []
        self._piece_by_folder: Dict[str, Piece] = {}
        self._current_detail: Optional[PieceDetail] = None
        self._composers: List[Composer] = []
        self.setWindowTitle(t("catalog.title"))
        self.resize(1200, 750)
        self._build_ui()
        QTimer.singleShot(100, self._init_services)

    def _build_ui(self):
        self._toolbar = QToolBar()
        self._toolbar.setMovable(False)
        self.addToolBar(self._toolbar)
        refresh_btn = QPushButton(t("catalog.toolbar.refresh"))
        refresh_btn.clicked.connect(self._refresh_all)
        self._toolbar.addWidget(refresh_btn)
        self._toolbar.addSeparator()
        self._search_entry = QLineEdit()
        self._search_entry.setPlaceholderText(t("catalog.toolbar.search"))
        self._search_entry.setFixedWidth(200)
        self._search_entry.textChanged.connect(self._on_search)
        self._toolbar.addWidget(self._search_entry)
        central = QWidget()
        self.setCentralWidget(central)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        splitter = QSplitter(Qt.Horizontal)
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setMinimumWidth(280)
        self._tree.itemExpanded.connect(self._on_expand)
        self._tree.currentItemChanged.connect(self._on_tree_select)
        splitter.addWidget(self._tree)
        self._detail_area = QWidget()
        self._detail_layout = QVBoxLayout(self._detail_area)
        self._detail_layout.setContentsMargins(4, 4, 4, 4)
        self._detail_placeholder = QLabel(t("catalog.drive.select_hint"))
        self._detail_placeholder.setAlignment(Qt.AlignCenter)
        self._detail_placeholder.setStyleSheet("color: gray; font-size: 14px;")
        self._detail_layout.addWidget(self._detail_placeholder)
        splitter.addWidget(self._detail_area)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter)
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

    def _init_services(self):
        """初始化 Google API 服務"""
        creds = self._auth.get_credentials()
        if not creds:
            self._status_bar.showMessage(t("catalog.no_connection"))
            return
        spreadsheet_id = self._prefs.get("catalog_spreadsheet_id") or ""
        if not spreadsheet_id:
            self._status_bar.showMessage(t("catalog.no_connection"))
            return
        try:
            self._sheets = SheetsService(creds, spreadsheet_id)
            self._drive = DriveService(creds)
            self._refresh_all()
        except Exception as e:
            self._status_bar.showMessage(t("catalog.error", error=str(e)))

    def _refresh_all(self):
        """重新載入 Drive 資料夾與元資料"""
        if not self._drive or not self._sheets:
            return
        root_id = self._prefs.get("catalog_root_folder_id") or ""
        if not root_id:
            self._status_bar.showMessage(t("catalog.no_connection"))
            return
        try:
            self._pieces = self._sheets.list_pieces()
            self._composers = self._sheets.list_composers()
            self._piece_by_folder = {
                p.active_edition_id: p for p in self._pieces
                if p.active_edition_id
            }
            for p in self._pieces:
                editions = self._sheets.list_editions(p.id)
                for e in editions:
                    if e.drive_folder_id:
                        self._piece_by_folder[e.drive_folder_id] = p
            self._tree.clear()
            self._load_drive_children(None, root_id)
            self._status_bar.showMessage(
                t("catalog.status.stats",
                  composers=len(self._composers),
                  pieces=len(self._pieces)),
            )
        except Exception as e:
            self._status_bar.showMessage(t("catalog.error", error=str(e)))

    # --- Drive 樹狀結構 ---

    def _load_drive_children(self, parent_node, folder_id: str):
        """載入 Drive 資料夾的子項目"""
        if not self._drive:
            return
        try:
            folders = self._drive.list_subfolders(folder_id)
            pdfs = self._drive.list_pdfs_in_folder(folder_id)
        except Exception:
            return
        for folder in folders:
            has_meta = folder["id"] in self._piece_by_folder
            label = folder["name"]
            if has_meta:
                label += "  *"
            node = QTreeWidgetItem([label])
            node.setData(0, Qt.UserRole, ("folder", folder["id"], folder["name"]))
            node.setData(0, Qt.UserRole + 1, False)
            placeholder = QTreeWidgetItem([t("catalog.loading")])
            node.addChild(placeholder)
            if parent_node is None:
                self._tree.addTopLevelItem(node)
            else:
                parent_node.addChild(node)
        for pdf in pdfs:
            node = QTreeWidgetItem([pdf["name"]])
            node.setData(0, Qt.UserRole, ("file", pdf["id"], pdf["name"]))
            if parent_node is None:
                self._tree.addTopLevelItem(node)
            else:
                parent_node.addChild(node)

    def _on_expand(self, item):
        """展開資料夾時載入子項目"""
        already_loaded = item.data(0, Qt.UserRole + 1)
        if already_loaded:
            return
        item.setData(0, Qt.UserRole + 1, True)
        item.takeChildren()
        data = item.data(0, Qt.UserRole)
        if data and data[0] == "folder":
            self._load_drive_children(item, data[1])

    def _on_search(self, text: str):
        """搜尋時篩選樹狀結構"""
        q = text.lower()
        for i in range(self._tree.topLevelItemCount()):
            item = self._tree.topLevelItem(i)
            self._filter_tree_item(item, q)

    def _filter_tree_item(self, item, q: str) -> bool:
        """遞迴篩選樹狀結構項目"""
        data = item.data(0, Qt.UserRole)
        name = data[2].lower() if data else ""
        match = not q or q in name
        child_match = False
        for i in range(item.childCount()):
            if self._filter_tree_item(item.child(i), q):
                child_match = True
        visible = match or child_match
        item.setHidden(not visible)
        return visible

    def _on_tree_select(self, current: QTreeWidgetItem, previous):
        """選取項目時更新右側面板"""
        if not current:
            return
        data = current.data(0, Qt.UserRole)
        if not data:
            return
        item_type, item_id, item_name = data
        if item_type == "folder":
            self._show_folder_detail(item_id, item_name)
        elif item_type == "file":
            self._show_file_detail(item_id, item_name)

    # --- 清除右側面板 ---

    def _clear_detail(self):
        """清除右側面板內容"""
        while self._detail_layout.count():
            child = self._detail_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    # --- 資料夾詳細 ---

    def _show_folder_detail(self, folder_id: str, folder_name: str):
        """顯示資料夾內容與元資料"""
        self._clear_detail()
        piece = self._piece_by_folder.get(folder_id)
        if piece:
            self._show_piece_detail(piece.id)
            return
        title = QLabel(folder_name)
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 4px;")
        self._detail_layout.addWidget(title)
        if self._drive:
            try:
                pdfs = self._drive.list_pdfs_in_folder(folder_id)
                if pdfs:
                    files_group = QGroupBox(
                        t("catalog.drive.files_in_folder", count=len(pdfs)),
                    )
                    files_layout = QVBoxLayout(files_group)
                    file_list = QListWidget()
                    for pdf in pdfs:
                        file_list.addItem(pdf["name"])
                    files_layout.addWidget(file_list)
                    self._detail_layout.addWidget(files_group)
            except Exception:
                pass
        tag_btn = QPushButton(t("catalog.drive.tag_as_piece"))
        tag_btn.clicked.connect(
            lambda: self._tag_folder_as_piece(folder_id, folder_name),
        )
        self._detail_layout.addWidget(tag_btn)
        self._detail_layout.addStretch()

    def _show_file_detail(self, file_id: str, file_name: str):
        """顯示檔案資訊"""
        self._clear_detail()
        title = QLabel(file_name)
        title.setStyleSheet("font-size: 16px; font-weight: bold; padding: 4px;")
        self._detail_layout.addWidget(title)
        info = QLabel(f"Drive ID: {file_id}")
        info.setStyleSheet("color: gray;")
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._detail_layout.addWidget(info)
        self._detail_layout.addStretch()

    def _tag_folder_as_piece(self, folder_id: str, folder_name: str):
        """將資料夾標記為一首曲目"""
        if not self._sheets:
            return
        try:
            edition_id = self._sheets.create_edition(
                Edition(piece_id="", publisher="", drive_folder_id=folder_id),
            )
            piece_id = self._sheets.create_piece(Piece(
                title=folder_name,
                active_edition_id=edition_id,
            ))
            edition = self._sheets.get_edition(edition_id)
            if edition:
                self._sheets.update_edition(
                    replace(edition, piece_id=piece_id),
                )
            self._refresh_all()
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    # --- 曲目詳細（含元資料頁籤） ---

    def _show_piece_detail(self, piece_id: str):
        """顯示曲目完整資訊"""
        if not self._sheets:
            return
        detail = self._sheets.get_piece_detail(piece_id)
        if not detail:
            return
        self._current_detail = detail
        self._clear_detail()
        tabs = QTabWidget()
        tabs.addTab(
            self._build_piece_info_tab(detail), t("catalog.piece.title"),
        )
        tabs.addTab(
            self._build_parts_tab(detail), t("catalog.part.title"),
        )
        tabs.addTab(
            self._build_performances_tab(detail), t("catalog.performance.title"),
        )
        tabs.addTab(
            self._build_movements_tab(detail), t("catalog.movement.title"),
        )
        tabs.addTab(
            self._build_editions_tab(detail), t("catalog.edition.title"),
        )
        self._detail_layout.addWidget(tabs)

    # --- 曲目資訊頁籤 ---

    def _build_piece_info_tab(self, detail: PieceDetail) -> QWidget:
        """建立曲目資訊頁籤"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        form = QFormLayout()
        piece = detail.piece
        self._piece_title_edit = QLineEdit(piece.title)
        form.addRow(t("catalog.piece.name"), self._piece_title_edit)
        self._piece_short_edit = QLineEdit(piece.title_short)
        form.addRow(t("catalog.piece.name_short"), self._piece_short_edit)
        self._piece_composer_combo = QComboBox()
        self._piece_composer_combo.setEditable(True)
        self._piece_composer_combo.addItem("")
        for c in self._composers:
            self._piece_composer_combo.addItem(c.name_short or c.name, c.id)
        idx = next(
            (i + 1 for i, c in enumerate(self._composers)
             if c.id == piece.composer_id), 0,
        )
        self._piece_composer_combo.setCurrentIndex(idx)
        form.addRow(t("catalog.piece.composer"), self._piece_composer_combo)
        self._piece_opus_edit = QLineEdit(piece.opus)
        form.addRow(t("catalog.piece.opus"), self._piece_opus_edit)
        self._piece_catalog_edit = QLineEdit(piece.catalog_number)
        form.addRow(t("catalog.piece.catalog_number"), self._piece_catalog_edit)
        self._piece_genre_edit = QLineEdit(piece.genre)
        form.addRow(t("catalog.piece.genre"), self._piece_genre_edit)
        self._piece_instrumentation_edit = QLineEdit(piece.instrumentation)
        form.addRow(t("catalog.piece.instrumentation"), self._piece_instrumentation_edit)
        self._piece_duration_edit = QLineEdit(piece.duration_minutes)
        self._piece_duration_edit.setFixedWidth(80)
        form.addRow(t("catalog.piece.duration"), self._piece_duration_edit)
        self._piece_difficulty_edit = QLineEdit(piece.difficulty_level)
        form.addRow(t("catalog.piece.difficulty"), self._piece_difficulty_edit)
        self._piece_notes_edit = QTextEdit(piece.notes)
        self._piece_notes_edit.setMaximumHeight(80)
        form.addRow(t("catalog.piece.notes"), self._piece_notes_edit)
        layout.addLayout(form)
        btn_row = QHBoxLayout()
        save_btn = QPushButton(t("catalog.save"))
        save_btn.clicked.connect(lambda: self._save_piece(detail))
        btn_row.addWidget(save_btn)
        del_btn = QPushButton(t("catalog.piece.delete"))
        del_btn.setStyleSheet("background-color: #c0392b; color: white;")
        del_btn.clicked.connect(lambda: self._delete_piece(detail.piece))
        btn_row.addWidget(del_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        layout.addStretch()
        return widget

    def _save_piece(self, detail: PieceDetail):
        """儲存曲目變更（作曲家不存在時自動建立）"""
        composer_text = self._piece_composer_combo.currentText().strip()
        composer_id = self._piece_composer_combo.currentData() or ""
        if not composer_id and composer_text:
            for c in self._composers:
                if (c.name_short or c.name) == composer_text or c.name == composer_text:
                    composer_id = c.id
                    break
            if not composer_id:
                composer_id = self._sheets.create_composer(
                    Composer(name=composer_text),
                )
        updated = replace(
            detail.piece,
            title=self._piece_title_edit.text().strip(),
            title_short=self._piece_short_edit.text().strip(),
            composer_id=composer_id,
            opus=self._piece_opus_edit.text().strip(),
            catalog_number=self._piece_catalog_edit.text().strip(),
            genre=self._piece_genre_edit.text().strip(),
            instrumentation=self._piece_instrumentation_edit.text().strip(),
            duration_minutes=self._piece_duration_edit.text().strip(),
            difficulty_level=self._piece_difficulty_edit.text().strip(),
            notes=self._piece_notes_edit.toPlainText().strip(),
        )
        try:
            self._sheets.update_piece(updated)
            self._refresh_all()
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _delete_piece(self, piece: Piece):
        """刪除曲目的元資料（不刪除 Drive 上的檔案）"""
        result = QMessageBox.question(
            self, t("catalog.piece.delete"),
            t("catalog.piece.confirm_delete", name=piece.title),
        )
        if result != QMessageBox.Yes:
            return
        try:
            if self._current_detail:
                for perf in self._current_detail.performances:
                    self._sheets.delete_performance(perf.id)
                for part in self._current_detail.parts:
                    self._sheets.delete_part(part.id)
                for edition in self._current_detail.editions:
                    self._sheets.delete_edition(edition.id)
                for movement in self._current_detail.movements:
                    self._sheets.delete_movement(movement.id)
            self._sheets.delete_piece(piece.id)
            self._refresh_all()
            self._clear_detail()
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    # --- 版本頁籤 ---

    def _build_editions_tab(self, detail: PieceDetail) -> QWidget:
        """建立版本管理頁籤"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        btn_row = QHBoxLayout()
        add_btn = QPushButton(t("catalog.edition.add"))
        add_btn.clicked.connect(lambda: self._add_edition(detail.piece.id))
        btn_row.addWidget(add_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        for edition in detail.editions:
            is_active = edition.id == detail.piece.active_edition_id
            group = QGroupBox(
                f"{edition.publisher or t('catalog.edition.title')}"
                f"{' ' + t('catalog.edition.active') if is_active else ''}",
            )
            form = QFormLayout(group)
            pub_edit = QLineEdit(edition.publisher)
            form.addRow(t("catalog.edition.publisher"), pub_edit)
            label_edit = QLineEdit(edition.edition_label)
            form.addRow(t("catalog.edition.label"), label_edit)
            cat_edit = QLineEdit(edition.catalog_number)
            form.addRow(t("catalog.edition.catalog_number"), cat_edit)
            year_edit = QLineEdit(edition.year)
            year_edit.setFixedWidth(80)
            form.addRow(t("catalog.edition.year"), year_edit)
            folder_label = QLabel(edition.drive_folder_id or "")
            folder_label.setStyleSheet("color: gray;")
            form.addRow(t("catalog.edition.drive_folder"), folder_label)
            notes_edit = QLineEdit(edition.notes)
            form.addRow(t("catalog.edition.notes"), notes_edit)
            ed_btn_row = QHBoxLayout()
            save_btn = QPushButton(t("catalog.save"))
            save_btn.clicked.connect(
                lambda checked=False, e=edition, pw=pub_edit, lw=label_edit,
                cw=cat_edit, yw=year_edit, nw=notes_edit:
                self._save_edition(e, pw, lw, cw, yw, nw),
            )
            ed_btn_row.addWidget(save_btn)
            if not is_active and len(detail.editions) > 1:
                active_btn = QPushButton(t("catalog.edition.set_active"))
                active_btn.clicked.connect(
                    lambda checked=False, eid=edition.id:
                    self._set_active_edition(detail.piece, eid),
                )
                ed_btn_row.addWidget(active_btn)
            ed_btn_row.addStretch()
            form.addRow("", ed_btn_row)
            layout.addWidget(group)
        layout.addStretch()
        return widget

    def _add_edition(self, piece_id: str):
        """新增版本"""
        publisher, ok = QInputDialog.getText(
            self, t("catalog.edition.add"), t("catalog.edition.publisher"),
        )
        if not ok or not publisher.strip():
            return
        try:
            self._sheets.create_edition(
                Edition(piece_id=piece_id, publisher=publisher.strip()),
            )
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _save_edition(self, original, pub_w, label_w, cat_w, year_w, notes_w):
        """儲存版本變更"""
        updated = replace(
            original,
            publisher=pub_w.text().strip(),
            edition_label=label_w.text().strip(),
            catalog_number=cat_w.text().strip(),
            year=year_w.text().strip(),
            notes=notes_w.text().strip(),
        )
        try:
            self._sheets.update_edition(updated)
            self._show_piece_detail(original.piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _set_active_edition(self, piece: Piece, edition_id: str):
        """設定使用中版本"""
        updated = replace(piece, active_edition_id=edition_id)
        try:
            self._sheets.update_piece(updated)
            self._refresh_all()
            self._show_piece_detail(piece.id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    # --- 樂章頁籤 ---

    def _build_movements_tab(self, detail: PieceDetail) -> QWidget:
        """建立樂章管理頁籤"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        btn_row = QHBoxLayout()
        add_btn = QPushButton(t("catalog.movement.add"))
        add_btn.clicked.connect(lambda: self._add_movement(detail.piece.id))
        btn_row.addWidget(add_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        table = QTableWidget()
        table.setColumnCount(6)
        table.setHorizontalHeaderLabels([
            t("catalog.movement.number"), t("catalog.movement.name"),
            t("catalog.movement.tempo"), t("catalog.movement.key"),
            t("catalog.save"), t("catalog.movement.delete"),
        ])
        table.horizontalHeader().setStretchLastSection(True)
        table.setRowCount(len(detail.movements))
        for row, mov in enumerate(detail.movements):
            table.setItem(row, 0, QTableWidgetItem(mov.movement_number))
            table.setItem(row, 1, QTableWidgetItem(mov.name))
            table.setItem(row, 2, QTableWidgetItem(mov.tempo_marking))
            table.setItem(row, 3, QTableWidgetItem(mov.key_signature))
            save_btn = QPushButton(t("catalog.save"))
            save_btn.clicked.connect(
                lambda checked=False, r=row, m=mov, tbl=table:
                self._save_movement(m, tbl, r, detail.piece.id),
            )
            table.setCellWidget(row, 4, save_btn)
            del_btn = QPushButton(t("catalog.movement.delete"))
            del_btn.setStyleSheet("color: #c0392b;")
            del_btn.clicked.connect(
                lambda checked=False, mid=mov.id:
                self._delete_movement(mid, detail.piece.id),
            )
            table.setCellWidget(row, 5, del_btn)
        table.resizeColumnsToContents()
        layout.addWidget(table)
        layout.addStretch()
        return widget

    def _add_movement(self, piece_id: str):
        """新增樂章"""
        movements = self._sheets.list_movements(piece_id)
        next_num = str(len(movements) + 1)
        try:
            self._sheets.create_movement(Movement(
                piece_id=piece_id,
                movement_number=next_num,
                sort_order=next_num,
            ))
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _save_movement(self, original, table, row, piece_id):
        """儲存樂章變更"""
        updated = replace(
            original,
            movement_number=table.item(row, 0).text().strip(),
            name=table.item(row, 1).text().strip(),
            tempo_marking=table.item(row, 2).text().strip(),
            key_signature=table.item(row, 3).text().strip(),
        )
        try:
            self._sheets.update_movement(updated)
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _delete_movement(self, movement_id: str, piece_id: str):
        """刪除樂章"""
        result = QMessageBox.question(
            self, t("catalog.movement.delete"),
            t("catalog.movement.confirm_delete"),
        )
        if result != QMessageBox.Yes:
            return
        try:
            parts = self._sheets.list_parts(movement_id=movement_id)
            for part in parts:
                self._sheets.delete_part(part.id)
            self._sheets.delete_movement(movement_id)
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    # --- 分譜狀態頁籤 ---

    def _build_parts_tab(self, detail: PieceDetail) -> QWidget:
        """建立分譜狀態矩陣頁籤"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        if not detail.editions:
            layout.addWidget(QLabel(t("catalog.tree.no_items")))
            return widget
        edition_combo = QComboBox()
        for edition in detail.editions:
            label = edition.publisher or t("catalog.edition.title")
            if edition.id == detail.piece.active_edition_id:
                label += f" {t('catalog.edition.active')}"
            edition_combo.addItem(label, edition.id)
        layout.addWidget(edition_combo)
        btn_row = QHBoxLayout()
        add_btn = QPushButton(t("catalog.part.add_instruments"))
        scan_btn = QPushButton(t("catalog.part.scan_folder"))
        btn_row.addWidget(add_btn)
        btn_row.addWidget(scan_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        parts_container = QWidget()
        parts_layout = QVBoxLayout(parts_container)
        layout.addWidget(parts_container)

        def get_current_edition_id():
            return edition_combo.currentData() or ""

        def rebuild_parts_grid():
            edition_id = get_current_edition_id()
            while parts_layout.count():
                child = parts_layout.takeAt(0)
                if child.widget():
                    child.widget().deleteLater()
            parts = [p for p in detail.parts if p.edition_id == edition_id]
            movements = sorted(detail.movements, key=lambda m: m.sort_order)
            if not movements:
                parts_layout.addWidget(QLabel(t("catalog.tree.no_items")))
                return
            instruments = sorted(set(p.instrument_name for p in parts))
            if not instruments:
                parts_layout.addWidget(QLabel(t("catalog.drive.no_parts_yet")))
                return
            table = QTableWidget()
            table.setRowCount(len(instruments))
            table.setColumnCount(len(movements))
            headers = [
                f"{m.movement_number}. {m.name}" if m.name
                else m.movement_number
                for m in movements
            ]
            table.setHorizontalHeaderLabels(headers)
            table.setVerticalHeaderLabels(instruments)
            part_map = {}
            for p in parts:
                part_map[(p.instrument_name, p.movement_id)] = p
            for row, inst in enumerate(instruments):
                for col, mov in enumerate(movements):
                    part = part_map.get((inst, mov.id))
                    status = part.status if part else PartStatus.UNKNOWN.value
                    status_label = t(f"catalog.part.status.{status}")
                    item = QTableWidgetItem(status_label)
                    item.setTextAlignment(Qt.AlignCenter)
                    color = _STATUS_COLORS.get(
                        status, _STATUS_COLORS[PartStatus.UNKNOWN.value],
                    )
                    item.setBackground(QBrush(color))
                    item.setForeground(QBrush(QColor("white")))
                    item.setData(Qt.UserRole, (part, inst, mov.id, edition_id))
                    table.setItem(row, col, item)
            table.cellDoubleClicked.connect(
                lambda r, c, tbl=table: self._toggle_part_status(
                    tbl, r, c, detail.piece.id,
                ),
            )
            table.resizeColumnsToContents()
            table.horizontalHeader().setStretchLastSection(True)
            parts_layout.addWidget(table)

        add_btn.clicked.connect(
            lambda: self._add_parts_from_instruments(
                get_current_edition_id(), detail, rebuild_parts_grid,
            ),
        )
        scan_btn.clicked.connect(
            lambda: self._scan_parts_from_drive(
                get_current_edition_id(), detail, rebuild_parts_grid,
            ),
        )
        edition_combo.currentIndexChanged.connect(lambda: rebuild_parts_grid())
        rebuild_parts_grid()
        return widget

    def _toggle_part_status(self, table, row, col, piece_id):
        """雙擊切換分譜狀態"""
        item = table.item(row, col)
        if not item:
            return
        data = item.data(Qt.UserRole)
        if not data:
            return
        part, inst, movement_id, edition_id = data
        cycle = [
            PartStatus.AVAILABLE.value,
            PartStatus.MISSING.value,
            PartStatus.DAMAGED.value,
            PartStatus.UNKNOWN.value,
        ]
        current = part.status if part else PartStatus.UNKNOWN.value
        idx = cycle.index(current) if current in cycle else 0
        new_status = cycle[(idx + 1) % len(cycle)]
        try:
            if part and part.id:
                self._sheets.update_part(replace(part, status=new_status))
            else:
                self._sheets.create_part(Part(
                    edition_id=edition_id,
                    movement_id=movement_id,
                    instrument_name=inst,
                    sort_order=str(row),
                    status=new_status,
                ))
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _add_parts_from_instruments(self, edition_id, detail, refresh_fn):
        """手動輸入樂器名稱以新增分譜"""
        text, ok = QInputDialog.getMultiLineText(
            self, t("catalog.part.add_instruments"),
            t("catalog.part.instrument"), "",
        )
        if not ok or not text.strip():
            return
        instruments = [l.strip() for l in text.strip().split("\n") if l.strip()]
        movements = detail.movements
        if not movements:
            movements = [Movement(id="1", piece_id=detail.piece.id,
                                  movement_number="1", sort_order="1")]
        try:
            for mov in movements:
                for i, inst in enumerate(instruments):
                    self._sheets.create_part(Part(
                        edition_id=edition_id,
                        movement_id=mov.id,
                        instrument_name=inst,
                        sort_order=str(i),
                        status=PartStatus.UNKNOWN.value,
                    ))
            self._show_piece_detail(detail.piece.id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _scan_parts_from_drive(self, edition_id, detail, refresh_fn):
        """從 Drive 資料夾掃描 PDF 建立分譜"""
        if not self._drive:
            return
        edition = self._sheets.get_edition(edition_id)
        if not edition or not edition.drive_folder_id:
            return
        try:
            pdfs = self._drive.list_pdfs_in_folder(edition.drive_folder_id)
            if not pdfs:
                return
            movements = detail.movements
            if not movements:
                mov_id = self._sheets.create_movement(Movement(
                    piece_id=detail.piece.id,
                    movement_number="1", sort_order="1",
                ))
                movements = [Movement(id=mov_id, piece_id=detail.piece.id,
                                      movement_number="1", sort_order="1")]
            first_mov = movements[0]
            for i, pdf in enumerate(pdfs):
                name = pdf["name"].replace(".pdf", "").replace(".PDF", "")
                self._sheets.create_part(Part(
                    edition_id=edition_id,
                    movement_id=first_mov.id,
                    instrument_name=name,
                    sort_order=str(i),
                    status=PartStatus.AVAILABLE.value,
                    drive_file_id=pdf["id"],
                    file_name=pdf["name"],
                ))
            self._show_piece_detail(detail.piece.id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    # --- 演出紀錄頁籤 ---

    def _build_performances_tab(self, detail: PieceDetail) -> QWidget:
        """建立演出紀錄頁籤"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        btn_row = QHBoxLayout()
        add_btn = QPushButton(t("catalog.performance.add"))
        add_btn.clicked.connect(
            lambda: self._add_performance(detail.piece.id),
        )
        btn_row.addWidget(add_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)
        table = QTableWidget()
        table.setColumnCount(8)
        table.setHorizontalHeaderLabels([
            t("catalog.performance.date"),
            t("catalog.performance.venue"),
            t("catalog.performance.conductor"),
            t("catalog.performance.ensemble"),
            t("catalog.performance.status"),
            t("catalog.performance.program_order"),
            t("catalog.save"),
            t("catalog.performance.delete"),
        ])
        table.horizontalHeader().setStretchLastSection(True)
        table.setRowCount(len(detail.performances))
        status_options = [
            PerformanceStatus.PLANNED.value,
            PerformanceStatus.REHEARSING.value,
            PerformanceStatus.PERFORMED.value,
            PerformanceStatus.CANCELLED.value,
        ]
        for row, perf in enumerate(detail.performances):
            table.setItem(row, 0, QTableWidgetItem(perf.performance_date))
            table.setItem(row, 1, QTableWidgetItem(perf.venue))
            table.setItem(row, 2, QTableWidgetItem(perf.conductor))
            table.setItem(row, 3, QTableWidgetItem(perf.ensemble))
            status_combo = QComboBox()
            for s in status_options:
                status_combo.addItem(
                    t(f"catalog.performance.status.{s}"), s,
                )
            idx = (status_options.index(perf.status)
                   if perf.status in status_options else 0)
            status_combo.setCurrentIndex(idx)
            table.setCellWidget(row, 4, status_combo)
            table.setItem(row, 5, QTableWidgetItem(perf.program_order))
            save_btn = QPushButton(t("catalog.save"))
            save_btn.clicked.connect(
                lambda checked=False, r=row, p=perf, tbl=table:
                self._save_performance(p, tbl, r, detail.piece.id),
            )
            table.setCellWidget(row, 6, save_btn)
            del_btn = QPushButton(t("catalog.performance.delete"))
            del_btn.setStyleSheet("color: #c0392b;")
            del_btn.clicked.connect(
                lambda checked=False, pid=perf.id:
                self._delete_performance(pid, detail.piece.id),
            )
            table.setCellWidget(row, 7, del_btn)
        table.resizeColumnsToContents()
        layout.addWidget(table)
        layout.addStretch()
        return widget

    def _add_performance(self, piece_id: str):
        """新增演出紀錄"""
        try:
            self._sheets.create_performance(Performance(
                piece_id=piece_id,
                status=PerformanceStatus.PLANNED.value,
            ))
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _save_performance(self, original, table, row, piece_id):
        """儲存演出紀錄變更"""
        status_combo = table.cellWidget(row, 4)
        updated = replace(
            original,
            performance_date=table.item(row, 0).text().strip(),
            venue=table.item(row, 1).text().strip(),
            conductor=table.item(row, 2).text().strip(),
            ensemble=table.item(row, 3).text().strip(),
            status=status_combo.currentData() if status_combo else original.status,
            program_order=table.item(row, 5).text().strip(),
        )
        try:
            self._sheets.update_performance(updated)
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _delete_performance(self, performance_id: str, piece_id: str):
        """刪除演出紀錄"""
        result = QMessageBox.question(
            self, t("catalog.performance.delete"),
            t("catalog.performance.confirm_delete"),
        )
        if result != QMessageBox.Yes:
            return
        try:
            self._sheets.delete_performance(performance_id)
            self._show_piece_detail(piece_id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))
