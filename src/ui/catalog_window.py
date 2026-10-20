# -*- coding: utf-8 -*-
"""
譜庫瀏覽器視窗

以 Drive 資料夾內容為主體，使用者可為資料夾加上元資料（曲目資訊、演出紀錄等）。
讀寫（曲目清單、作曲家、版本、資料夾與版本資料夾的檔，以及曲目、作曲家、版本、樂章、演出紀錄的增刪改、
建資料夾、搬檔與 Drive 重新命名，以及全社團聲部組對照的編輯）都經譜庫服務（services/catalog_service.py）在背景執行；
讀不到時頂端顯示錯誤提示列（ui/catalog_error_bar.py）並寫進紀錄檔，內容區寫「讀不到」，不顯示成空清單；
寫不進去時提示列以「寫不進譜庫」開頭，按「重試」以同一批資料重送（新增的列不會重複）。
版本頁的「下載…」勾選幾份檔或整個版本，下載到自選的資料夾（ui/catalog_download_dialog.py 挑選，
services/download_service.py 下載並核對 MD5）：在背景執行，狀態列顯示進度與取消，一次一件，下載到一半失敗時
提示列寫出已完成幾份、按「重試」補齊。
版本頁的「存入紀錄…」列出動到這個版本的每次存入與撤銷存入（ui/deposit_revert_dialog.py），選一次存入按「撤銷存入…」，
在等待中讀出撤銷的預覽、開預覽對話框；按「撤銷存入」後交給主視窗（DepositReverter）在背景撤銷、鎖住專案，
結束時中途失敗的在這個視窗頂端的提示列寫出已撤掉幾份、按「重試」再開一次預覽，沒撤銷的群組列出原因，版本頁重新讀取。
工具列的「發譜紀錄」（有 HuaXia 登入服務時）在右側面板列出每次發譜、撤回、收回與不收回（ui/publish_retract_dialog.py），
選一次發譜按「撤回…」：沒登入 HuaXia 時先登入，在等待中讀出撤回的預覽（services/retract_service.py）、開預覽對話框，
按「撤回」後在等待中撤回、寫一列「撤回」，寫出結果並重新讀取列表。按「收回…」同樣先登入，在等待中讀出收回的預覽
（services/recall_service.py）、開收回對話框（ui/publish_recall_dialog.py），按「收回」或「勾選的不收回」後在等待中
收回或寫「不收回」，寫出結果並重新讀取列表。
工具列的「聲部組對照」在右側面板開聲部組對照的編輯器（ui/section_map_editor.py），按儲存才寫入，
寫入走存檔寫回的同一套比對，被別人改過時開衝突畫面（ui/catalog_conflict_dialog.py）選邊。
讀到總覽（連上譜庫）後另在背景讀社團設定，把啟用的發譜平台記在偏好設定（services/club_settings_service.py），
讀完由社團設定服務通知主視窗；這一項讀不到時沿用記住的結果，不顯示提示列。
"""
import functools
import threading
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Sequence, Tuple
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QStackedWidget,
    QTreeWidget, QTreeWidgetItem, QLabel, QLineEdit, QPushButton,
    QToolBar, QStatusBar, QMessageBox, QComboBox, QFormLayout,
    QGroupBox, QTabWidget, QTableWidget, QTableWidgetItem,
    QTextEdit, QDialog, QDialogButtonBox, QFileDialog, QInputDialog,
    QListWidget,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QCursor
from core.constants import CATALOG_ROOT_FOLDER_ID_KEY, CATALOG_SPREADSHEET_ID_KEY, DEFAULT_ORCHESTRA_TYPE, OrchestraType
from core.locale import t
from core.catalog_constants import (
    PerformanceStatus, SHEET_COMPOSERS, SHEET_EDITIONS, SHEET_MOVEMENTS, SHEET_PERFORMANCES, SHEET_PIECES,
)
from core.catalog_download import DownloadItem, folder_items
from core.catalog_errors import CatalogError, CatalogFailure
from core.catalog_models import Composer, DepositLogEntry, DriveFile, FolderPdf, Piece, Edition, PieceDetail
from core.deposit_revert import Deposit, RevertChoice, RevertPreview, deposits
from core.publish_log import PublishAction, PublishLogEntry, publish_entries
from core.catalog_sync import Decision, SyncField, SyncItem, SyncOutcome
from core.voice_suggestions import VoiceCandidates
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService, PendingRows
from services.club_settings_service import ClubSettingsService
from services.deposit_revert_service import RevertOutcome
from services.download_service import DownloadOutcome, DownloadService
from services.google_auth_service import GoogleAuthService
from services.huaxia_auth_service import HuaXiaAuthService
from services.recall_reminder import RecallReminder
from services.recall_service import RecallResult, RecallService
from services.retract_service import RetractResult, RetractService
from services.google_catalog import open_google_catalog
from services.preferences_service import PreferencesService
from ui.catalog_error_bar import CatalogErrorBar
from ui.background_task import BackgroundTask, TaskSlot, TaskTexts
from ui.catalog_tasks import CatalogTasks, catalog_work, wait_for_catalog
from ui.huaxia_prompts import ensure_signed_in, not_officer_text
from ui.section_map_editor import SectionMapEditor
from ui.widgets import not_downloaded_text


_FOLDER_ICON = "\U0001f4c1 "
_PDF_ICON = "\U0001f4c4 "


class DepositReverter(Protocol):
    """撤銷存入的一方（主視窗）：以開著的專案讀預覽，鎖住專案在背景執行撤銷，結束時把指紋交回專案"""

    def revert_reader(self, deposit_id: str, paths: Sequence[str]) -> Callable[[CatalogService], RevertPreview]:
        """主執行緒記下開著的專案（群組、本機已清除的檔、樂團類型），交回在背景以譜庫服務讀預覽的函式"""

    def revert_deposit(
        self, preview: RevertPreview, choices: Sequence[RevertChoice], done: Callable[[RevertOutcome], None],
    ) -> bool:
        """在背景撤銷選了的群組，結束時以結果呼叫 done；已有背景工作在跑時不做，回傳 False"""


