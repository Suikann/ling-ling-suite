# -*- coding: utf-8 -*-
"""
HuaXia 用戶端

全程式唯一碰 HuaXia 的地方：以 requests 直接打 HuaXia 的 Supabase REST API（Auth、PostgREST、Storage），
不用 supabase-py（ADR 0003）。HuaXia 的網址與 publishable key 內附在這裡（publishable key 本來就設計成公開）。
每個請求都帶 apikey；以使用者身分的請求另帶 Authorization: Bearer <access token>。

用戶端不記任何登入狀態：access token 由呼叫端傳入，續期與輪替、安全儲存由 services/huaxia_auth_service.py 管。
失敗一律丟出帶類別的 HuaXiaError（連不上、續期權杖已失效、資料庫函式不存在、其他回應錯誤），並帶 HuaXia 回的
錯誤代碼與狀態碼。

泠靈依賴 HuaXia 的什麼由 HuaXia 的依賴說明文件定，泠靈這邊的複本是 docs/huaxia/LINGLING.md（對 HuaXia 的合併請求 #117）：
- 發譜函式 PUBLISH_FUNCTION（publish）一次切換一首曲目，全成或全不成；它的介面版本由 PUBLISH_VERSION_FUNCTION
  回報（publish_interface_version），泠靈要的是 PUBLISH_INTERFACE_VERSION。輸入、輸出與錯誤代碼照文件。
- 泠靈的 Storage 路徑格式（lingling_storage_path、is_lingling_path）：「固定前綴 LINGLING_STORAGE_PREFIX／曲目編號／
  內容的 SHA-256-登記位置.pdf」。登記位置是 1 到 32 個小寫英數字，由發譜決定（publish_service.storage_slot），
  讓每筆樂譜登記各有自己的 Storage 檔：HuaXia 網頁刪樂譜時會連檔一起刪，共用的檔會弄壞另一筆登記。
  HuaXia 網頁上傳的路徑是「曲目編號／聲部編號或 full／時間戳_檔名」，不會以這個前綴開頭，所以只看路徑就分得出
  哪份是泠靈發的。發譜的第 1 段以 upload 傳檔（不覆寫，路徑已有檔就是同一份內容）、第 3 段以 delete_files 刪掉
  被取代的檔。
- 撤回與收回不經發譜函式，以使用者身分經 REST 刪除（delete_scores、delete_parts，HuaXia 的 RLS 照常生效）；
  刪聲部會連帶刪掉它的排人與樂譜，所以先以 assigned_parts 確認沒排人、以 configured_parts 確認沒人在網頁上設定過。收回刪檔前以 registered_paths 確認
  沒有任何樂譜登記還指著，曲目掛的公演以 piece_events、events_by_id 讀。
請求會卡住呼叫的執行緒，要在背景執行緒呼叫。

使用範例：
    from services.huaxia_client import HuaXiaClient
    client = HuaXiaClient()
    url = client.authorization_url("http://127.0.0.1:53682/", challenge)
    tokens = client.exchange_code(code, verifier)
    client.rpc("is_officer", tokens.access_token)
"""
import datetime
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, NamedTuple, Optional, Set, Tuple
from urllib.parse import urlencode

import requests

from core.constants import HuaXiaEnsemble

