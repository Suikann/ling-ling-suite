# -*- coding: utf-8 -*-
"""
HuaXia 登入服務

幹部以自己的 Discord 帳號從泠靈登入 HuaXia（PKCE）：經本機回呼（services/local_callback.py）開瀏覽器到
HuaXia 的授權網址，回呼帶回授權碼後經 HuaXia 用戶端（services/huaxia_client.py）兌換。登入後立刻查
is_officer：不是幹部就讀自己的帳號狀態，分成四種（這次登入才建立的帳號、早就在等核准、已停用、
已啟用但不是幹部），以 scope=local 登出、不存續期權杖。是幹部才把續期權杖存進安全儲存（HUAXIA_KEYRING_SERVICE），
重開泠靈時以它續期，不必再登入。

續期權杖只能用一次（重用舊的會撤銷整個 session）：同一時間只有一個續期在進行，換到新的立刻寫回安全儲存，
寫不進去時通知訂閱者（下次啟動需要重新登入）。access token 的有效期讀回應的 expires_in。
續期時 HuaXia 回報續期權杖已不存在（例如在 HuaXia 網頁登出全部），就忘掉這次登入、刪掉安全儲存那份，
以 SESSION_GONE 丟出並記在狀態裡。

登出以 scope=local 請 HuaXia 作廢這台的 session，再刪安全儲存那份；連不上或請求失敗時只刪本機那份，不另外告知。
登入、續期、重查幹部身分與登出都會連網，要在背景執行緒呼叫；看狀態（status）不連網。

使用範例：
    from services.huaxia_auth_service import HuaXiaAuthService
    auth = HuaXiaAuthService(opener=ExternalOpener())
    name = auth.authenticate()          # 開瀏覽器登入；不是幹部時丟出 HuaXiaLoginFailed（附 NotOfficer）
    token = auth.access_token()         # 需要時續期
    not_officer = auth.recheck_officer()  # 發譜前重查；不是幹部時已登出，回傳分類
    auth.logout()
"""
import base64
import datetime
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, List, Optional

from core.constants import HUAXIA_KEYRING_SERVICE
from services.external_opener import ExternalOpener
from services.huaxia_client import AuthTokens, HuaXiaClient, HuaXiaError, HuaXiaErrorKind
from services.local_callback import (
    DEFAULT_TIMEOUT_SECONDS, BrowserNotOpened, CallbackCancelled, CallbackError, CallbackTimedOut, LocalCallback,
)
from services.secure_storage import SecureStorage, StorageStatus

# Supabase 帶回本機回呼的 error：使用者在 Discord 拒絕授權
_ACCESS_DENIED = "access_denied"
# access token 在到期前這麼多秒就當成過期，續期之後才送的請求不會剛好碰上到期
_EXPIRY_MARGIN_SECONDS = 60
# 判定「這次登入才建立的帳號」時，容許 HuaXia 與這台電腦的時鐘差這麼多
_CLOCK_SKEW = datetime.timedelta(minutes=1)


class HuaXiaLoginFailure(Enum):
    """HuaXia 登入沒有完成的原因"""

    BROWSER_NOT_OPENED = "browser_not_opened"  # 瀏覽器開不了
    TIMED_OUT = "timed_out"                    # 等瀏覽器回來超過上限（可能被導回 HuaXia 首頁）
    CANCELLED = "cancelled"                    # 使用者取消了等待
    DENIED = "denied"                          # 使用者在 Discord 拒絕授權
    NOT_OFFICER = "not_officer"                # 登入了，但不是幹部（分類見 not_officer）
    FAILED = "failed"                          # 兌換失敗、連不上 HuaXia 或回呼不對


# 本機回呼沒等到回呼的原因對應到登入沒有完成的原因
_CALLBACK_FAILURES = {
    CallbackTimedOut: HuaXiaLoginFailure.TIMED_OUT,
    CallbackCancelled: HuaXiaLoginFailure.CANCELLED,
    BrowserNotOpened: HuaXiaLoginFailure.BROWSER_NOT_OPENED,
}


class AccountStanding(Enum):
    """不是幹部時，帳號在 HuaXia 的狀況"""

    JUST_CREATED = "just_created"            # 待核准，且是這次登入才建立的（多出一個帳號，或新社員）
    AWAITING_APPROVAL = "awaiting_approval"  # 早就在等核准
    DISABLED = "disabled"                    # 已停用
    NOT_OFFICER = "not_officer"              # 已啟用，但沒有 admin 或 officer


