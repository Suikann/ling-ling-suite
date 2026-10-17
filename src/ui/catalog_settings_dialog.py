# -*- coding: utf-8 -*-
"""
譜庫設定對話框

引導使用者完成：登入 Google → 選擇譜庫資料夾 → 自動偵測或建立譜庫目錄。
登入區顯示登入的帳號 email 與帳號提醒；登入時在背景等瀏覽器回來，期間顯示可取消的等待視窗。
瀏覽 Drive 資料夾、找資料夾裡的試算表與建立譜庫目錄都經譜庫服務（services/catalog_service.py）在背景執行，
失敗時在失敗的那個視窗頂端顯示錯誤提示列（ui/catalog_error_bar.py）。換了譜庫試算表時，忘掉記住的發譜平台，
改讀新譜庫的社團設定（services/club_settings_service.py）。
"""
import re
from typing import Callable, List, Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QGroupBox,
    QTreeWidget, QTreeWidgetItem, QDialogButtonBox, QWidget,
)
from PySide6.QtCore import Qt
from core.catalog_errors import CatalogError, CatalogFailure
from core.catalog_models import DriveFile
from core.locale import t
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.club_settings_service import ClubSettingsService
from services.google_auth_service import GoogleAuthService, LoginFailed, LoginFailure, LoginReason
from services.google_catalog import open_google_catalog, open_google_drive
from services.preferences_service import PreferencesService
from ui.catalog_error_bar import CatalogErrorBar
from ui.catalog_tasks import CatalogTasks
from ui.waiting_dialog import run_while_waiting

# 登入狀態旁的原因對應的介面字串
_LOGIN_REASON_KEYS = {
    LoginReason.NO_SECURE_STORAGE: "catalog.auth.reason.no_secure_storage",
    LoginReason.STORAGE_LOCKED: "catalog.auth.reason.storage_locked",
    LoginReason.NOT_SAVED: "catalog.auth.reason.not_saved",
    LoginReason.TOKEN_REJECTED: "catalog.auth.reason.token_rejected",
    LoginReason.UNREACHABLE: "catalog.auth.reason.unreachable",
}
# 登入沒有完成時提示的介面字串；取消不提示，FAILED 另帶錯誤內容
_LOGIN_FAILURE_KEYS = {
    LoginFailure.CLIENT_MISSING: "catalog.auth.credentials_missing",
    LoginFailure.BROWSER_NOT_OPENED: "catalog.auth.browser_not_opened",
    LoginFailure.TIMED_OUT: "catalog.auth.timed_out",
    LoginFailure.DENIED: "catalog.auth.denied",
    LoginFailure.SCOPES_MISSING: "catalog.auth.scopes_missing",
}


def _extract_folder_id(text: str) -> str:
    """從網址或純 ID 中擷取資料夾 ID"""
    text = text.strip()
    match = re.search(r'/folders/([a-zA-Z0-9_-]+)', text)
    if match:
        return match.group(1)
    return text


def sign_in_google(parent: QWidget, auth: GoogleAuthService) -> bool:
    """開瀏覽器登入 Google，期間顯示可取消的等待視窗；沒有完成時提示原因（取消不提示）

    譜庫設定的「登入」與錯誤提示列的「重新登入 Google」共用。

    Args:
        parent: 等待視窗與提示的父元件
        auth: Google 登入服務

    Returns:
        是否登入成功
    """
    try:
        run_while_waiting(
            parent, t("catalog.auth.title"), t("catalog.auth.waiting"), auth.authenticate, auth.cancel_login,
        )
    except LoginFailed as e:
        if e.failure is not LoginFailure.CANCELLED:
            key = _LOGIN_FAILURE_KEYS.get(e.failure)
            message = t(key) if key else t("catalog.auth.failed", error=str(e))
            QMessageBox.warning(parent, t("catalog.auth.title"), message)
        return False
    return True


