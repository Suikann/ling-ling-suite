# -*- coding: utf-8 -*-
"""
譜庫的通事：接 Google 的試算表與 Drive

很薄，只做兩件事：把關口（services/catalog_ports.py）的呼叫轉成 Google API 請求，
把 Google 丟出的例外轉成六種錯誤類別（CatalogError）。全程式只有這裡匯入 googleapiclient，
而且是在第一次用到時才匯入：只匯入譜庫服務與關口的模組不會載入 Google 程式庫。

同一個通事物件可能被幾個背景執行緒同時使用，而 httplib2 不能多執行緒共用，所以每個通事一次只送一個請求。

使用範例：
    from services.google_catalog import open_google_catalog, catalog_error_from
    catalog = open_google_catalog(auth, spreadsheet_id)   # 拿不到憑證時丟出 CatalogError（要在背景執行緒呼叫）
    error = catalog_error_from(exception)                 # CatalogError，kind 為六種類別之一
"""
import json
import threading
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Sequence, Tuple

from core.catalog_errors import CatalogError, CatalogErrorKind
from core.catalog_models import DriveFile, DriveListing
from services.catalog_ports import CellWrite, sheet_range
from services.catalog_service import CatalogService
from services.google_auth_service import GoogleAuthService, LoginReason

if TYPE_CHECKING:
    from google.oauth2.credentials import Credentials

# 403 裡算「達到用量上限」的 reason（去掉底線、轉小寫後比對；Sheets 寫成 RATE_LIMIT_EXCEEDED）
_USAGE_LIMIT_REASONS = {"ratelimitexceeded", "userratelimitexceeded", "quotaexceeded", "dailylimitexceeded"}
# HTTP 狀態碼對應的類別；403 另看 reason，其餘狀態碼為其他
_STATUS_KINDS = {
    401: CatalogErrorKind.SIGN_IN,
    403: CatalogErrorKind.NO_PERMISSION,
    404: CatalogErrorKind.NOT_FOUND,
    429: CatalogErrorKind.RATE_LIMITED,
}
# Drive 列出檔案時一頁最多幾筆（API 上限 1000）
_DRIVE_PAGE_SIZE = 1000
_DRIVE_FILE_FIELDS = "nextPageToken, files(id, name, mimeType, parents, md5Checksum)"


def open_google_catalog(auth: GoogleAuthService, spreadsheet_id: str) -> CatalogService:
    """以登入服務的憑證建立接 Google 的譜庫服務（會續期憑證，要在背景執行緒呼叫）

    Args:
        auth: Google 登入服務
        spreadsheet_id: 譜庫試算表的 ID

    Returns:
        接上 Google 試算表與 Drive 的譜庫服務

    Raises:
        CatalogError: 沒設定試算表 ID 時為找不到；拿不到憑證時，連不上 Google 為斷網，其餘（沒登入、
            安全儲存沒解鎖、Google 不認這個權杖）為要重新登入
    """
    if not spreadsheet_id:
        raise CatalogError(CatalogErrorKind.NOT_FOUND, "catalog_spreadsheet_id is empty")
    credentials = auth.get_credentials()
    if credentials is None:
        reason = auth.status().reason
        kind = CatalogErrorKind.OFFLINE if reason is LoginReason.UNREACHABLE else CatalogErrorKind.SIGN_IN
        raise CatalogError(kind, f"no Google credentials: {reason.value if reason else 'not signed in'}")
    return CatalogService(GoogleSheets(spreadsheet_id, credentials), GoogleDrive(credentials))


class _Gateway:
    """通事的共用部分：建出 Google API 的用戶端，一次送一個請求，例外轉成 CatalogError"""

    def __init__(self, api: str, version: str, credentials: Optional["Credentials"], http: Any):
        """
        Args:
            api: API 名稱（"sheets"、"drive"）
            version: API 版本
            credentials: Google 憑證；給 http 時省略
            http: 代替網路的 httplib2.Http（測試用）；省略時以 credentials 建立
        """
        from googleapiclient.discovery import build
        options = {"http": http} if http is not None else {"credentials": credentials}
        self._api = build(api, version, cache_discovery=False, static_discovery=True, **options)
        self._lock = threading.Lock()

    def _execute(self, request) -> dict:
        """送出一個請求，回傳回應內容；失敗時丟出 CatalogError"""
        with self._lock:
            try:
                return request.execute() or {}
            except Exception as e:
                raise catalog_error_from(e) from e