# HuaXia 的 Supabase 專案（HuaXia 的 config.js，main 5cf811d）
HUAXIA_URL = "https://cluuvrdreqpchfoscowc.supabase.co"
HUAXIA_PUBLISHABLE_KEY = "sb_publishable_CK-Xpp2OOiaUVh3b1dmgGg_ktahQV_8"
# HuaXia 的發譜函式、回報它介面版本的資料庫函式，以及這版泠靈要的介面版本（docs/huaxia/LINGLING.md）
PUBLISH_FUNCTION = "lingling_publish"
PUBLISH_VERSION_FUNCTION = "lingling_publish_version"
PUBLISH_INTERFACE_VERSION = 1
# 泠靈發的檔在 Storage bucket scores 裡的路徑前綴（docs/huaxia/LINGLING.md）
LINGLING_STORAGE_PREFIX = "lingling/"
_LINGLING_PATH = re.compile(re.escape(LINGLING_STORAGE_PREFIX) + r"[^/]+/[0-9a-f]{64}-[0-9a-z]{1,32}\.pdf")
# Storage 列出檔案時一次要幾筆（Supabase 的預設）
_STORAGE_PAGE_SIZE = 100
# 樂譜所在的 Storage bucket；上傳的檔案類型
_STORAGE_BUCKET = "scores"
_PDF_MIME = "application/pdf"
# Storage 回報路徑已經有檔（不覆寫的上傳）的錯誤：舊版回 HTTP 400、本體的 statusCode 是 "409"，新版回 409
_STORAGE_DUPLICATE = "Duplicate"
# 讀資料表時一次要幾列（Supabase 的 max_rows 預設 1000；HuaXia 設得更小時回得較少，照樣讀到回空為止）
_TABLE_PAGE_SIZE = 1000
# 以 in.(…) 篩選時一個請求帶幾個編號（避免網址過長），多的分成幾個請求
_IN_FILTER_CHUNK = 50
# 讀場次的欄位
_EVENT_COLUMNS = "id,title,starts_at,ends_at,kinds,location"
# 一個請求最多等這麼久（秒）
_REQUEST_TIMEOUT = 30
# PostgREST 回報找不到資料庫函式的錯誤代碼
_MISSING_FUNCTION_CODE = "PGRST202"
# Supabase Auth 回報續期權杖或 session 已不存在（在別處登出、被撤銷、重用了舊的）的錯誤代碼
_SESSION_GONE_CODES = frozenset({
    "refresh_token_not_found", "refresh_token_already_used", "session_not_found", "session_expired",
})


class HuaXiaErrorKind(Enum):
    """HuaXia 請求失敗的類別"""

    UNREACHABLE = "unreachable"    # 連不上 HuaXia
    SIGNED_OUT = "signed_out"      # 這台電腦沒有登入 HuaXia
    SESSION_GONE = "session_gone"  # 續期權杖已失效（可能在別處登出了）
    MISSING_FUNCTION = "missing_function"  # HuaXia 沒有這個資料庫函式
    REJECTED = "rejected"          # HuaXia 回了其他錯誤


class HuaXiaError(Exception):
    """HuaXia 請求失敗"""

    def __init__(self, kind: HuaXiaErrorKind, detail: str = "", code: str = "", status: Optional[int] = None):
        """
        Args:
            kind: 失敗的類別
            detail: 補充說明（狀態碼與錯誤訊息），沒有時為空字串
            code: HuaXia 回的錯誤代碼（Auth 的 error_code，或 PostgREST 的 code，例如發譜函式的 PT409），沒有時為空字串
            status: HTTP 狀態碼；不是 HuaXia 回的錯誤（例如連不上）時為 None
        """
        super().__init__(detail or kind.value)
        self.kind = kind
        self.detail = detail
        self.code = code
        self.status = status


@dataclass(frozen=True)
class AuthTokens:
    """Auth 兌換或續期的結果"""

    access_token: str
    refresh_token: str
    expires_in: int     # access token 的有效秒數
    user_id: str
    discord_name: str   # 登入的 Discord 帳號名稱；查不到時為空字串


class HuaXiaPiece(NamedTuple):
    """HuaXia 的一首曲目（pieces 的一列）

    Attributes:
        id: 曲目編號
        title: 曲名
        composer: 作曲家
        duration_min: 時長（分鐘）；沒填為 None
        ensemble: 合奏或絲竹
    """
    id: str
    title: str
    composer: str
    duration_min: Optional[float]
    ensemble: HuaXiaEnsemble


class HuaXiaPart(NamedTuple):
    """HuaXia 曲目上的一個聲部（piece_parts 的一列）

    Attributes:
        id: 聲部編號
        piece_id: 所屬曲目的編號
        name: 聲部名稱（HuaXia 的寫法）
        needed: 人數的記法（「6」「6-8」「2↑」）
        needed_min: 最少人數
        section: 聲部組；沒有時為空字串
        sort: 排序
    """
    id: str
    piece_id: str
    name: str
    needed: str
    needed_min: int
    section: str
    sort: int