@dataclass(frozen=True)
class NotOfficer:
    """不是幹部：帳號的狀況與目前登入的 Discord 帳號名稱"""

    standing: AccountStanding
    discord_name: str


class HuaXiaLoginFailed(Exception):
    """HuaXia 登入沒有完成"""

    def __init__(self, failure: HuaXiaLoginFailure, detail: str = "", not_officer: Optional[NotOfficer] = None):
        """
        Args:
            failure: 登入沒有完成的原因
            detail: 補充說明（例如錯誤訊息），沒有時為空字串
            not_officer: 原因是不是幹部時的分類
        """
        super().__init__(detail or failure.value)
        self.failure = failure
        self.detail = detail
        self.not_officer = not_officer


class HuaXiaReason(Enum):
    """HuaXia 登入狀態旁要說明的原因"""

    NO_SECURE_STORAGE = "no_secure_storage"  # 這台電腦沒有安全儲存，登入不會被記住
    STORAGE_LOCKED = "storage_locked"        # 安全儲存沒有解鎖
    NOT_SAVED = "not_saved"                  # 寫不進安全儲存，下次啟動需要重新登入
    SESSION_GONE = "session_gone"            # 登入已失效（可能在別處登出了）


# 安全儲存讀寫的結果對應到登入狀態旁的原因；AVAILABLE 沒有原因
_STORAGE_REASONS = {
    StorageStatus.AVAILABLE: None,
    StorageStatus.NO_STORAGE: HuaXiaReason.NO_SECURE_STORAGE,
    StorageStatus.LOCKED: HuaXiaReason.STORAGE_LOCKED,
    StorageStatus.WRITE_FAILED: HuaXiaReason.NOT_SAVED,
}


@dataclass(frozen=True)
class HuaXiaStatus:
    """目前的 HuaXia 登入狀態"""

    signed_in: bool
    discord_name: str = ""          # 這次執行登入或續期後才知道；重開泠靈後還沒續期時為空字串
    reason: Optional[HuaXiaReason] = None


