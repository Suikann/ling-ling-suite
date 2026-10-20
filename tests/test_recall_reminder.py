# -*- coding: utf-8 -*-
"""
收回提醒的摘要測試

對假 HuaXia、假 Google 與臨時目錄：每次讀寫發譜紀錄（發譜、收回、不收回、撤回、收回的預覽）後，偏好設定裡的摘要
跟著更新；開泠靈時只讀偏好設定就知道哪幾首的公演已結束、還沒收回（services/recall_reminder.py 的 RecallReminder）。
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.catalog_errors import CatalogError, CatalogErrorKind
from services.activity_log import ActivityLog
from services.file_service import FileService
from services.preferences_service import PreferencesService
from services.publish_runner import PublishRunner
from services.recall_reminder import RecallReminder
from services.recall_service import RecallService
from services.retract_service import RetractService
from test_publish_recall import RecallTestCase

FENGHUO = "長城隨想－烽火操"


class UnreadableCatalog:
    """讀不到發譜紀錄的譜庫服務"""

    def publish_log(self):
        """丟出連不上"""
        raise CatalogError(CatalogErrorKind.OFFLINE, "offline")


class ReminderTestCase(RecallTestCase):
    """RecallTestCase 的烽火操發完、公演結束十天後；這台電腦的偏好設定還沒有摘要，收回服務帶著這份摘要"""

    def setUp(self):
        super().setUp()
        self.preferences_path = os.path.join(self.temp_dir, "preferences.json")
        self.preferences = PreferencesService(self.preferences_path)
        self.log_dir = os.path.join(self.temp_dir, "logs")
        self.reminder = RecallReminder(self.preferences, ActivityLog(FileService(), self.log_dir), now=lambda: self.now)
        self.recaller = RecallService(self.auth, now=lambda: self.now, reminder=self.reminder)

    def due_at_startup(self):
        """下次開泠靈時（重讀偏好設定檔）要提醒的曲名"""
        preferences = PreferencesService(self.preferences_path)
        preferences.load()
        return RecallReminder(preferences, now=lambda: self.now).due()


class TestPublishing(ReminderTestCase):
    """發譜寫完發譜紀錄後，摘要記下這首與它掛的公演"""

    def test_a_publish_is_remembered_and_reminded_after_its_concert(self):
        self.runner = PublishRunner(
            self.auth, FileService(), self.compressor, temp_dir=self.scratch, reminder=self.reminder,
        )
        self.run_publish(self.press_publish())
        self.assertEqual(self.due_at_startup(), [FENGHUO])
        self.now -= datetime.timedelta(days=30)
        self.assertEqual(self.reminder.due(), [])


class TestReading(ReminderTestCase):
    """讀發譜紀錄（收回的預覽、譜庫視窗的列表）也更新摘要：別台電腦的發譜要等這台讀過一次"""

    def test_the_recall_preview_remembers_what_it_read(self):
        self.assertEqual(self.reminder.due(), [])
        self.recaller.preview(self.catalog)
        self.assertEqual(self.due_at_startup(), [FENGHUO])

    def test_reading_the_log_remembers_it(self):
        self.reminder.read(self.catalog)
        self.assertEqual(self.due_at_startup(), [FENGHUO])

    def test_an_unreadable_log_after_writing_keeps_the_summary_and_is_logged(self):
        self.reminder.read(self.catalog)
        self.reminder.refresh(UnreadableCatalog())
        self.assertEqual(self.reminder.due(), [FENGHUO])
        self.assertTrue(os.listdir(self.log_dir))


class TestRecallingAndKeeping(ReminderTestCase):
    """收回、標不收回或整次撤回之後不再提醒"""

    def setUp(self):
        super().setUp()
        self.reminder.read(self.catalog)
        self.assertEqual(self.reminder.due(), [FENGHUO])

    def test_recalling_stops_the_reminder(self):
        self.recaller.recall([self.piece()], self.catalog)
        self.assertEqual(self.due_at_startup(), [])

    def test_keeping_stops_the_reminder(self):
        self.recaller.keep([self.piece()], self.catalog)
        self.assertEqual(self.due_at_startup(), [])

    def test_retracting_the_whole_publish_stops_the_reminder(self):
        RetractService(self.auth, reminder=self.reminder).retract(self.entry(), [self.group.id], self.catalog)
        self.assertEqual(self.due_at_startup(), [])


if __name__ == "__main__":
    unittest.main()