class CatalogSettingsDialog(QDialog):
    """譜庫設定對話框"""

    def __init__(
        self, auth_service: GoogleAuthService,
        preferences: PreferencesService, parent=None,
        catalog_factory: Optional[Callable[[str], CatalogService]] = None,
        log: Optional[ActivityLog] = None,
    ):
        """
        Args:
            auth_service: Google 登入服務
            preferences: 偏好設定（譜庫試算表 ID、根資料夾 ID、記住的發譜平台）
            parent: 父元件
            catalog_factory: 在背景執行緒以試算表 ID（還沒有時為空字串）建立譜庫服務的函式，拿不到時丟出 CatalogError；
                省略時以登入服務的憑證接 Google（測試傳入接假 Google 的）
            log: 紀錄檔，讀寫失敗時寫進去；省略時以使用者資料目錄下的紀錄檔資料夾建立
        """
        super().__init__(parent)
        self._auth = auth_service
        self._prefs = preferences
        self._catalog_factory = catalog_factory or self._open_google
        self._club_settings = ClubSettingsService(preferences)
        self._log = log or ActivityLog()
        self._tasks = CatalogTasks(self)
        self._retry: Optional[Callable[[], None]] = None
        self.setWindowTitle(t("catalog.settings.title"))
        self.setMinimumWidth(560)
        self._build_ui()

    def done(self, result: int):
        """關閉對話框：放棄還在跑的背景工作，之後它們不再碰這個對話框"""
        self._tasks.abandon_all()
        super().done(result)

    def _open_google(self, spreadsheet_id: str) -> CatalogService:
        """以登入服務的憑證接 Google（背景執行緒呼叫）"""
        if spreadsheet_id:
            return open_google_catalog(self._auth, spreadsheet_id)
        return open_google_drive(self._auth)

    def _build_ui(self):
        """排出提示列、登入、選擇資料夾兩個步驟與底部按鈕，顯示目前的登入狀態"""
        layout = QVBoxLayout(self)
        self._error_bar = CatalogErrorBar(log=self._log)
        self._error_bar.connect_remedies(self._retry_failed, self._sign_in_again, self._focus_folder)
        layout.addWidget(self._error_bar)
        # --- 步驟 1：登入 ---
        step1 = QGroupBox(t("catalog.settings.step1"))
        step1_layout = QVBoxLayout(step1)
        status_row = QHBoxLayout()
        self._auth_status = QLabel()
        status_row.addWidget(self._auth_status)
        status_row.addStretch()
        status_row.addWidget(QLabel(t("catalog.auth.account_reminder")))
        self._login_btn = QPushButton()
        self._login_btn.clicked.connect(self._toggle_auth)
        status_row.addWidget(self._login_btn)
        step1_layout.addLayout(status_row)
        self._auth_reason = QLabel()
        self._auth_reason.setWordWrap(True)
        self._auth_reason.setStyleSheet("color: #f39c12;")
        step1_layout.addWidget(self._auth_reason)
        layout.addWidget(step1)
        # --- 步驟 2：選擇資料夾 ---
        step2 = QGroupBox(t("catalog.settings.step2"))
        step2_layout = QVBoxLayout(step2)
        folder_row = QHBoxLayout()
        self._folder_entry = QLineEdit(
            self._prefs.get("catalog_root_folder_id") or "",
        )
        self._folder_entry.setPlaceholderText(
            t("catalog.settings.folder_hint"),
        )
        folder_row.addWidget(self._folder_entry)
        browse_btn = QPushButton(t("catalog.settings.browse"))
        browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_btn)
        step2_layout.addLayout(folder_row)
        self._folder_status = QLabel("")
        self._folder_status.setWordWrap(True)
        step2_layout.addWidget(self._folder_status)
        layout.addWidget(step2)
        # --- 底部按鈕 ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton(t("catalog.settings.save"))
        save_btn.clicked.connect(self._save_settings)
        btn_row.addWidget(save_btn)
        cancel_btn = QPushButton(t("catalog.cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
        self._update_auth_display()
        # 開啟時只在已登入時自動找譜庫目錄：安全儲存沒解鎖時不為此再跳一次解鎖視窗
        if self._folder_entry.text().strip() and self._auth.status().signed_in:
            self._check_catalog_in_folder()

    def _update_auth_display(self):
        """更新登入狀態與旁邊的原因"""
        status = self._auth.status()
        self._auth_reason.setText(t(_LOGIN_REASON_KEYS[status.reason]) if status.reason else "")
        self._auth_reason.setVisible(status.reason is not None)
        if status.signed_in:
            email = self._auth.account_email
            text = t("catalog.auth.connected_as", email=email) if email else t("catalog.auth.connected")
            self._auth_status.setText(text)
            self._auth_status.setStyleSheet("color: #2ecc71; font-weight: bold;")
            self._login_btn.setText(t("catalog.auth.disconnect"))
        else:
            self._auth_status.setText(t("catalog.auth.not_connected"))
            self._auth_status.setStyleSheet("color: #e74c3c;")
            self._login_btn.setText(t("catalog.auth.connect"))

    def _toggle_auth(self):
        """登入或登出"""
        if self._auth.status().signed_in:
            self._auth.logout()
            self._update_auth_display()
            return
        signed_in = sign_in_google(self, self._auth)
        self._update_auth_display()
        if signed_in:
            QMessageBox.information(self, t("catalog.auth.title"), t("catalog.auth.success"))

    # --- 經譜庫服務的背景工作 ---

    def _run(
        self, work: Callable[[], object], done: Callable[[object], None], heading: str, action: str,
        retry: Callable[[], None],
    ):
        """在背景執行 work，完成時呼叫 done；失敗時清掉資料夾狀態、顯示提示列，按「重試」呼叫 retry

        Args:
            work: 經譜庫服務的工作（背景執行緒呼叫）
            done: 成功時以結果呼叫
            heading: 提示列的開頭（讀不到譜庫、寫不進譜庫）
            action: 失敗的動作，記進詳細資料與紀錄檔
            retry: 提示列按「重試」時呼叫
        """
        def failed(error: CatalogError):
            """失敗：提示列寫出類別與怎麼辦"""
            self._folder_status.setText("")
            self._retry = retry
            self._error_bar.show_failure(heading, CatalogFailure.from_error(error, action))
        self._error_bar.clear()
        self._tasks.run(work, done, failed)

    def _retry_failed(self):
        """提示列的「重試」：重做失敗的那件事"""
        if self._retry is not None:
            self._retry()

    def _sign_in_again(self) -> bool:
        """提示列的「重新登入 Google」：登入並更新登入狀態，回傳是否登入成功"""
        signed_in = sign_in_google(self, self._auth)
        self._update_auth_display()
        return signed_in

    def _focus_folder(self) -> bool:
        """提示列的「開啟譜庫設定」：已經在譜庫設定裡，收起提示列、把游標移到資料夾欄位（不重試）"""
        self._error_bar.clear()
        self._folder_entry.setFocus()
        return False

    def _browse_folder(self):
        """以階層式樹狀結構瀏覽 Drive 資料夾，選了就找裡面的譜庫目錄"""
        dialog = _DriveFolderBrowser(lambda: self._catalog_factory(""), self._sign_in_again, self._log, self)
        if dialog.exec() == QDialog.Accepted and dialog.selected_id:
            self._folder_entry.setText(dialog.selected_id)
            self._check_catalog_in_folder()

    def _check_catalog_in_folder(self):
        """在背景找選定資料夾裡的試算表：一份就用它，多份讓使用者選，沒有則提議建立"""
        folder_id = _extract_folder_id(self._folder_entry.text())
        if not folder_id:
            return
        self._show_folder_status(t("catalog.loading"), "gray")

        def found(sheets: List[DriveFile]):
            """找到了"""
            self._folder_status.setText("")
            if len(sheets) == 1:
                self._use_catalog(sheets[0])
            elif len(sheets) > 1:
                self._pick_catalog(sheets)
            else:
                self._offer_create_catalog(folder_id)
        self._run(
            lambda: self._catalog_factory("").spreadsheets_in(folder_id), found,
            t("catalog.error.cannot_read"), t("catalog.action.read"), self._check_catalog_in_folder,
        )

    def _show_folder_status(self, text: str, color: str):
        """資料夾欄位下方的狀態"""
        self._folder_status.setText(text)
        self._folder_status.setStyleSheet(f"color: {color};")

    def _use_catalog(self, sheet: DriveFile):
        """使用找到的試算表作為譜庫目錄"""
        self._connect_catalog(sheet.id)
        self._show_folder_status(t("catalog.settings.catalog_found", name=sheet.name), "#2ecc71")

    def _connect_catalog(self, spreadsheet_id: str):
        """記下譜庫試算表；換了試算表時忘掉記住的發譜平台，在背景改讀新譜庫的社團設定（讀不到時就是沒有）"""
        if spreadsheet_id == self._prefs.get("catalog_spreadsheet_id"):
            return
        self._prefs.set("catalog_spreadsheet_id", spreadsheet_id)
        self._club_settings.forget()
        self._tasks.run(
            lambda: self._club_settings.read(self._catalog_factory(spreadsheet_id)),
            lambda platform: None, lambda error: None,
        )

    def _pick_catalog(self, sheets: List[DriveFile]):
        """資料夾內有多個試算表時，讓使用者選擇"""
        from PySide6.QtWidgets import QInputDialog
        names = [s.name for s in sheets]
        chosen, ok = QInputDialog.getItem(
            self, t("catalog.settings.title"),
            t("catalog.settings.pick_catalog"),
            names, 0, False,
        )
        if not ok:
            return
        for s in sheets:
            if s.name == chosen:
                self._use_catalog(s)
                return

    def _offer_create_catalog(self, folder_id: str):
        """資料夾內沒有試算表時，讓使用者命名並在背景建立"""
        from PySide6.QtWidgets import QInputDialog
        name, ok = QInputDialog.getText(
            self, t("catalog.settings.title"),
            t("catalog.settings.create_prompt"),
            text=t("catalog.settings.default_catalog_name"),
        )
        if not ok or not name.strip():
            self._show_folder_status(t("catalog.settings.catalog_not_found"), "#f39c12")
            return
        name = name.strip()

        def created(spreadsheet_id: str):
            """建好了"""
            self._connect_catalog(spreadsheet_id)
            self._show_folder_status(t("catalog.settings.catalog_created"), "#2ecc71")

        def create():
            """建立譜庫目錄（重試時再呼叫一次）"""
            self._show_folder_status(t("catalog.loading"), "gray")
            self._run(
                lambda: self._catalog_factory("").create_catalog(name, folder_id), created,
                t("catalog.error.cannot_write"), t("catalog.action.create_catalog"), create,
            )
        create()

    def _save_settings(self):
        """儲存設定"""
        folder_id = _extract_folder_id(self._folder_entry.text())
        self._prefs.set("catalog_root_folder_id", folder_id)
        self._prefs.save()
        self.accept()


class _DriveFolderBrowser(QDialog):
    """階層式 Google Drive 資料夾瀏覽器：子資料夾經譜庫服務在背景列出，讀不到時頂端顯示提示列"""

    def __init__(
        self, connect: Callable[[], CatalogService], sign_in: Callable[[], bool], log: ActivityLog, parent=None,
    ):
        """
        Args:
            connect: 在背景執行緒建立譜庫服務的函式
            sign_in: 提示列「重新登入 Google」時呼叫，回傳是否登入成功
            log: 紀錄檔，讀不到時寫進去
            parent: 父元件
        """
        super().__init__(parent)
        self.setWindowTitle(t("catalog.settings.pick_folder"))
        self.setMinimumSize(500, 450)
        self.selected_id = ""
        self._connect = connect
        self._tasks = CatalogTasks(self)
        self._retry: Callable[[], None] = lambda: None
        layout = QVBoxLayout(self)
        self._error_bar = CatalogErrorBar(log=log)
        self._error_bar.connect_remedies(lambda: self._retry(), sign_in)
        layout.addWidget(self._error_bar)
        self._path_label = QLabel("My Drive")
        self._path_label.setStyleSheet("font-weight: bold; padding: 4px;")
        layout.addWidget(self._path_label)
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.itemExpanded.connect(self._on_expand)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        self._tree.currentItemChanged.connect(self._on_select)
        layout.addWidget(self._tree)
        self._selected_label = QLabel("")
        self._selected_label.setStyleSheet("color: #7c6ddf; padding: 4px;")
        layout.addWidget(self._selected_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._load_children(None, "root")

    def done(self, result: int):
        """關閉瀏覽器：放棄還在跑的背景讀取"""
        self._tasks.abandon_all()
        super().done(result)

    def _load_children(self, parent_node: Optional[QTreeWidgetItem], folder_id: str):
        """在背景列出子資料夾並加入樹狀結構；讀不到時顯示提示列，下次展開時再讀"""
        def listed(folders: List[DriveFile]):
            """讀到了"""
            if parent_node is not None:
                parent_node.takeChildren()
            for folder in folders:
                node = QTreeWidgetItem([folder.name])
                node.setData(0, Qt.UserRole, folder.id)
                node.setData(0, Qt.UserRole + 1, False)
                node.addChild(QTreeWidgetItem([t("catalog.loading")]))
                if parent_node is None:
                    self._tree.addTopLevelItem(node)
                else:
                    parent_node.addChild(node)
            if parent_node is not None and not folders:
                empty = QTreeWidgetItem([t("catalog.settings.no_folders")])
                empty.setFlags(Qt.NoItemFlags)
                parent_node.addChild(empty)

        def failed(error: CatalogError):
            """讀不到"""
            if parent_node is not None:
                parent_node.setData(0, Qt.UserRole + 1, False)
            self._retry = lambda: self._load_children(parent_node, folder_id)
            self._error_bar.show_failure(
                t("catalog.error.cannot_read"), CatalogFailure.from_error(error, t("catalog.action.read")),
            )
        self._error_bar.clear()
        self._tasks.run(lambda: self._connect().subfolders(folder_id), listed, failed)

    def _on_expand(self, item):
        """展開資料夾時在背景載入子資料夾"""
        if item.data(0, Qt.UserRole + 1):
            return
        item.setData(0, Qt.UserRole + 1, True)
        folder_id = item.data(0, Qt.UserRole) or ""
        if folder_id:
            self._load_children(item, folder_id)

    def _on_select(self, current, previous):
        """選取變更時更新顯示"""
        if current and current.data(0, Qt.UserRole):
            self._selected_label.setText(current.text(0))

    def _on_double_click(self, item, column):
        """雙擊選取資料夾"""
        folder_id = item.data(0, Qt.UserRole) or ""
        if folder_id:
            self.selected_id = folder_id
            self.accept()

    def _on_accept(self):
        """確認選取"""
        current = self._tree.currentItem()
        if current:
            self.selected_id = current.data(0, Qt.UserRole) or ""
        self.accept()
