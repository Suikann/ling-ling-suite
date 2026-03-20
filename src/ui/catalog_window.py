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
from PySide6.QtCore import Qt, QTimer, QThread, Signal, QObject
from PySide6.QtGui import QColor, QBrush, QCursor
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


class _Worker(QThread):
    """在背景執行緒執行工作並回傳結果"""
    finished = Signal(object)
    error = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            result = self._fn()
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


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
        self._workers = []
        self._build_ui()
        QTimer.singleShot(100, self._init_services)

    def _run_async(self, fn, on_done, on_error=None):
        """在背景執行緒執行 fn，完成後在主執行緒呼叫 on_done"""
        self.setCursor(QCursor(Qt.WaitCursor))
        self._status_bar.showMessage(t("catalog.loading"))
        worker = _Worker(fn, self)
        worker.finished.connect(lambda result: self._on_worker_done(worker, on_done, result))
        worker.error.connect(lambda msg: self._on_worker_error(worker, on_error, msg))
        self._workers.append(worker)
        worker.start()

    def _on_worker_done(self, worker, callback, result):
        """背景工作完成"""
        self.unsetCursor()
        self._status_bar.clearMessage()
        self._workers = [w for w in self._workers if w is not worker]
        callback(result)

    def _on_worker_error(self, worker, callback, msg):
        """背景工作失敗"""
        self.unsetCursor()
        self._workers = [w for w in self._workers if w is not worker]
        if callback:
            callback(msg)
        else:
            self._status_bar.showMessage(t("catalog.error", error=msg))

    def _build_ui(self):
        self._toolbar = QToolBar()
        self._toolbar.setMovable(False)
        self.addToolBar(self._toolbar)
        new_folder_btn = QPushButton(t("catalog.toolbar.new_folder"))
        new_folder_btn.clicked.connect(self._create_subfolder)
        self._toolbar.addWidget(new_folder_btn)
        self._toolbar.addSeparator()
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

        def load():
            pieces = self._sheets.list_pieces()
            composers = self._sheets.list_composers()
            folder_map = {}
            for p in pieces:
                if p.active_edition_id:
                    folder_map[p.active_edition_id] = p
                editions = self._sheets.list_editions(p.id)
                for e in editions:
                    if e.drive_folder_id:
                        folder_map[e.drive_folder_id] = p
            folders = self._drive.list_subfolders(root_id)
            pdfs = self._drive.list_pdfs_in_folder(root_id)
            return pieces, composers, folder_map, folders, pdfs

        def on_done(result):
            pieces, composers, folder_map, folders, pdfs = result
            self._pieces = pieces
            self._composers = composers
            self._piece_by_folder = folder_map
            self._tree.clear()
            self._populate_tree(None, folders, pdfs)
            self._status_bar.showMessage(
                t("catalog.status.stats",
                  composers=len(self._composers),
                  pieces=len(self._pieces)),
            )

        self._run_async(load, on_done)

    # --- Drive 樹狀結構 ---

    def _populate_tree(self, parent_node, folders, pdfs):
        """將資料夾與 PDF 清單填入樹狀結構（在主執行緒呼叫）"""
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
        """展開資料夾時在背景載入子項目"""
        already_loaded = item.data(0, Qt.UserRole + 1)
        if already_loaded:
            return
        item.setData(0, Qt.UserRole + 1, True)
        data = item.data(0, Qt.UserRole)
        if not data or data[0] != "folder" or not self._drive:
            return
        folder_id = data[1]

        def load():
            folders = self._drive.list_subfolders(folder_id)
            pdfs = self._drive.list_pdfs_in_folder(folder_id)
            return folders, pdfs

        def on_done(result):
            item.takeChildren()
            folders, pdfs = result
            self._populate_tree(item, folders, pdfs)

        self._run_async(load, on_done)

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

    def _create_subfolder(self):
        """在目前選取的資料夾中建立子資料夾"""
        if not self._drive:
            return
        current = self._tree.currentItem()
        parent_id = self._prefs.get("catalog_root_folder_id") or ""
        if current:
            data = current.data(0, Qt.UserRole)
            if data and data[0] == "folder":
                parent_id = data[1]
        if not parent_id:
            return
        name, ok = QInputDialog.getText(
            self, t("catalog.toolbar.new_folder"),
            t("catalog.toolbar.new_folder_name"),
        )
        if not ok or not name.strip():
            return
        try:
            self._drive.create_folder(name.strip(), parent_id)
            self._refresh_all()
        except Exception as e:
            QMessageBox.critical(
                self, t("catalog.error", error=""), str(e),
            )

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
        actions_group = QGroupBox(t("catalog.drive.actions"))
        actions_layout = QVBoxLayout(actions_group)
        tag_row = QHBoxLayout()
        tag_btn = QPushButton(t("catalog.drive.tag_as_piece"))
        tag_btn.clicked.connect(
            lambda: self._tag_folder_as_piece(folder_id, folder_name),
        )
        tag_row.addWidget(tag_btn)
        tag_desc = QLabel(t("catalog.drive.tag_desc"))
        tag_desc.setStyleSheet("color: gray; font-size: 12px;")
        tag_desc.setWordWrap(True)
        tag_row.addWidget(tag_desc, stretch=1)
        actions_layout.addLayout(tag_row)
        collect_row = QHBoxLayout()
        collect_btn = QPushButton(t("catalog.drive.collect_files"))
        collect_btn.clicked.connect(
            lambda: self._collect_files_to_folder(folder_id, folder_name),
        )
        collect_row.addWidget(collect_btn)
        collect_desc = QLabel(t("catalog.drive.collect_desc"))
        collect_desc.setStyleSheet("color: gray; font-size: 12px;")
        collect_desc.setWordWrap(True)
        collect_row.addWidget(collect_desc, stretch=1)
        actions_layout.addLayout(collect_row)
        self._detail_layout.addWidget(actions_group)
        if self._drive:
            try:
                pdfs = self._drive.list_pdfs_in_folder(folder_id)
                if pdfs:
                    files_label = QLabel(
                        t("catalog.drive.files_in_folder", count=len(pdfs)),
                    )
                    files_label.setStyleSheet("font-weight: bold; padding: 4px 0;")
                    self._detail_layout.addWidget(files_label)
                    file_list = QListWidget()
                    for pdf in pdfs:
                        file_list.addItem(pdf["name"])
                    self._detail_layout.addWidget(file_list, stretch=1)
                else:
                    empty = QLabel(t("catalog.drive.folder_empty"))
                    empty.setStyleSheet("color: gray; padding: 8px;")
                    self._detail_layout.addWidget(empty)
                    self._detail_layout.addStretch()
            except Exception:
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

    def _collect_files_to_folder(self, target_folder_id: str, folder_name: str):
        """從 Drive 其他位置收集檔案到此資料夾"""
        if not self._drive:
            return
        root_id = self._prefs.get("catalog_root_folder_id") or ""
        if not root_id:
            return
        dialog = _DriveFilePicker(self._drive, root_id, self)
        if dialog.exec() != QDialog.Accepted or not dialog.selected_files:
            return
        moved = 0
        errors = []
        for file_info in dialog.selected_files:
            ok = self._drive.move_file(file_info["id"], target_folder_id)
            if ok:
                moved += 1
            else:
                errors.append(file_info["name"])
        msg = t("catalog.drive.collect_result", moved=moved, total=len(dialog.selected_files))
        if errors:
            msg += "\n" + t("catalog.drive.collect_errors", files=", ".join(errors))
        QMessageBox.information(self, t("catalog.drive.collect_files"), msg)
        self._show_folder_detail(target_folder_id, folder_name)

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
        """建立分譜狀態頁籤

        自動偵測資料夾結構：
        - 有子資料夾 → 每個子資料夾視為一個樂章，分段顯示
        - 無子資料夾 → 直接列出 PDF 檔案
        """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        if not detail.editions:
            layout.addWidget(QLabel(t("catalog.tree.no_items")))
            return widget
        active_edition = None
        for e in detail.editions:
            if e.id == detail.piece.active_edition_id:
                active_edition = e
                break
        if not active_edition:
            active_edition = detail.editions[0]
        if not active_edition.drive_folder_id or not self._drive:
            layout.addWidget(QLabel(t("catalog.drive.no_parts_yet")))
            return widget
        try:
            subfolders = self._drive.list_subfolders(active_edition.drive_folder_id)
            root_pdfs = self._drive.list_pdfs_in_folder(active_edition.drive_folder_id)
        except Exception:
            subfolders = []
            root_pdfs = []
        part_map = {p.drive_file_id: p for p in detail.parts if p.drive_file_id}
        name_map = {p.file_name: p for p in detail.parts if p.file_name}
        if subfolders:
            scroll_content = QWidget()
            scroll_layout = QVBoxLayout(scroll_content)
            scroll_layout.setContentsMargins(0, 0, 0, 0)
            for sf in subfolders:
                try:
                    sf_pdfs = self._drive.list_pdfs_in_folder(sf["id"])
                except Exception:
                    sf_pdfs = []
                group = QGroupBox(f"{sf['name']}  ({len(sf_pdfs)} {t('catalog.part.file')})")
                group_layout = QVBoxLayout(group)
                if sf_pdfs:
                    table = self._build_file_status_table(
                        sf_pdfs, part_map, name_map, active_edition, detail,
                    )
                    group_layout.addWidget(table)
                else:
                    empty = QLabel(t("catalog.drive.folder_empty"))
                    empty.setStyleSheet("color: gray;")
                    group_layout.addWidget(empty)
                scroll_layout.addWidget(group)
            if root_pdfs:
                root_group = QGroupBox(
                    t("catalog.drive.root_files", count=len(root_pdfs)),
                )
                root_layout = QVBoxLayout(root_group)
                table = self._build_file_status_table(
                    root_pdfs, part_map, name_map, active_edition, detail,
                )
                root_layout.addWidget(table)
                scroll_layout.addWidget(root_group)
            scroll_layout.addStretch()
            from PySide6.QtWidgets import QScrollArea
            scroll = QScrollArea()
            scroll.setWidget(scroll_content)
            scroll.setWidgetResizable(True)
            layout.addWidget(scroll, stretch=1)
        elif root_pdfs:
            table = self._build_file_status_table(
                root_pdfs, part_map, name_map, active_edition, detail,
            )
            layout.addWidget(table, stretch=1)
        else:
            layout.addWidget(QLabel(t("catalog.drive.no_parts_yet")))
        return widget

    def _build_file_status_table(self, pdfs, part_map, name_map, edition, detail):
        """建立檔案狀態表格"""
        table = QTableWidget()
        table.setRowCount(len(pdfs))
        table.setColumnCount(2)
        table.setHorizontalHeaderLabels([
            t("catalog.part.file"), t("catalog.part.status"),
        ])
        table.horizontalHeader().setStretchLastSection(True)
        table.setColumnWidth(0, 350)
        status_options = [s.value for s in PartStatus]
        for row, pdf in enumerate(pdfs):
            name_item = QTableWidgetItem(pdf["name"])
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 0, name_item)
            existing = part_map.get(pdf["id"]) or name_map.get(pdf["name"])
            current_status = existing.status if existing else PartStatus.UNKNOWN.value
            combo = QComboBox()
            for s in status_options:
                combo.addItem(t(f"catalog.part.status.{s}"), s)
            idx = status_options.index(current_status) if current_status in status_options else 3
            combo.setCurrentIndex(idx)
            color = _STATUS_COLORS.get(
                current_status, _STATUS_COLORS[PartStatus.UNKNOWN.value],
            )
            combo.setStyleSheet(f"background-color: {color.name()};")
            combo.currentIndexChanged.connect(
                lambda index, c=combo, pdf_info=pdf, ed=edition, det=detail, ex=existing:
                self._on_part_status_changed(c, pdf_info, ed, det, ex),
            )
            table.setCellWidget(row, 1, combo)
        table.setMaximumHeight(min(len(pdfs) * 35 + 30, 300))
        return table

    def _on_part_status_changed(self, combo, pdf_info, edition, detail, existing):
        """分譜狀態下拉選單變更"""
        new_status = combo.currentData()
        color = _STATUS_COLORS.get(new_status, _STATUS_COLORS[PartStatus.UNKNOWN.value])
        combo.setStyleSheet(f"background-color: {color.name()};")
        try:
            if existing and existing.id:
                self._sheets.update_part(replace(existing, status=new_status))
            else:
                self._sheets.create_part(Part(
                    edition_id=edition.id,
                    movement_id="",
                    instrument_name=pdf_info["name"].replace(".pdf", "").replace(".PDF", ""),
                    sort_order="0",
                    status=new_status,
                    drive_file_id=pdf_info["id"],
                    file_name=pdf_info["name"],
                ))
        except Exception as e:
            self._status_bar.showMessage(t("catalog.error", error=str(e)))

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


