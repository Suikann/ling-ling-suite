# -*- coding: utf-8 -*-
"""
Google 登入服務

以 OAuth2 桌面應用程式流程登入 Google：經本機回呼（services/local_callback.py）開瀏覽器、取得授權碼，
再向 Google 兌換權杖。用戶端檔（client_secret.json）隨程式內附，位置見 core.launch_env.bundled_client_secrets_path。
登入後只把續期權杖存進系統的安全儲存（見 services/secure_storage.py），重開泠靈時以它加上
用戶端的 client_id、client_secret 重建憑證。啟動時不讀安全儲存，第一次用到 Google 時才讀；
安全儲存沒解鎖（使用者取消了解鎖視窗）之後，看狀態（status）不再讀，等下一次要取得憑證（get_credentials）才再讀，
一次操作最多跳一次解鎖視窗。

對外的狀態（status）分得出：沒被記住的原因（這台電腦沒有安全儲存、沒有解鎖、寫不進去），
以及續期失敗的原因（Google 不認這個權杖、連不上 Google）。

使用範例：
    from services.google_auth_service import GoogleAuthService
    auth = GoogleAuthService(file_service, opener=ExternalOpener(), preferences=prefs)
    auth.remove_legacy_token()      # 啟動時刪掉舊版的明文權杖檔
    creds = auth.get_credentials()  # 已登入時直接取得，尚未登入為 None
    creds = auth.authenticate()     # 開瀏覽器登入（卡住呼叫的執行緒，要在背景執行緒呼叫）
    auth.cancel_login()             # 從別的執行緒取消等待中的登入
    auth.status().reason            # 沒被記住或續期失敗的原因
    auth.subscribe_not_saved(warn)  # 登入或換發的權杖寫不進安全儲存時通知
"""
import datetime
import json
import os
import secrets
import sys
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Callable, Dict, List, Optional

from core.catalog_constants import GOOGLE_SCOPES
from core.constants import GOOGLE_ACCOUNT_EMAIL_KEY, GOOGLE_KEYRING_SERVICE, LEGACY_TOKEN_FILE
from core.launch_env import bundled_client_secrets_path
from services.external_opener import ExternalOpener
from services.file_service import FileService
from services.local_callback import (
    DEFAULT_TIMEOUT_SECONDS, BrowserNotOpened, CallbackCancelled, CallbackError, CallbackTimedOut, LocalCallback,
)
from services.preferences_service import PreferencesService
from services.secure_storage import SecureStorage, StorageStatus

if TYPE_CHECKING:
    from google.oauth2.credentials import Credentials

# google 與 OAuth 程式庫在第一次用到 Google 時才匯入：只用分割與重新命名的人不必在啟動時等它們載入

# repo 根目錄（src/ 的上層）：從原始碼執行時內附用戶端檔放在這裡
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_DEFAULT_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
# 登入帳號的 email：drive 範圍已涵蓋，不必多要 openid 或 email 範圍
_DRIVE_ABOUT_URL = "https://www.googleapis.com/drive/v3/about?fields=user(emailAddress)"


def _bundled_client_secrets() -> str:
    """這次執行的內附用戶端檔位置"""
    return bundled_client_secrets_path(getattr(sys, "frozen", False), getattr(sys, "_MEIPASS", ""), _REPO_ROOT)


class LoginFailure(Enum):
    """登入沒有完成的原因"""

    CLIENT_MISSING = "client_missing"           # 這個版本缺少 Google 用戶端設定
    BROWSER_NOT_OPENED = "browser_not_opened"   # 瀏覽器開不了
    TIMED_OUT = "timed_out"                     # 等瀏覽器回來超過上限
    CANCELLED = "cancelled"                     # 使用者取消了等待
    DENIED = "denied"                           # 使用者在同意畫面拒絕授權
    SCOPES_MISSING = "scopes_missing"           # 同意畫面沒勾選全部權限
    FAILED = "failed"                           # 兌換權杖失敗、回呼不對或連不上 Google


# 本機回呼沒等到回呼的原因對應到登入沒有完成的原因
_CALLBACK_FAILURES = {
    CallbackTimedOut: LoginFailure.TIMED_OUT,
    CallbackCancelled: LoginFailure.CANCELLED,
    BrowserNotOpened: LoginFailure.BROWSER_NOT_OPENED,
}
# Google 回呼的 error：使用者在同意畫面拒絕
_ACCESS_DENIED = "access_denied"


