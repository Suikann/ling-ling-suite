# -*- coding: utf-8 -*-
"""
假 HuaXia：記憶體裡的 Supabase，取代 HuaXia 用戶端的 HTTP session

照 requests.Session 的 request 介面（方法、網址、params、json、headers）收請求，回傳有 status_code、json() 的回應，
注入給 HuaXiaClient；不連網。實作泠靈用到的 Supabase 端點：

- Auth：authorize（由開瀏覽器的替身呼叫 authorize 取得授權碼）、PKCE 兌換、續期與輪替
  （續期權杖只能用一次，重用用過的會撤銷整個 session）、logout 的 scope=local 與 global
  （sign_out_everywhere 扮演在 HuaXia 網頁按了登出全部）。
- PostgREST：資料表的讀取（select 欄位、eq 與 in 篩選、order、limit 與 offset），各表的讀取權限照 HuaXia 的 RLS
  寫在 _READ_POLICIES。一次最多回傳 max_rows 列（同 Supabase 的 max_rows，預設 1000），多的要分頁讀。
- rpc：is_officer（帳號啟用且有 admin 或 officer）；發譜函式的版本查詢預設不存在（同 HuaXia 合併 #117 之前），
  以 install_publish_function 裝上指定的版本。
- 曲目（pieces）與聲部（piece_parts）：以 add_piece、add_part 加入，讀取權限照 HuaXia 的 RLS 簡化成 is_insider
  （不實作 in_piece 的排人判斷）；聲部組沒給時照 HuaXia 的觸發器以 guess_section 依名稱補上。
- 樂譜（scores）、學期（semesters）與場次（events）：以 add_score、add_semester、add_event 加入，曲目掛上場次
  （event_pieces）以 attach_piece 加入；樂譜的讀取權限
  簡化成 is_staff（幹部或老師，不實作排到聲部的人），場次照 can_see_event 簡化成幹部全看得到、其他社內成員看得到
  不是只給幹部的場次，event_pieces 照它的場次，學期是 is_active。rename_score 扮演幹部在網頁上改了樂譜名稱。
- Storage（bucket scores）：put_object 放檔，列出（POST /storage/v1/object/list/scores）回傳某個資料夾底下一層的
  檔與子資料夾，讀取權限簡化成 is_staff。

擴充（之後的工單）：新資料表加進 tables 與 _READ_POLICIES；寫入在 _rest 裡依方法分派（POST、PATCH、DELETE）；
新的 rpc 函式加進 functions（名稱對應以（使用者 ID，參數）求值的函式）；Storage 的上傳與刪除在 _storage 依路徑分派。
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
import re
import threading
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit

import requests

from services.huaxia_client import PUBLISH_VERSION_FUNCTION

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
        self.max_rows = 1000
        self.requests: List[Dict[str, Any]] = []
        self.tables: Dict[str, List[Dict[str, Any]]] = {
            "profiles": [], "user_roles": [], "pieces": [], "piece_parts": [], "scores": [], "semesters": [],
            "events": [], "event_pieces": [],
        }
        # Storage bucket scores 裡的檔：路徑對應內容
        self.objects: Dict[str, bytes] = {}
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
        created_at: Optional[datetime.datetime] = None, metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """加入一個以 Discord 登入的帳號，回傳使用者 ID

        Args:
            discord_name: Discord 帳號名稱
            status: profiles.status（pending、active、inactive）
            roles: user_roles 裡的身分組
            created_at: 帳號建立時間；省略時為一年前
            metadata: Auth 回應裡 user 的 user_metadata；省略時照 Discord 的欄位以 discord_name 填
        """
        with self._lock:
            user_id = str(uuid.uuid4())
            created = created_at or _now() - datetime.timedelta(days=365)
            self._identities[discord_name] = user_id
            self._users[user_id] = {"discord_name": discord_name, "created_at": created, "metadata": metadata}
            self.tables["profiles"].append({
                "id": user_id, "display_name": discord_name, "status": status, "created_at": created.isoformat(),
            })
            for role in roles:
                self.tables["user_roles"].append({"user_id": user_id, "role": role})
            return user_id

    def add_piece(
        self, title: str, composer: str = "", duration_min: Optional[float] = None, ensemble: str = "tutti",
        archived: bool = False,
    ) -> str:
        """加入一首曲目（pieces 的一列，欄位照 HuaXia 的 schema），回傳曲目編號"""
        with self._lock:
            piece_id = str(uuid.uuid4())
            self.tables["pieces"].append({
                "id": piece_id, "title": title, "composer": composer, "duration_min": duration_min, "notes": "",
                "archived": archived, "ensemble": ensemble, "created_at": _now().isoformat(),
            })
            return piece_id

    def add_part(
        self, piece_id: str, name: str, needed: str = "1", needed_min: int = 1, section: Optional[str] = None,
        sort: int = 0,
    ) -> str:
        """在曲目上加入一個聲部（piece_parts 的一列），回傳聲部編號；聲部組沒給時照觸發器以 guess_section 補上"""
        with self._lock:
            part_id = str(uuid.uuid4())
            self.tables["piece_parts"].append({
                "id": part_id, "piece_id": piece_id, "name": name, "needed": needed, "needed_min": needed_min,
                "sort": sort, "tutor_id": None, "note": "", "section": section or guess_section(name),
            })
            return part_id

    def remove_part(self, part_id: str) -> None:
        """刪掉一個聲部（幹部在網頁上刪了它）"""
        with self._lock:
            self.tables["piece_parts"] = [row for row in self.tables["piece_parts"] if row["id"] != part_id]

    def rename_part(self, part_id: str, name: str) -> None:
        """改一個聲部的名稱（幹部在網頁上改了它）"""
        with self._lock:
            for row in self.tables["piece_parts"]:
                if row["id"] == part_id:
                    row["name"] = name

    def add_score(
        self, piece_id: str, part_id: Optional[str], file_path: str, title: str = "",
        section: Optional[str] = None, created_by: Optional[str] = None,
    ) -> str:
        """在曲目上登記一份樂譜（scores 的一列），回傳樂譜編號；part_id 為 None 是總譜（section 不為 None 是舊的分組樂譜）"""
        with self._lock:
            score_id = str(uuid.uuid4())
            self.tables["scores"].append({
                "id": score_id, "piece_id": piece_id, "part_id": part_id, "title": title, "file_path": file_path,
                "audio_url": "", "section": section, "created_by": created_by, "created_at": _now().isoformat(),
            })
            return score_id

    def rename_score(self, score_id: str, title: str) -> None:
        """改一份樂譜的名稱（幹部在網頁上改了它）"""
        with self._lock:
            for row in self.tables["scores"]:
                if row["id"] == score_id:
                    row["title"] = title

    def add_semester(self, name: str, is_current: bool = False) -> str:
        """加入一個學期（semesters 的一列），回傳學期編號；設成本學期時其他學期不再是本學期（同 HuaXia 的唯一索引）"""
        with self._lock:
            semester_id = str(uuid.uuid4())
            if is_current:
                for row in self.tables["semesters"]:
                    row["is_current"] = False
            today = _now().date()
            self.tables["semesters"].append({
                "id": semester_id, "name": name, "starts_on": today.isoformat(), "ends_on": today.isoformat(),
                "is_current": is_current, "created_at": _now().isoformat(),
            })
            return semester_id

    def add_event(
        self, semester_id: Optional[str], title: str, starts_at: datetime.datetime, ends_at: datetime.datetime,
        kinds: Iterable[str] = ("concert",), location: str = "", audience: str = "insiders",
    ) -> str:
        """加入一個場次（events 的一列），回傳場次編號；kind 照觸發器取 kinds 的第一個"""
        with self._lock:
            event_id = str(uuid.uuid4())
            kinds = list(kinds)
            self.tables["events"].append({
                "id": event_id, "semester_id": semester_id, "kind": kinds[0], "kinds": kinds, "title": title,
                "starts_at": starts_at.isoformat(), "ends_at": ends_at.isoformat(), "location": location, "note": "",
                "audience": audience, "counts_attendance": True,
            })
            return event_id

    def attach_piece(self, event_id: str, piece_id: str) -> None:
        """把曲目掛上場次（event_pieces 的一列，幹部在網頁上掛的）"""
        with self._lock:
            self.tables["event_pieces"].append({"event_id": event_id, "piece_id": piece_id})

    def put_object(self, path: str, content: bytes = b"pdf") -> None:
        """在 Storage 的 bucket scores 放一個檔"""
        with self._lock:
            self.objects[path] = content

    def install_publish_function(self, version: Optional[int]) -> None:
        """HuaXia 的發譜函式：回報這個介面版本；None 為沒有發譜函式（呼叫時回 PGRST202）"""
        with self._lock:
            if version is None:
                self.functions.pop(PUBLISH_VERSION_FUNCTION, None)
            else:
                self.functions[PUBLISH_VERSION_FUNCTION] = lambda _user_id, _args: version

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
        if path.startswith("/storage/v1/"):
            return self._storage(path[len("/storage/v1/"):], record)
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
                "user_metadata": user.get("metadata") if user.get("metadata") is not None else {
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
        """/rest/v1/<table>：目前只有讀取（GET），照 RLS 只回呼叫者看得到的列，依 order 排序、照 offset 與 limit
        取一段，一次最多 max_rows 列"""
        if table not in self.tables:
            return _Response(404, {"code": "PGRST205", "message": f"Could not find the table public.{table}"})
        if record["method"] != "GET":
            return _Response(405, {"message": "Method not supported by the fake"})
        caller = self._caller(record)
        params = dict(record["params"])
        columns = [c.strip() for c in params.pop("select", "*").split(",")]
        order = params.pop("order", "")
        offset = int(params.pop("offset", 0))
        limit = min(int(params.pop("limit", self.max_rows)), self.max_rows)
        rows = [
            row for row in self.tables[table]
            if _READ_POLICIES[table](self, caller, row) and all(_matches(row, k, v) for k, v in params.items())
        ]
        for column in reversed([c for c in order.split(",") if c]):
            name, _, direction = column.partition(".")
            rows.sort(key=lambda row: str(row.get(name)), reverse=direction == "desc")
        rows = rows[offset:offset + limit]
        if columns != ["*"]:
            rows = [{c: row.get(c) for c in columns} for row in rows]
        return _Response(200, rows)

    def _storage(self, path: str, record: Dict[str, Any]) -> _Response:
        """/storage/v1/...：目前只有列出 bucket scores 某個資料夾底下一層（照 Supabase 的回應格式）"""
        if path != "object/list/scores" or record["method"] != "POST":
            return _Response(405, {"message": "Storage request not supported by the fake"})
        if not self._is_staff(self._caller(record)):
            return _Response(200, [])
        body = record["json"] or {}
        prefix = body.get("prefix", "").strip("/")
        folder = f"{prefix}/" if prefix else ""
        files: Dict[str, int] = {}
        folders = set()
        for name, content in self.objects.items():
            if not name.startswith(folder):
                continue
            rest = name[len(folder):]
            if "/" in rest:
                folders.add(rest.split("/", 1)[0])
            else:
                files[rest] = len(content)
        entries = [{"name": name, "id": None, "metadata": None} for name in sorted(folders)]
        entries += [
            {"name": name, "id": str(uuid.uuid5(uuid.NAMESPACE_URL, folder + name)), "metadata": {"size": size}}
            for name, size in sorted(files.items())
        ]
        offset = int(body.get("offset", 0))
        return _Response(200, entries[offset:offset + int(body.get("limit", 100))])

    def _is_staff(self, user_id: Optional[str]) -> bool:
        """HuaXia 的 is_staff()：幹部或老師"""
        return self._is_officer(user_id, {}) or (self._is_active(user_id) and any(
            r["user_id"] == user_id and r["role"] == "teacher" for r in self.tables["user_roles"]
        ))

    def _can_see_event(self, user_id: Optional[str], row: Dict[str, Any]) -> bool:
        """HuaXia 的 can_see_event 簡化：幹部全看得到，其他社內成員看得到不是只給幹部的（不實作排人判斷）"""
        if self._is_officer(user_id, {}):
            return True
        return row["audience"] != "officers" and self._is_insider(user_id)

    def _is_active(self, user_id: Optional[str]) -> bool:
        """HuaXia 的 is_active()"""
        return any(p["id"] == user_id and p["status"] == "active" for p in self.tables["profiles"])

    def _is_insider(self, user_id: Optional[str]) -> bool:
        """HuaXia 的 is_insider()：帳號啟用，且身分組是社內的（admin、officer、leader、member、newbie、teacher、alumni）"""
        return self._is_active(user_id) and any(
            r["user_id"] == user_id and r["role"] in _INSIDER_ROLES for r in self.tables["user_roles"]
        )


def _matches(row: Dict[str, Any], column: str, condition: str) -> bool:
    """PostgREST 的篩選（目前只支援 eq. 與 in.(…)）；是非值照 PostgREST 寫成 true、false"""
    operator, _, value = condition.partition(".")
    assert operator in ("eq", "in"), f"fake supports eq and in only: {column}={condition}"
    actual = row.get(column)
    text = str(actual).lower() if isinstance(actual, bool) else str(actual)
    if operator == "in":
        assert value.startswith("(") and value.endswith(")"), condition
        return text in value[1:-1].split(",")
    return text == value


# HuaXia 的 guess_section(n)：依名稱猜聲部組，依序比對，都不符時為 None（省略其中的簡體寫法）
_SECTION_GUESSES = (
    ("吹管", "笛|笙|嗩吶|管子|簫|巴烏|葫蘆絲|dizi|sheng|suona|flute"),
    ("低音", "大提|低音提|倍大提|革胡|貝斯|cello|bass"),
    ("拉弦", "胡|erhu|gaohu|zhonghu"),
    ("彈撥", "琵琶|阮|柳琴|揚琴|箏|三弦|箜篌|pipa|ruan|liuqin|yangqin|guzheng"),
    ("打擊", "打擊|鼓|鑼|鈸|木魚|定音|鐘琴|鐵琴|木琴|鈴|梆子|板|perc|timp|drum"),
)


def guess_section(name: str) -> Optional[str]:
    """HuaXia 的 guess_section：piece_parts 的聲部組沒填時，觸發器依名稱補上的值"""
    return next((section for section, pattern in _SECTION_GUESSES if re.search(pattern, name, re.IGNORECASE)), None)


# HuaXia 的 is_insider() 認的身分組
_INSIDER_ROLES = frozenset({"admin", "officer", "leader", "member", "newbie", "teacher", "alumni"})


# 各資料表的讀取權限，照 HuaXia schema.sql 的 RLS：（假 HuaXia，呼叫者的使用者 ID，列）→ 看不看得到
_READ_POLICIES: Dict[str, Callable[[FakeHuaXia, Optional[str], Dict[str, Any]], bool]] = {
    "profiles": lambda fake, caller, row: row["id"] == caller or fake._is_active(caller),
    "user_roles": lambda fake, caller, row: row["user_id"] == caller or fake._is_active(caller),
    "pieces": lambda fake, caller, row: fake._is_insider(caller),
    "piece_parts": lambda fake, caller, row: fake._is_insider(caller),
    "scores": lambda fake, caller, row: fake._is_staff(caller),
    "semesters": lambda fake, caller, row: fake._is_active(caller),
    "events": lambda fake, caller, row: fake._can_see_event(caller, row),
    "event_pieces": lambda fake, caller, row: any(
        event["id"] == row["event_id"] and fake._can_see_event(caller, event) for event in fake.tables["events"]
    ),
}
