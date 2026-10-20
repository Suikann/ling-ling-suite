# -*- coding: utf-8 -*-
"""
譜庫設定對話框

引導使用者完成：登入 Google → 選擇譜庫資料夾 → 自動偵測或建立譜庫總表。
新建的譜庫總表標題列用當下的介面語言；選資料夾的步驟下方的「欄位語言」選單寫出連到的譜庫總表現在的標題語言
（各工作表不一致時不選），換語言時在背景把各工作表的標題列整列改寫（core/catalog_headers.py）。
登入區顯示登入的帳號 email 與帳號提醒；登入時在背景等瀏覽器回來，期間顯示可取消的等待視窗。
選資料夾的步驟下方寫出這台電腦記住的發譜平台（照社團設定裡的寫法，沒有時寫未設定）與到譜庫總表的社團設定工作表設定；
開著的時候重讀社團設定完成（例如譜庫視窗連上譜庫），經 Qt 訊號回到主執行緒改寫這一行，HuaXia 登入區跟著出現或藏起。
瀏覽 Drive 資料夾（最上層是我的雲端硬碟，它本身也能選）、找資料夾裡的試算表與建立譜庫總表都經譜庫服務
（services/catalog_service.py）在背景執行；根資料夾一律存成它真正的 ID，不存別名 root。
失敗時在失敗的那個視窗頂端顯示錯誤提示列（ui/catalog_error_bar.py）。找到或建好的譜庫總表先記在對話框裡，
按「儲存設定」才寫進偏好設定，取消時偏好設定不變；存了就在背景重讀社團設定（services/club_settings_service.py），
存的是另一份譜庫總表時先忘掉記住的發譜平台。重讀完成時由社團設定服務通知主視窗，不經這個（可能已關閉的）對話框。
這台電腦記住的社團設定啟用 HuaXia 發譜時，另有 HuaXia 登入區（services/huaxia_auth_service.py）：
登入前寫出連結 Discord 的提醒，以 Discord 登入時顯示可取消的等待視窗，不是幹部時說明原因；登出在背景進行。
"""
import re
from typing import Callable, List, Optional, Tuple

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QGroupBox, QComboBox,
    QTreeWidget, QTreeWidgetItem, QDialogButtonBox, QWidget,
)
from PySide6.QtCore import Qt, Signal
from core.catalog_constants import (
    DRIVE_ROOT_ALIAS, PUBLISHING_PLATFORM_NAMES, SHEET_CLUB_SETTINGS, PublishingPlatform,
)
from core.catalog_errors import CatalogError, CatalogFailure
from core.catalog_models import DriveFile
from core.constants import CATALOG_ROOT_FOLDER_ID_KEY, CATALOG_SPREADSHEET_ID_KEY
from core.locale import get_locale, language_names, t
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService, PendingCatalog
from services.club_settings_service import ClubSettingsService
from services.google_auth_service import GoogleAuthService, LoginFailed, LoginFailure, LoginReason
from services.google_catalog import open_google_catalog, open_google_drive
from services.huaxia_auth_service import HuaXiaAuthService, HuaXiaReason
from services.preferences_service import PreferencesService
from ui.catalog_error_bar import CatalogErrorBar
from ui.background_task import BackgroundTask
from ui.catalog_tasks import CatalogTasks
from ui.huaxia_prompts import sign_in_huaxia
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
# HuaXia 登入狀態旁的原因對應的介面字串（安全儲存的三種與 Google 共用）
_HUAXIA_REASON_KEYS = {
    HuaXiaReason.NO_SECURE_STORAGE: "catalog.auth.reason.no_secure_storage",
    HuaXiaReason.STORAGE_LOCKED: "catalog.auth.reason.storage_locked",
    HuaXiaReason.NOT_SAVED: "catalog.auth.reason.not_saved",
    HuaXiaReason.SESSION_GONE: "huaxia.auth.session_gone",
}



