# -*- coding: utf-8 -*-
"""
譜庫設定對話框的登入區接線測試

只測與登入服務的接線：登入狀態旁的原因、帳號 email、帳號提醒，以及登入沒有完成時的提示；不測畫面。
登入服務的安全儲存、Google 權杖端點與開瀏覽器都換成替身。
"""
import os
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from core.locale import t
from services.google_auth_service import GoogleAuthService
from services.preferences_service import PreferencesService
from services.secure_storage import SecureStorage
from tests.fake_browser import CallbackBrowser
from tests.fake_google import FakeTokenEndpoint, write_client_secrets
from tests.memory_keyring import MemoryKeyring
from tests.test_main_window import answering_prompts
from ui.catalog_settings_dialog import CatalogSettingsDialog


class LoginAreaTestCase(unittest.TestCase):
    """譜庫設定登入區測試的共用骨架：替身組成的登入服務、臨時目錄裡的偏好設定"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.keyring = MemoryKeyring()
        self.endpoint = FakeTokenEndpoint()
        self.browser = CallbackBrowser()
        self.addCleanup(self.browser.finish)
        self.preferences = PreferencesService(os.path.join(self.temp_dir, "preferences.json"))
        self.auth = self.login_service()

    def login_service(self, login_timeout: float = 30) -> GoogleAuthService:
        """建立登入服務，安全儲存、權杖端點與開瀏覽器都是替身"""
        return GoogleAuthService(
            storage=SecureStorage("test-google", backend=self.keyring),
            request=self.endpoint,
            opener=self.browser,
            preferences=self.preferences,
            client_secrets_path=write_client_secrets(os.path.join(self.temp_dir, "client_secret.json")),
            legacy_token_path=os.path.join(self.temp_dir, "google_token.json"),
            login_timeout=login_timeout,
        )

    def open_dialog(self) -> CatalogSettingsDialog:
        """開啟譜庫設定"""
        dialog = CatalogSettingsDialog(self.auth, self.preferences)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def shows(self, dialog: CatalogSettingsDialog, text: str) -> bool:
        """對話框裡是否有一段文字等於 text 的標籤"""
        return any(label.text() == text for label in dialog.findChildren(QLabel))

    def sign_in(self, dialog: CatalogSettingsDialog) -> list:
        """按下登入，回傳跳出過的提示框（標題，訊息）"""
        button = next(b for b in dialog.findChildren(QPushButton) if b.text() == t("catalog.auth.connect"))
        with answering_prompts() as shown:
            button.click()
        return shown


class TestLoginReason(LoginAreaTestCase):
    """登入狀態旁寫出這次登入沒被記住的原因"""

    def test_without_secure_storage_says_login_will_not_be_remembered(self):
        self.keyring.viable = False
        dialog = self.open_dialog()
        self.assertTrue(self.shows(dialog, t("catalog.auth.reason.no_secure_storage")))
        self.sign_in(dialog)
        self.assertTrue(self.shows(dialog, t("catalog.auth.connected_as", email="library@example.com")))
        self.assertTrue(self.shows(dialog, t("catalog.auth.reason.no_secure_storage")))

    def test_cancelled_unlock_says_storage_is_locked(self):
        self.keyring.locked = True
        dialog = self.open_dialog()
        self.assertTrue(self.shows(dialog, t("catalog.auth.reason.storage_locked")))

    def test_cancelled_unlock_is_asked_only_once_when_opening_with_a_folder_set(self):
        self.auth.authenticate()
        self.auth = self.login_service()
        self.preferences.set("catalog_root_folder_id", "folder-id")
        self.keyring.locked = True
        self.keyring.calls.clear()
        dialog = self.open_dialog()
        self.assertEqual(self.keyring.calls.count("get"), 1)
        self.assertTrue(self.shows(dialog, t("catalog.auth.reason.storage_locked")))

    def test_login_that_cannot_be_saved_says_sign_in_again_next_time(self):
        self.keyring.failing_writes = True
        dialog = self.open_dialog()
        self.assertFalse(self.shows(dialog, t("catalog.auth.reason.not_saved")))
        self.sign_in(dialog)
        self.assertTrue(self.shows(dialog, t("catalog.auth.reason.not_saved")))


class TestLoginArea(LoginAreaTestCase):
    """登入區顯示帳號 email 與帳號提醒；等待瀏覽器時可取消，逾時有提示"""

    def test_login_shows_account_email_and_reminder(self):
        self.endpoint.sign_in_as("library@example.com")
        dialog = self.open_dialog()
        self.assertTrue(self.shows(dialog, t("catalog.auth.account_reminder")))
        shown = self.sign_in(dialog)
        self.assertIn((t("catalog.auth.title"), t("catalog.auth.success")), shown)
        self.assertTrue(self.shows(dialog, t("catalog.auth.connected_as", email="library@example.com")))
        self.assertTrue(self.shows(dialog, t("catalog.auth.account_reminder")))

    def test_timeout_says_try_again(self):
        self.auth = self.login_service(login_timeout=0.2)
        self.browser.stay_silent()
        dialog = self.open_dialog()
        shown = self.sign_in(dialog)
        self.assertEqual(shown, [(t("catalog.auth.title"), t("catalog.auth.timed_out"))])
        self.assertTrue(self.shows(dialog, t("catalog.auth.not_connected")))

    def test_cancelling_the_wait_ends_login_without_a_prompt(self):
        self.browser.stay_silent()
        dialog = self.open_dialog()
        waiting = []

        def press_cancel():
            """等待視窗出現後按「取消」；還沒出現就稍後再看"""
            box = QApplication.activeModalWidget()
            if box is None or box is dialog:
                QTimer.singleShot(20, dialog, press_cancel)
                return
            waiting.append(box)
            next(b for b in box.findChildren(QPushButton) if b.text() == t("dialog.cancel_btn")).click()
        QTimer.singleShot(20, dialog, press_cancel)
        shown = self.sign_in(dialog)
        self.assertEqual(len(waiting), 1)
        self.assertEqual(shown, [])
        self.assertTrue(self.shows(dialog, t("catalog.auth.not_connected")))


if __name__ == "__main__":
    unittest.main()
