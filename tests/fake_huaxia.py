# -*- coding: utf-8 -*-
"""
假 HuaXia：記憶體裡的 Supabase，取代 HuaXia 用戶端的 HTTP session

照 requests.Session 的 request 介面（方法、網址、params、json、headers）收請求，回傳有 status_code、json() 的回應，
注入給 HuaXiaClient；不連網。實作泠靈用到的 Supabase 端點：

- Auth：authorize（由開瀏覽器的替身呼叫 authorize 取得授權碼）、PKCE 兌換、續期與輪替
  （續期權杖只能用一次，重用用過的會撤銷整個 session）、logout 的 scope=local 與 global
  （sign_out_everywhere 扮演在 HuaXia 網頁按了登出全部）。
- PostgREST：資料表的讀取（select 欄位與 eq 篩選），各表的讀取權限照 HuaXia 的 RLS 寫在 _READ_POLICIES。
- rpc：is_officer（帳號啟用且有 admin 或 officer）。

擴充（之後的工單）：新資料表加進 tables 與 _READ_POLICIES；寫入在 _rest 裡依方法分派（POST、PATCH、DELETE）；
新的 rpc 函式加進 functions（名稱對應以（使用者 ID，參數）求值的函式）；Storage 在 _route 加 /storage/v1/ 的分支。
每個請求都記在 requests，可以設成連不上（go_offline）或讓續期卡住直到放行（hold_refreshes）。

使用範例：
    from tests.fake_huaxia import FakeHuaXia
    huaxia = FakeHuaXia()
    huaxia.add_account("officer_amy", roles=("officer",))
    huaxia.sign_in_as("officer_amy")
    browser = CallbackBrowser(provider=huaxia.authorize)
    client = HuaXiaClient(session=huaxia)
"""
import base64
import datetime
import hashlib
import itertools
import threading
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import requests

# 假 HuaXia 發的 access token 有效秒數（Supabase 預設 3600）
TOKEN_LIFETIME = 3600
# Supabase 對 loopback IP 的回呼網址直接放行；其他網址會被導回 HuaXia 首頁
_LOOPBACK_PREFIX = "http://127.0.0.1:"


def _now() -> datetime.datetime:
    """現在的 UTC 時間（帶時區）"""
    return datetime.datetime.now(datetime.timezone.utc)


def _s256(verifier: str) -> str:
    """PKCE 的 S256 challenge：BASE64URL(SHA256(verifier))，不補 ="""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class _Response:
    """requests.Response 的替身：狀態碼與 JSON 內容"""

    def __init__(self, status_code: int, payload: Any = None):
        """以狀態碼與 JSON 內容建立回應；payload 為 None 時沒有內容（例如 204）"""
        self.status_code = status_code
        self._payload = payload

    @property
    def ok(self) -> bool:
        """狀態碼小於 400"""
        return self.status_code < 400

    @property
    def text(self) -> str:
        """回應內容的文字"""
        import json
        return "" if self._payload is None else json.dumps(self._payload, ensure_ascii=False)

    def json(self) -> Any:
        """回應內容；沒有內容時丟 ValueError（同 requests）"""
        if self._payload is None:
            raise ValueError("No content")
        return self._payload


def _auth_error(status: int, error_code: str, msg: str) -> _Response:
    """Supabase Auth 的錯誤回應格式"""
    return _Response(status, {"code": status, "error_code": error_code, "msg": msg})


