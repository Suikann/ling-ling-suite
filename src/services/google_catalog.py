# -*- coding: utf-8 -*-
"""
譜庫的通事：接 Google 的試算表與 Drive

很薄，只做兩件事：把關口（services/catalog_ports.py）的呼叫轉成 Google API 請求，
把 Google 丟出的例外轉成六種錯誤類別（CatalogError）。全程式只有這裡匯入 googleapiclient，
而且是在第一次用到時才匯入：只匯入譜庫服務與關口的模組不會載入 Google 程式庫。

使用範例：
    from services.google_catalog import catalog_error_from
    error = catalog_error_from(exception)   # CatalogError，kind 為六種類別之一
"""
import json

from core.catalog_errors import CatalogError, CatalogErrorKind

# 403 裡算「達到用量上限」的 reason（去掉底線、轉小寫後比對；Sheets 寫成 RATE_LIMIT_EXCEEDED）
_USAGE_LIMIT_REASONS = {"ratelimitexceeded", "userratelimitexceeded", "quotaexceeded", "dailylimitexceeded"}
# HTTP 狀態碼對應的類別；403 另看 reason，其餘狀態碼為其他
_STATUS_KINDS = {
    401: CatalogErrorKind.SIGN_IN,
    403: CatalogErrorKind.NO_PERMISSION,
    404: CatalogErrorKind.NOT_FOUND,
    429: CatalogErrorKind.RATE_LIMITED,
}


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
