# -*- coding: utf-8 -*-
"""
譜庫通事的測試

Google 程式庫丟出的真的例外物件轉成六種錯誤類別；關口的呼叫轉成的 Google API 請求（以 googleapiclient 的
HttpMockSequence 代替網路）；拿不到憑證時依登入服務回報的原因分類。
"""
import json
import os
import shutil
import socket
import sys
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse

import httplib2
from google.auth import exceptions as auth_exceptions
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpMockSequence

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from core.catalog_constants import DRIVE_FOLDER_MIME
from core.catalog_errors import CatalogError, CatalogErrorKind
from services.catalog_service import CatalogService
from services.google_auth_service import GoogleAuthService
from services.google_catalog import (
    GoogleDrive, GoogleSheets, catalog_error_from, open_google_catalog, open_google_drive,
)
from services.secure_storage import SecureStorage
from tests.fake_browser import CallbackBrowser
from tests.fake_google import FakeTokenEndpoint, write_client_secrets
from tests.memory_keyring import MemoryKeyring


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


def ok(payload: dict) -> tuple:
    """HttpMockSequence 的一個 200 回應"""
    return {"status": "200"}, json.dumps(payload)


def sent_body(http: HttpMockSequence, index: int) -> dict:
    """第 index 個請求送出的 JSON 內容"""
    return json.loads(http.request_sequence[index][2])


def sent_query(http: HttpMockSequence, index: int) -> dict:
    """第 index 個請求網址的 query 參數"""
    return parse_qs(urlparse(http.request_sequence[index][0]).query)


class TestSheetsRequests(unittest.TestCase):
    """試算表關口一次呼叫一次請求，值以 RAW 寫入"""

    def test_reads_several_ranges_in_one_batch_get(self):
        http = HttpMockSequence([ok({"valueRanges": [{"values": [["id"], ["p1"]]}, {}]})])
        values = GoogleSheets("sheet-1", http=http).read_ranges(["'曲目'!A:ZZ", "'版本'!A:ZZ"])
        self.assertEqual(values, [[["id"], ["p1"]], []])
        self.assertEqual(len(http.request_sequence), 1)
        self.assertIn("values:batchGet", http.request_sequence[0][0])
        self.assertEqual(sent_query(http, 0)["ranges"], ["'曲目'!A:ZZ", "'版本'!A:ZZ"])

    def test_writes_and_appends_values_as_raw_text(self):
        http = HttpMockSequence([ok({}), ok({})])
        sheets = GoogleSheets("sheet-1", http=http)
        sheets.write_cells([("'曲目'!C2", [["=1+1"]])])
        sheets.append_rows("曲目", [["p1", "", "=SUM(A1)"]])
        self.assertEqual(sent_body(http, 0)["valueInputOption"], "RAW")
        self.assertEqual(sent_body(http, 0)["data"], [{"range": "'曲目'!C2", "values": [["=1+1"]]}])
        self.assertEqual(sent_query(http, 1)["valueInputOption"], ["RAW"])
        self.assertEqual(sent_body(http, 1)["values"], [["p1", "", "=SUM(A1)"]])

    def test_adds_a_sheet(self):
        http = HttpMockSequence([ok({})])
        GoogleSheets("sheet-1", http=http).add_sheet("社團設定")
        self.assertEqual(sent_body(http, 0)["requests"], [{"addSheet": {"properties": {"title": "社團設定"}}}])

    def test_lists_sheet_titles_with_one_spreadsheets_get(self):
        http = HttpMockSequence([ok({"sheets": [{"properties": {"title": "曲目"}}, {"properties": {"title": "社團設定"}}]})])
        titles = GoogleSheets("sheet-1", http=http).sheet_titles()
        self.assertEqual(titles, ["曲目", "社團設定"])
        self.assertEqual(len(http.request_sequence), 1)
        self.assertIn("/spreadsheets/sheet-1?", http.request_sequence[0][0])
        self.assertEqual(sent_query(http, 0)["fields"], ["sheets.properties.title"])

    def test_lists_sheet_ids(self):
        http = HttpMockSequence([ok({"sheets": [
            {"properties": {"sheetId": 0, "title": "曲目"}}, {"properties": {"sheetId": 7, "title": "版本"}},
        ]})])
        self.assertEqual(GoogleSheets("sheet-1", http=http).sheet_ids(), {"曲目": 0, "版本": 7})
        self.assertEqual(sent_query(http, 0)["fields"], ["sheets.properties(sheetId,title)"])

    def test_deletes_rows_bottom_up_in_one_batch_update(self):
        http = HttpMockSequence([ok({})])
        GoogleSheets("sheet-1", http=http).delete_rows([(0, 2), (7, 1), (0, 5)])
        starts = [
            (r["deleteDimension"]["range"]["sheetId"], r["deleteDimension"]["range"]["startIndex"],
             r["deleteDimension"]["range"]["endIndex"])
            for r in sent_body(http, 0)["requests"]
        ]
        self.assertEqual(starts, [(0, 5, 6), (0, 2, 3), (7, 1, 2)])
        self.assertEqual(len(http.request_sequence), 1)

    def test_creates_a_spreadsheet_with_header_rows_frozen_in_one_request(self):
        http = HttpMockSequence([ok({"spreadsheetId": "new-1"})])
        new_id = GoogleSheets("", http=http).create_spreadsheet("譜庫目錄", {"曲目": ["id", "title"], "版本": ["id"]})
        self.assertEqual(new_id, "new-1")
        self.assertEqual(len(http.request_sequence), 1)
        body = sent_body(http, 0)
        self.assertEqual(body["properties"]["title"], "譜庫目錄")
        sheets = [
            (s["properties"]["title"], s["properties"]["gridProperties"]["frozenRowCount"],
             [v["userEnteredValue"]["stringValue"] for v in s["data"][0]["rowData"][0]["values"]])
            for s in body["sheets"]
        ]
        self.assertEqual(sheets, [("曲目", 1, ["id", "title"]), ("版本", 1, ["id"])])

    def test_without_a_spreadsheet_id_reading_is_not_found_without_a_request(self):
        http = HttpMockSequence([])
        with self.assertRaises(CatalogError) as raised:
            GoogleSheets("", http=http).read_ranges(["'曲目'!A:ZZ"])
        self.assertEqual(raised.exception.kind, CatalogErrorKind.NOT_FOUND)
        self.assertEqual(http.request_sequence, [])

    def test_without_a_spreadsheet_id_listing_sheet_titles_is_not_found_without_a_request(self):
        http = HttpMockSequence([])
        with self.assertRaises(CatalogError) as raised:
            GoogleSheets("", http=http).sheet_titles()
        self.assertEqual(raised.exception.kind, CatalogErrorKind.NOT_FOUND)
        self.assertEqual(http.request_sequence, [])

    def test_google_errors_become_catalog_errors(self):
        http = HttpMockSequence([({"status": "404"}, json.dumps({"error": {"code": 404, "message": "not found"}}))])
        with self.assertRaises(CatalogError) as raised:
            GoogleSheets("sheet-1", http=http).read_ranges(["'曲目'!A:ZZ"])
        self.assertEqual(raised.exception.kind, CatalogErrorKind.NOT_FOUND)


