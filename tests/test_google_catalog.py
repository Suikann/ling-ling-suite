# -*- coding: utf-8 -*-
"""
譜庫通事的測試：Google 程式庫丟出的真的例外物件轉成六種錯誤類別
"""
import json
import os
import socket
import sys
import unittest

import httplib2
from google.auth import exceptions as auth_exceptions
from googleapiclient.errors import HttpError

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from core.catalog_errors import CatalogErrorKind
from services.google_catalog import catalog_error_from


def http_error(status: int, message: str = "error", reason: str = "", rpc_reason: str = "") -> HttpError:
    """建出 Google API 回應狀態碼為 status 的 HttpError

    Args:
        status: HTTP 狀態碼
        message: 錯誤訊息
        reason: Drive 舊式錯誤清單（errors）裡的 reason
        rpc_reason: Sheets 新式錯誤詳情（details）裡的 reason
    """
    response = httplib2.Response({"status": status})
    response.reason = "reason"
    error = {"code": status, "message": message}
    if reason:
        error["errors"] = [{"reason": reason, "domain": "usageLimits", "message": message}]
    if rpc_reason:
        error["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": rpc_reason}]
    return HttpError(response, json.dumps({"error": error}).encode(), uri="https://www.googleapis.com/x")


class TestErrorKinds(unittest.TestCase):
    """Google 的例外依 spec 的表轉成錯誤類別"""

    def test_google_exceptions_map_to_the_six_kinds(self):
        cases = [
            (http_error(401, "Request had invalid authentication credentials."), CatalogErrorKind.SIGN_IN),
            (auth_exceptions.RefreshError("invalid_grant: Token has been expired or revoked."),
             CatalogErrorKind.SIGN_IN),
            (http_error(403, "The caller does not have permission"), CatalogErrorKind.NO_PERMISSION),
            (http_error(403, "Insufficient permissions", reason="insufficientFilePermissions"),
             CatalogErrorKind.NO_PERMISSION),
            (http_error(404, "Requested entity was not found."), CatalogErrorKind.NOT_FOUND),
            (http_error(429, "Quota exceeded for quota metric 'Write requests'", rpc_reason="RATE_LIMIT_EXCEEDED"),
             CatalogErrorKind.RATE_LIMITED),
            (http_error(403, "Rate Limit Exceeded", reason="rateLimitExceeded"), CatalogErrorKind.RATE_LIMITED),
            (http_error(403, "User Rate Limit Exceeded", reason="userRateLimitExceeded"),
             CatalogErrorKind.RATE_LIMITED),
            (http_error(403, "Quota exceeded", reason="quotaExceeded"), CatalogErrorKind.RATE_LIMITED),
            (http_error(403, "Daily Limit Exceeded", reason="dailyLimitExceeded"), CatalogErrorKind.RATE_LIMITED),
            (http_error(500, "Internal error encountered."), CatalogErrorKind.OTHER),
            (http_error(400, "Unable to parse range: '社團設定'!A:B"), CatalogErrorKind.OTHER),
            (httplib2.error.ServerNotFoundError("Unable to find the server at www.googleapis.com"),
             CatalogErrorKind.OFFLINE),
            (TimeoutError("timed out"), CatalogErrorKind.OFFLINE),
            (socket.timeout("timed out"), CatalogErrorKind.OFFLINE),
            (ConnectionRefusedError(111, "Connection refused"), CatalogErrorKind.OFFLINE),
            (ConnectionResetError(104, "Connection reset by peer"), CatalogErrorKind.OFFLINE),
            (auth_exceptions.TransportError("Failed to establish a new connection"), CatalogErrorKind.OFFLINE),
            (ValueError("unexpected"), CatalogErrorKind.OTHER),
        ]
        for error, kind in cases:
            with self.subTest(error=repr(error)):
                self.assertEqual(catalog_error_from(error).kind, kind)

    def test_the_original_message_is_kept_for_the_details(self):
        error = catalog_error_from(http_error(404, "Requested entity was not found."))
        self.assertIn("Requested entity was not found.", error.message)
        self.assertIn("404", error.message)


if __name__ == '__main__':
    unittest.main()
