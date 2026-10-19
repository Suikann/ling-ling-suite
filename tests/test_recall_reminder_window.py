# -*- coding: utf-8 -*-
"""
收回提醒的接線測試（offscreen）

開泠靈時主視窗頂端的提示列依偏好設定裡的摘要寫出哪幾首的公演已結束、還沒收回（不連網、不讀安全儲存），
按「之後再說」收起、下次開啟再提醒，按「查看並收回」開譜庫視窗的收回畫面。譜庫視窗讀到發譜紀錄時摘要跟著更新。
不測畫面。
"""
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication, QDialog

from core.constants import CATALOG_SPREADSHEET_ID_KEY, RECALL_REMINDER_KEY
from core.locale import get_locale, set_locale, t
from fake_google_catalog import FakeGoogle
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.file_service import FileService
from services.huaxia_auth_service import HuaXiaAuthService
from services.huaxia_client import HuaXiaClient
from services.preferences_service import PreferencesService
from services.secure_storage import SecureStorage
from test_catalog_service import empty_catalog
from test_publish_recall import RecallTestCase
from test_publish_retract_window import TestRetractFromCatalogWindow
from tests.fake_browser import CallbackBrowser
from tests.fake_huaxia import FakeHuaXia
from tests.memory_keyring import MemoryKeyring
from tests.test_main_window import MainWindowTestCase, answering_prompts
from ui.catalog_window import CatalogWindow
from ui.main_window import MainWindow
from ui.recall_reminder_bar import RecallReminderBar

ENDED = "2026-06-20T21:00:00+08:00"
LONG_AFTER = "2999-12-20T21:00:00+08:00"
SUMMARY = [
    {"piece_id": "hx-1", "piece_title": "長城隨想－烽火操", "ends_at": [ENDED]},
    {"piece_id": "hx-2", "piece_title": "茉莉花", "ends_at": [ENDED, LONG_AFTER]},
    {"piece_id": "hx-3", "piece_title": "春節序曲", "ends_at": [ENDED]},
    {"piece_id": "hx-4", "piece_title": "春江花月夜", "ends_at": []},
]


class ReminderWindowTestCase(MainWindowTestCase):
    """偏好設定裡的摘要有四首：烽火操、春節序曲的公演都結束了，茉莉花還要演，春江花月夜沒掛公演"""

    def setUp(self):
        super().setUp()
        self.preferences.set(RECALL_REMINDER_KEY, SUMMARY)
        self.huaxia = FakeHuaXia()
        self.huaxia_keyring = MemoryKeyring()
        self.huaxia_auth = HuaXiaAuthService(
            HuaXiaClient(session=self.huaxia), storage=SecureStorage("test-huaxia", backend=self.huaxia_keyring),
            opener=CallbackBrowser(),
        )
        self.google = FakeGoogle()
        empty_catalog(self.google)
        self.keyring.calls.clear()
        self.window = self.new_window()

    def new_window(self) -> MainWindow:
        """關掉目前的主視窗，換一個接假 Google 與假 HuaXia 的（等同重新開泠靈）"""
        with answering_prompts(t("dialog.discard_btn")):
            self.window.close()
        return MainWindow(
            self.preferences, history=self.history, opener=self.opener, auth_service=self.auth,
            compressor=self.compressor(), log=ActivityLog(FileService(), self.log_dir),
            huaxia_auth=self.huaxia_auth,
            catalog_factory=lambda: CatalogService(self.google.sheets, self.google.drive, sleep=lambda s: None),
        )

    def bar(self) -> RecallReminderBar:
        """主視窗頂端的收回提醒"""
        return self.window.findChild(RecallReminderBar)


class TestStartup(ReminderWindowTestCase):
    """開泠靈時只看偏好設定：公演都結束、還沒收回的才提醒，不連網、不讀安全儲存"""

    def test_pieces_whose_concerts_are_over_are_named_without_network_or_secure_storage(self):
        self.assertFalse(self.bar().isHidden())
        self.assertEqual(self.bar().message.text(), t(
            "recall.reminder.text", count=2, titles=t("recall.reminder.separator").join(["長城隨想－烽火操", "春節序曲"]),
        ))
        self.assertEqual(self.keyring.calls, [])
        self.assertEqual(self.huaxia_keyring.calls, [])
        self.assertEqual((self.huaxia.requests, self.google.requests), ([], 0))

    def test_nothing_due_shows_no_reminder(self):
        self.preferences.set(RECALL_REMINDER_KEY, SUMMARY[1:2] + SUMMARY[3:])
        self.window = self.new_window()
        self.assertTrue(self.bar().isHidden())

    def test_the_reminder_survives_switching_the_language(self):
        self.addCleanup(set_locale, get_locale())
        self.trigger_menu(t("menu.view.language.en"))
        self.assertFalse(self.bar().isHidden())
        self.assertIn("長城隨想－烽火操", self.bar().message.text())


class TestButtons(ReminderWindowTestCase):
    """「之後再說」收起、下次開啟再提醒；「查看並收回」開譜庫視窗的收回畫面"""

    def test_later_hides_it_until_next_time(self):
        self.bar().later_button.click()
        self.assertTrue(self.bar().isHidden())
        self.window = self.new_window()
        self.assertFalse(self.bar().isHidden())

    def test_view_opens_the_recall_screen_of_the_catalog_window(self):
        self.auth.authenticate()
        self.preferences.set(CATALOG_SPREADSHEET_ID_KEY, "sheet-1")
        with mock.patch.object(CatalogWindow, "recall") as recall:
            self.bar().view_button.click()
        recall.assert_called_once_with()
        self.assertIsInstance(self.window._catalog_window, CatalogWindow)
        self.addCleanup(self.window._catalog_window.close)
        self.assertTrue(self.bar().isHidden())

    def test_view_without_a_catalog_set_up_opens_the_settings_first(self):
        from ui.catalog_settings_dialog import CatalogSettingsDialog
        with mock.patch.object(CatalogSettingsDialog, "exec", return_value=QDialog.Rejected) as settings, \
                mock.patch.object(CatalogWindow, "recall") as recall:
            self.bar().view_button.click()
        settings.assert_called_once()
        recall.assert_not_called()


class TestCatalogWindowRemembers(RecallTestCase):
    """譜庫視窗讀到發譜紀錄時，偏好設定裡的摘要跟著更新"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    open_catalog = TestRetractFromCatalogWindow.open_catalog

    def test_reading_the_log_updates_the_summary(self):
        self.open_catalog()
        preferences = PreferencesService(os.path.join(self.temp_dir, "preferences.json"))
        preferences.load()
        self.assertEqual([piece["piece_id"] for piece in preferences.get(RECALL_REMINDER_KEY)], [self.fenghuo])


if __name__ == "__main__":
    unittest.main()
