# -*- coding: utf-8 -*-
"""
紀錄檔測試

以臨時目錄與注入的檔案服務測：寫入的一筆含時間、動作、曲名、檔名、路徑、錯誤類別與原始訊息，
與提示列的詳細資料相同；不含權杖；超過保留天數的檔被刪；寫不進去時不影響呼叫端。
"""
import datetime
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.catalog_errors import CatalogError, CatalogErrorKind, CatalogFailure
from core.log_entry import LogEntry
from failing_writes import FailingWrites
from services.activity_log import ActivityLog
from services.file_service import FileService

NOW = datetime.datetime(2026, 10, 18, 21, 16, 5)


class ActivityLogTestCase(unittest.TestCase):
    """紀錄檔測試的共用骨架：紀錄檔資料夾在臨時目錄下，現在固定為 NOW"""

    def setUp(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, True)
        self.folder = os.path.join(temp_dir, "logs")
        self.file_service = FailingWrites()
        self.log = ActivityLog(self.file_service, self.folder, clock=lambda: NOW)

    def log_text(self) -> str:
        """紀錄檔資料夾裡所有檔的內容"""
        if not os.path.isdir(self.folder):
            return ""
        texts = []
        for name in sorted(os.listdir(self.folder)):
            with open(os.path.join(self.folder, name), encoding="utf-8") as f:
                texts.append(f.read())
        return "".join(texts)


class TestRecordsFailures(ActivityLogTestCase):
    """譜庫讀寫失敗寫成一筆，與提示列的詳細資料相同"""

    def failure(self, message: str = "HttpError 404 Requested entity was not found.") -> CatalogFailure:
        return CatalogFailure(
            CatalogErrorKind.NOT_FOUND, message, "存入譜庫",
            file_name="春江花月夜_中阮.pdf", piece_title="春江花月夜",
            path="/scores/春江花月夜/春江花月夜_中阮.pdf", time=NOW,
        )

    def test_a_failure_is_written_with_every_field(self):
        self.log.record(self.failure().log_entry())
        text = self.log_text()
        for field in (
            "2026-10-18 21:16:05", "存入譜庫", "春江花月夜", "春江花月夜_中阮.pdf",
            "/scores/春江花月夜/春江花月夜_中阮.pdf", "找不到譜庫", "HttpError 404 Requested entity was not found.",
        ):
            self.assertIn(field, text)

    def test_the_record_reads_the_same_as_the_banner_details(self):
        failure = self.failure()
        self.log.record(failure.log_entry())
        self.assertEqual(self.log_text().strip(), failure.detail_text())

    def test_records_of_one_day_go_to_the_same_file_in_order(self):
        self.log.record(LogEntry("讀取譜庫", time=NOW))
        self.log.record(LogEntry("存入譜庫", time=NOW + datetime.timedelta(minutes=1)))
        self.assertEqual(os.listdir(self.folder), ["2026-10-18.log"])
        text = self.log_text()
        self.assertLess(text.index("讀取譜庫"), text.index("存入譜庫"))

    def test_tokens_in_the_original_message_are_not_written(self):
        message = (
            "Authorization: Bearer ya29.a0AfB_secretAccess "
            "{'refresh_token': '1//0gSecretRefresh', 'access_token': 'ya29.other'} password=hunter2"
        )
        failure = self.failure(message)
        self.log.record(failure.log_entry())
        for secret in ("ya29", "secretAccess", "1//0gSecretRefresh", "SecretRefresh", "hunter2"):
            self.assertNotIn(secret, self.log_text())
            self.assertNotIn(secret, failure.detail_text())
        self.assertIn("refresh_token", self.log_text())

    def test_from_error_keeps_the_kind_and_original_message(self):
        failure = CatalogFailure.from_error(
            CatalogError(CatalogErrorKind.OFFLINE, "ServerNotFoundError"), "讀取譜庫", piece_title="春江花月夜",
        )
        self.log.record(failure.log_entry())
        text = self.log_text()
        self.assertIn("連不上網路", text)
        self.assertIn("ServerNotFoundError", text)
        self.assertIn("春江花月夜", text)

    def test_a_log_that_cannot_be_written_does_not_stop_the_caller(self):
        self.file_service.block(os.path.join(self.folder, "2026-10-18.log"))
        self.log.record(self.failure().log_entry())
        self.assertEqual(self.log_text(), "")


class TestRemovesExpiredLogs(ActivityLogTestCase):
    """保留 30 天：更早的紀錄檔被刪，其他檔不動"""

    def write(self, name: str) -> str:
        """在紀錄檔資料夾放一個檔"""
        os.makedirs(self.folder, exist_ok=True)
        path = os.path.join(self.folder, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("x")
        return path

    def test_logs_older_than_thirty_days_are_removed(self):
        expired = self.write("2026-09-17.log")
        oldest_kept = self.write("2026-09-18.log")
        today = self.write("2026-10-18.log")
        self.log.remove_expired()
        self.assertFalse(os.path.exists(expired))
        self.assertTrue(os.path.exists(oldest_kept))
        self.assertTrue(os.path.exists(today))

    def test_files_not_named_by_date_are_left_alone(self):
        other = self.write("notes.log")
        not_a_log = self.write("2020-01-01.txt")
        self.log.remove_expired()
        self.assertTrue(os.path.exists(other))
        self.assertTrue(os.path.exists(not_a_log))

    def test_a_missing_folder_is_not_an_error(self):
        self.log.remove_expired()
        self.assertFalse(os.path.exists(self.folder))


if __name__ == '__main__':
    unittest.main()