def _code_challenge(verifier: str) -> str:
    """PKCE 的 S256 challenge：BASE64URL(SHA256(verifier))，不補 ="""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class HuaXiaAuthService:
    """HuaXia 登入：登入與幹部檢查、取得 access token（續期與輪替）、重查幹部身分、登出、目前狀態"""

    def __init__(
        self,
        client: Optional[HuaXiaClient] = None,
        storage: Optional[SecureStorage] = None,
        opener: Optional[ExternalOpener] = None,
        login_timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ):
        """建立登入服務；不碰安全儲存、不連網

        Args:
            client: HuaXia 用戶端；省略時接真的 HuaXia（測試傳入接假 HuaXia 的）
            storage: 存續期權杖的安全儲存；省略時為系統的安全儲存
            opener: 開瀏覽器用的外部開啟服務；省略時依目前的平台建立
            login_timeout: 登入時等瀏覽器回來的上限（秒）
        """
        self._client = client or HuaXiaClient()
        self._storage = storage or SecureStorage(HUAXIA_KEYRING_SERVICE)
        self._opener = opener or ExternalOpener()
        self._login_timeout = login_timeout
        self._callback: Optional[LocalCallback] = None
        self._not_saved_listeners: List[Callable[[], None]] = []
        # 以下登入狀態只在持有 _lock 時讀寫：同一時間只有一個續期在進行
        self._lock = threading.Lock()
        self._refresh_token: Optional[str] = None
        self._access_token: Optional[str] = None
        self._expires_at = 0.0
        self._user_id = ""
        self._discord_name = ""
        self._reason: Optional[HuaXiaReason] = None
        # 登出後到下次登入前不再讀安全儲存：那份刪不掉時，這次執行也不會又被當成已登入
        self._signed_out = False

    def subscribe_not_saved(self, listener: Callable[[], None]) -> None:
        """登入成功或續期換了新的續期權杖，但有安全儲存卻沒寫進去時呼叫 listener（在背景執行緒呼叫）"""
        self._not_saved_listeners.append(listener)

    def status(self) -> HuaXiaStatus:
        """目前是否已登入、登入的 Discord 帳號與狀態旁要說明的原因；不連網

        記憶體裡沒有登入時讀安全儲存，但上次讀到沒解鎖時直接回報沒登入，等下一次 access_token 才再讀。
        """
        with self._lock:
            if self._refresh_token is None and not self._signed_out and self._reason is not HuaXiaReason.STORAGE_LOCKED:
                self._restore()
            return HuaXiaStatus(self._refresh_token is not None, self._discord_name, self._reason)

    def authenticate(self) -> str:
        """開瀏覽器以 Discord 登入 HuaXia，確認是幹部後把續期權杖存進安全儲存；存不進去仍算登入成功

        等待期間卡住呼叫的執行緒。

        Returns:
            登入的 Discord 帳號名稱

        Raises:
            HuaXiaLoginFailed: 登入沒有完成；不是幹部時 failure 為 NOT_OFFICER、附上分類，且已登出、沒有保存
        """
        started = datetime.datetime.now(datetime.timezone.utc)
        callback = LocalCallback(self._opener, self._login_timeout)
        self._callback = callback
        verifier = secrets.token_urlsafe(48)
        challenge = _code_challenge(verifier)
        try:
            params = callback.receive(lambda redirect_uri: self._client.authorization_url(redirect_uri, challenge))
        except CallbackError as e:
            raise HuaXiaLoginFailed(_CALLBACK_FAILURES[type(e)]) from e
        except OSError as e:
            raise HuaXiaLoginFailed(HuaXiaLoginFailure.FAILED, str(e)) from e
        if params.get("error") == _ACCESS_DENIED:
            raise HuaXiaLoginFailed(HuaXiaLoginFailure.DENIED)
        if "code" not in params:
            raise HuaXiaLoginFailed(HuaXiaLoginFailure.FAILED, params.get("error_description") or params.get("error", ""))
        try:
            tokens = self._client.exchange_code(params["code"], verifier)
        except HuaXiaError as e:
            raise HuaXiaLoginFailed(HuaXiaLoginFailure.FAILED, str(e)) from e
        try:
            not_officer = self._not_officer(tokens.access_token, tokens.user_id, tokens.discord_name, started)
        except HuaXiaError as e:
            self._end_session(tokens.access_token)
            raise HuaXiaLoginFailed(HuaXiaLoginFailure.FAILED, str(e)) from e
        if not_officer is not None:
            self._end_session(tokens.access_token)
            raise HuaXiaLoginFailed(HuaXiaLoginFailure.NOT_OFFICER, not_officer=not_officer)
        with self._lock:
            self._signed_out = False
            self._reason = None
            self._adopt(tokens)
        return tokens.discord_name

    def cancel_login(self) -> None:
        """取消等待中的登入（可從別的執行緒呼叫）；authenticate 隨即以 CANCELLED 結束"""
        if self._callback is not None:
            self._callback.cancel()

    def access_token(self) -> str:
        """取得有效的 access token；快到期或還沒有時以續期權杖續期，換到的新續期權杖立刻寫回安全儲存

        同時從多個執行緒呼叫時只發出一次續期，其餘等它結束後沿用。

        Raises:
            HuaXiaError: 沒有登入（SIGNED_OUT）、登入已失效（SESSION_GONE，已忘掉這次登入）、連不上 HuaXia
        """
        with self._lock:
            if self._access_token is not None and time.monotonic() < self._expires_at:
                return self._access_token
            if self._refresh_token is None and not self._signed_out:
                self._restore()
            if self._refresh_token is None:
                raise HuaXiaError(HuaXiaErrorKind.SIGNED_OUT)
            try:
                tokens = self._client.refresh(self._refresh_token)
            except HuaXiaError as e:
                if e.kind is HuaXiaErrorKind.SESSION_GONE:
                    # 安全儲存那份已作廢：刪不掉時這次執行也不再讀它
                    self._forget()
                    self._signed_out = True
                    self._reason = HuaXiaReason.SESSION_GONE
                    self._storage.delete()
                raise
            self._adopt(tokens)
            return tokens.access_token

    def recheck_officer(self) -> Optional[NotOfficer]:
        """重查幹部身分（每次開始發譜時）；不是幹部就登出

        Returns:
            是幹部時為 None；不是時為分類與 Discord 帳號名稱（已登出）

        Raises:
            HuaXiaError: 沒有登入、登入已失效或連不上 HuaXia
        """
        token = self.access_token()
        with self._lock:
            user_id, discord_name = self._user_id, self._discord_name
        not_officer = self._not_officer(token, user_id, discord_name, None)
        if not_officer is not None:
            self.logout()
        return not_officer

    def logout(self) -> None:
        """登出：以 scope=local 請 HuaXia 作廢這台的 session，再刪安全儲存那份；請求失敗時只刪本機那份，不拋出"""
        try:
            self._end_session(self.access_token())
        except HuaXiaError:
            pass
        with self._lock:
            self._forget()
            self._signed_out = True
            if self._reason in (HuaXiaReason.NOT_SAVED, HuaXiaReason.SESSION_GONE):
                self._reason = None
        self._storage.delete()

    def _not_officer(
        self, access_token: str, user_id: str, discord_name: str, login_started: Optional[datetime.datetime],
    ) -> Optional[NotOfficer]:
        """查 is_officer；不是幹部時讀自己的帳號狀態分類

        帳號狀態為啟用時，is_officer 為 false 就表示沒有 admin 或 officer，不必另讀身分組。

        Args:
            access_token: 使用者的 access token
            user_id: 使用者 ID
            discord_name: 登入的 Discord 帳號名稱
            login_started: 這次登入開始的時間；重查時為 None（待核准一律算早就在等）

        Returns:
            是幹部時為 None，否則為分類
        """
        if self._client.rpc("is_officer", access_token) is True:
            return None
        rows = self._client.select("profiles", access_token, "status,created_at", id=user_id)
        if not rows:
            raise HuaXiaError(HuaXiaErrorKind.REJECTED, "profile not found")
        status = rows[0].get("status")
        if status == "inactive":
            standing = AccountStanding.DISABLED
        elif status == "pending":
            standing = AccountStanding.AWAITING_APPROVAL
            if login_started is not None and _created_since(rows[0].get("created_at"), login_started - _CLOCK_SKEW):
                standing = AccountStanding.JUST_CREATED
        else:
            standing = AccountStanding.NOT_OFFICER
        return NotOfficer(standing, discord_name)

    def _end_session(self, access_token: str) -> None:
        """以 scope=local 結束 HuaXia 上的這個 session；連不上或請求失敗時略過"""
        try:
            self._client.logout(access_token)
        except HuaXiaError:
            pass

    def _adopt(self, tokens: AuthTokens) -> None:
        """記下兌換或續期的結果，新的續期權杖寫回安全儲存（持有 _lock 時呼叫）"""
        self._access_token = tokens.access_token
        self._expires_at = time.monotonic() + tokens.expires_in - _EXPIRY_MARGIN_SECONDS
        self._user_id = tokens.user_id
        self._discord_name = tokens.discord_name
        if tokens.refresh_token != self._refresh_token:
            self._refresh_token = tokens.refresh_token
            self._save(tokens.refresh_token)

    def _forget(self) -> None:
        """忘掉記憶體裡的登入（持有 _lock 時呼叫）"""
        self._refresh_token = None
        self._access_token = None
        self._expires_at = 0.0
        self._user_id = ""
        self._discord_name = ""

    def _restore(self) -> None:
        """讀安全儲存裡的續期權杖，記下讀不出來的原因（持有 _lock 時呼叫）"""
        refresh_token, status = self._storage.read()
        self._reason = _STORAGE_REASONS[status]
        self._refresh_token = refresh_token or None

    def _save(self, refresh_token: str) -> None:
        """把續期權杖寫進安全儲存，記下沒存成的原因；有安全儲存卻沒寫進去時通知訂閱者"""
        status = self._storage.write(refresh_token)
        self._reason = _STORAGE_REASONS[status]
        if status in (StorageStatus.LOCKED, StorageStatus.WRITE_FAILED):
            for listener in self._not_saved_listeners:
                listener()


def _created_since(created_at: Optional[str], since: datetime.datetime) -> bool:
    """HuaXia 的帳號建立時間（ISO 8601）是否不早於 since；讀不出來時當成不是"""
    try:
        created = datetime.datetime.fromisoformat(created_at)
    except (TypeError, ValueError):
        return False
    if created.tzinfo is None:
        created = created.replace(tzinfo=datetime.timezone.utc)
    return created >= since