def _extract_folder_id(text: str) -> str:
    """從網址或純 ID 中擷取資料夾 ID；我的雲端硬碟的網址為根資料夾的別名"""
    text = text.strip()
    match = re.search(r'/folders/([a-zA-Z0-9_-]+)', text)
    if match:
        return match.group(1)
    if re.search(r'/drive/(u/\d+/)?my-drive\b', text):
        return DRIVE_ROOT_ALIAS
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

    # 社團設定服務寫入記住的結果（可能在背景執行緒），轉回主執行緒改寫發譜平台那一行
    _club_settings_read = Signal()

    def __init__(
        self, auth_service: GoogleAuthService,
        preferences: PreferencesService, parent=None,
        catalog_factory: Optional[Callable[[str], CatalogService]] = None,
        log: Optional[ActivityLog] = None,
        huaxia_auth: Optional[HuaXiaAuthService] = None,
        club_settings: Optional[ClubSettingsService] = None,
    ):
        """
        Args:
            auth_service: Google 登入服務
            preferences: 偏好設定（譜庫總表 ID、根資料夾 ID、記住的發譜平台）
            parent: 父元件
            catalog_factory: 在背景執行緒以試算表 ID（還沒有時為空字串）建立譜庫服務的函式，拿不到時丟出 CatalogError；
                省略時以登入服務的憑證接 Google（測試傳入接假 Google 的）
            log: 紀錄檔，讀寫失敗時寫進去；省略時以使用者資料目錄下的紀錄檔資料夾建立
            huaxia_auth: HuaXia 登入服務（全程式一份）；省略時沒有 HuaXia 登入區
            club_settings: 社團設定服務（全程式一份，主視窗訂閱它的通知）；省略時以偏好設定與紀錄檔建立
        """
        super().__init__(parent)
        self._huaxia = huaxia_auth
        self._huaxia_task: Optional[BackgroundTask] = None
        self._auth = auth_service
        self._prefs = preferences
        self._catalog_factory = catalog_factory or self._open_google
        self._log = log or ActivityLog()
        self._club_settings = club_settings or ClubSettingsService(preferences, self._log)
        self._huaxia_area: Optional[QGroupBox] = None
        self._tasks = CatalogTasks(self)
        # 找到或建好的譜庫總表；按「儲存設定」才寫進偏好設定
        self._spreadsheet_id = self._prefs.get(CATALOG_SPREADSHEET_ID_KEY) or ""
        # 連到的譜庫總表現在的標題語言；還沒讀到或各工作表不一致時為 None
        self._header_locale: Optional[str] = None
        self.setWindowTitle(t("catalog.settings.title"))
        self.setMinimumWidth(560)
        self._build_ui()
        self._club_settings_read.connect(self._show_publishing_platform)
        self._club_settings.subscribe(self._club_settings_read.emit)

    def done(self, result: int):
        """關閉對話框：不再收社團設定的通知，放棄還在跑的背景工作，之後它們不再碰這個對話框
        （重讀社團設定與 HuaXia 登出照常在背景跑完）"""
        self._club_settings.unsubscribe(self._club_settings_read.emit)
        self._tasks.abandon_all()
        if self._huaxia_task is not None:
            self._huaxia_task.abandon()
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
        self._error_bar.connect_remedies(self._sign_in_again, self._focus_folder)
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
            self._prefs.get(CATALOG_ROOT_FOLDER_ID_KEY) or "",
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
        language_row = QHBoxLayout()
        language_row.addWidget(QLabel(t("catalog.settings.header_language")))
        self._header_language = QComboBox()
        for locale, name in language_names():
            self._header_language.addItem(name, locale)
        self._header_language.activated.connect(lambda _: self._rewrite_headers(self._header_language.currentData()))
        language_row.addWidget(self._header_language)
        language_row.addStretch()
        step2_layout.addLayout(language_row)
        self._show_header_language(None, enabled=False)
        layout.addWidget(step2)
        self._platform_hint = QLabel()
        self._platform_hint.setWordWrap(True)
        layout.addWidget(self._platform_hint)
        self._show_publishing_platform()
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
        # 開啟時只在已登入時自動找譜庫總表：安全儲存沒解鎖時不為此再跳一次解鎖視窗
        if self._folder_entry.text().strip() and self._auth.status().signed_in:
            self._check_catalog_in_folder()

    def _show_publishing_platform(self):
        """寫出這台電腦記住的發譜平台（照社團設定裡的寫法）與到哪裡設定；啟用 HuaXia 時顯示 HuaXia 登入區

        登入區第一次要顯示時才建（建立時讀安全儲存），之後拿掉 HuaXia 只藏起來。
        """
        platform = self._club_settings.remembered()
        names = [name for name, value in PUBLISHING_PLATFORM_NAMES.items() if value is platform]
        name = names[0] if names else t("catalog.settings.publishing_platform_unset")
        self._platform_hint.setText(t("catalog.settings.publishing_platform", platform=name, sheet=SHEET_CLUB_SETTINGS))
        if self._huaxia is None:
            return
        huaxia = platform is PublishingPlatform.HUAXIA
        if huaxia and self._huaxia_area is None:
            self._huaxia_area = self._build_huaxia_area()
            layout = self.layout()
            layout.insertWidget(layout.indexOf(self._platform_hint) + 1, self._huaxia_area)
        if self._huaxia_area is not None:
            self._huaxia_area.setVisible(huaxia)

    def _build_huaxia_area(self) -> QGroupBox:
        """排出 HuaXia 登入區：登入狀態、登入或登出、連結 Discord 的提醒與狀態旁的原因"""
        group = QGroupBox(t("huaxia.settings.group"))
        group_layout = QVBoxLayout(group)
        row = QHBoxLayout()
        self._huaxia_status = QLabel()
        row.addWidget(self._huaxia_status)
        row.addStretch()
        self._huaxia_btn = QPushButton()
        self._huaxia_btn.clicked.connect(self._toggle_huaxia)
        row.addWidget(self._huaxia_btn)
        group_layout.addLayout(row)
        self._huaxia_reminder = QLabel(t("huaxia.auth.link_discord_first"))
        self._huaxia_reminder.setWordWrap(True)
        group_layout.addWidget(self._huaxia_reminder)
        self._huaxia_reason = QLabel()
        self._huaxia_reason.setWordWrap(True)
        self._huaxia_reason.setStyleSheet("color: #f39c12;")
        group_layout.addWidget(self._huaxia_reason)
        self._update_huaxia_display()
        return group

    def _update_huaxia_display(self):
        """更新 HuaXia 登入狀態、按鈕、提醒（只在沒登入時）與狀態旁的原因"""
        status = self._huaxia.status()
        self._huaxia_reason.setText(t(_HUAXIA_REASON_KEYS[status.reason]) if status.reason else "")
        self._huaxia_reason.setVisible(status.reason is not None)
        self._huaxia_reminder.setVisible(not status.signed_in)
        self._huaxia_btn.setEnabled(True)
        if status.signed_in:
            name = status.discord_name
            text = t("huaxia.auth.connected_as", name=name) if name else t("huaxia.auth.connected")
            self._huaxia_status.setText(text)
            self._huaxia_status.setStyleSheet("color: #2ecc71; font-weight: bold;")
            self._huaxia_btn.setText(t("huaxia.auth.disconnect"))
        else:
            self._huaxia_status.setText(t("huaxia.auth.not_connected"))
            self._huaxia_status.setStyleSheet("color: #e74c3c;")
            self._huaxia_btn.setText(t("huaxia.auth.connect"))

    def _toggle_huaxia(self):
        """以 Discord 登入 HuaXia，或在背景登出（登出要連網，結束後才更新狀態）"""
        if not self._huaxia.status().signed_in:
            sign_in_huaxia(self, self._huaxia)
            self._update_huaxia_display()
            return
        self._huaxia_btn.setEnabled(False)
        auth = self._huaxia
        self._huaxia_task = BackgroundTask(lambda should_cancel, report: auth.logout(), self)
        self._huaxia_task.finished.connect(lambda _: self._update_huaxia_display())
        self._huaxia_task.failed.connect(lambda _: self._update_huaxia_display())
        self._huaxia_task.start()

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
        retry: Callable[[], None], undo: Optional[Callable[[], None]] = None,
    ):
        """在背景執行 work，完成時呼叫 done；失敗時清掉資料夾狀態（或呼叫 undo）、顯示提示列，按「重試」呼叫 retry

        Args:
            work: 經譜庫服務的工作（背景執行緒呼叫）
            done: 成功時以結果呼叫
            heading: 提示列的開頭（讀不到譜庫、寫不進譜庫）
            action: 失敗的動作，記進詳細資料與紀錄檔
            retry: 提示列按「重試」時呼叫
            undo: 失敗時代替清掉資料夾狀態要做的事；省略時清掉資料夾狀態
        """
        def failed(error: CatalogError):
            """失敗：提示列寫出類別與怎麼辦"""
            if undo is None:
                self._folder_status.setText("")
            else:
                undo()
            self._error_bar.show_failure(heading, CatalogFailure.from_error(error, action), retry)
        self._error_bar.clear()
        self._tasks.run(work, done, failed)

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
        """以階層式樹狀結構瀏覽 Drive 資料夾，選了就找裡面的譜庫總表"""
        dialog = _DriveFolderBrowser(lambda: self._catalog_factory(""), self._sign_in_again, self._log, self)
        if dialog.exec() == QDialog.Accepted and dialog.selected_id:
            self._folder_entry.setText(dialog.selected_id)
            self._check_catalog_in_folder()

    def _check_catalog_in_folder(self):
        """在背景找選定資料夾裡的試算表：一份就用它，多份讓使用者選，沒有則提議建立

        資料夾寫的是根資料夾的別名時先換成真正的 ID 填回欄位，存起來的才比對得到檔案的 parents。
        """
        folder_id = _extract_folder_id(self._folder_entry.text())
        if not folder_id:
            return
        self._show_folder_status(t("catalog.loading"), "gray")

        def search() -> Tuple[str, List[DriveFile]]:
            """（資料夾真正的 ID，裡面的試算表）"""
            catalog = self._catalog_factory("")
            real_id = catalog.my_drive().id if folder_id == DRIVE_ROOT_ALIAS else folder_id
            return real_id, catalog.spreadsheets_in(real_id)

        def found(result: Tuple[str, List[DriveFile]]):
            """找到了"""
            real_id, sheets = result
            if real_id != folder_id:
                self._folder_entry.setText(real_id)
            self._folder_status.setText("")
            if len(sheets) == 1:
                self._use_catalog(sheets[0])
            elif len(sheets) > 1:
                self._pick_catalog(sheets)
            else:
                self._offer_create_catalog(real_id)
        self._run(
            search, found, t("catalog.error.cannot_read"), t("catalog.action.read"), self._check_catalog_in_folder,
        )

    def _show_folder_status(self, text: str, color: str):
        """資料夾欄位下方的狀態"""
        self._folder_status.setText(text)
        self._folder_status.setStyleSheet(f"color: {color};")

    def _use_catalog(self, sheet: DriveFile):
        """使用找到的試算表作為譜庫總表（按「儲存設定」才寫進偏好設定），在背景讀它的標題語言"""
        self._spreadsheet_id = sheet.id
        self._show_folder_status(t("catalog.settings.catalog_found", name=sheet.name), "#2ecc71")
        self._read_header_language()

    def _read_header_language(self):
        """在背景讀連到的譜庫總表的標題語言，寫進欄位語言選單"""
        spreadsheet_id = self._spreadsheet_id
        self._show_header_language(None, enabled=False)
        self._run(
            lambda: self._catalog_factory(spreadsheet_id).header_locale(), self._show_header_language,
            t("catalog.error.cannot_read"), t("catalog.action.read_header_language"), self._read_header_language,
            undo=lambda: None,
        )

    def _rewrite_headers(self, locale: str):
        """在背景把連到的譜庫總表各工作表的標題列改寫成 locale 的語言；寫不進去時選單改回原本的語言"""
        if locale == self._header_locale:
            return
        spreadsheet_id = self._spreadsheet_id
        before = self._header_locale
        self._show_header_language(locale, enabled=False)
        self._run(
            lambda: self._catalog_factory(spreadsheet_id).set_header_locale(locale),
            lambda _: self._show_header_language(locale),
            t("catalog.error.cannot_write"), t("catalog.action.write_header_language"),
            lambda: self._rewrite_headers(locale), undo=lambda: self._show_header_language(before),
        )

    def _show_header_language(self, locale: Optional[str], enabled: bool = True):
        """欄位語言選單選到 locale（None 時不選），記為譜庫總表現在的標題語言"""
        self._header_locale = locale
        self._header_language.setCurrentIndex(self._header_language.findData(locale) if locale else -1)
        self._header_language.setEnabled(enabled)

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
        pending = PendingCatalog(name.strip(), folder_id, get_locale())

        def created(spreadsheet_id: str):
            """建好了（按「儲存設定」才寫進偏好設定）"""
            self._spreadsheet_id = spreadsheet_id
            self._show_folder_status(t("catalog.settings.catalog_created"), "#2ecc71")
            self._show_header_language(pending.locale)

        def create():
            """建立譜庫總表（重試時再呼叫一次）"""
            self._show_folder_status(t("catalog.loading"), "gray")
            self._run(
                lambda: self._catalog_factory("").create_catalog(pending), created,
                t("catalog.error.cannot_write"), t("catalog.action.create_catalog"), create,
            )
        create()

    def _save_settings(self):
        """儲存設定，在背景重讀譜庫的社團設定；換了譜庫總表時先忘掉記住的發譜平台

        讀不到時記住的不變（換了試算表時就是沒有），寫進紀錄檔；讀完由社團設定服務通知主視窗。
        """
        spreadsheet_id = self._spreadsheet_id
        changed = spreadsheet_id != (self._prefs.get(CATALOG_SPREADSHEET_ID_KEY) or "")
        self._prefs.set(CATALOG_ROOT_FOLDER_ID_KEY, _extract_folder_id(self._folder_entry.text()))
        self._prefs.set(CATALOG_SPREADSHEET_ID_KEY, spreadsheet_id)
        if changed:
            self._club_settings.forget()
        self._prefs.save()
        if spreadsheet_id:
            # 對話框關閉後背景工作照常跑完（只是不再交回）；refresh 不丟出例外，讀不到時自己寫進紀錄檔
            club, connect = self._club_settings, self._catalog_factory
            self._tasks.run(lambda: club.refresh(lambda: connect(spreadsheet_id)), lambda _: None, lambda _: None)
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
        layout = QVBoxLayout(self)
        self._error_bar = CatalogErrorBar(log=log)
        self._error_bar.connect_remedies(sign_in)
        layout.addWidget(self._error_bar)
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
        self._load_my_drive()

    def done(self, result: int):
        """關閉瀏覽器：放棄還在跑的背景讀取"""
        self._tasks.abandon_all()
        super().done(result)

    def _load_my_drive(self):
        """在背景取我的雲端硬碟（根資料夾真正的 ID），放在最上層並展開，它本身也能選；讀不到時顯示提示列"""
        def found(root: DriveFile):
            """讀到了：展開時列出子資料夾"""
            node = _folder_node(t("catalog.settings.my_drive"), root.id)
            self._tree.addTopLevelItem(node)
            node.setExpanded(True)

        def failed(error: CatalogError):
            """讀不到"""
            self._error_bar.show_failure(
                t("catalog.error.cannot_read"), CatalogFailure.from_error(error, t("catalog.action.read")),
                self._load_my_drive,
            )
        self._error_bar.clear()
        self._tasks.run(lambda: self._connect().my_drive(), found, failed)

    def _load_children(self, parent_node: QTreeWidgetItem, folder_id: str):
        """在背景列出子資料夾並加入樹狀結構；讀不到時顯示提示列，下次展開時再讀"""
        def listed(folders: List[DriveFile]):
            """讀到了"""
            parent_node.takeChildren()
            for folder in folders:
                parent_node.addChild(_folder_node(folder.name, folder.id))
            if not folders:
                empty = QTreeWidgetItem([t("catalog.settings.no_folders")])
                empty.setFlags(Qt.NoItemFlags)
                parent_node.addChild(empty)

        def failed(error: CatalogError):
            """讀不到"""
            parent_node.setData(0, Qt.UserRole + 1, False)
            self._error_bar.show_failure(
                t("catalog.error.cannot_read"), CatalogFailure.from_error(error, t("catalog.action.read")),
                lambda: self._load_children(parent_node, folder_id),
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


def _folder_node(name: str, folder_id: str) -> QTreeWidgetItem:
    """資料夾瀏覽器裡一個還沒展開過的資料夾：底下先放「載入中」，展開時才列出子資料夾"""
    node = QTreeWidgetItem([name])
    node.setData(0, Qt.UserRole, folder_id)
    node.setData(0, Qt.UserRole + 1, False)
    node.addChild(QTreeWidgetItem([t("catalog.loading")]))
    return node
