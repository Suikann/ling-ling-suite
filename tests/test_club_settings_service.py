# -*- coding: utf-8 -*-
"""
社團設定服務的測試：對假 Google 讀「發譜平台」，結果記在臨時目錄裡的偏好設定

「重開泠靈」以同一個偏好檔重新載入一份偏好設定代表；記住的結果只看偏好設定，不經過假 Google。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.catalog_constants import PublishingPlatform
from core.catalog_errors import CatalogError, CatalogErrorKind
from core.constants import CLUB_PUBLISHING_PLATFORM_KEY
from failing_writes import FailingWrites
from fake_google_catalog import FakeGoogle
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.club_settings_service import ClubSettingsService
from services.file_service import FileService
from services.preferences_service import PreferencesService
from test_catalog_service import empty_catalog


class ClubSettingsTestCase(unittest.TestCase):
    """假 Google 裡有一份譜庫總表；偏好設定在臨時目錄"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.path = os.path.join(self.temp_dir, "preferences.json")
        self.google = FakeGoogle()
        empty_catalog(self.google)
        self.catalog = CatalogService(self.google.sheets, self.google.drive, sleep=lambda seconds: None)
        self.club = ClubSettingsService(PreferencesService(self.path))

    def club_settings(self, *rows):
        """社團設定工作表寫成標題列加 rows"""
        self.google.sheets.create_tab("社團設定", [["鍵", "值"], *rows])

    def restarted(self) -> ClubSettingsService:
        """重開泠靈：以偏好檔重新載入"""
        preferences = PreferencesService(self.path)
        preferences.load()
        return ClubSettingsService(preferences)


class TestRemembered(ClubSettingsTestCase):
    """這台電腦記住的結果：從沒讀到過是隱藏，讀到之後重開仍在"""

    def test_never_read_is_hidden(self):
        self.assertIsNone(self.club.remembered())
        self.assertIsNone(self.restarted().remembered())

    def test_huaxia_is_remembered_across_restarts_without_reading_the_catalog(self):
        self.club_settings(["發譜平台", "華夏國樂社"])
        self.assertIs(self.club.read(self.catalog), PublishingPlatform.HUAXIA)
        requests = self.google.requests
        self.assertIs(self.club.remembered(), PublishingPlatform.HUAXIA)
        self.assertIs(self.restarted().remembered(), PublishingPlatform.HUAXIA)
        self.assertEqual(self.google.requests, requests)

    def test_without_the_sheet_nothing_is_enabled(self):
        google = FakeGoogle()
        google.sheets.create_tab("曲目", [["id"]])
        self.assertIsNone(self.club.read(CatalogService(google.sheets, google.drive, sleep=lambda seconds: None)))
        self.assertIsNone(self.restarted().remembered())

    def test_a_removed_row_is_reported_and_remembered(self):
        self.club_settings(["發譜平台", "華夏國樂社"])
        self.club.read(self.catalog)
        self.club_settings()
        self.assertIsNone(self.club.read(self.catalog))
        self.assertIsNone(self.club.remembered())
        self.assertIsNone(self.restarted().remembered())

    def test_still_reports_what_it_read_when_preferences_cannot_be_written(self):
        club = ClubSettingsService(PreferencesService(self.path, FailingWrites(self.path)))
        self.club_settings(["發譜平台", "華夏國樂社"])
        self.assertIs(club.read(self.catalog), PublishingPlatform.HUAXIA)
        self.assertIs(club.remembered(), PublishingPlatform.HUAXIA)

    def test_an_unknown_remembered_value_is_hidden(self):
        preferences = PreferencesService(self.path)
        preferences.set(CLUB_PUBLISHING_PLATFORM_KEY, "someday")
        self.assertIsNone(ClubSettingsService(preferences).remembered())


class TestUnreadable(ClubSettingsTestCase):
    """讀不到時丟出帶類別的錯誤，沿用記住的結果"""

    def test_keeps_the_remembered_result_and_reports_why(self):
        self.club_settings(["發譜平台", "華夏國樂社"])
        self.club.read(self.catalog)
        self.google.fail_next(CatalogErrorKind.OFFLINE)
        with self.assertRaises(CatalogError) as raised:
            self.club.read(self.catalog)
        self.assertEqual(raised.exception.kind, CatalogErrorKind.OFFLINE)
        self.assertIs(self.club.remembered(), PublishingPlatform.HUAXIA)
        self.assertIs(self.restarted().remembered(), PublishingPlatform.HUAXIA)

    def test_never_read_stays_hidden(self):
        self.google.fail_next(CatalogErrorKind.NO_PERMISSION)
        with self.assertRaises(CatalogError):
            self.club.read(self.catalog)
        self.assertIsNone(self.club.remembered())



