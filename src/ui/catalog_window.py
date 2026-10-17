# -*- coding: utf-8 -*-
"""
譜庫瀏覽器視窗

以 Drive 資料夾內容為主體，使用者可為資料夾加上元資料（曲目資訊、演出紀錄等）。
讀取（曲目清單、作曲家、版本、資料夾與版本資料夾的檔）都經譜庫服務（services/catalog_service.py）在背景執行；
讀不到時頂端顯示錯誤提示列（ui/catalog_error_bar.py），內容區寫「讀不到」，不顯示成空清單。
"""
import threading
from dataclasses import replace
from typing import Callable, Dict, List, Optional
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QStackedWidget,
    QTreeWidget, QTreeWidgetItem, QLabel, QLineEdit, QPushButton,
    QToolBar, QStatusBar, QMessageBox, QComboBox, QFormLayout,
    QGroupBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QDialog, QDialogButtonBox, QInputDialog,
    QListWidget,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QCursor
from core.locale import t
from core.catalog_constants import PartStatus, PerformanceStatus
from core.catalog_errors import CatalogError, CatalogFailure
from core.catalog_models import (
    Composer, DriveFile, EditionFiles, Piece, Edition, Movement, Part, Performance, PieceDetail,
)
from services.catalog_service import CatalogService
from services.sheets_service import SheetsService
from services.drive_service import DriveService
from services.google_auth_service import GoogleAuthService
from services.google_catalog import open_google_catalog
from services.preferences_service import PreferencesService
from ui.catalog_error_bar import CatalogErrorBar
from ui.catalog_tasks import CatalogTasks


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

    def __init__(
        self, auth_service: GoogleAuthService,
        preferences: PreferencesService, parent=None,
        catalog_factory: Optional[Callable[[], CatalogService]] = None,
    ):
        """
        Args:
            auth_service: Google 登入服務
            preferences: 偏好設定（譜庫試算表 ID、根資料夾 ID）
            parent: 父元件
            catalog_factory: 在背景執行緒建立譜庫服務的函式，拿不到時丟出 CatalogError；
                省略時以登入服務的憑證接 Google（測試傳入接假 Google 的）
        """
        super().__init__(parent)
        self._auth = auth_service
        self._prefs = preferences
        self._catalog_factory = catalog_factory or self._open_google_catalog
        self._catalog: Optional[CatalogService] = None
        self._catalog_lock = threading.Lock()
        self._sheets: Optional[SheetsService] = None
        self._drive: Optional[DriveService] = None
        self._pieces: List[Piece] = []
        self._piece_by_folder: Dict[str, Piece] = {}
        self._current_detail: Optional[PieceDetail] = None
        self._composers: List[Composer] = []
        self._tasks = CatalogTasks(self)
        self._retry: Optional[Callable[[], None]] = None
        # 樹狀清單與詳細面板每換一次內容就加一：背景讀取交回時內容已經換掉的，就不再填回
        self._tree_version = 0
        self._detail_version = 0
        self.setWindowTitle(t("catalog.title"))
        self.resize(1200, 750)
        self._build_ui()
        QTimer.singleShot(100, self._init_services)

    def closeEvent(self, event):
        """關閉視窗：放棄還在跑的背景讀取，之後它們不再碰這個視窗"""
        self._tasks.abandon_all()
        super().closeEvent(event)

    # --- 經譜庫服務的背景讀取 ---

    def _open_google_catalog(self) -> CatalogService:
        """以登入服務的憑證接 Google（背景執行緒呼叫）"""
        return open_google_catalog(self._auth, self._prefs.get("catalog_spreadsheet_id") or "")

    def _connected(self) -> CatalogService:
        """譜庫服務，第一次用到時才建立（背景執行緒呼叫，同時有幾件讀取也只建一次；拿不到時丟出 CatalogError）"""
        with self._catalog_lock:
            if self._catalog is None:
                self._catalog = self._catalog_factory()
            return self._catalog

    def _read(
        self, read: Callable[[CatalogService], object], done: Callable[[object], None],
        unreadable: Callable[[], None], retry: Callable[[], None],
    ):
        """在背景經譜庫服務讀取，完成時在主執行緒呼叫 done；失敗時呼叫 unreadable 並顯示提示列

        Args:
            read: 以譜庫服務讀取的函式（背景執行緒呼叫）
            done: 讀到時以結果呼叫
            unreadable: 讀不到時呼叫，在內容區寫「讀不到」
            retry: 提示列按「重試」時呼叫
        """
        self.setCursor(QCursor(Qt.WaitCursor))
        self._status_bar.showMessage(t("catalog.loading"))

        def finished(result):
            """讀到了"""
            self._settle_loading()
            done(result)

        def failed(error: CatalogError):
            """讀不到：內容區寫讀不到，頂端顯示提示列"""
            self._settle_loading()
            unreadable()
            self._retry = retry
            self._error_bar.show_failure(
                t("catalog.error.cannot_read"), CatalogFailure.from_error(error, t("catalog.action.read")),
            )
        self._tasks.run(lambda: read(self._connected()), finished, failed)

    def _settle_loading(self):
        """沒有背景讀取在跑時恢復游標、清掉狀態列的載入中"""
        if not self._tasks.busy:
            self.unsetCursor()
            self._status_bar.clearMessage()

    def _retry_failed(self):
        """提示列的「重試」：收起提示列，重做失敗的那次讀取"""
        self._error_bar.clear()
        if self._retry is not None:
            self._retry()

    def _sign_in_again(self):
        """提示列的「重新登入 Google」：登入成功就重新連上譜庫、重做失敗的讀取"""
        from ui.catalog_settings_dialog import sign_in_google
        if sign_in_google(self, self._auth):
            self._reconnect()

    def _open_settings(self):
        """提示列的「開啟譜庫設定」：存了設定就重新連上譜庫、重做失敗的讀取"""
        from ui.catalog_settings_dialog import CatalogSettingsDialog
        if CatalogSettingsDialog(self._auth, self._prefs, self).exec() == QDialog.Accepted:
            self._reconnect()

    def _reconnect(self):
        """登入或設定改了：下次讀取時重新建立譜庫服務，並重做失敗的讀取"""
        self._catalog = None
        self._connect_legacy_services()
        self._retry_failed()

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
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)
        self._error_bar = CatalogErrorBar()
        self._error_bar.retry_requested.connect(self._retry_failed)
        self._error_bar.sign_in_requested.connect(self._sign_in_again)
        self._error_bar.settings_requested.connect(self._open_settings)
        layout.addWidget(self._error_bar)
        splitter = QSplitter(Qt.Horizontal)
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.setMinimumWidth(280)
        self._tree.itemExpanded.connect(self._on_expand)
        self._tree.currentItemChanged.connect(self._on_tree_select)
        self._tree_pane = QStackedWidget()
        self._tree_pane.addWidget(self._tree)
        self._tree_pane.addWidget(self._unreadable_label())
        splitter.addWidget(self._tree_pane)
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
        layout.addWidget(splitter, stretch=1)
        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

    @staticmethod
    def _unreadable_label() -> QLabel:
        """內容區的「讀不到」（灰字置中）"""
        label = QLabel(t("catalog.error.unreadable"))
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("color: gray; font-size: 14px;")
        return label

    def _init_services(self):
        """連上譜庫並讀取總覽；還沒改走譜庫服務的寫入用服務在主執行緒以登入服務的憑證建立"""
        self._connect_legacy_services()
        self._refresh_all()

    def _connect_legacy_services(self):
        """以登入服務的憑證建立還沒改走譜庫服務的寫入用服務；拿不到憑證或沒設定試算表時不建立"""
        self._sheets = None
        self._drive = None
        spreadsheet_id = self._prefs.get("catalog_spreadsheet_id") or ""
        if not spreadsheet_id:
            return
        creds = self._auth.get_credentials()
        if not creds:
            return
        self._sheets = SheetsService(creds, spreadsheet_id)
        self._drive = DriveService(creds)

    def _refresh_all(self):
        """經譜庫服務重新讀取曲目、作曲家與譜庫根資料夾的內容"""
        root_id = self._prefs.get("catalog_root_folder_id") or ""

        def on_done(overview):
            """讀到了：填進樹狀清單，收起提示列"""
            self._error_bar.clear()
            self._tree_pane.setCurrentIndex(0)
            self._pieces = overview.pieces
            self._composers = overview.composers
            self._piece_by_folder = overview.folder_pieces
            self._clear_tree()
            self._populate_tree(None, overview.root.folders, overview.root.pdfs)
            self._status_bar.showMessage(
                t("catalog.status.stats",
                  composers=len(self._composers),
                  pieces=len(self._pieces)),
            )

        def unreadable():
            """讀不到：樹狀清單換成「讀不到」"""
            self._clear_tree()
            self._tree_pane.setCurrentIndex(1)

        self._read(lambda catalog: catalog.overview(root_id), on_done, unreadable, self._refresh_all)

    # --- Drive 樹狀結構 ---

    def _clear_tree(self):
        """清空樹狀清單；還在讀的展開結果不再填回"""
        self._tree_version += 1
        self._tree.clear()

    def _populate_tree(self, parent_node, folders: List[DriveFile], pdfs: List[DriveFile]):
        """將資料夾與 PDF 清單填入樹狀結構（在主執行緒呼叫）"""
        for folder in folders:
            has_meta = folder.id in self._piece_by_folder
            label = folder.name
            if has_meta:
                label += "  *"
            node = QTreeWidgetItem([label])
            node.setData(0, Qt.UserRole, ("folder", folder.id, folder.name))
            node.setData(0, Qt.UserRole + 1, False)
            placeholder = QTreeWidgetItem([t("catalog.loading")])
            node.addChild(placeholder)
            if parent_node is None:
                self._tree.addTopLevelItem(node)
            else:
                parent_node.addChild(node)
        for pdf in pdfs:
            node = QTreeWidgetItem([pdf.name])
            node.setData(0, Qt.UserRole, ("file", pdf.id, pdf.name))
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
        if not data or data[0] != "folder":
            return
        folder_id = data[1]
        version = self._tree_version

        def on_done(contents):
            """讀到了：換掉「載入中」"""
            if version == self._tree_version:
                item.takeChildren()
                self._populate_tree(item, contents.folders, contents.pdfs)

        def unreadable():
            """讀不到：子項目寫「讀不到」，下次展開時再讀"""
            if version == self._tree_version:
                item.takeChildren()
                item.addChild(QTreeWidgetItem([t("catalog.error.unreadable")]))
                item.setData(0, Qt.UserRole + 1, False)

        self._read(lambda catalog: catalog.folder_contents(folder_id), on_done, unreadable, self._refresh_all)

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
        """清除右側面板內容；還在讀的詳細資料不再填回"""
        self._detail_version += 1
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
        """顯示資料夾的操作，並經譜庫服務在背景列出資料夾裡的 PDF；標記過的資料夾改顯示曲目"""
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
        files_area = QWidget()
        files_layout = QVBoxLayout(files_area)
        files_layout.setContentsMargins(0, 0, 0, 0)
        self._detail_layout.addWidget(files_area, stretch=1)
        version = self._detail_version

        def on_done(contents):
            """讀到了：列出資料夾裡的 PDF"""
            if version != self._detail_version:
                return
            pdfs = contents.pdfs
            if pdfs:
                files_label = QLabel(
                    t("catalog.drive.files_in_folder", count=len(pdfs)),
                )
                files_label.setStyleSheet("font-weight: bold; padding: 4px 0;")
                files_layout.addWidget(files_label)
                file_list = QListWidget()
                for pdf in pdfs:
                    file_list.addItem(pdf.name)
                files_layout.addWidget(file_list, stretch=1)
            else:
                empty = QLabel(t("catalog.drive.folder_empty"))
                empty.setStyleSheet("color: gray; padding: 8px;")
                files_layout.addWidget(empty)
                files_layout.addStretch()

        def unreadable():
            """讀不到：檔案區寫「讀不到」"""
            if version == self._detail_version:
                files_layout.addWidget(self._unreadable_label(), stretch=1)

        self._read(
            lambda catalog: catalog.folder_contents(folder_id), on_done, unreadable,
            lambda: self._show_folder_detail(folder_id, folder_name),
        )

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
        """經譜庫服務讀取曲目完整資訊與使用中版本資料夾的檔，讀到後顯示"""
        self._clear_detail()
        loading = QLabel(t("catalog.loading"))
        loading.setAlignment(Qt.AlignCenter)
        self._detail_layout.addWidget(loading)
        version = self._detail_version

        def read(catalog: CatalogService):
            """曲目完整資訊，以及使用中版本資料夾的檔（沒有資料夾時為 None）"""
            detail = catalog.piece_detail(piece_id)
            if detail is None:
                return None, None
            edition = self._active_edition(detail)
            folder_id = edition.drive_folder_id if edition else ""
            return detail, (catalog.edition_files(folder_id) if folder_id else None)

        def on_done(result):
            """讀到了：換成曲目的各頁籤"""
            if version == self._detail_version:
                detail, files = result
                self._show_piece_tabs(detail, files)

        def unreadable():
            """讀不到：詳細面板寫「讀不到」"""
            if version == self._detail_version:
                self._clear_detail()
                self._detail_layout.addWidget(self._unreadable_label())

        self._read(read, on_done, unreadable, lambda: self._show_piece_detail(piece_id))

    @staticmethod
    def _active_edition(detail: PieceDetail) -> Optional[Edition]:
        """曲目使用中的版本；沒設定時為第一個版本，沒有版本時為 None"""
        for edition in detail.editions:
            if edition.id == detail.piece.active_edition_id:
                return edition
        return detail.editions[0] if detail.editions else None

    def _show_piece_tabs(self, detail: Optional[PieceDetail], files: Optional[EditionFiles]):
        """顯示曲目的各頁籤；譜庫裡已經沒有這首曲目時回到選取提示"""
        self._clear_detail()
        if detail is None:
            self._current_detail = None
            hint = QLabel(t("catalog.drive.select_hint"))
            hint.setAlignment(Qt.AlignCenter)
            self._detail_layout.addWidget(hint)
            return
        self._current_detail = detail
        tabs = QTabWidget()
        tabs.addTab(
            self._build_piece_info_tab(detail), t("catalog.piece.title"),
        )
        tabs.addTab(
            self._build_parts_tab(detail, files), t("catalog.part.title"),
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

    def _build_parts_tab(self, detail: PieceDetail, files: Optional[EditionFiles]) -> QWidget:
        """建立分譜狀態頁籤

        依版本資料夾的結構：
        - 有子資料夾 → 每個子資料夾視為一個樂章，分段顯示
        - 無子資料夾 → 直接列出 PDF 檔案

        Args:
            detail: 曲目完整資訊
            files: 使用中版本資料夾的檔；版本沒有資料夾時為 None
        """
        widget = QWidget()
        layout = QVBoxLayout(widget)
        if not detail.editions:
            layout.addWidget(QLabel(t("catalog.tree.no_items")))
            return widget
        active_edition = self._active_edition(detail)
        if files is None:
            layout.addWidget(QLabel(t("catalog.drive.no_parts_yet")))
            return widget
        rename_btn = QPushButton(t("catalog.drive.apply_template"))
        rename_btn.clicked.connect(
            lambda: self._open_drive_rename(active_edition, detail),
        )
        layout.addWidget(rename_btn)
        root_pdfs = files.root_pdfs
        part_map = {p.drive_file_id: p for p in detail.parts if p.drive_file_id}
        name_map = {p.file_name: p for p in detail.parts if p.file_name}
        if files.subfolders:
            scroll_content = QWidget()
            scroll_layout = QVBoxLayout(scroll_content)
            scroll_layout.setContentsMargins(0, 0, 0, 0)
            for sf, sf_pdfs in files.subfolders:
                group = QGroupBox(f"{sf.name}  ({len(sf_pdfs)} {t('catalog.part.file')})")
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
            name_item = QTableWidgetItem(pdf.name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            table.setItem(row, 0, name_item)
            existing = part_map.get(pdf.id) or name_map.get(pdf.name)
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

    def _open_drive_rename(self, edition, detail):
        """開啟 Drive 重新命名對話框"""
        if not self._drive or not edition.drive_folder_id:
            return
        try:
            from services.drive_rename_service import build_groups_from_drive
            composer = None
            if detail.composer:
                composer = detail.composer
            elif detail.piece.composer_id:
                composer = self._sheets.get_composer(detail.piece.composer_id)
            groups = build_groups_from_drive(
                self._drive, edition.drive_folder_id, detail, composer,
            )
            from ui.drive_rename_dialog import DriveRenameDialog
            dialog = DriveRenameDialog(self._drive, groups, self)
            if dialog.exec() == DriveRenameDialog.Accepted:
                self._show_piece_detail(detail.piece.id)
        except Exception as e:
            QMessageBox.critical(self, t("catalog.error", error=""), str(e))

    def _on_part_status_changed(self, combo, pdf_info: DriveFile, edition, detail, existing):
        """分譜狀態下拉選單變更"""
        new_status = combo.currentData()
        color = _STATUS_COLORS.get(new_status, _STATUS_COLORS[PartStatus.UNKNOWN.value])
        combo.setStyleSheet(f"background-color: {color.name()};")
        if not self._sheets:
            return
        try:
            if existing and existing.id:
                self._sheets.update_part(replace(existing, status=new_status))
            else:
                self._sheets.create_part(Part(
                    edition_id=edition.id,
                    movement_id="",
                    instrument_name=pdf_info.name.replace(".pdf", "").replace(".PDF", ""),
                    sort_order="0",
                    status=new_status,
                    drive_file_id=pdf_info.id,
                    file_name=pdf_info.name,
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