class HuaXiaScore(NamedTuple):
    """HuaXia 曲目上登記的一份樂譜（scores 的一列）

    Attributes:
        id: 樂譜編號
        piece_id: 所屬曲目的編號
        part_id: 所屬聲部的編號；總譜（與舊的分組樂譜）為 None
        title: 樂譜名稱（幹部可以在網頁上改）
        file_path: 檔案在 Storage bucket scores 裡的路徑；沒有檔案時為空字串
        section: 舊的分組樂譜的聲部組；其他為空字串
    """
    id: str
    piece_id: str
    part_id: Optional[str]
    title: str
    file_path: str
    section: str

    @property
    def lingling(self) -> bool:
        """是泠靈發的（路徑是泠靈的 Storage 路徑格式）"""
        return is_lingling_path(self.file_path)


class HuaXiaEvent(NamedTuple):
    """HuaXia 的一個場次（events 的一列）

    Attributes:
        id: 場次編號
        title: 名稱
        starts_at: 開始時間（帶時區）
        ends_at: 結束時間（帶時區）
        kinds: 類型（可以複選，例如 dress、concert）
        location: 地點
    """
    id: str
    title: str
    starts_at: datetime.datetime
    ends_at: datetime.datetime
    kinds: Tuple[str, ...]
    location: str


def lingling_storage_path(piece_id: str, sha256: str, slot: str) -> str:
    """泠靈發的一份檔在 Storage bucket scores 裡的路徑

    Args:
        piece_id: 曲目編號
        sha256: 內容的 SHA-256（十六進位小寫）
        slot: 登記位置（1 到 32 個小寫英數字）：同一份內容登記在不同位置時各是一個檔

    Returns:
        「固定前綴／曲目編號／SHA-256-登記位置.pdf」
    """
    return f"{lingling_storage_folder(piece_id)}/{sha256}-{slot}.pdf"


def lingling_storage_folder(piece_id: str) -> str:
    """泠靈發到一首曲目的檔所在的 Storage 資料夾：固定前綴與曲目編號（stored_files 列出它）"""
    return f"{LINGLING_STORAGE_PREFIX}{piece_id}"


def is_lingling_path(path: str) -> bool:
    """Storage 路徑是不是泠靈的路徑格式（lingling_storage_path），也就是泠靈發的檔"""
    return bool(_LINGLING_PATH.fullmatch(path or ""))


def _events(rows: List[Dict[str, Any]]) -> List[HuaXiaEvent]:
    """events 的列轉成場次，依開始時間

    Raises:
        HuaXiaError: 回應的格式不對
    """
    try:
        events = [
            HuaXiaEvent(
                row["id"], row["title"], datetime.datetime.fromisoformat(row["starts_at"]),
                datetime.datetime.fromisoformat(row["ends_at"]), tuple(row.get("kinds") or ()),
                row.get("location") or "",
            )
            for row in rows
        ]
    except (KeyError, TypeError, ValueError) as e:
        raise HuaXiaError(HuaXiaErrorKind.REJECTED, f"unexpected events: {e}") from e
    return sorted(events, key=lambda event: event.starts_at)