class TestDriveRequests(unittest.TestCase):
    """Drive 關口照呼叫端給的查詢字串列出一頁"""

    def test_lists_a_page_with_the_query_as_given(self):
        http = HttpMockSequence([ok({
            "nextPageToken": "next",
            "files": [{"id": "f1", "name": "a.pdf", "mimeType": "application/pdf", "parents": ["d1"],
                       "md5Checksum": "abc"}],
        })])
        query = "'d1' in parents and name = 'O\\'Neil.pdf' and trashed = false"
        listing = GoogleDrive(http=http).list_files(query, "token")
        self.assertEqual(sent_query(http, 0)["q"], [query])
        self.assertEqual(sent_query(http, 0)["pageToken"], ["token"])
        self.assertEqual(listing.next_page_token, "next")
        file = listing.files[0]
        self.assertEqual((file.id, file.name, file.mime_type, file.parents, file.md5),
                         ("f1", "a.pdf", "application/pdf", ("d1",), "abc"))


class TestDriveWriteRequests(unittest.TestCase):
    """Drive 關口的中繼資料、建資料夾、改名與搬移、丟進垃圾桶，各一次請求"""

    FILE = {"id": "f1", "name": "a.pdf", "mimeType": "application/pdf", "parents": ["d1"], "md5Checksum": "abc"}

    def test_gets_file_metadata(self):
        http = HttpMockSequence([ok(self.FILE)])
        file = GoogleDrive(http=http).get_file("f1")
        self.assertEqual((file.id, file.parents, file.md5), ("f1", ("d1",), "abc"))
        self.assertIn("/files/f1", http.request_sequence[0][0])

    def test_creates_a_folder_in_the_parent(self):
        http = HttpMockSequence([ok({"id": "d2", "name": "新", "mimeType": DRIVE_FOLDER_MIME, "parents": ["d1"]})])
        folder = GoogleDrive(http=http).create_folder("新", "d1")
        self.assertEqual(folder.id, "d2")
        self.assertEqual(sent_body(http, 0), {"name": "新", "mimeType": DRIVE_FOLDER_MIME, "parents": ["d1"]})

    def test_renames_and_moves_in_one_update(self):
        http = HttpMockSequence([ok(self.FILE), ok(self.FILE)])
        drive = GoogleDrive(http=http)
        drive.rename_or_move("f1", name="b.pdf")
        drive.rename_or_move("f1", add_parent="d2", remove_parents=["d1", "d3"])
        self.assertEqual(sent_body(http, 0), {"name": "b.pdf"})
        self.assertNotIn("addParents", sent_query(http, 0))
        self.assertEqual(sent_query(http, 1)["addParents"], ["d2"])
        self.assertEqual(sent_query(http, 1)["removeParents"], ["d1,d3"])
        self.assertEqual(len(http.request_sequence), 2)

    def test_moves_a_file_to_the_trash_in_one_update(self):
        http = HttpMockSequence([ok(self.FILE)])
        GoogleDrive(http=http).move_to_trash("f1")
        self.assertEqual(sent_body(http, 0), {"trashed": True})
        self.assertIn("/files/f1", http.request_sequence[0][0])
        self.assertEqual(http.request_sequence[0][1], "PATCH")