class LoginFailed(Exception):
    """登入沒有完成"""

    def __init__(self, failure: LoginFailure, detail: str = ""):
        super().__init__(detail or failure.value)
        self.failure = failure
        self.detail = detail


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
        opener: Optional[ExternalOpener] = None,
        preferences: Optional[PreferencesService] = None,
        client_secrets_path: Optional[str] = None,
        legacy_token_path: str = LEGACY_TOKEN_FILE,
        login_timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        """建立登入服務；不碰安全儲存

        Args:
            file_service: 檔案服務
            storage: 存續期權杖的安全儲存；省略時為系統的安全儲存
            request: 送出兌換、續期與查帳號請求的 google-auth transport；省略時用 requests
            opener: 開瀏覽器用的外部開啟服務；省略時依目前的平台建立
            preferences: 記住登入帳號 email 的偏好設定；省略時只記在這次執行
            client_secrets_path: 用戶端檔位置；省略時為內附的 client_secret.json
            legacy_token_path: 舊版的明文權杖檔，啟動時刪除
            login_timeout: 登入時等瀏覽器回來的上限（秒）
        """
        self.file_service = file_service or FileService()
        self._storage = storage or SecureStorage(GOOGLE_KEYRING_SERVICE)
        self._request = request
        self._opener = opener or ExternalOpener()
        self._preferences = preferences
        self._client_secrets_path = client_secrets_path or _bundled_client_secrets()
        self._legacy_token_path = legacy_token_path
        self._login_timeout = login_timeout
        self._callback: Optional[LocalCallback] = None
        self._email: Optional[str] = None
        self._credentials: Optional["Credentials"] = None
        self._storage_reason: Optional[LoginReason] = None
        self._refresh_reason: Optional[LoginReason] = None
        self._not_saved_listeners: List[Callable[[], None]] = []
        # 登出後到下次登入前不再讀安全儲存：那份刪不掉時，這次執行也不會又被當成已登入
        self._signed_out = False

    @property
    def account_email(self) -> Optional[str]:
        """上次登入的 Google 帳號 email；沒登入過或查不到時為 None"""
        if self._preferences is not None:
            return self._preferences.get(GOOGLE_ACCOUNT_EMAIL_KEY) or None
        return self._email

    def subscribe_not_saved(self, listener: Callable[[], None]) -> None:
        """登入成功或續期換了新的續期權杖，但有安全儲存卻沒寫進去時呼叫 listener（下次啟動需要重新登入）"""
        self._not_saved_listeners.append(listener)

    def status(self) -> LoginStatus:
        """目前是否已登入與登入狀態旁要說明的原因

        記憶體裡沒有憑證時照 get_credentials 讀安全儲存，但上次讀到沒解鎖時直接回報沒登入：
        看狀態不會再跳解鎖視窗，等下一次 get_credentials 才再讀。
        """
        if self._credentials is None and self._storage_reason is LoginReason.STORAGE_LOCKED:
            signed_in = False
        else:
            signed_in = self.get_credentials() is not None
        return LoginStatus(signed_in, self._refresh_reason or self._storage_reason)

    def get_credentials(self) -> Optional["Credentials"]:
        """取得有效的憑證（不開瀏覽器）

        記憶體裡沒有憑證時，以安全儲存裡的續期權杖重建（每次呼叫都重讀，上次沒解鎖時會再跳解鎖視窗）；
        憑證不是有效的就續期。

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

        經本機回呼等瀏覽器帶回授權碼，再向 Google 兌換權杖；等待期間卡住呼叫的執行緒。

        Returns:
            Google 憑證

        Raises:
            LoginFailed: 登入沒有完成，原因見 failure
        """
        callback = LocalCallback(self._opener, self._login_timeout)
        self._callback = callback
        client = self._client_config()
        if client is None:
            raise LoginFailed(LoginFailure.CLIENT_MISSING)
        from oauthlib.oauth2 import WebApplicationClient
        oauth = WebApplicationClient(client["client_id"])
        verifier = oauth.create_code_verifier(64)
        state = secrets.token_urlsafe(16)
        redirect: Dict[str, str] = {}

        # prompt=consent：重新登入時 Google 才會再給續期權杖，也讓漏勾的權限重新出現
        extra = {"access_type": "offline", "prompt": "consent"}
        if self.account_email:
            extra["login_hint"] = self.account_email

        def authorization_url(redirect_uri: str) -> str:
            redirect["uri"] = redirect_uri
            return oauth.prepare_request_uri(
                client["auth_uri"], redirect_uri=redirect_uri, scope=GOOGLE_SCOPES, state=state,
                code_challenge=oauth.create_code_challenge(verifier, "S256"), code_challenge_method="S256",
                **extra,
            )
        params = self._receive(callback, authorization_url)
        if params.get("state") != state:
            raise LoginFailed(LoginFailure.FAILED, "state mismatch")
        if params.get("error") == _ACCESS_DENIED:
            raise LoginFailed(LoginFailure.DENIED)
        if "code" not in params:
            raise LoginFailed(LoginFailure.FAILED, params.get("error", ""))
        token = self._exchange(oauth, client, params["code"], redirect["uri"], verifier)
        # 未經驗證的正式版讓使用者逐項勾選權限；權杖端點沒寫 scope 時照 RFC 6749 等於全給
        granted = token.get("scope", " ".join(GOOGLE_SCOPES)).split()
        if not set(GOOGLE_SCOPES) <= set(granted):
            raise LoginFailed(LoginFailure.SCOPES_MISSING)
        credentials = self._credentials_from(token, client)
        self._credentials = credentials
        self._refresh_reason = None
        self._signed_out = False
        self._save(credentials.refresh_token)
        self._remember_email(self._fetch_email(credentials.token))
        return credentials

    def cancel_login(self) -> None:
        """取消等待中的登入（可從別的執行緒呼叫）；authenticate 隨即以 CANCELLED 結束"""
        if self._callback is not None:
            self._callback.cancel()

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

    @staticmethod
    def _receive(callback: LocalCallback, authorization_url: Callable[[str], str]) -> Dict[str, str]:
        """經本機回呼等瀏覽器帶回 query 參數；沒等到時以對應的原因拋出 LoginFailed"""
        try:
            return callback.receive(authorization_url)
        except CallbackError as e:
            raise LoginFailed(_CALLBACK_FAILURES[type(e)]) from e
        except OSError as e:
            raise LoginFailed(LoginFailure.FAILED, str(e)) from e

    def _exchange(self, oauth, client: dict, code: str, redirect_uri: str, verifier: str) -> dict:
        """向 Google 兌換授權碼，回傳權杖端點的回應內容"""
        body = oauth.prepare_request_body(
            code=code, redirect_uri=redirect_uri, code_verifier=verifier, client_secret=client["client_secret"],
        )
        try:
            response = self._transport()(
                url=client["token_uri"], method="POST", body=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            token = json.loads(response.data)
        except Exception as e:
            raise LoginFailed(LoginFailure.FAILED, str(e)) from e
        if response.status != 200 or "access_token" not in token:
            raise LoginFailed(LoginFailure.FAILED, str(token.get("error", response.status)))
        return token

    def _fetch_email(self, access_token: str) -> Optional[str]:
        """以 Drive 的 about 查登入帳號的 email；查不到時為 None"""
        try:
            response = self._transport()(
                url=_DRIVE_ABOUT_URL, method="GET", headers={"Authorization": f"Bearer {access_token}"},
            )
            if response.status != 200:
                return None
            return json.loads(response.data)["user"]["emailAddress"]
        except Exception:
            return None

    def _remember_email(self, email: Optional[str]) -> None:
        """記下登入帳號的 email；寫不進偏好設定檔時只記在這次執行，不另外告知"""
        if self._preferences is None:
            self._email = email
            return
        self._preferences.set(GOOGLE_ACCOUNT_EMAIL_KEY, email or "")
        try:
            self._preferences.save()
        except OSError:
            pass

    @staticmethod
    def _credentials_from(token: dict, client: dict) -> "Credentials":
        """以權杖端點的回應建立憑證"""
        from google.oauth2.credentials import Credentials
        # google-auth 以不帶時區的 UTC 時間比對到期
        now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        return Credentials(
            token=token["access_token"], refresh_token=token.get("refresh_token"), token_uri=client["token_uri"],
            client_id=client["client_id"], client_secret=client["client_secret"], scopes=GOOGLE_SCOPES,
            expiry=now + datetime.timedelta(seconds=int(token.get("expires_in", 0))),
        )

    def _client_config(self) -> Optional[dict]:
        """內附用戶端檔裡的 client_id、client_secret、auth_uri 與 token_uri；找不到或讀不出來時為 None"""
        if not self.file_service.file_exists(self._client_secrets_path):
            return None
        try:
            data = self.file_service.read_json(self._client_secrets_path)
            client = data.get("installed") or data.get("web")
            config = {key: client[key] for key in ("client_id", "client_secret", "token_uri")}
            config["auth_uri"] = client.get("auth_uri") or _DEFAULT_AUTH_URI
            return config
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def _transport(self):
        """送出續期請求的 transport"""
        if self._request is None:
            from google.auth.transport.requests import Request
            self._request = Request()
        return self._request