class CatalogWindow(QMainWindow):
    """譜庫管理主視窗"""

    def __init__(
        self, auth_service: GoogleAuthService,
        preferences: PreferencesService, parent=None,
        catalog_factory: Optional[Callable[[], CatalogService]] = None,
        log: Optional[ActivityLog] = None,
        huaxia_auth: Optional[HuaXiaAuthService] = None,
        voice_candidates: Optional[VoiceCandidates] = None,
        reverter: Optional[DepositReverter] = None,
        downloads: Optional[DownloadService] = None,
        orchestra_type: OrchestraType = DEFAULT_ORCHESTRA_TYPE,
        club_settings: Optional[ClubSettingsService] = None,
    ):
        """
        Args:
            auth_service: Google 登入服務
            preferences: 偏好設定（譜庫總表 ID、根資料夾 ID；連上譜庫時記下社團設定的發譜平台）
            parent: 父元件
            catalog_factory: 在背景執行緒建立譜庫服務的函式，拿不到時丟出 CatalogError；
                省略時以登入服務的憑證接 Google（測試傳入接假 Google 的）
            log: 紀錄檔，讀寫失敗時寫進去；省略時以使用者資料目錄下的紀錄檔資料夾建立
            huaxia_auth: HuaXia 登入服務，交給譜庫設定的 HuaXia 登入區，也用在撤回與收回；省略時譜庫設定沒有 HuaXia 登入區，
                工具列沒有「發譜紀錄」
            voice_candidates: 聲部名稱自動完成的候選來源，讀到譜庫總覽時記下用過的聲部名稱；省略時不記
            reverter: 撤銷存入在背景執行的一方（主視窗）；省略時版本頁沒有「存入紀錄…」
            downloads: 下載服務（主視窗那一份）；省略時版本頁沒有「下載…」
            orchestra_type: 開著的專案的樂團類型：版本頁與聲部組對照一開始顯示這一套，可以切換
            club_settings: 社團設定服務（全程式一份，主視窗訂閱它的通知），也交給譜庫設定；省略時以偏好設定與紀錄檔建立
        """
        super().__init__(parent)
        self._auth = auth_service
        self._huaxia_auth = huaxia_auth
        self._voice_candidates = voice_candidates
        self._prefs = preferences
        self._catalog_factory = catalog_factory or self._open_google_catalog
        self._catalog: Optional[CatalogService] = None
        self._catalog_lock = threading.Lock()
        self._log = log or ActivityLog()
        self._club_settings = club_settings or ClubSettingsService(preferences, self._log)
        # 讀到發譜紀錄、撤回與收回寫完時更新收回提醒的摘要
        self._reminder = RecallReminder(preferences, self._log)
        self._retracter = RetractService(huaxia_auth, self._log, self._reminder) if huaxia_auth is not None else None
        self._recaller = (
            RecallService(huaxia_auth, self._log, reminder=self._reminder) if huaxia_auth is not None else None
        )
        self._downloads = downloads
        self._reverter = reverter
        self._orchestra_type = orchestra_type
        self._pieces: List[Piece] = []
        self._piece_by_folder: Dict[str, Piece] = {}
        self._current_detail: Optional[PieceDetail] = None
        self._composers: List[Composer] = []
        self._tasks = CatalogTasks(self)
        # 讀社團設定另用一組：它不顯示載入中，也不讓其他讀取以為還有東西在跑
        self._club_tasks = CatalogTasks(self)
        # 樹狀清單與詳細面板每換一次內容就加一：背景讀取交回時內容已經換掉的，就不再填回
        self._tree_version = 0
        self._detail_version = 0
        self.setWindowTitle(t("catalog.title"))
        self.resize(1200, 750)
        self._build_ui()
        # 一般下載（一次一件）：進度寫在狀態列，取消鈕在狀態列右邊
        self._downloading = TaskSlot(self, self._status_bar.showMessage, self._show_download_state)
        QTimer.singleShot(100, self._refresh_all)

    def closeEvent(self, event):
        """關閉視窗：下載進行中先問要不要取消（不取消就不關）；放棄還在跑的背景讀取，之後它們不再碰這個視窗"""
        if not self._downloading.confirm_close():
            event.ignore()
            return
        self._tasks.abandon_all()
        self._club_tasks.abandon_all()
        super().closeEvent(event)

    # --- 經譜庫服務的背景讀寫 ---

    def _open_google_catalog(self) -> CatalogService:
        """以登入服務的憑證接 Google（背景執行緒呼叫）"""
        return open_google_catalog(self._auth, self._prefs.get(CATALOG_SPREADSHEET_ID_KEY) or "")

    def _connected(self) -> CatalogService:
        """譜庫服務，第一次用到時才建立（背景執行緒呼叫，同時有幾件讀取也只建一次；拿不到時丟出 CatalogError）"""
        with self._catalog_lock:
            if self._catalog is None:
                self._catalog = self._catalog_factory()
            return self._catalog

    def _read(
        self, read: Callable[[CatalogService], object], done: Callable[[object], None],
        unreadable: Callable[[], None], retry: Callable[[], None], piece_title: str = "",
    ):
        """在背景經譜庫服務讀取，完成時在主執行緒呼叫 done；失敗時呼叫 unreadable、顯示提示列並寫進紀錄檔

        Args:
            read: 以譜庫服務讀取的函式（背景執行緒呼叫）
            done: 讀到時以結果呼叫
            unreadable: 讀不到時呼叫，在內容區寫「讀不到」
            retry: 提示列按「重試」時呼叫
            piece_title: 讀的是哪首曲目，記進詳細資料與紀錄檔；不是讀某首曲目時為空字串
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
            self._error_bar.show_failure(
                t("catalog.error.cannot_read"),
                CatalogFailure.from_error(error, t("catalog.action.read"), piece_title=piece_title), retry,
            )
        self._tasks.run(lambda: read(self._connected()), finished, failed)

    def _write(
        self, action: str, write: Callable[[CatalogService], object], done: Callable[[object], None],
        piece_title: str = "", file_name: Callable[[], str] = lambda: "",
    ):
        """在背景經譜庫服務寫入，完成時在主執行緒呼叫 done；失敗時顯示「寫不進譜庫」提示列並寫進紀錄檔

        按「重試」時以同一個 write 再寫一次，所以 write 要能重送：新增列用 _adding 或 _adding_prepared 準備，批次的搬檔、改名從失敗的那份接著做。

        Args:
            action: 寫入的動作（介面上的名稱），記進詳細資料與紀錄檔
            write: 以譜庫服務寫入的函式（背景執行緒呼叫）
            done: 寫好時以結果呼叫
            piece_title: 寫的是哪首曲目，記進詳細資料與紀錄檔
            file_name: 失敗時正在處理的檔名（失敗時才呼叫）
        """
        self.setCursor(QCursor(Qt.WaitCursor))
        self._status_bar.showMessage(t("catalog.saving"))

        def finished(result):
            """寫好了"""
            self._settle_loading()
            self._error_bar.clear()
            done(result)

        def failed(error: CatalogError):
            """寫不進去：頂端顯示提示列"""
            self._settle_loading()
            self._error_bar.show_failure(
                t("catalog.error.cannot_write"),
                CatalogFailure.from_error(error, action, file_name=file_name(), piece_title=piece_title),
                lambda: self._write(action, write, done, piece_title, file_name),
            )
        self._tasks.run(lambda: write(self._connected()), finished, failed)

    @staticmethod
    def _adding(sheet: str, records: Sequence[Mapping[str, str]]) -> Callable[[CatalogService], List[str]]:
        """新增列的寫入：第一次送出時準備好這批列，重試時以同一批重送，不重複新增

        Args:
            sheet: 工作表名稱
            records: 每一列的欄位值，不含 id

        Returns:
            以譜庫服務新增、回傳各列 ID 的函式
        """
        return CatalogWindow._adding_prepared(lambda catalog: catalog.new_rows(sheet, records))

    @staticmethod
    def _adding_prepared(prepare: Callable[[CatalogService], PendingRows]) -> Callable[[CatalogService], List[str]]:
        """新增列的寫入：第一次送出時在背景以 prepare 準備這批列，重試時以同一批重送，不重複新增

        Args:
            prepare: 以譜庫服務準備這批列的函式（例如寫入前一刻才算編號的 new_movement）

        Returns:
            以譜庫服務新增、回傳各列 ID 的函式
        """
        pending: List[PendingRows] = []

        def add(catalog: CatalogService) -> List[str]:
            """送出這批列"""
            if not pending:
                pending.append(prepare(catalog))
            return catalog.add_rows(pending[0])
        return add

    def _settle_loading(self):
        """沒有背景讀寫在跑時恢復游標、清掉狀態列的載入中"""
        if not self._tasks.busy:
            self.unsetCursor()
            self._status_bar.clearMessage()

    def _sign_in_again(self) -> bool:
        """提示列的「重新登入 Google」：登入成功就在下次讀寫時以新的憑證重新連上譜庫

        Returns:
            是否登入成功
        """
        from ui.catalog_settings_dialog import sign_in_google
        if not sign_in_google(self, self._auth):
            return False
        self._forget_connection()
        return True

    def _open_settings(self) -> bool:
        """提示列的「開啟譜庫設定」：存了設定就在下次讀寫時重新連上譜庫

        Returns:
            是否存了設定
        """
        from ui.catalog_settings_dialog import CatalogSettingsDialog
        dialog = CatalogSettingsDialog(
            self._auth, self._prefs, self, log=self._log, huaxia_auth=self._huaxia_auth, club_settings=self._club_settings,
        )
        if dialog.exec() != QDialog.Accepted:
            return False
        self._forget_connection()
        return True

    def _reload(self):
        """提示列的「重新讀取」：重新讀取樹狀清單與正在顯示的曲目（要改的紀錄已經不在譜庫時）"""
        self._refresh_all()
        if self._current_detail is not None:
            self._show_piece_detail(self._current_detail.piece.id)

    def _forget_connection(self):
        """登入或設定改了：下次讀寫時重新建立譜庫服務"""
        with self._catalog_lock:
            self._catalog = None

    def _build_ui(self):
        """排出工具列（新增資料夾、重新整理、聲部組對照、發譜紀錄、搜尋）、錯誤提示列、左邊的樹狀清單與右邊的詳細面板、狀態列"""
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
        section_map_btn = QPushButton(t("catalog.sections.title"))
        section_map_btn.clicked.connect(lambda: self._show_section_map(self._orchestra_type))
        self._toolbar.addWidget(section_map_btn)
        if self._retracter is not None:
            publish_log_btn = QPushButton(t("publish_log.button"))
            publish_log_btn.clicked.connect(self._show_publish_log)
            self._toolbar.addWidget(publish_log_btn)
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
        self._error_bar = CatalogErrorBar(log=self._log)
        self._error_bar.connect_remedies(self._sign_in_again, self._open_settings, self._reload)
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
        self._download_cancel_btn = QPushButton(t("download.cancel"))
        self._download_cancel_btn.clicked.connect(lambda: self._downloading.cancel())
        self._download_cancel_btn.setVisible(False)
        self._status_bar.addPermanentWidget(self._download_cancel_btn)

    @staticmethod
    def _unreadable_label() -> QLabel:
        """內容區的「讀不到」（灰字置中）"""
        label = QLabel(t("catalog.error.unreadable"))
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("color: gray; font-size: 14px;")
        return label

    def _refresh_all(self):
        """經譜庫服務重新讀取曲目、作曲家與譜庫根資料夾的內容；用過的聲部名稱記進自動完成的候選來源"""
        root_id = self._prefs.get(CATALOG_ROOT_FOLDER_ID_KEY) or ""

        def on_done(overview):
            """讀到了：填進樹狀清單，收起提示列"""
            self._error_bar.clear()
            self._tree_pane.setCurrentIndex(0)
            self._pieces = overview.pieces
            self._composers = overview.composers
            self._piece_by_folder = overview.folder_pieces
            if self._voice_candidates is not None:
                self._voice_candidates.remember_catalog(overview.voice_names)
            self._clear_tree()
            self._populate_tree(None, overview.root.folders, overview.root.pdfs)
            self._status_bar.showMessage(
                t("catalog.status.stats",
                  composers=len(self._composers),
                  pieces=len(self._pieces)),
            )
            self._remember_club_settings()

        def unreadable():
            """讀不到：樹狀清單換成「讀不到」"""
            self._clear_tree()
            self._tree_pane.setCurrentIndex(1)

        self._read(lambda catalog: catalog.overview(root_id), on_done, unreadable, self._refresh_all)

    def _remember_club_settings(self):
        """連上譜庫後在背景讀社團設定並記住；讀不到時沿用記住的結果、寫進紀錄檔，不另外顯示提示列（按下發譜時才說明原因）"""
        if self._club_tasks.busy:
            return
        # refresh 不丟出例外：讀不到時自己寫進紀錄檔，交回時沒有要顯示的
        self._club_tasks.run(lambda: self._club_settings.refresh(self._connected), lambda _: None, lambda _: None)

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
        current = self._tree.currentItem()
        parent_id = self._prefs.get(CATALOG_ROOT_FOLDER_ID_KEY) or ""
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
        name = name.strip()
        self._write(
            t("catalog.toolbar.new_folder"), lambda catalog: catalog.create_folder(name, parent_id),
            lambda folder: self._refresh_all(), file_name=lambda: name,
        )

    # --- 聲部組對照 ---

    def _show_section_map(self, orchestra_type: OrchestraType):
        """經譜庫服務讀全社團的聲部組對照，讀到後在右側面板顯示編輯器

        Args:
            orchestra_type: 一開始顯示哪一套（寫入後重新讀取時維持原本顯示的那套）
        """
        self._clear_detail()
        self._current_detail = None
        loading = QLabel(t("catalog.loading"))
        loading.setAlignment(Qt.AlignCenter)
        self._detail_layout.addWidget(loading)
        version = self._detail_version

        def on_done(entries):
            """讀到了：換成編輯器"""
            if version == self._detail_version:
                self._clear_detail()
                editor = SectionMapEditor(entries, orchestra_type)
                editor.save_requested.connect(
                    lambda: self._save_section_map(editor.changes(), editor.orchestra_type(), {}),
                )
                self._detail_layout.addWidget(editor)

        def unreadable():
            """讀不到：詳細面板寫「讀不到」"""
            if version == self._detail_version:
                self._clear_detail()
                self._detail_layout.addWidget(self._unreadable_label())

        self._read(
            lambda catalog: catalog.section_map(), on_done, unreadable, lambda: self._show_section_map(orchestra_type),
        )

    def _save_section_map(
        self, items: Dict[OrchestraType, List[SyncItem]], orchestra_type: OrchestraType,
        decisions: Dict[str, Dict[Tuple[SyncField, object], Decision]],
    ):
        """把編輯器改過的樂器寫進譜庫；被別人改過時開衝突畫面，選邊後帶著選擇再寫一次，寫好後重新讀取對照

        Args:
            items: 改過的樂器（SectionMapEditor.changes）
            orchestra_type: 編輯器正在顯示的樂團類型
            decisions: 先前在衝突畫面的選擇：樂團類型的值 → （SyncItem.key → Decision）
        """
        if not items:
            return
        operator = self._auth.account_email or ""
        version = self._detail_version

        def done(outcome: SyncOutcome):
            """寫好就重新讀取；有衝突就開衝突畫面"""
            if outcome.written:
                if version == self._detail_version:
                    self._show_section_map(orchestra_type)
                return
            from ui.catalog_conflict_dialog import CatalogConflictDialog
            dialog = CatalogConflictDialog(outcome, self, confirm_text=t("catalog.sections.confirm"))
            if dialog.exec() != QDialog.Accepted:
                return
            chosen = {key: dict(value) for key, value in decisions.items()}
            for group_id, picked in dialog.decisions().items():
                chosen.setdefault(group_id, {}).update(picked)
            self._save_section_map(items, orchestra_type, chosen)

        self._write(
            t("catalog.sections.title"), lambda catalog: catalog.sync_section_map(items, operator, decisions), done,
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
        """將資料夾標記為一首曲目，寫好後顯示它"""
        def tagged(piece_id: str):
            """標記好了"""
            self._refresh_all()
            self._show_piece_detail(piece_id)
        self._write(
            t("catalog.drive.tag_as_piece"), lambda catalog: catalog.tag_folder_as_piece(folder_id, folder_name),
            tagged, piece_title=folder_name,
        )

    def _collect_files_to_folder(self, target_folder_id: str, folder_name: str):
        """從 Drive 其他位置挑檔案或資料夾搬進此資料夾；中途失敗時重試從失敗的那份接著搬"""
        root_id = self._prefs.get(CATALOG_ROOT_FOLDER_ID_KEY) or ""
        if not root_id:
            return
        dialog = _DriveFilePicker(
            self._connected, root_id, self, sign_in=self._sign_in_again, open_settings=self._open_settings,
            log=self._log,
        )
        if dialog.exec() != QDialog.Accepted or not dialog.selected_files:
            return
        pending = list(dialog.selected_files)
        total = len(pending)

        def moved(_result):
            """全部搬好了"""
            QMessageBox.information(
                self, t("catalog.drive.collect_files"), t("catalog.drive.collect_result", moved=total, total=total),
            )
            self._show_folder_detail(target_folder_id, folder_name)
        self._write(
            t("catalog.drive.collect_files"), lambda catalog: catalog.move_files(pending, target_folder_id), moved,
            file_name=lambda: pending[0].name if pending else "",
        )

    # --- 曲目詳細（含元資料頁籤） ---

    def _show_piece_detail(self, piece_id: str):
        """經譜庫服務讀取曲目完整資訊，讀到後顯示（版本頁另在背景讀它那一處）"""
        self._clear_detail()
        loading = QLabel(t("catalog.loading"))
        loading.setAlignment(Qt.AlignCenter)
        self._detail_layout.addWidget(loading)
        version = self._detail_version

        def on_done(detail: Optional[PieceDetail]):
            """讀到了：換成曲目的各頁籤"""
            if version == self._detail_version:
                self._show_piece_tabs(detail)

        def unreadable():
            """讀不到：詳細面板寫「讀不到」"""
            if version == self._detail_version:
                self._clear_detail()
                self._detail_layout.addWidget(self._unreadable_label())

        title = next((piece.title for piece in self._pieces if piece.id == piece_id), "")
        self._read(
            lambda catalog: catalog.piece_detail(piece_id), on_done, unreadable,
            lambda: self._show_piece_detail(piece_id), piece_title=title,
        )

    @staticmethod
    def _active_edition(detail: PieceDetail) -> Optional[Edition]:
        """曲目使用中的版本；沒設定時為第一個版本，沒有版本時為 None"""
        for edition in detail.editions:
            if edition.id == detail.piece.active_edition_id:
                return edition
        return detail.editions[0] if detail.editions else None

    def _show_piece_tabs(self, detail: Optional[PieceDetail]):
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
        piece = detail.piece
        composer_text = self._piece_composer_combo.currentText().strip()
        composer_id = self._piece_composer_combo.currentData() or ""
        if not composer_id and composer_text:
            for c in self._composers:
                if (c.name_short or c.name) == composer_text or c.name == composer_text:
                    composer_id = c.id
                    break
        add_composer = self._adding(SHEET_COMPOSERS, [{"name": composer_text}])
        values = {
            "title": self._piece_title_edit.text().strip(),
            "title_short": self._piece_short_edit.text().strip(),
            "opus": self._piece_opus_edit.text().strip(),
            "catalog_number": self._piece_catalog_edit.text().strip(),
            "genre": self._piece_genre_edit.text().strip(),
            "instrumentation": self._piece_instrumentation_edit.text().strip(),
            "duration_minutes": self._piece_duration_edit.text().strip(),
            "difficulty_level": self._piece_difficulty_edit.text().strip(),
            "notes": self._piece_notes_edit.toPlainText().strip(),
        }

        def save(catalog: CatalogService):
            """新的作曲家先新增，再寫曲目的欄位"""
            new_composer = add_composer(catalog)[0] if not composer_id and composer_text else composer_id
            catalog.update_record(SHEET_PIECES, piece.id, {**values, "composer_id": new_composer})
        self._write(t("catalog.save"), save, lambda _result: self._refresh_all(), piece_title=piece.title)

    def _delete_piece(self, piece: Piece):
        """刪除曲目的元資料（不刪除 Drive 上的檔案）"""
        result = QMessageBox.question(
            self, t("catalog.piece.delete"),
            t("catalog.piece.confirm_delete", name=piece.title),
        )
        if result != QMessageBox.Yes:
            return

        def deleted(_result):
            """刪好了"""
            self._refresh_all()
            self._clear_detail()
        self._write(
            t("catalog.piece.delete"), lambda catalog: catalog.delete_piece(piece.id), deleted, piece_title=piece.title,
        )

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
        self._write(
            t("catalog.edition.add"),
            self._adding(SHEET_EDITIONS, [{"piece_id": piece_id, "publisher": publisher.strip()}]),
            lambda ids: self._show_piece_detail(piece_id), piece_title=self._piece_title(piece_id),
        )

    def _save_edition(self, original: Edition, pub_w, label_w, cat_w, year_w, notes_w):
        """儲存版本變更"""
        values = {
            "publisher": pub_w.text().strip(),
            "edition_label": label_w.text().strip(),
            "catalog_number": cat_w.text().strip(),
            "year": year_w.text().strip(),
            "notes": notes_w.text().strip(),
        }
        self._write(
            t("catalog.save"), lambda catalog: catalog.update_record(SHEET_EDITIONS, original.id, values),
            lambda _result: self._show_piece_detail(original.piece_id),
            piece_title=self._piece_title(original.piece_id),
        )

    def _set_active_edition(self, piece: Piece, edition_id: str):
        """設定使用中版本"""
        def done(_result):
            """設好了"""
            self._refresh_all()
            self._show_piece_detail(piece.id)
        self._write(
            t("catalog.edition.set_active"),
            lambda catalog: catalog.update_record(SHEET_PIECES, piece.id, {"active_edition_id": edition_id}),
            done, piece_title=piece.title,
        )

    def _piece_title(self, piece_id: str) -> str:
        """曲目的曲名（記進詳細資料與紀錄檔）；不知道時為空字串"""
        if self._current_detail is not None and self._current_detail.piece.id == piece_id:
            return self._current_detail.piece.title
        return next((piece.title for piece in self._pieces if piece.id == piece_id), "")

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
        """新增樂章，編號接在寫入前一刻讀到的這首曲目已有的樂章之後"""
        self._write(
            t("catalog.movement.add"), self._adding_prepared(lambda catalog: catalog.new_movement(piece_id)),
            lambda ids: self._show_piece_detail(piece_id), piece_title=self._piece_title(piece_id),
        )

    def _save_movement(self, original, table, row, piece_id):
        """儲存樂章變更"""
        values = {
            "movement_number": table.item(row, 0).text().strip(),
            "name": table.item(row, 1).text().strip(),
            "tempo_marking": table.item(row, 2).text().strip(),
            "key_signature": table.item(row, 3).text().strip(),
        }
        self._write(
            t("catalog.save"), lambda catalog: catalog.update_record(SHEET_MOVEMENTS, original.id, values),
            lambda _result: self._show_piece_detail(piece_id), piece_title=self._piece_title(piece_id),
        )

    def _delete_movement(self, movement_id: str, piece_id: str):
        """刪除樂章及其分譜"""
        result = QMessageBox.question(
            self, t("catalog.movement.delete"),
            t("catalog.movement.confirm_delete"),
        )
        if result != QMessageBox.Yes:
            return
        self._write(
            t("catalog.movement.delete"), lambda catalog: catalog.delete_movement(movement_id),
            lambda _result: self._show_piece_detail(piece_id), piece_title=self._piece_title(piece_id),
        )

    # --- 版本頁籤 ---

    def _build_parts_tab(self, detail: PieceDetail) -> QWidget:
        """建立版本頁（ui/edition_page.py）：編制表加沒登記的檔，按儲存才寫進譜庫；上方另有套用命名格式

        Args:
            detail: 曲目完整資訊
        """
        if not detail.editions:
            widget = QWidget()
            QVBoxLayout(widget).addWidget(QLabel(t("catalog.tree.no_items")))
            return widget
        from ui.edition_page import EditionPagePanel
        version = self._detail_version
        page = EditionPagePanel(
            detail, self._read, self._write, lambda: self._auth.account_email or "",
            lambda: version == self._detail_version, self._voice_candidates, self._orchestra_type,
        )
        rename_btn = QPushButton(t("catalog.drive.apply_template"))
        rename_btn.clicked.connect(lambda: self._open_drive_rename(page.selected_edition(), detail))
        page.actions.addWidget(rename_btn)
        if self._downloads is not None:
            download_btn = QPushButton(t("catalog.download.button"))
            download_btn.clicked.connect(lambda: self._download_files(page.folder_pdfs(), detail.piece.title))
            page.actions.addWidget(download_btn)
        if self._reverter is not None:
            log_btn = QPushButton(t("deposit_log.button"))
            log_btn.clicked.connect(lambda: self._show_deposit_log(page, detail.piece.title, version))
            page.actions.addWidget(log_btn)
        page.actions.addStretch()
        return page

    # --- 存入紀錄與撤銷存入 ---

    def _show_deposit_log(self, page, piece_title: str, version: int):
        """版本頁的「存入紀錄…」：讀存入紀錄，列出動到這個版本的存入；選一次按「撤銷存入…」就開撤銷的預覽

        Args:
            page: 版本頁（EditionPagePanel）
            piece_title: 曲名，讀不到時記進詳細資料與紀錄檔
            version: 建出版本頁時的詳細面板版本（頁面已換掉就不再開）
        """
        edition = page.selected_edition()
        if edition is None:
            return

        def done(entries: List[DepositLogEntry]):
            """讀到了：開存入紀錄，選了一次存入就接著撤銷"""
            if version != self._detail_version:
                return
            from ui.deposit_revert_dialog import DepositLogDialog
            dialog = DepositLogDialog(deposits(entries, edition.id), self)
            accepted = dialog.exec() == QDialog.Accepted
            chosen = dialog.chosen()
            dialog.deleteLater()
            if accepted and chosen is not None:
                self._revert_deposit(chosen, page, piece_title, version)

        self._read(
            lambda catalog: catalog.deposit_log(), done, lambda: None,
            lambda: self._show_deposit_log(page, piece_title, version), piece_title,
        )

    def _revert_deposit(self, deposit: Deposit, page, piece_title: str, version: int):
        """讀出撤銷那次存入的預覽、開預覽對話框，按「撤銷存入」後交給主視窗在背景撤銷

        Args:
            deposit: 要撤銷的那次存入
            page: 版本頁（撤銷後重新讀取）
            piece_title: 曲名，失敗時記進詳細資料與紀錄檔
            version: 建出版本頁時的詳細面板版本
        """
        read = self._reverter.revert_reader(deposit.deposit_id, [entry.local_path for entry in deposit.entries])

        def done(preview: RevertPreview):
            """讀到了：開預覽，勾了群組按下就交給主視窗"""
            from ui.deposit_revert_dialog import DepositRevertDialog
            dialog = DepositRevertDialog(preview, self)
            accepted = dialog.exec() == QDialog.Accepted
            choices = dialog.choices()
            dialog.deleteLater()
            if not accepted or not choices:
                return

            def finished(outcome: RevertOutcome):
                """主視窗的撤銷結束"""
                self._revert_finished(outcome, lambda: self._revert_deposit(deposit, page, piece_title, version))
                if version == self._detail_version:
                    page.reload()

            if not self._reverter.revert_deposit(preview, choices, finished):
                QMessageBox.information(self, t("revert.confirm"), t("revert.busy"))

        self._read(
            read, done, lambda: None, lambda: self._revert_deposit(deposit, page, piece_title, version), piece_title,
        )

    def _revert_finished(self, outcome: RevertOutcome, retry: Callable[[], None]):
        """撤銷存入結束（進度與完成幾份在主視窗的狀態列）：中途失敗時頂端提示列寫出已撤掉幾份、按「重試」再開一次預覽；
        沒撤銷的群組列出原因"""
        if outcome.error is not None:
            failure = CatalogFailure.from_error(outcome.error, t("revert.confirm"))
            self._error_bar.show_failure(t("revert.stopped", count=outcome.completed), failure, retry)
        else:
            self._error_bar.clear()
        if outcome.skipped:
            from ui.deposit_revert_dialog import skipped_text
            QMessageBox.warning(self, t("revert.confirm"), skipped_text(outcome))

    # --- 發譜紀錄、撤回與收回 ---

    def _show_publish_log(self):
        """經譜庫服務讀發譜紀錄（一併更新收回提醒的摘要），讀到後在右側面板列出每次發譜、撤回、收回與不收回；
        選一次發譜按「撤回…」就開撤回的預覽，按「收回…」就開收回的預覽"""
        from ui.publish_retract_dialog import PublishLogPanel
        self._clear_detail()
        self._current_detail = None
        loading = QLabel(t("catalog.loading"))
        loading.setAlignment(Qt.AlignCenter)
        self._detail_layout.addWidget(loading)
        version = self._detail_version

        def on_done(records):
            """讀到了：換成列表"""
            if version == self._detail_version:
                self._clear_detail()
                panel = PublishLogPanel(publish_entries(records))
                panel.retract_requested.connect(self._retract)
                panel.recall_requested.connect(self.recall)
                self._detail_layout.addWidget(panel)

        def unreadable():
            """讀不到：詳細面板寫「讀不到」"""
            if version == self._detail_version:
                self._clear_detail()
                self._detail_layout.addWidget(self._unreadable_label())

        self._read(self._reminder.read, on_done, unreadable, self._show_publish_log)

    def _retract(self, entry: PublishLogEntry):
        """撤回那次發譜：沒登入 HuaXia 時先登入，在等待中讀預覽、開預覽對話框，按「撤回」後在等待中撤回並寫出結果

        Args:
            entry: 發譜紀錄列表裡選的那次發譜
        """
        from ui.publish_retract_dialog import RetractDialog
        if not self._signed_in_huaxia("retract"):
            return
        retry = functools.partial(self._retract, entry)
        done, preview = self._while_waiting(
            "retract", t("retract.reading"), lambda: self._retracter.preview(entry), retry,
        )
        if not done:
            return
        dialog = RetractDialog(preview, self)
        accepted = dialog.exec() == QDialog.Accepted
        chosen = dialog.chosen()
        dialog.deleteLater()
        if not accepted or not chosen:
            return
        done, result = self._while_waiting(
            "retract", t("retract.running"), lambda: self._retracter.retract(entry, chosen, self._connected()), retry,
        )
        if done:
            self._retract_finished(result)

    def _signed_in_huaxia(self, action: str) -> bool:
        """撤回或收回前確認已登入 HuaXia（huaxia_prompts.ensure_signed_in）

        Args:
            action: 介面字串鍵的開頭（"retract" 或 "recall"）
        """
        return ensure_signed_in(self, self._huaxia_auth, t(f"{action}.title_short"), f"{action}.sign_in_first")

    def _while_waiting(
        self, action: str, message: str, work: Callable[[], object], retry: Callable[[], None],
        catalog_problem: str = "catalog.error.cannot_write",
    ) -> Tuple[bool, object]:
        """在等待視窗裡做撤回或收回的一步（catalog_tasks.wait_for_catalog）：連不上 HuaXia 時寫出原因，其他失敗
        顯示提示列

        Args:
            action: 介面字串鍵的開頭（"retract" 或 "recall"）：標題與連不上、失敗的說明
            message: 等待視窗的說明
            work: 在背景執行緒執行的那一步
            retry: 提示列按「重試」重來一次
            catalog_problem: 提示列開頭的介面字串鍵（這一步是讀譜庫時傳讀不到的）

        Returns:
            （成功與否，那一步的結果；失敗時為 None）
        """
        return wait_for_catalog(
            self, self._error_bar, t(f"{action}.title_short"), message, work, t(catalog_problem), retry,
            huaxia_action=action,
        )

    def _retract_finished(self, result: RetractResult):
        """撤回結束：寫出結果，重新讀取發譜紀錄（_show_removal_result）"""
        from ui.publish_retract_dialog import result_text
        self._show_removal_result("retract", result, result_text)

    def _show_removal_result(self, action: str, result, text: Callable[[Any], str]):
        """撤回或收回結束：不是幹部時寫出原因（什麼都沒做）；否則寫出結果（中途停下或紀錄寫不進譜庫時是警告），
        重新讀取發譜紀錄

        Args:
            action: 介面字串鍵的開頭（"retract" 或 "recall"）
            result: 撤回或收回的結果（RetractResult、RecallResult：not_officer、error、log_error）
            text: 結果的說明
        """
        title = t(f"{action}.title_short")
        if result.not_officer is not None:
            QMessageBox.warning(self, title, not_officer_text(result.not_officer))
            return
        if result.error is not None or result.log_error is not None:
            QMessageBox.warning(self, title, text(result))
        else:
            QMessageBox.information(self, title, text(result))
        self._show_publish_log()

    def recall(self):
        """收回：沒登入 HuaXia 時先登入，在等待中讀預覽、開收回對話框，按「收回」或「勾選的不收回」後在等待中執行並寫出結果

        發譜紀錄的「收回…」與主視窗收回提醒的「查看並收回」都從這裡開始。
        """
        from ui.publish_recall_dialog import RecallDialog
        if not self._signed_in_huaxia("recall"):
            return
        done, preview = self._while_waiting(
            "recall", t("recall.reading"), lambda: self._recaller.preview(self._connected()), self.recall,
            "catalog.error.cannot_read",
        )
        if not done:
            return
        dialog = RecallDialog(preview, self)
        accepted = dialog.exec() == QDialog.Accepted
        chosen, decision = dialog.chosen(), dialog.decision
        dialog.deleteLater()
        if not accepted or not chosen:
            return
        if decision is PublishAction.KEEP:
            done, _ = self._while_waiting(
                "recall", t("recall.keeping"), lambda: self._recaller.keep(chosen, self._connected()),
                self.recall,
            )
            if done:
                QMessageBox.information(self, t("recall.title_short"), t("recall.kept", count=len(chosen)))
                self._show_publish_log()
            return
        done, result = self._while_waiting(
            "recall", t("recall.running"), lambda: self._recaller.recall(chosen, self._connected()), self.recall,
        )
        if done:
            self._recall_finished(result)

    def _recall_finished(self, result: RecallResult):
        """收回結束：寫出結果，重新讀取發譜紀錄（_show_removal_result）"""
        from ui.publish_recall_dialog import result_text
        self._show_removal_result("recall", result, result_text)

    # --- 一般下載 ---

    def _download_files(self, pdfs: List[FolderPdf], piece_title: str):
        """版本頁的「下載…」：勾選要下載的檔或整個版本，選資料夾後在背景下載；已有一件下載在跑時不做

        Args:
            pdfs: 版本資料夾裡全部的 PDF
            piece_title: 曲名，記進紀錄檔
        """
        if self._downloading.busy:
            return
        if not pdfs:
            QMessageBox.information(self, t("download.title"), t("catalog.download.no_files"))
            return
        from ui.catalog_download_dialog import CatalogDownloadDialog
        dialog = CatalogDownloadDialog(pdfs, self)
        accepted = dialog.exec() == QDialog.Accepted
        chosen, whole = dialog.chosen(), dialog.whole()
        dialog.deleteLater()
        if not accepted:
            return
        folder = QFileDialog.getExistingDirectory(self, t("catalog.download.folder"))
        if folder:
            self._run_download(folder_items(chosen, folder, whole, piece_title), piece_title)

    @property
    def _download_task(self) -> Optional[BackgroundTask]:
        """進行中的一般下載；沒有時為 None"""
        return self._downloading.task

    def _run_download(self, items: List[DownloadItem], piece_title: str):
        """在背景下載到自選的資料夾（TaskSlot，與主視窗的長工作同一套），狀態列顯示進度與取消鈕

        Args:
            items: 要下載的檔與放的位置
            piece_title: 曲名，失敗時記進詳細資料與紀錄檔
        """
        service = self._downloads
        work = catalog_work(
            self._connected, lambda catalog, cancel, report: service.download(items, catalog, cancel, report),
            lambda error: DownloadOutcome(total=len(items), error=error),
        )
        self._downloading.run(
            work, TaskTexts.of("download"),
            lambda outcome, _cancelled: self._download_finished(
                outcome, lambda: self._run_download(items, piece_title), piece_title,
            ),
        )

    def _show_download_state(self):
        """依有沒有一般下載在跑，顯示取消鈕（要求取消後按不下去）"""
        self._download_cancel_btn.setVisible(self._downloading.busy)
        self._download_cancel_btn.setEnabled(self._downloading.busy and not self._downloading.cancel_requested)

    def _download_finished(self, outcome: DownloadOutcome, retry: Callable[[], None], piece_title: str):
        """一般下載結束：狀態列寫出完成幾份；中途失敗時頂端提示列寫出已完成幾份、按「重試」補齊；沒下載好的列出來"""
        if outcome.error is not None:
            failure = CatalogFailure.from_error(outcome.error, t("download.title"), piece_title=piece_title)
            self._error_bar.show_failure(t("download.stopped", count=outcome.completed), failure, retry)
            self._status_bar.clearMessage()
        else:
            key = "download.cancelled" if outcome.cancelled else "download.done"
            self._status_bar.showMessage(t(key, count=outcome.completed))
        text = not_downloaded_text(outcome)
        if text:
            QMessageBox.warning(self, t("download.title"), text)

    def _open_drive_rename(self, edition: Edition, detail: PieceDetail):
        """經譜庫服務讀版本資料夾的群組，再開啟 Drive 重新命名對話框；改好後重新顯示曲目"""
        if not edition.drive_folder_id:
            return
        from services.drive_rename_service import build_groups_from_drive
        from ui.drive_rename_dialog import DriveRenameDialog

        def opened(groups):
            """讀到群組了"""
            dialog = DriveRenameDialog(
                self._connected, groups, self, sign_in=self._sign_in_again, open_settings=self._open_settings,
                log=self._log,
            )
            if dialog.exec() == QDialog.Accepted:
                self._show_piece_detail(detail.piece.id)
        self._read(
            lambda catalog: build_groups_from_drive(catalog, edition.drive_folder_id, detail, detail.composer),
            opened, lambda: None, lambda: self._open_drive_rename(edition, detail), piece_title=detail.piece.title,
        )

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
        self._write(
            t("catalog.performance.add"),
            self._adding(SHEET_PERFORMANCES, [{"piece_id": piece_id, "status": PerformanceStatus.PLANNED.value}]),
            lambda ids: self._show_piece_detail(piece_id), piece_title=self._piece_title(piece_id),
        )

    def _save_performance(self, original, table, row, piece_id):
        """儲存演出紀錄變更"""
        status_combo = table.cellWidget(row, 4)
        values = {
            "performance_date": table.item(row, 0).text().strip(),
            "venue": table.item(row, 1).text().strip(),
            "conductor": table.item(row, 2).text().strip(),
            "ensemble": table.item(row, 3).text().strip(),
            "status": status_combo.currentData() if status_combo else original.status,
            "program_order": table.item(row, 5).text().strip(),
        }
        self._write(
            t("catalog.save"), lambda catalog: catalog.update_record(SHEET_PERFORMANCES, original.id, values),
            lambda _result: self._show_piece_detail(piece_id), piece_title=self._piece_title(piece_id),
        )

    def _delete_performance(self, performance_id: str, piece_id: str):
        """刪除演出紀錄"""
        result = QMessageBox.question(
            self, t("catalog.performance.delete"),
            t("catalog.performance.confirm_delete"),
        )
        if result != QMessageBox.Yes:
            return
        self._write(
            t("catalog.performance.delete"),
            lambda catalog: catalog.delete_record(SHEET_PERFORMANCES, performance_id),
            lambda _result: self._show_piece_detail(piece_id), piece_title=self._piece_title(piece_id),
        )


class _DriveFilePicker(QDialog):
    """從 Drive 中勾選資料夾或檔案的對話框：內容經譜庫服務在背景列出，讀不到時頂端顯示提示列"""

    def __init__(
        self, connect: Callable[[], CatalogService], root_folder_id: str, parent=None,
        sign_in: Optional[Callable[[], bool]] = None, open_settings: Optional[Callable[[], bool]] = None,
        log: Optional[ActivityLog] = None,
    ):
        """
        Args:
            connect: 在背景執行緒取得譜庫服務的函式
            root_folder_id: 從哪個資料夾開始列
            parent: 父元件
            sign_in: 提示列「重新登入 Google」時呼叫，回傳是否登入成功
            open_settings: 提示列「開啟譜庫設定」時呼叫，回傳是否改了設定
            log: 紀錄檔，讀不到時寫進去
        """
        super().__init__(parent)
        self.setWindowTitle(t("catalog.drive.collect_files"))
        self.setMinimumSize(550, 500)
        self.selected_files: List[DriveFile] = []
        self._connect = connect
        self._root_id = root_folder_id
        self._tasks = CatalogTasks(self)
        layout = QVBoxLayout(self)
        self._error_bar = CatalogErrorBar(log=log)
        self._error_bar.connect_remedies(sign_in, open_settings)
        layout.addWidget(self._error_bar)
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

    def done(self, result: int):
        """關閉對話框：放棄還在跑的背景讀取"""
        self._tasks.abandon_all()
        super().done(result)

    def _load_children(self, parent_node: Optional[QTreeWidgetItem], folder_id: str):
        """在背景載入資料夾內容（資料夾與 PDF 皆可勾選）；讀不到時顯示提示列，下次展開時再讀"""
        def listed(contents):
            """讀到了"""
            if parent_node is not None:
                parent_node.takeChildren()
            for folder in contents.folders:
                node = QTreeWidgetItem([folder.name])
                node.setData(0, Qt.UserRole, ("folder", folder))
                node.setData(0, Qt.UserRole + 1, False)
                node.setCheckState(0, Qt.Unchecked)
                node.addChild(QTreeWidgetItem([t("catalog.loading")]))
                self._add_node(parent_node, node)
            for pdf in contents.pdfs:
                node = QTreeWidgetItem([pdf.name])
                node.setData(0, Qt.UserRole, ("file", pdf))
                node.setCheckState(0, Qt.Unchecked)
                self._add_node(parent_node, node)

        def failed(error: CatalogError):
            """讀不到"""
            if parent_node is not None:
                parent_node.setData(0, Qt.UserRole + 1, False)
            self._error_bar.show_failure(
                t("catalog.error.cannot_read"), CatalogFailure.from_error(error, t("catalog.action.read")),
                lambda: self._load_children(parent_node, folder_id),
            )
        self._error_bar.clear()
        self._tasks.run(lambda: self._connect().folder_contents(folder_id), listed, failed)

    def _add_node(self, parent_node: Optional[QTreeWidgetItem], node: QTreeWidgetItem):
        """把項目加到樹狀結構的最上層或 parent_node 底下"""
        if parent_node is None:
            self._tree.addTopLevelItem(node)
        else:
            parent_node.addChild(node)

    def _on_expand(self, item):
        """展開資料夾時在背景載入"""
        if item.data(0, Qt.UserRole + 1):
            return
        item.setData(0, Qt.UserRole + 1, True)
        data = item.data(0, Qt.UserRole)
        if data and data[0] == "folder":
            self._load_children(item, data[1].id)

    def _update_count(self):
        """更新已勾選數量顯示"""
        count = len(self._collect_checked())
        self._selected_label.setText(
            t("catalog.drive.collect_selected", count=count),
        )

    def _collect_checked(self, parent=None) -> List[DriveFile]:
        """遞迴收集所有勾選的資料夾與檔案（勾選資料夾時不重複收集其子項）"""
        results = []
        if parent is None:
            for i in range(self._tree.topLevelItemCount()):
                results.extend(self._collect_checked(self._tree.topLevelItem(i)))
        else:
            data = parent.data(0, Qt.UserRole)
            if data and parent.checkState(0) == Qt.Checked:
                results.append(data[1])
                return results
            for i in range(parent.childCount()):
                results.extend(self._collect_checked(parent.child(i)))
        return results

    def _on_accept(self):
        """確認選取"""
        self.selected_files = self._collect_checked()
        self.accept()
