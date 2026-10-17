# -*- coding: utf-8 -*-
"""
譜庫設定對話框的接線測試

登入區只測與登入服務的接線：登入狀態旁的原因、帳號 email、帳號提醒，以及登入沒有完成時的提示。
選資料夾、找與建立譜庫試算表經譜庫服務、對假 Google：結果寫進偏好設定與假 Google，失敗時提示列出現。不測畫面。
登入服務的安全儲存、Google 權杖端點與開瀏覽器都換成替身。
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QInputDialog, QLabel, QPushButton, QTreeWidget

from core.catalog_constants import DRIVE_SPREADSHEET_MIME, SHEET_HEADERS, PublishingPlatform
from core.catalog_errors import CatalogErrorKind
from core.constants import CATALOG_ROOT_FOLDER_ID_KEY, CATALOG_SPREADSHEET_ID_KEY
from core.locale import t
from services.catalog_service import CatalogService
from services.club_settings_service import ClubSettingsService
from services.google_auth_service import GoogleAuthService
from services.preferences_service import PreferencesService
from services.secure_storage import SecureStorage
from tests.fake_browser import CallbackBrowser
from tests.fake_google import FakeTokenEndpoint, write_client_secrets
from tests.fake_google_catalog import FakeGoogle
from tests.memory_keyring import MemoryKeyring
from tests.test_main_window import answering_prompts, wait_until
from ui.catalog_error_bar import CatalogErrorBar
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
        self.preferences.set(CATALOG_ROOT_FOLDER_ID_KEY, "folder-id")
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



class CatalogFolderTestCase(LoginAreaTestCase):
    """選資料夾與譜庫試算表的共用骨架：已登入；譜庫服務接假 Google，記下以哪個試算表 ID 建立"""

    def setUp(self):
        super().setUp()
        self.auth.authenticate()
        self.google = FakeGoogle()
        self.folder = self.google.drive.add_folder("譜庫")
        self.connected_to = []

    def catalog(self, spreadsheet_id: str) -> CatalogService:
        """接假 Google 的譜庫服務"""
        self.connected_to.append(spreadsheet_id)
        return CatalogService(self.google.sheets, self.google.drive, sleep=lambda seconds: None)

    def open_dialog(self) -> CatalogSettingsDialog:
        """開啟譜庫設定，譜庫服務接假 Google"""
        dialog = CatalogSettingsDialog(self.auth, self.preferences, catalog_factory=self.catalog)
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.reject)
        return dialog

    def open_with_folder(self) -> CatalogSettingsDialog:
        """資料夾已設定時開啟，等找譜庫試算表的結果"""
        self.preferences.set(CATALOG_ROOT_FOLDER_ID_KEY, self.folder)
        before = self.preferences.get(CATALOG_SPREADSHEET_ID_KEY)
        dialog = self.open_dialog()
        wait_until(lambda: not self.bar(dialog).isHidden() or self.preferences.get(CATALOG_SPREADSHEET_ID_KEY) != before)
        return dialog

    def bar(self, dialog: QDialog) -> CatalogErrorBar:
        """對話框頂端的錯誤提示列"""
        return dialog.findChild(CatalogErrorBar)

    def bar_text(self, dialog: QDialog) -> str:
        """提示列上的訊息"""
        return self.bar(dialog).findChild(QLabel).text()

    def add_spreadsheet(self, name: str) -> str:
        """在譜庫資料夾放一份試算表，回傳 ID"""
        return self.google.drive.add_file(name, self.folder, mime_type=DRIVE_SPREADSHEET_MIME)


class TestFindCatalog(CatalogFolderTestCase):
    """資料夾裡有一份試算表就用它；沒有就建立；讀不到時頂端出現提示列"""

    def test_uses_the_only_spreadsheet_in_the_folder(self):
        sheet = self.add_spreadsheet("譜庫目錄")
        dialog = self.open_with_folder()
        self.assertEqual(self.preferences.get(CATALOG_SPREADSHEET_ID_KEY), sheet)
        self.assertTrue(self.shows(dialog, t("catalog.settings.catalog_found", name="譜庫目錄")))

    def test_creates_a_catalog_in_an_empty_folder(self):
        with mock.patch.object(QInputDialog, "getText", return_value=("社團譜庫", True)):
            dialog = self.open_with_folder()
        sheet = self.preferences.get(CATALOG_SPREADSHEET_ID_KEY)
        self.assertEqual(self.google.drive.file(sheet).parents, (self.folder,))
        self.assertEqual(self.google.drive.file(sheet).name, "社團譜庫")
        self.assertEqual(self.google.sheets.tabs(), list(SHEET_HEADERS))
        self.assertTrue(self.shows(dialog, t("catalog.settings.catalog_created")))

    def test_unreadable_folder_shows_the_bar_and_retry_finds_the_catalog(self):
        sheet = self.add_spreadsheet("譜庫目錄")
        self.google.fail_next(CatalogErrorKind.OFFLINE)
        dialog = self.open_with_folder()
        self.assertTrue(self.bar_text(dialog).startswith("讀不到譜庫｜連不上網路："))
        next(b for b in self.bar(dialog).findChildren(QPushButton) if b.text() == "重試").click()
        wait_until(lambda: self.preferences.get(CATALOG_SPREADSHEET_ID_KEY) == sheet)
        self.assertTrue(self.bar(dialog).isHidden())

    def test_failing_to_create_shows_the_write_bar(self):
        self.google.fail_at(2, CatalogErrorKind.NO_PERMISSION)
        with mock.patch.object(QInputDialog, "getText", return_value=("社團譜庫", True)):
            dialog = self.open_with_folder()
        self.assertTrue(self.bar_text(dialog).startswith("寫不進譜庫｜沒有權限："))
        self.assertFalse(self.preferences.get(CATALOG_SPREADSHEET_ID_KEY))


class TestClubSettingsFollowTheCatalog(CatalogFolderTestCase):
    """換了譜庫試算表時，記住的發譜平台改成新譜庫的社團設定"""

    def remember_huaxia(self):
        """這台電腦記著上一個譜庫啟用 HuaXia"""
        old = FakeGoogle()
        old.sheets.create_tab("社團設定", [["鍵", "值"], ["發譜平台", "華夏國樂社"]])
        ClubSettingsService(self.preferences).read(CatalogService(old.sheets, old.drive))
        self.preferences.set(CATALOG_SPREADSHEET_ID_KEY, "old-sheet")

    def test_a_new_catalog_without_the_platform_hides_publishing(self):
        self.remember_huaxia()
        self.add_spreadsheet("譜庫目錄")
        self.google.sheets.create_tab("曲目", [SHEET_HEADERS["曲目"]])
        self.open_with_folder()
        self.assertIsNone(ClubSettingsService(self.preferences).remembered())
        wait_until(lambda: self.google.requests >= 2)
        self.assertIsNone(ClubSettingsService(self.preferences).remembered())

    def test_a_new_catalog_with_the_platform_keeps_publishing(self):
        self.remember_huaxia()
        sheet = self.add_spreadsheet("譜庫目錄")
        self.google.sheets.create_tab("社團設定", [["鍵", "值"], ["發譜平台", "華夏國樂社"]])
        self.open_with_folder()
        wait_until(lambda: ClubSettingsService(self.preferences).remembered() is PublishingPlatform.HUAXIA)
        self.assertIn(sheet, self.connected_to)


class TestBrowseFolders(CatalogFolderTestCase):
    """瀏覽 Drive 資料夾：子資料夾經譜庫服務列出，選了就找裡面的譜庫試算表；讀不到時瀏覽視窗出現提示列"""

    def browse(self, dialog: CatalogSettingsDialog, pick: str = ""):
        """按「瀏覽」；瀏覽視窗列出資料夾後選 pick 並按確定（pick 為空時只回傳瀏覽視窗，不選）"""
        seen = []

        def fake_exec(browser):
            seen.append(browser)
            tree = browser.findChild(QTreeWidget)
            wait_until(lambda: tree.topLevelItemCount() > 0 or not browser.findChild(CatalogErrorBar).isHidden())
            if not pick:
                return QDialog.Rejected
            items = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
            tree.setCurrentItem(next(item for item in items if item.text(0) == pick))
            browser.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
            return browser.result()
        button = next(b for b in dialog.findChildren(QPushButton) if b.text() == t("catalog.settings.browse"))
        with mock.patch.object(QDialog, "exec", fake_exec):
            button.click()
        return seen[0]

    def test_picking_a_folder_finds_its_catalog(self):
        sheet = self.add_spreadsheet("譜庫目錄")
        dialog = self.open_dialog()
        self.browse(dialog, "譜庫")
        wait_until(lambda: self.preferences.get(CATALOG_SPREADSHEET_ID_KEY) == sheet)

    def test_unreadable_drive_shows_the_bar_in_the_browser(self):
        self.google.fail_next(CatalogErrorKind.RATE_LIMITED, times=6)
        dialog = self.open_dialog()
        browser = self.browse(dialog)
        self.assertTrue(self.bar_text(browser).startswith("讀不到譜庫｜Google 暫時不接受請求："))


if __name__ == "__main__":
    unittest.main()