class GoogleSheets(_Gateway):
    """試算表關口的通事：一份 Google 試算表"""

    def __init__(self, spreadsheet_id: str, credentials: Optional["Credentials"] = None, http: Any = None):
        """
        Args:
            spreadsheet_id: 譜庫試算表的 ID
            credentials: Google 憑證
            http: 代替網路的 httplib2.Http（測試用）
        """
        super().__init__("sheets", "v4", credentials, http)
        self._spreadsheet_id = spreadsheet_id

    def read_ranges(self, ranges: Sequence[str]) -> List[List[List[str]]]:
        """讀多個範圍（values.batchGet）"""
        response = self._execute(self._api.spreadsheets().values().batchGet(
            spreadsheetId=self._spreadsheet_id, ranges=list(ranges),
        ))
        return [
            [[str(value) for value in row] for row in value_range.get("values", [])]
            for value_range in response.get("valueRanges", [])
        ]

    def write_cells(self, data: Sequence[CellWrite]) -> None:
        """一次寫入多個範圍（values.batchUpdate），值以 RAW 寫入"""
        self._execute(self._api.spreadsheets().values().batchUpdate(
            spreadsheetId=self._spreadsheet_id,
            body={"valueInputOption": "RAW", "data": [{"range": r, "values": v} for r, v in data]},
        ))

    def append_rows(self, sheet: str, rows: Sequence[List[str]]) -> None:
        """在工作表最後一列之後新增多列（values.append），值以 RAW 寫入"""
        self._execute(self._api.spreadsheets().values().append(
            spreadsheetId=self._spreadsheet_id, range=sheet_range(sheet, "A:A"),
            valueInputOption="RAW", insertDataOption="INSERT_ROWS", body={"values": [list(row) for row in rows]},
        ))

    def add_sheet(self, title: str) -> None:
        """建立一張空的工作表"""
        self._execute(self._api.spreadsheets().batchUpdate(
            spreadsheetId=self._spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": title}}}]},
        ))

    def sheet_titles(self) -> List[str]:
        """列出所有工作表的名稱（spreadsheets.get，只取名稱）"""
        response = self._execute(self._api.spreadsheets().get(
            spreadsheetId=self._spreadsheet_id, fields="sheets.properties.title",
        ))
        return [sheet.get("properties", {}).get("title", "") for sheet in response.get("sheets", [])]

    def sheet_ids(self) -> Dict[str, int]:
        """每張工作表的名稱與 sheetId（spreadsheets.get）"""
        response = self._execute(self._api.spreadsheets().get(
            spreadsheetId=self._spreadsheet_id, fields="sheets.properties(sheetId,title)",
        ))
        return {
            sheet["properties"]["title"]: sheet["properties"]["sheetId"] for sheet in response.get("sheets", [])
        }

    def delete_rows(self, rows: Sequence[Tuple[int, int]]) -> None:
        """一次刪掉多列（batchUpdate）：由下往上刪，列號都以刪除之前的為準"""
        self._execute(self._api.spreadsheets().batchUpdate(
            spreadsheetId=self._spreadsheet_id,
            body={"requests": [
                {"deleteDimension": {"range": {
                    "sheetId": sheet_id, "dimension": "ROWS", "startIndex": row, "endIndex": row + 1,
                }}}
                for sheet_id, row in sorted(set(rows), key=lambda target: -target[1])
            ]},
        ))


class GoogleDrive(_Gateway):
    """Drive 關口的通事"""

    def __init__(self, credentials: Optional["Credentials"] = None, http: Any = None):
        """
        Args:
            credentials: Google 憑證
            http: 代替網路的 httplib2.Http（測試用）
        """
        super().__init__("drive", "v3", credentials, http)

    def list_files(self, query: str, page_token: str = "") -> DriveListing:
        """以查詢字串列出檔案的一頁（files.list，依名稱排序）"""
        response = self._execute(self._api.files().list(
            q=query, fields=_DRIVE_FILE_FIELDS, orderBy="name", pageSize=_DRIVE_PAGE_SIZE,
            pageToken=page_token or None,
        ))
        files = [
            DriveFile(
                id=item["id"], name=item.get("name", ""), mime_type=item.get("mimeType", ""),
                parents=tuple(item.get("parents", [])), md5=item.get("md5Checksum", ""),
            )
            for item in response.get("files", [])
        ]
        return DriveListing(files, response.get("nextPageToken", ""))


def catalog_error_from(error: BaseException) -> CatalogError:
    """把 Google 程式庫或傳輸層丟出的例外轉成譜庫錯誤

    401、續期被拒（invalid_grant）為要重新登入；403 為沒有權限，用量類的 403 與 429 為達到用量上限；
    404 為找不到；連不上、逾時為斷網；其餘為其他。

    Args:
        error: 呼叫 Google 時丟出的例外

    Returns:
        帶類別與原始訊息的譜庫錯誤
    """
    if isinstance(error, CatalogError):
        return error
    message = f"{type(error).__name__}: {error}"
    return CatalogError(_kind_of(error), message)


def _kind_of(error: BaseException) -> CatalogErrorKind:
    """例外的錯誤類別"""
    from google.auth import exceptions as auth_exceptions
    from googleapiclient.errors import HttpError
    import httplib2
    if isinstance(error, HttpError):
        status = error.resp.status
        if status == 403 and _reasons(error.content) & _USAGE_LIMIT_REASONS:
            return CatalogErrorKind.RATE_LIMITED
        return _STATUS_KINDS.get(status, CatalogErrorKind.OTHER)
    if isinstance(error, auth_exceptions.RefreshError):
        # 可重試的續期失敗是權杖端點暫時出錯，不是權杖被拒
        return CatalogErrorKind.OTHER if error.retryable else CatalogErrorKind.SIGN_IN
    if isinstance(error, (auth_exceptions.TransportError, httplib2.ServerNotFoundError, OSError)):
        return CatalogErrorKind.OFFLINE
    return CatalogErrorKind.OTHER


def _reasons(content: bytes) -> set:
    """Google 錯誤回應裡所有的 reason（去掉底線、轉小寫）；不是 JSON 時為空集合"""
    try:
        error = json.loads(content.decode("utf-8"))["error"]
    except (ValueError, KeyError, TypeError, AttributeError):
        return set()
    entries = list(error.get("errors") or []) + list(error.get("details") or [])
    return {
        str(entry.get("reason", "")).replace("_", "").lower()
        for entry in entries if isinstance(entry, dict)
    }