def _quoted(value: str) -> str:
    """PostgREST 的 in.(…) 裡的一個值：加雙引號，裡面的反斜線與雙引號前加反斜線（值裡可能有逗號或括號）"""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


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

    def select_all(
        self, table: str, access_token: str, columns: str, filters: Optional[Dict[str, str]] = None,
        within: Optional[Tuple[str, List[str]]] = None, order: str = "id.asc",
    ) -> List[Dict[str, Any]]:
        """以使用者身分讀資料表裡符合篩選的所有列：依 order 分頁讀到回空為止，不受 HuaXia 一次最多回傳幾列限制

        Args:
            table: 資料表名稱
            access_token: 使用者的 access token
            columns: 要讀的欄位，以逗號分隔
            filters: 欄位名稱對應 PostgREST 的篩選（例如 "eq.false"）
            within: （欄位名稱，值）：只讀這個欄位是其中一個值的列；值是空的時不送請求、回傳空的
            order: 分頁依的排序（PostgREST 的 order），要能唯一決定每一列的先後；預設依 id

        Returns:
            讀到的列（照 HuaXia 的 RLS，只有看得到的）

        Raises:
            HuaXiaError: 連不上或讀取失敗
        """
        if within is None:
            batches = [dict(filters or {})]
        else:
            column, values = within
            batches = [
                {**(filters or {}), column: f"in.({','.join(values[i:i + _IN_FILTER_CHUNK])})"}
                for i in range(0, len(values), _IN_FILTER_CHUNK)
            ]
        rows: List[Dict[str, Any]] = []
        for batch in batches:
            offset = 0
            while True:
                page = self._call("GET", f"/rest/v1/{table}", access_token, params={
                    "select": columns, "order": order, "offset": str(offset), "limit": str(_TABLE_PAGE_SIZE),
                    **batch,
                })
                if not isinstance(page, list):
                    raise HuaXiaError(HuaXiaErrorKind.REJECTED, f"unexpected {table}: {page!r}")
                if not page:
                    break
                rows.extend(page)
                offset += len(page)
        return rows

    def publish_interface_version(self, access_token: str) -> Optional[int]:
        """HuaXia 發譜函式的介面版本；HuaXia 沒有發譜函式時為 None

        Raises:
            HuaXiaError: 連不上、呼叫失敗或回傳的不是整數
        """
        try:
            version = self.rpc(PUBLISH_VERSION_FUNCTION, access_token)
        except HuaXiaError as e:
            if e.kind is HuaXiaErrorKind.MISSING_FUNCTION:
                return None
            raise
        if isinstance(version, bool) or not isinstance(version, int):
            raise HuaXiaError(HuaXiaErrorKind.REJECTED, f"unexpected publish version: {version!r}")
        return version

    def publish(self, access_token: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """以使用者身分呼叫 HuaXia 的發譜函式，切換一首曲目：整次呼叫的寫入全成或全不成

        Args:
            access_token: 使用者的 access token
            payload: 這首曲目的切換內容，形狀照 docs/huaxia/LINGLING.md 的「輸入」（含 version）

        Returns:
            發譜函式的輸出（docs/huaxia/LINGLING.md 的「輸出」：新建的聲部、登記與實際撤掉的樂譜、新掛上的場次）

        Raises:
            HuaXiaError: 連不上或呼叫失敗；code 是 HuaXia 回的錯誤代碼（例如版本不符的 PT409）
        """
        result = self.rpc(PUBLISH_FUNCTION, access_token, {"payload": payload})
        if not isinstance(result, dict):
            raise HuaXiaError(HuaXiaErrorKind.REJECTED, f"unexpected publish result: {result!r}")
        return result

    def pieces(self, access_token: str) -> List[HuaXiaPiece]:
        """沒封存的曲目，依曲名排序（同 HuaXia 網頁的 order(title)；照 HuaXia 的 RLS，只回看得到的）

        Raises:
            HuaXiaError: 連不上或讀取失敗
        """
        rows = self.select_all(
            "pieces", access_token, "id,title,composer,duration_min,ensemble", filters={"archived": "eq.false"},
        )
        try:
            pieces = [
                HuaXiaPiece(
                    row["id"], row["title"], row.get("composer") or "",
                    float(row["duration_min"]) if row.get("duration_min") is not None else None,
                    HuaXiaEnsemble(row.get("ensemble") or HuaXiaEnsemble.TUTTI.value),
                )
                for row in rows
            ]
        except (KeyError, TypeError, ValueError) as e:
            raise HuaXiaError(HuaXiaErrorKind.REJECTED, f"unexpected pieces: {e}") from e
        return sorted(pieces, key=lambda piece: piece.title)

    def piece_parts(self, access_token: str, piece_ids: List[str]) -> List[HuaXiaPart]:
        """這幾首曲目的聲部（全部讀完），依曲目、排序與名稱（同 HuaXia 網頁的 order(sort)）

        Raises:
            HuaXiaError: 連不上或讀取失敗
        """
        rows = self.select_all(
            "piece_parts", access_token, "id,piece_id,name,needed,needed_min,section,sort",
            within=("piece_id", list(piece_ids)),
        )
        try:
            parts = [
                HuaXiaPart(
                    row["id"], row["piece_id"], row["name"], row.get("needed") or "", int(row.get("needed_min") or 0),
                    row.get("section") or "", int(row.get("sort") or 0),
                )
                for row in rows
            ]
        except (KeyError, TypeError, ValueError) as e:
            raise HuaXiaError(HuaXiaErrorKind.REJECTED, f"unexpected piece parts: {e}") from e
        return sorted(parts, key=lambda part: (part.piece_id, part.sort, part.name))

    def scores(self, access_token: str, piece_ids: List[str]) -> List[HuaXiaScore]:
        """這幾首曲目上登記的樂譜（全部讀完），依曲目與登記的先後

        Raises:
            HuaXiaError: 連不上或讀取失敗
        """
        rows = self.select_all(
            "scores", access_token, "id,piece_id,part_id,title,file_path,section,created_at",
            within=("piece_id", list(piece_ids)),
        )
        try:
            ordered = sorted(rows, key=lambda row: (row["piece_id"], row.get("created_at") or ""))
            return [
                HuaXiaScore(
                    row["id"], row["piece_id"], row.get("part_id"), row.get("title") or "",
                    row.get("file_path") or "", row.get("section") or "",
                )
                for row in ordered
            ]
        except (KeyError, TypeError) as e:
            raise HuaXiaError(HuaXiaErrorKind.REJECTED, f"unexpected scores: {e}") from e

    def current_semester(self, access_token: str) -> Optional[str]:
        """本學期（semesters.is_current）的編號；沒有設定本學期時為 None

        Raises:
            HuaXiaError: 連不上或讀取失敗
        """
        rows = self.select("semesters", access_token, "id", is_current="true")
        try:
            return rows[0]["id"] if rows else None
        except (KeyError, TypeError) as e:
            raise HuaXiaError(HuaXiaErrorKind.REJECTED, f"unexpected semesters: {e}") from e

    def events(self, access_token: str, semester_id: str) -> List[HuaXiaEvent]:
        """這個學期看得到的場次，依開始時間

        Raises:
            HuaXiaError: 連不上或讀取失敗
        """
        return _events(self.select("events", access_token, _EVENT_COLUMNS, semester_id=semester_id))

    def events_by_id(self, access_token: str, event_ids: List[str]) -> List[HuaXiaEvent]:
        """這幾個場次（看得到的，不分學期；全部讀完），依開始時間

        Raises:
            HuaXiaError: 連不上或讀取失敗
        """
        return _events(self.select_all("events", access_token, _EVENT_COLUMNS, within=("id", list(event_ids))))

    def assigned_parts(self, access_token: str, part_ids: List[str]) -> Set[str]:
        """這幾個聲部裡已經排了人的（part_assignments 有它的列）

        Raises:
            HuaXiaError: 連不上或讀取失敗
        """
        rows = self.select_all("part_assignments", access_token, "id,part_id", within=("part_id", list(part_ids)))
        try:
            return {row["part_id"] for row in rows}
        except (KeyError, TypeError) as e:
            raise HuaXiaError(HuaXiaErrorKind.REJECTED, f"unexpected part assignments: {e}") from e

    def configured_parts(self, access_token: str, part_ids: List[str]) -> Set[str]:
        """這幾個聲部裡有人在網頁上設定過的：填了小老師（tutor_id）或備註（note），或有練習回報指著它
        （practice_reports.part_id；刪聲部時 HuaXia 會把它設成 null，回報就對不回聲部）

        Raises:
            HuaXiaError: 連不上或讀取失敗
        """
        parts = self.select_all("piece_parts", access_token, "id,tutor_id,note", within=("id", list(part_ids)))
        reports = self.select_all(
            "practice_reports", access_token, "id,part_id", within=("part_id", list(part_ids)),
        )
        try:
            return {row["id"] for row in parts if row.get("tutor_id") or (row.get("note") or "").strip()} | {
                row["part_id"] for row in reports
            }
        except (KeyError, TypeError) as e:
            raise HuaXiaError(HuaXiaErrorKind.REJECTED, f"unexpected configured parts: {e}") from e

    def delete_scores(self, access_token: str, score_ids: List[str]) -> List[str]:
        """以使用者身分撤掉這些樂譜的登記（刪 scores 的列，不刪 Storage 的檔）；已經不在或沒有權限的略過

        Returns:
            真的刪掉的樂譜編號

        Raises:
            HuaXiaError: 連不上或刪除失敗
        """
        return self._delete_ids("scores", access_token, score_ids)

    def delete_parts(self, access_token: str, part_ids: List[str]) -> List[str]:
        """以使用者身分刪掉這些聲部；HuaXia 會連帶刪掉它們的排人與樂譜（on delete cascade），呼叫前先確認沒排人

        Returns:
            真的刪掉的聲部編號（已經不在或沒有權限的略過）

        Raises:
            HuaXiaError: 連不上或刪除失敗
        """
        return self._delete_ids("piece_parts", access_token, part_ids)

    def _delete_ids(self, table: str, access_token: str, ids: List[str]) -> List[str]:
        """刪掉資料表裡編號是其中之一的列（照 RLS，看不到或不能刪的不刪），回傳真的刪掉的編號"""
        deleted: List[str] = []
        for i in range(0, len(ids), _IN_FILTER_CHUNK):
            rows = self._call(
                "DELETE", f"/rest/v1/{table}", access_token,
                params={"id": f"in.({','.join(ids[i:i + _IN_FILTER_CHUNK])})", "select": "id"},
                headers={"Prefer": "return=representation"},
            )
            try:
                deleted.extend(row["id"] for row in rows or [])
            except (KeyError, TypeError) as e:
                raise HuaXiaError(HuaXiaErrorKind.REJECTED, f"unexpected {table} deletion: {e}") from e
        return deleted

    def event_pieces(self, access_token: str, event_ids: List[str]) -> List[Tuple[str, str]]:
        """這幾個場次掛著的曲目（event_pieces，全部讀完）：（場次編號，曲目編號）

        Raises:
            HuaXiaError: 連不上或讀取失敗
        """
        return self._event_pieces(access_token, "event_id", event_ids)

    def piece_events(self, access_token: str, piece_ids: List[str]) -> List[Tuple[str, str]]:
        """這幾首曲目掛上的場次（event_pieces，全部讀完）：（場次編號，曲目編號）

        Raises:
            HuaXiaError: 連不上或讀取失敗
        """
        return self._event_pieces(access_token, "piece_id", piece_ids)

    def _event_pieces(self, access_token: str, column: str, ids: List[str]) -> List[Tuple[str, str]]:
        """event_pieces 裡這個欄位是其中一個值的列：（場次編號，曲目編號）"""
        rows = self.select_all(
            "event_pieces", access_token, "event_id,piece_id", within=(column, list(ids)),
            order="event_id.asc,piece_id.asc",
        )
        try:
            return [(row["event_id"], row["piece_id"]) for row in rows]
        except (KeyError, TypeError) as e:
            raise HuaXiaError(HuaXiaErrorKind.REJECTED, f"unexpected event pieces: {e}") from e

    def registered_paths(self, access_token: str, paths: List[str]) -> Set[str]:
        """這些 Storage 路徑裡還有樂譜登記指著的（任何曲目的 scores 列；照 RLS，幹部看得到全部）

        Raises:
            HuaXiaError: 連不上或讀取失敗
        """
        rows = self.select_all(
            "scores", access_token, "id,file_path", within=("file_path", [_quoted(path) for path in paths]),
        )
        try:
            return {row["file_path"] for row in rows}
        except (KeyError, TypeError) as e:
            raise HuaXiaError(HuaXiaErrorKind.REJECTED, f"unexpected scores: {e}") from e

    # --- Storage ---

    def stored_files(self, access_token: str, folder: str, page_size: int = _STORAGE_PAGE_SIZE) -> Dict[str, int]:
        """Storage bucket scores 裡某個資料夾底下一層的檔（不含子資料夾），分頁讀完

        Args:
            access_token: 使用者的 access token
            folder: 資料夾路徑，例如泠靈發到一首曲目的檔所在的「前綴／曲目編號」
            page_size: 一次要幾筆

        Returns:
            檔案的完整路徑對應大小（位元組）

        Raises:
            HuaXiaError: 連不上或讀取失敗
        """
        folder = folder.strip("/")
        found: Dict[str, int] = {}
        offset = 0
        while True:
            entries = self._call(
                "POST", f"/storage/v1/object/list/{_STORAGE_BUCKET}", access_token,
                json={"prefix": folder, "limit": page_size, "offset": offset, "sortBy": {"column": "name"}},
            )
            try:
                for entry in entries:
                    if entry.get("id") is not None:
                        found[f"{folder}/{entry['name']}"] = int((entry.get("metadata") or {}).get("size") or 0)
            except (KeyError, TypeError, ValueError, AttributeError) as e:
                raise HuaXiaError(HuaXiaErrorKind.REJECTED, f"unexpected storage listing: {e}") from e
            if len(entries) < page_size:
                return found
            offset += page_size

    def upload(self, access_token: str, path: str, content: bytes) -> bool:
        """把一份 PDF 傳到 Storage bucket scores 的 path，不覆寫已有的檔

        泠靈的 Storage 路徑由內容決定，path 已經有檔就是同一份內容：不算失敗。

        Args:
            access_token: 使用者的 access token
            path: Storage 裡的路徑（lingling_storage_path）
            content: 檔案內容

        Returns:
            這次傳上去了為 True；path 已經有檔時為 False

        Raises:
            HuaXiaError: 連不上或上傳失敗（例如不是幹部）
        """
        try:
            self._call(
                "POST", f"/storage/v1/object/{_STORAGE_BUCKET}/{path}", access_token, data=content,
                headers={"Content-Type": _PDF_MIME, "x-upsert": "false"},
            )
        except HuaXiaError as e:
            if e.status == 409 or e.code == _STORAGE_DUPLICATE:
                return False
            raise
        return True

    def delete_files(self, access_token: str, paths: List[str]) -> None:
        """刪掉 Storage bucket scores 裡的這些檔（一個請求）；已經沒有的略過

        Raises:
            HuaXiaError: 連不上或刪除失敗
        """
        self._call("DELETE", f"/storage/v1/object/{_STORAGE_BUCKET}", access_token, json={"prefixes": list(paths)})

    # --- 共用 ---

    def _call(
        self, method: str, path: str, access_token: Optional[str] = None,
        params: Optional[Dict[str, str]] = None, json: Any = None, data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Any:
        """送出一個請求，回傳 JSON 內容（沒有內容時為 None）；失敗時丟出帶類別的 HuaXiaError

        本體是 JSON 時給 json；上傳檔案時給 data（原始位元組）與它的 Content-Type（headers）。
        """
        headers = {**(headers or {}), "apikey": self._key}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        body = {"json": json} if data is None else {"data": data}
        try:
            response = self._session.request(
                method, self._url + path, params=params, headers=headers, timeout=_REQUEST_TIMEOUT, **body,
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
    """以錯誤回應建立 HuaXiaError：Auth 回報續期權杖或 session 已不存在時類別為 SESSION_GONE，
    PostgREST 找不到資料庫函式時為 MISSING_FUNCTION"""
    body = payload if isinstance(payload, dict) else {}
    code = body.get("error_code") or body.get("code") or body.get("error") or ""
    message = body.get("msg") or body.get("message") or body.get("error_description") or ""
    # 舊版 Auth 以 OAuth 的 invalid_grant 回報續期權杖不存在
    gone = code in _SESSION_GONE_CODES or (code == "invalid_grant" and "Refresh Token" in message)
    if gone:
        kind = HuaXiaErrorKind.SESSION_GONE
    elif code == _MISSING_FUNCTION_CODE:
        kind = HuaXiaErrorKind.MISSING_FUNCTION
    else:
        kind = HuaXiaErrorKind.REJECTED
    return HuaXiaError(kind, f"HTTP {status} {code}: {message}".strip(), code=str(code), status=status)
