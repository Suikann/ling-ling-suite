# -*- coding: utf-8 -*-
"""
Google 登入服務

以 OAuth2 桌面應用程式流程登入 Google。用戶端檔（client_secret.json）隨程式打包；
登入後只把續期權杖存進系統的安全儲存（見 services/secure_storage.py），重開泠靈時以它加上
用戶端的 client_id、client_secret 重建憑證。啟動時不讀安全儲存，第一次用到 Google 時才讀。

對外的狀態（status）分得出：沒被記住的原因（這台電腦沒有安全儲存、沒有解鎖、寫不進去），
以及續期失敗的原因（Google 不認這個權杖、連不上 Google）。

使用範例：
    from services.google_auth_service import GoogleAuthService
    auth = GoogleAuthService(file_service)
    auth.remove_legacy_token()      # 啟動時刪掉舊版的明文權杖檔
    creds = auth.get_credentials()  # 已登入時直接取得，尚未登入為 None
    creds = auth.authenticate()     # 開瀏覽器登入
    auth.status().reason            # 沒被記住或續期失敗的原因
    auth.subscribe_not_saved(warn)  # 登入或換發的權杖寫不進安全儲存時通知
"""
import os
import sys
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable, List, Optional

from core.catalog_constants import CLIENT_SECRETS_FILE, GOOGLE_SCOPES
from core.constants import GOOGLE_KEYRING_SERVICE, LEGACY_TOKEN_FILE
from services.file_service import FileService
from services.secure_storage import SecureStorage, StorageStatus

if TYPE_CHECKING:
    from google.oauth2.credentials import Credentials

# google 與 OAuth 程式庫在第一次用到 Google 時才匯入：只用分割與重新命名的人不必在啟動時等它們載入


