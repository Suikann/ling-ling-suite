# -*- coding: utf-8 -*-
"""
社團設定（譜庫設定對話框）的登入區接線測試

只測登入狀態旁顯示登入服務回報的原因，不測畫面。登入服務的安全儲存與 Google 都換成替身。
"""
import os
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from core.locale import t
from services.google_auth_service import GoogleAuthService
from services.preferences_service import PreferencesService
from services.secure_storage import SecureStorage
from tests.fake_google import FakeBrowserLogin, FakeTokenEndpoint, write_client_secrets
from tests.memory_keyring import MemoryKeyring
from tests.test_main_window import answering_prompts
from ui.catalog_settings_dialog import CatalogSettingsDialog


class TestLoginReason(unittest.TestCase):
    """登入狀態旁寫出這次登入沒被記住的原因"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.keyring = MemoryKeyring()
        self.auth = GoogleAuthService(
            storage=SecureStorage("test-google", backend=self.keyring),
            request=FakeTokenEndpoint(),
            browser_login=FakeBrowserLogin(),
            client_secrets_path=write_client_secrets(os.path.join(self.temp_dir, "client_secret.json")),
            legacy_token_path=os.path.join(self.temp_dir, "google_token.json"),
        )
        self.preferences = PreferencesService(os.path.join(self.temp_dir, "preferences.json"))

    def open_dialog(self) -> CatalogSettingsDialog:
        """開啟社團設定"""
        dialog = CatalogSettingsDialog(self.auth, self.preferences)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def shows(self, dialog: CatalogSettingsDialog, text: str) -> bool:
        """對話框裡是否有一段文字等於 text 的標籤"""
        return any(label.text() == text for label in dialog.findChildren(QLabel))

    def sign_in(self, dialog: CatalogSettingsDialog):
        """按下登入"""
        button = next(b for b in dialog.findChildren(QPushButton) if b.text() == t("catalog.auth.connect"))
        with answering_prompts():
            button.click()

    def test_without_secure_storage_says_login_will_not_be_remembered(self):
        self.keyring.viable = False
        dialog = self.open_dialog()
        self.assertTrue(self.shows(dialog, t("catalog.auth.reason.no_secure_storage")))
        self.sign_in(dialog)
        self.assertTrue(self.shows(dialog, t("catalog.auth.connected")))
        self.assertTrue(self.shows(dialog, t("catalog.auth.reason.no_secure_storage")))

    def test_cancelled_unlock_says_storage_is_locked(self):
        self.keyring.locked = True
        dialog = self.open_dialog()
        self.assertTrue(self.shows(dialog, t("catalog.auth.reason.storage_locked")))

    def test_login_that_cannot_be_saved_says_sign_in_again_next_time(self):
        self.keyring.failing_writes = True
        dialog = self.open_dialog()
        self.assertFalse(self.shows(dialog, t("catalog.auth.reason.not_saved")))
        self.sign_in(dialog)
        self.assertTrue(self.shows(dialog, t("catalog.auth.reason.not_saved")))


if __name__ == "__main__":
    unittest.main()