class FakeHuaXia:
    """記憶體裡的 HuaXia（Supabase）；可從多個執行緒同時送請求"""

    def __init__(self):
        """建立沒有帳號的假 HuaXia；新的 Discord 帳號第一次登入時自動建立待核准的帳號（同 HuaXia 的觸發器）"""
        self._lock = threading.RLock()
        self._counter = itertools.count(1)
        self.expires_in = TOKEN_LIFETIME
        self.offline = False
        self.requests: List[Dict[str, Any]] = []
        self.tables: Dict[str, List[Dict[str, Any]]] = {"profiles": [], "user_roles": []}
        self.functions: Dict[str, Callable[[Optional[str], dict], Any]] = {"is_officer": self._is_officer}
        # Discord 帳號名稱對應 HuaXia 的使用者 ID；使用者 ID 對應 auth.users 的那筆
        self._identities: Dict[str, str] = {}
        self._users: Dict[str, Dict[str, Any]] = {}
        self._signing_in = ""
        # 授權碼對應（code_challenge，使用者 ID）；兌換一次就刪
        self._codes: Dict[str, Tuple[str, str]] = {}
        # session ID 對應使用者 ID；續期權杖對應（session ID，是否用過）；access token 對應（session ID，使用者 ID）
        self._sessions: Dict[str, str] = {}
        self._refresh_tokens: Dict[str, List[Any]] = {}
        self._access_tokens: Dict[str, Tuple[str, str]] = {}
        self._refresh_gate: Optional[threading.Event] = None

    # --- 測試設定 ---

    def add_account(
        self, discord_name: str, status: str = "active", roles: Iterable[str] = ("officer",),
        created_at: Optional[datetime.datetime] = None,
    ) -> str:
        """加入一個以 Discord 登入的帳號，回傳使用者 ID

        Args:
            discord_name: Discord 帳號名稱
            status: profiles.status（pending、active、inactive）
            roles: user_roles 裡的身分組
            created_at: 帳號建立時間；省略時為一年前
        """
        with self._lock:
            user_id = str(uuid.uuid4())
            created = created_at or _now() - datetime.timedelta(days=365)
            self._identities[discord_name] = user_id
            self._users[user_id] = {"discord_name": discord_name, "created_at": created}
            self.tables["profiles"].append({
                "id": user_id, "display_name": discord_name, "status": status, "created_at": created.isoformat(),
            })
            for role in roles:
                self.tables["user_roles"].append({"user_id": user_id, "role": role})
            return user_id

    def sign_in_as(self, discord_name: str) -> None:
        """之後在瀏覽器登入的是這個 Discord 帳號"""
        self._signing_in = discord_name

    def set_status(self, discord_name: str, status: str) -> None:
        """改這個 Discord 帳號在 HuaXia 的帳號狀態"""
        with self._lock:
            user_id = self._identities[discord_name]
            for row in self.tables["profiles"]:
                if row["id"] == user_id:
                    row["status"] = status

    def remove_roles(self, discord_name: str) -> None:
        """拿掉這個 Discord 帳號在 HuaXia 的所有身分組"""
        with self._lock:
            user_id = self._identities[discord_name]
            self.tables["user_roles"] = [r for r in self.tables["user_roles"] if r["user_id"] != user_id]

    def sign_out_everywhere(self, discord_name: str) -> None:
        """在 HuaXia 網頁登出全部（global）：這個帳號所有 session 的續期權杖都作廢"""
        with self._lock:
            self._end_sessions(lambda session_user: session_user == self._identities[discord_name])

    def go_offline(self) -> None:
        """之後的請求都連不上"""
        self.offline = True

    def hold_refreshes(self) -> threading.Event:
        """之後的續期請求收到後先卡住，直到回傳的 Event 被 set 才回應"""
        self._refresh_gate = threading.Event()
        return self._refresh_gate

    # --- 查詢收到的請求 ---

    def refreshes(self) -> List[str]:
        """各次續期送出的續期權杖"""
        return [r["json"]["refresh_token"] for r in self._token_requests("refresh_token")]

    def logouts(self) -> List[str]:
        """各次登出請求的 scope"""
        return [r["params"].get("scope", "global") for r in self.requests if r["path"] == "/auth/v1/logout"]

    def live_sessions(self, discord_name: str) -> int:
        """這個帳號還有效（續期權杖還能用）的 session 數"""
        with self._lock:
            user_id = self._identities.get(discord_name)
            return sum(1 for session_user in self._sessions.values() if session_user == user_id)

    def _token_requests(self, grant_type: str) -> List[Dict[str, Any]]:
        """這種 grant_type 的權杖請求"""
        return [
            r for r in self.requests
            if r["path"] == "/auth/v1/token" and r["params"].get("grant_type") == grant_type
        ]

    # --- 瀏覽器那一端 ---

    def authorize(self, url: str) -> Dict[str, str]:
        """瀏覽器開了授權網址、在 Discord 同意之後，Supabase 帶回本機回呼的參數

        記下 code_challenge，發一個綁定目前登入的 Discord 帳號的授權碼；這個 Discord 帳號在 HuaXia 還沒有帳號時，
        像 HuaXia 的觸發器一樣建立一個待核准、沒有身分組的帳號。

        Args:
            url: 泠靈開的授權網址

        Returns:
            回呼的 query 參數（code）
        """
        query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
        assert urlsplit(url).path == "/auth/v1/authorize", url
        assert query["provider"] == "discord", query
        assert query["code_challenge_method"].lower() == "s256", query
        assert query["redirect_to"].startswith(_LOOPBACK_PREFIX), query
        with self._lock:
            user_id = self._identities.get(self._signing_in)
            if user_id is None:
                user_id = self.add_account(self._signing_in, status="pending", roles=(), created_at=_now())
            code = f"code-{next(self._counter)}"
            self._codes[code] = (query["code_challenge"], user_id)
        return {"code": code}

    # --- requests.Session 介面 ---

    def request(self, method: str, url: str, params=None, json=None, headers=None, timeout=None, **kwargs):
        """收下一個請求，依端點回應；連不上時丟 requests.ConnectionError"""
        parts = urlsplit(url)
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        query.update(params or {})
        record = {"method": method.upper(), "path": parts.path, "params": query, "json": json, "headers": headers or {}}
        with self._lock:
            self.requests.append(record)
        if self.offline:
            raise requests.ConnectionError("Failed to establish a new connection")
        if not record["headers"].get("apikey"):
            return _Response(401, {"message": "No API key found in request"})
        if record["path"] == "/auth/v1/token" and query.get("grant_type") == "refresh_token" and self._refresh_gate:
            self._refresh_gate.wait(10)
        with self._lock:
            return self._route(record)

    def _route(self, record: Dict[str, Any]) -> _Response:
        """依路徑分派到 Auth、rpc 或 PostgREST"""
        path = record["path"]
        if path == "/auth/v1/token":
            return self._token(record["params"].get("grant_type"), record["json"] or {})
        if path == "/auth/v1/logout":
            return self._logout(record)
        if path.startswith("/rest/v1/rpc/"):
            return self._rpc(path[len("/rest/v1/rpc/"):], record)
        if path.startswith("/rest/v1/"):
            return self._rest(path[len("/rest/v1/"):], record)
        return _Response(404, {"message": "Not found"})

    # --- Auth ---

    def _token(self, grant_type: str, body: dict) -> _Response:
        """POST /auth/v1/token：PKCE 兌換或續期"""
        if grant_type == "pkce":
            entry = self._codes.pop(body.get("auth_code"), None)
            if entry is None:
                return _auth_error(404, "flow_state_not_found", "invalid flow state, no valid flow state found")
            challenge, user_id = entry
            if _s256(body.get("code_verifier", "")) != challenge:
                return _auth_error(
                    400, "bad_code_verifier", "code challenge does not match previously saved code verifier",
                )
            session_id = str(uuid.uuid4())
            self._sessions[session_id] = user_id
            return self._issue(session_id)
        if grant_type == "refresh_token":
            entry = self._refresh_tokens.get(body.get("refresh_token"))
            if entry is None:
                return _auth_error(400, "refresh_token_not_found", "Invalid Refresh Token: Refresh Token Not Found")
            session_id, used = entry
            if used:
                # 重用用過的續期權杖視為竊用：撤銷整個 session
                self._end_sessions(lambda _user: False, session_id)
                return _auth_error(400, "refresh_token_already_used", "Invalid Refresh Token: Already Used")
            entry[1] = True
            return self._issue(session_id)
        return _auth_error(400, "unsupported_grant_type", "unsupported_grant_type")

    def _issue(self, session_id: str) -> _Response:
        """為 session 發新的 access token 與續期權杖"""
        user_id = self._sessions[session_id]
        n = next(self._counter)
        access, refresh = f"access-{n}", f"refresh-{n}"
        self._access_tokens[access] = (session_id, user_id)
        self._refresh_tokens[refresh] = [session_id, False]
        user = self._users[user_id]
        name = user["discord_name"]
        return _Response(200, {
            "access_token": access, "token_type": "bearer", "expires_in": self.expires_in,
            "refresh_token": refresh,
            "user": {
                "id": user_id, "aud": "authenticated", "role": "authenticated",
                "created_at": user["created_at"].isoformat(),
                "app_metadata": {"provider": "discord", "providers": ["discord"]},
                "user_metadata": {
                    "full_name": name, "name": f"{name}#0", "custom_claims": {"global_name": name.title()},
                },
            },
        })

    def _logout(self, record: Dict[str, Any]) -> _Response:
        """POST /auth/v1/logout：local 只結束這個 session，global 結束這個帳號的所有 session"""
        bearer = self._bearer(record)
        if bearer is None:
            return _auth_error(401, "no_authorization", "This endpoint requires a valid Bearer token")
        session_id, user_id = bearer
        scope = record["params"].get("scope", "global")
        if scope == "local":
            self._end_sessions(lambda _user: False, session_id)
        else:
            self._end_sessions(lambda session_user: session_user == user_id)
        return _Response(204)

    def _end_sessions(self, matches: Callable[[str], bool], session_id: Optional[str] = None) -> None:
        """結束 session_id 那個 session 與 matches 判定為真的帳號的所有 session：續期權杖全部作廢"""
        ended = {sid for sid, user in self._sessions.items() if sid == session_id or matches(user)}
        for sid in ended:
            del self._sessions[sid]
        self._refresh_tokens = {t: e for t, e in self._refresh_tokens.items() if e[0] not in ended}

    def _bearer(self, record: Dict[str, Any]) -> Optional[Tuple[str, str]]:
        """請求帶的 access token 對應的（session ID，使用者 ID）；沒帶或不認得時為 None"""
        authorization = record["headers"].get("Authorization", "")
        if not authorization.startswith("Bearer "):
            return None
        return self._access_tokens.get(authorization[len("Bearer "):])

    def _caller(self, record: Dict[str, Any]) -> Optional[str]:
        """呼叫者的使用者 ID（PostgREST 的 auth.uid()）；匿名時為 None"""
        bearer = self._bearer(record)
        return bearer[1] if bearer else None

    # --- rpc 與 PostgREST ---

    def _rpc(self, name: str, record: Dict[str, Any]) -> _Response:
        """POST /rest/v1/rpc/<name>：呼叫 functions 裡的函式，回傳它的值"""
        function = self.functions.get(name)
        if function is None:
            return _Response(404, {"code": "PGRST202", "message": f"Could not find the function public.{name}"})
        return _Response(200, function(self._caller(record), record["json"] or {}))

    def _is_officer(self, user_id: Optional[str], _args: dict) -> bool:
        """HuaXia 的 is_officer()：帳號啟用且有 admin 或 officer"""
        if user_id is None:
            return False
        active = any(p["id"] == user_id and p["status"] == "active" for p in self.tables["profiles"])
        return active and any(
            r["user_id"] == user_id and r["role"] in ("admin", "officer") for r in self.tables["user_roles"]
        )

    def _rest(self, table: str, record: Dict[str, Any]) -> _Response:
        """/rest/v1/<table>：目前只有讀取（GET），照 RLS 只回呼叫者看得到的列"""
        if table not in self.tables:
            return _Response(404, {"code": "PGRST205", "message": f"Could not find the table public.{table}"})
        if record["method"] != "GET":
            return _Response(405, {"message": "Method not supported by the fake"})
        caller = self._caller(record)
        params = dict(record["params"])
        columns = [c.strip() for c in params.pop("select", "*").split(",")]
        rows = [
            row for row in self.tables[table]
            if _READ_POLICIES[table](self, caller, row) and all(_matches(row, k, v) for k, v in params.items())
        ]
        if columns != ["*"]:
            rows = [{c: row.get(c) for c in columns} for row in rows]
        return _Response(200, rows)

    def _is_active(self, user_id: Optional[str]) -> bool:
        """HuaXia 的 is_active()"""
        return any(p["id"] == user_id and p["status"] == "active" for p in self.tables["profiles"])


def _matches(row: Dict[str, Any], column: str, condition: str) -> bool:
    """PostgREST 的篩選（目前只支援 eq.）"""
    operator, _, value = condition.partition(".")
    assert operator == "eq", f"fake supports eq only: {column}={condition}"
    return str(row.get(column)) == value


# 各資料表的讀取權限，照 HuaXia schema.sql 的 RLS：（假 HuaXia，呼叫者的使用者 ID，列）→ 看不看得到
_READ_POLICIES: Dict[str, Callable[[FakeHuaXia, Optional[str], Dict[str, Any]], bool]] = {
    "profiles": lambda fake, caller, row: row["id"] == caller or fake._is_active(caller),
    "user_roles": lambda fake, caller, row: row["user_id"] == caller or fake._is_active(caller),
}