class TestOpenGoogleCatalog(unittest.TestCase):
    """拿不到憑證或沒設定試算表時，依原因分類"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.keyring = MemoryKeyring()
        self.endpoint = FakeTokenEndpoint()
        self.browser = CallbackBrowser()
        self.addCleanup(self.browser.finish)

    def auth(self) -> GoogleAuthService:
        """建立登入服務（每次都是新的，如同重開程式），替身與同一份安全儲存"""
        return GoogleAuthService(
            storage=SecureStorage("test-google", backend=self.keyring),
            request=self.endpoint,
            opener=self.browser,
            client_secrets_path=write_client_secrets(os.path.join(self.temp_dir, "client_secret.json")),
            legacy_token_path=os.path.join(self.temp_dir, "google_token.json"),
        )

    def kind_when_opening(self, spreadsheet_id: str = "sheet-1") -> CatalogErrorKind:
        """開啟譜庫時丟出的錯誤類別"""
        with self.assertRaises(CatalogError) as raised:
            open_google_catalog(self.auth(), spreadsheet_id)
        return raised.exception.kind

    def test_a_token_google_no_longer_accepts_means_signing_in_again(self):
        self.auth().authenticate()
        self.endpoint.reject()
        self.assertEqual(self.kind_when_opening(), CatalogErrorKind.SIGN_IN)

    def test_unreachable_google_is_offline(self):
        self.auth().authenticate()
        self.endpoint.go_offline()
        self.assertEqual(self.kind_when_opening(), CatalogErrorKind.OFFLINE)

    def test_never_signed_in_means_signing_in(self):
        self.assertEqual(self.kind_when_opening(), CatalogErrorKind.SIGN_IN)

    def test_no_spreadsheet_id_is_not_found(self):
        self.auth().authenticate()
        self.assertEqual(self.kind_when_opening(""), CatalogErrorKind.NOT_FOUND)

    def test_without_a_catalog_yet_opening_drive_still_asks_to_sign_in(self):
        with self.assertRaises(CatalogError) as raised:
            open_google_drive(self.auth())
        self.assertEqual(raised.exception.kind, CatalogErrorKind.SIGN_IN)

    def test_signed_in_opens_a_catalog_service_without_a_spreadsheet(self):
        self.auth().authenticate()
        self.endpoint.grant("access-2")
        catalog = open_google_drive(self.auth())
        with self.assertRaises(CatalogError) as raised:
            catalog.piece_detail("p1")
        self.assertEqual(raised.exception.kind, CatalogErrorKind.NOT_FOUND)

    def test_signed_in_opens_a_catalog_service(self):
        self.auth().authenticate()
        self.endpoint.grant("access-2")
        self.assertIsInstance(open_google_catalog(self.auth(), "sheet-1"), CatalogService)


if __name__ == '__main__':
    unittest.main()