class _DriveFilePicker(QDialog):
    """從 Drive 中勾選資料夾或檔案的對話框"""

    def __init__(self, drive_service: DriveService, root_folder_id: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("catalog.drive.collect_files"))
        self.setMinimumSize(550, 500)
        self.selected_files = []
        self._drive = drive_service
        self._root_id = root_folder_id
        layout = QVBoxLayout(self)
        hint = QLabel(t("catalog.drive.collect_hint"))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.itemExpanded.connect(self._on_expand)
        layout.addWidget(self._tree)
        self._selected_label = QLabel(
            t("catalog.drive.collect_selected", count=0),
        )
        layout.addWidget(self._selected_label)
        self._tree.itemChanged.connect(self._update_count)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._load_children(None, self._root_id)

    def _load_children(self, parent_node, folder_id: str):
        """載入資料夾內容（資料夾與 PDF 皆可勾選）"""
        try:
            folders = self._drive.list_subfolders(folder_id)
            pdfs = self._drive.list_pdfs_in_folder(folder_id)
        except Exception:
            return
        for folder in folders:
            node = QTreeWidgetItem([folder["name"]])
            node.setData(0, Qt.UserRole, ("folder", folder["id"], folder["name"]))
            node.setData(0, Qt.UserRole + 1, False)
            node.setCheckState(0, Qt.Unchecked)
            placeholder = QTreeWidgetItem([t("catalog.loading")])
            node.addChild(placeholder)
            if parent_node is None:
                self._tree.addTopLevelItem(node)
            else:
                parent_node.addChild(node)
        for pdf in pdfs:
            node = QTreeWidgetItem([pdf["name"]])
            node.setData(0, Qt.UserRole, ("file", pdf["id"], pdf["name"]))
            node.setCheckState(0, Qt.Unchecked)
            if parent_node is None:
                self._tree.addTopLevelItem(node)
            else:
                parent_node.addChild(node)

    def _on_expand(self, item):
        """展開資料夾時載入"""
        already_loaded = item.data(0, Qt.UserRole + 1)
        if already_loaded:
            return
        item.setData(0, Qt.UserRole + 1, True)
        item.takeChildren()
        data = item.data(0, Qt.UserRole)
        if data and data[0] == "folder":
            self._load_children(item, data[1])

    def _update_count(self):
        """更新已勾選數量顯示"""
        count = len(self._collect_checked())
        self._selected_label.setText(
            t("catalog.drive.collect_selected", count=count),
        )

    def _collect_checked(self, parent=None) -> list:
        """遞迴收集所有勾選的資料夾與檔案（勾選資料夾時不重複收集其子項）"""
        results = []
        if parent is None:
            for i in range(self._tree.topLevelItemCount()):
                results.extend(self._collect_checked(self._tree.topLevelItem(i)))
        else:
            data = parent.data(0, Qt.UserRole)
            if data and parent.checkState(0) == Qt.Checked:
                results.append({
                    "id": data[1], "name": data[2], "type": data[0],
                })
                return results
            for i in range(parent.childCount()):
                results.extend(self._collect_checked(parent.child(i)))
        return results

    def _on_accept(self):
        """確認選取"""
        self.selected_files = self._collect_checked()
        self.accept()