class TestForget(ClubSettingsTestCase):
    """換了譜庫時忘掉記住的結果：重開後也是沒有，直到讀到新譜庫的社團設定"""

    def test_forgotten_platform_stays_hidden_after_restart(self):
        self.club_settings(["發譜平台", "華夏國樂社"])
        self.club.read(self.catalog)
        self.club.forget()
        self.assertIsNone(self.club.remembered())
        self.assertIsNone(self.restarted().remembered())

if __name__ == '__main__':
    unittest.main()


class TestRefresh(ClubSettingsTestCase):
    """連上譜庫時重讀：讀不到不丟出例外，記住的不變，失敗寫進紀錄檔"""

    def setUp(self):
        super().setUp()
        self.log_dir = os.path.join(self.temp_dir, "logs")
        self.club = ClubSettingsService(PreferencesService(self.path), ActivityLog(FileService(), self.log_dir))

    def log_text(self) -> str:
        """紀錄檔資料夾裡所有檔的內容"""
        if not os.path.isdir(self.log_dir):
            return ""
        texts = []
        for name in sorted(os.listdir(self.log_dir)):
            with open(os.path.join(self.log_dir, name), encoding="utf-8") as f:
                texts.append(f.read())
        return "".join(texts)

    def test_remembers_what_it_read_and_writes_nothing_to_the_log(self):
        self.club_settings(["發譜平台", "華夏國樂社"])
        self.club.refresh(lambda: self.catalog)
        self.assertIs(self.restarted().remembered(), PublishingPlatform.HUAXIA)
        self.assertEqual(self.log_text(), "")

    def test_unreadable_keeps_the_remembered_result_and_writes_the_failure_to_the_log(self):
        self.club_settings(["發譜平台", "華夏國樂社"])
        self.club.read(self.catalog)
        self.google.fail_next(CatalogErrorKind.OFFLINE)
        self.club.refresh(lambda: self.catalog)
        self.assertIs(self.restarted().remembered(), PublishingPlatform.HUAXIA)
        self.assertIn("讀取社團設定", self.log_text())
        self.assertIn("連不上網路", self.log_text())

    def test_failing_to_connect_is_written_to_the_log(self):
        def connect():
            """拿不到憑證"""
            raise CatalogError(CatalogErrorKind.SIGN_IN, "no Google credentials")
        self.club.refresh(connect)
        self.assertIsNone(self.club.remembered())
        self.assertIn("no Google credentials", self.log_text())

    def test_a_program_error_is_written_to_the_log_as_other(self):
        def connect():
            """程式的錯"""
            raise ValueError("bad value")
        self.club.refresh(connect)
        self.assertIn("ValueError: bad value", self.log_text())
        self.assertIn("譜庫讀寫失敗", self.log_text())


class TestSubscribe(ClubSettingsTestCase):
    """記住的結果寫入後通知訂閱者（重讀完成、忘掉時）；讀不到時記住的不變，不通知"""

    def setUp(self):
        super().setUp()
        self.notified = []
        self.listener = lambda: self.notified.append(self.club.remembered())
        self.club.subscribe(self.listener)

    def test_reading_notifies_with_the_new_result_remembered(self):
        self.club_settings(["發譜平台", "華夏國樂社"])
        self.club.refresh(lambda: self.catalog)
        self.club_settings()
        self.club.read(self.catalog)
        self.assertEqual(self.notified, [PublishingPlatform.HUAXIA, None])

    def test_forgetting_notifies(self):
        self.club.forget()
        self.assertEqual(self.notified, [None])

    def test_unreadable_does_not_notify(self):
        self.google.fail_next(CatalogErrorKind.OFFLINE)
        self.club.refresh(lambda: self.catalog)
        self.assertEqual(self.notified, [])

    def test_no_longer_notified_after_unsubscribing(self):
        self.club.unsubscribe(self.listener)
        self.club.forget()
        self.assertEqual(self.notified, [])
