# -*- coding: utf-8 -*-
"""
HuaXia 用戶端

全程式唯一碰 HuaXia 的地方：以 requests 直接打 HuaXia 的 Supabase REST API（Auth、PostgREST、Storage），
不用 supabase-py（ADR 0003）。HuaXia 的網址與 publishable key 內附在這裡（publishable key 本來就設計成公開）。
每個請求都帶 apikey；以使用者身分的請求另帶 Authorization: Bearer <access token>。

用戶端不記任何登入狀態：access token 由呼叫端傳入，續期與輪替、安全儲存由 services/huaxia_auth_service.py 管。
失敗一律丟出帶類別的 HuaXiaError（連不上、續期權杖已失效、其他回應錯誤）。
請求會卡住呼叫的執行緒，要在背景執行緒呼叫。

使用範例：
    from services.huaxia_client import HuaXiaClient
    client = HuaXiaClient()
    url = client.authorization_url("http://127.0.0.1:53682/", challenge)
    tokens = client.exchange_code(code, verifier)
    client.rpc("is_officer", tokens.access_token)
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests

# HuaXia 的 Supabase 專案（HuaXia 的 config.js，main 5cf811d）
HUAXIA_URL = "https://cluuvrdreqpchfoscowc.supabase.co"
HUAXIA_PUBLISHABLE_KEY = "sb_publishable_CK-Xpp2OOiaUVh3b1dmgGg_ktahQV_8"
# 一個請求最多等這麼久（秒）
_REQUEST_TIMEOUT = 30
# Supabase Auth 回報續期權杖或 session 已不存在（在別處登出、被撤銷、重用了舊的）的錯誤代碼
_SESSION_GONE_CODES = frozenset({
    "refresh_token_not_found", "refresh_token_already_used", "session_not_found", "session_expired",
})


class HuaXiaErrorKind(Enum):
    """HuaXia 請求失敗的類別"""

    UNREACHABLE = "unreachable"    # 連不上 HuaXia
    SIGNED_OUT = "signed_out"      # 這台電腦沒有登入 HuaXia
    SESSION_GONE = "session_gone"  # 續期權杖已失效（可能在別處登出了）
    REJECTED = "rejected"          # HuaXia 回了其他錯誤


class HuaXiaError(Exception):
    """HuaXia 請求失敗"""

    def __init__(self, kind: HuaXiaErrorKind, detail: str = ""):
        """
        Args:
            kind: 失敗的類別
            detail: 補充說明（狀態碼與錯誤訊息），沒有時為空字串
        """
        super().__init__(detail or kind.value)
        self.kind = kind
        self.detail = detail


@dataclass(frozen=True)
class AuthTokens:
    """Auth 兌換或續期的結果"""

    access_token: str
    refresh_token: str
    expires_in: int     # access token 的有效秒數
    user_id: str
    discord_name: str   # 登入的 Discord 帳號名稱；查不到時為空字串


def _discord_name(user: Dict[str, Any]) -> str:
    """Auth 回應裡 user 的 Discord 帳號名稱：使用者名稱，沒有時依序退回 name、user_name、preferred_username、顯示名稱"""
    metadata = user.get("user_metadata") or {}
    claims = metadata.get("custom_claims") or {}
    for name in (metadata.get(field) for field in ("full_name", "name", "user_name", "preferred_username")):
        if name:
            return name
    return claims.get("global_name") or ""


class HuaXiaClient:
    """HuaXia 的 Supabase REST API"""

    def __init__(self, session=None, url: str = HUAXIA_URL, key: str = HUAXIA_PUBLISHABLE_KEY):
        """
        Args:
            session: 送出請求的 HTTP session（requests.Session 的介面）；省略時建立一個（測試傳入假 HuaXia）
            url: HuaXia 的 Supabase 專案網址
            key: publishable key
        """
        self._session = session or requests.Session()
        self._url = url.rstrip("/")
        self._key = key

    # --- Auth ---

    def authorization_url(self, redirect_uri: str, code_challenge: str) -> str:
        """Discord 登入的授權網址（PKCE，S256）

        Args:
            redirect_uri: 本機回呼網址
            code_challenge: BASE64URL(SHA256(code_verifier))

        Returns:
            要在瀏覽器開啟的網址
        """
        query = urlencode({
            "provider": "discord", "redirect_to": redirect_uri,
            "code_challenge": code_challenge, "code_challenge_method": "s256",
        })
        return f"{self._url}/auth/v1/authorize?{query}"

    def exchange_code(self, code: str, code_verifier: str) -> AuthTokens:
        """以回呼帶回的授權碼兌換權杖

        Raises:
            HuaXiaError: 連不上或兌換失敗
        """
        return self._tokens(self._call(
            "POST", "/auth/v1/token", params={"grant_type": "pkce"},
            json={"auth_code": code, "code_verifier": code_verifier},
        ))

    def refresh(self, refresh_token: str) -> AuthTokens:
        """以續期權杖換新的 access token 與續期權杖；用過的續期權杖就作廢

        Raises:
            HuaXiaError: 連不上；續期權杖已失效時類別為 SESSION_GONE
        """
        return self._tokens(self._call(
            "POST", "/auth/v1/token", params={"grant_type": "refresh_token"}, json={"refresh_token": refresh_token},
        ))

    def logout(self, access_token: str) -> None:
        """只結束這個 session（scope=local），不影響瀏覽器裡的 HuaXia 網頁與別台電腦

        Raises:
            HuaXiaError: 連不上或請求失敗
        """
        self._call("POST", "/auth/v1/logout", access_token, params={"scope": "local"})

    # --- PostgREST ---

    def rpc(self, function: str, access_token: str, args: Optional[Dict[str, Any]] = None) -> Any:
        """以使用者身分呼叫資料庫函式，回傳它的值

        Raises:
            HuaXiaError: 連不上或呼叫失敗
        """
        return self._call("POST", f"/rest/v1/rpc/{function}", access_token, json=args or {})

    def select(self, table: str, access_token: str, columns: str, **equals: str) -> List[Dict[str, Any]]:
        """以使用者身分讀資料表裡欄位等於指定值的列（照 HuaXia 的 RLS，只回看得到的）

        Args:
            table: 資料表名稱
            access_token: 使用者的 access token
            columns: 要讀的欄位，以逗號分隔
            equals: 欄位名稱對應要等於的值

        Returns:
            讀到的列

        Raises:
            HuaXiaError: 連不上或讀取失敗
        """
        params = {"select": columns, **{column: f"eq.{value}" for column, value in equals.items()}}
        return self._call("GET", f"/rest/v1/{table}", access_token, params=params)

    # --- 共用 ---

    def _call(
        self, method: str, path: str, access_token: Optional[str] = None,
        params: Optional[Dict[str, str]] = None, json: Any = None,
    ) -> Any:
        """送出一個請求，回傳 JSON 內容（沒有內容時為 None）；失敗時丟出帶類別的 HuaXiaError"""
        headers = {"apikey": self._key}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        try:
            response = self._session.request(
                method, self._url + path, params=params, json=json, headers=headers, timeout=_REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            raise HuaXiaError(HuaXiaErrorKind.UNREACHABLE, str(e)) from e
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code >= 400:
            raise _error_from(response.status_code, payload)
        return payload

    @staticmethod
    def _tokens(payload: Dict[str, Any]) -> AuthTokens:
        """Auth 權杖端點的回應"""
        try:
            user = payload["user"]
            return AuthTokens(
                payload["access_token"], payload["refresh_token"], int(payload["expires_in"]),
                user["id"], _discord_name(user),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise HuaXiaError(HuaXiaErrorKind.REJECTED, f"unexpected token response: {e}") from e


def _error_from(status: int, payload: Any) -> HuaXiaError:
    """以錯誤回應建立 HuaXiaError：Auth 回報續期權杖或 session 已不存在時類別為 SESSION_GONE"""
    body = payload if isinstance(payload, dict) else {}
    code = body.get("error_code") or body.get("code") or body.get("error") or ""
    message = body.get("msg") or body.get("message") or body.get("error_description") or ""
    # 舊版 Auth 以 OAuth 的 invalid_grant 回報續期權杖不存在
    gone = code in _SESSION_GONE_CODES or (code == "invalid_grant" and "Refresh Token" in message)
    kind = HuaXiaErrorKind.SESSION_GONE if gone else HuaXiaErrorKind.REJECTED
    return HuaXiaError(kind, f"HTTP {status} {code}: {message}".strip())