def _get_app_dir() -> str:
    """取得應用程式根目錄（支援 PyInstaller 打包環境）

    開發環境：回傳 src/ 的上層（專案根目錄）
    打包環境：回傳 exe 所在目錄
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(src_dir)


def _run_browser_login(client_secrets_path: str, scopes: List[str]) -> "Credentials":
    """開瀏覽器走 OAuth2 桌面應用程式流程，回傳拿到的憑證"""
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(client_secrets_path, scopes)
    return flow.run_local_server(port=0)


class LoginReason(Enum):
    """登入狀態旁要說明的原因"""

    NO_SECURE_STORAGE = "no_secure_storage"  # 這台電腦沒有安全儲存，登入不會被記住
    STORAGE_LOCKED = "storage_locked"        # 安全儲存沒有解鎖
    NOT_SAVED = "not_saved"                  # 寫不進安全儲存，下次啟動需要重新登入
    TOKEN_REJECTED = "token_rejected"        # Google 不認存著的權杖（換了用戶端、撤銷授權、太久沒用）
    UNREACHABLE = "unreachable"              # 連不上 Google


# 安全儲存讀寫的結果對應到登入狀態旁的原因；AVAILABLE 沒有原因
_STORAGE_REASONS = {
    StorageStatus.AVAILABLE: None,
    StorageStatus.NO_STORAGE: LoginReason.NO_SECURE_STORAGE,
    StorageStatus.LOCKED: LoginReason.STORAGE_LOCKED,
    StorageStatus.WRITE_FAILED: LoginReason.NOT_SAVED,
}


@dataclass(frozen=True)
class LoginStatus:
    """目前的登入狀態"""

    signed_in: bool
    reason: Optional[LoginReason] = None


class GoogleAuthService:
    """Google 登入：登入、取得憑證、登出、目前狀態與原因、啟動清理"""

    def __init__(
        self,
        file_service: Optional[FileService] = None,
        storage: Optional[SecureStorage] = None,
        request=None,
        browser_login: Optional[Callable[[str, List[str]], "Credentials"]] = None,
        client_secrets_path: Optional[str] = None,
        legacy_token_path: str = LEGACY_TOKEN_FILE,
    ):
        """建立登入服務；不碰安全儲存

        Args:
            file_service: 檔案服務
            storage: 存續期權杖的安全儲存；省略時為系統的安全儲存
            request: 送出續期請求的 google-auth transport；省略時用 requests
            browser_login: 開瀏覽器登入、回傳憑證的函式，參數為用戶端檔路徑與範圍
            client_secrets_path: 用戶端檔位置；省略時為應用程式根目錄的 client_secret.json
            legacy_token_path: 舊版的明文權杖檔，啟動時刪除
        """
        self.file_service = file_service or FileService()
        self._storage = storage or SecureStorage(GOOGLE_KEYRING_SERVICE)
        self._request = request
        self._browser_login = browser_login or _run_browser_login
        self._client_secrets_path = client_secrets_path or os.path.join(_get_app_dir(), CLIENT_SECRETS_FILE)
        self._legacy_token_path = legacy_token_path
        self._credentials: Optional["Credentials"] = None
        self._storage_reason: Optional[LoginReason] = None
        self._refresh_reason: Optional[LoginReason] = None
        self._not_saved_listeners: List[Callable[[], None]] = []
        # 登出後到下次登入前不再讀安全儲存：那份刪不掉時，這次執行也不會又被當成已登入
        self._signed_out = False

    @property
    def has_client_secrets(self) -> bool:
        """是否有 OAuth 用戶端憑證檔"""
        return os.path.isfile(self._client_secrets_path)

    @property
    def client_secrets_path(self) -> str:
        """用戶端憑證檔路徑"""
        return self._client_secrets_path

    def subscribe_not_saved(self, listener: Callable[[], None]) -> None:
        """登入成功或續期換了新的續期權杖，但有安全儲存卻沒寫進去時呼叫 listener（下次啟動需要重新登入）"""
        self._not_saved_listeners.append(listener)

    def status(self) -> LoginStatus:
        """目前是否已登入與登入狀態旁要說明的原因；還沒讀過安全儲存時會讀"""
        signed_in = self.get_credentials() is not None
        return LoginStatus(signed_in, self._refresh_reason or self._storage_reason)

    def get_credentials(self) -> Optional["Credentials"]:
        """取得有效的憑證（不開瀏覽器）

        記憶體裡沒有憑證時，以安全儲存裡的續期權杖重建；憑證不是有效的就續期。

        Returns:
            Google 憑證；尚未登入、讀不出安全儲存或續期失敗時為 None（原因見 status）
        """
        if self._credentials is None and (self._signed_out or not self._restore()):
            return None
        if self._credentials.valid:
            return self._credentials
        return self._credentials if self._refresh() else None

    def authenticate(self) -> "Credentials":
        """開瀏覽器登入 Google，把續期權杖存進安全儲存；存不進去仍算這次登入成功

        Returns:
            Google 憑證

        Raises:
            FileNotFoundError: 找不到用戶端憑證檔
            RuntimeError: 登入流程失敗或被取消
        """
        if not self.has_client_secrets:
            raise FileNotFoundError(self._client_secrets_path)
        try:
            credentials = self._browser_login(self._client_secrets_path, GOOGLE_SCOPES)
        except Exception as e:
            raise RuntimeError(f"OAuth 登入流程失敗：{e}") from e
        if not credentials:
            raise RuntimeError("使用者取消了登入")
        self._credentials = credentials
        self._refresh_reason = None
        self._signed_out = False
        self._save(credentials.refresh_token)
        return credentials

    def logout(self) -> None:
        """登出：只刪這台電腦安全儲存裡的那份，不請 Google 撤銷；刪不掉也不拋出"""
        self._credentials = None
        self._refresh_reason = None
        self._signed_out = True
        if self._storage_reason is LoginReason.NOT_SAVED:
            self._storage_reason = None
        self._storage.delete()

    def remove_legacy_token(self) -> None:
        """刪掉舊版留在使用者資料目錄的明文權杖檔；刪不掉就等下次啟動，不拋出、不讀它"""
        try:
            self.file_service.remove_file(self._legacy_token_path)
        except OSError:
            pass

    def _restore(self) -> bool:
        """以安全儲存裡的續期權杖重建憑證，回傳是否重建了"""
        refresh_token, status = self._storage.read()
        self._storage_reason = _STORAGE_REASONS[status]
        self._refresh_reason = None
        if not refresh_token:
            return False
        client = self._client_config()
        if client is None:
            return False
        from google.oauth2.credentials import Credentials
        self._credentials = Credentials(
            token=None, refresh_token=refresh_token, token_uri=client["token_uri"],
            client_id=client["client_id"], client_secret=client["client_secret"], scopes=GOOGLE_SCOPES,
        )
        return True

    def _refresh(self) -> bool:
        """續期記憶體裡的憑證，回傳是否成功；換了新的續期權杖時寫回安全儲存"""
        from google.auth.exceptions import RefreshError
        previous = self._credentials.refresh_token
        try:
            self._credentials.refresh(self._transport())
        except RefreshError as e:
            self._refresh_reason = LoginReason.UNREACHABLE if e.retryable else LoginReason.TOKEN_REJECTED
            self._credentials = None
            return False
        except Exception:
            self._refresh_reason = LoginReason.UNREACHABLE
            self._credentials = None
            return False
        self._refresh_reason = None
        if self._credentials.refresh_token != previous:
            self._save(self._credentials.refresh_token)
        return True

    def _save(self, refresh_token: Optional[str]) -> None:
        """把續期權杖寫進安全儲存，記下沒存成的原因；有安全儲存卻沒寫進去時通知訂閱者"""
        status = self._storage.write(refresh_token) if refresh_token else StorageStatus.WRITE_FAILED
        self._storage_reason = _STORAGE_REASONS[status]
        if status in (StorageStatus.LOCKED, StorageStatus.WRITE_FAILED):
            for listener in self._not_saved_listeners:
                listener()

    def _client_config(self) -> Optional[dict]:
        """用戶端檔裡的 client_id、client_secret 與 token_uri；讀不出來時為 None"""
        try:
            data = self.file_service.read_json(self._client_secrets_path)
            client = data.get("installed") or data.get("web")
            return {key: client[key] for key in ("client_id", "client_secret", "token_uri")}
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def _transport(self):
        """送出續期請求的 transport"""
        if self._request is None:
            from google.auth.transport.requests import Request
            self._request = Request()
        return self._request
