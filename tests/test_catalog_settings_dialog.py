# -*- coding: utf-8 -*-
"""
譜庫設定對話框的接線測試

登入區只測與登入服務的接線：登入狀態旁的原因、帳號 email、帳號提醒，以及登入沒有完成時的提示。
選資料夾、找與建立譜庫試算表經譜庫服務、對假 Google：結果寫進偏好設定與假 Google，失敗時提示列出現。不測畫面。
登入服務的安全儲存、Google 權杖端點與開瀏覽器都換成替身。
HuaXia 登入區只在社團設定啟用 HuaXia 發譜時出現，對假 HuaXia 測登入、不是幹部的說明與登出的接線。
記住的發譜平台照社團設定裡的寫法寫出，沒有時寫未設定。
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QInputDialog, QLabel, QPushButton, QTreeWidget

from core.catalog_constants import DRIVE_SPREADSHEET_MIME, SHEET_HEADERS, PublishingPlatform
from core.catalog_errors import CatalogErrorKind
from core.constants import CATALOG_ROOT_FOLDER_ID_KEY, CATALOG_SPREADSHEET_ID_KEY, CLUB_PUBLISHING_PLATFORM_KEY
from core.locale import t
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.club_settings_service import ClubSettingsService
from services.file_service import FileService
from services.google_auth_service import GoogleAuthService
from services.huaxia_auth_service import HuaXiaAuthService
from services.huaxia_client import HuaXiaClient
from services.preferences_service import PreferencesService
from services.secure_storage import SecureStorage
from tests.fake_browser import CallbackBrowser
from tests.fake_google import FakeTokenEndpoint, write_client_secrets
from tests.fake_google_catalog import ROOT, FakeGoogle
from tests.fake_huaxia import FakeHuaXia
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


class TestHuaXiaLoginArea(LoginAreaTestCase):
    """HuaXia 登入區：社團設定啟用 HuaXia 發譜才出現；以 Discord 登入、不是幹部時說明原因、登出只結束這台電腦"""

    def setUp(self):
        super().setUp()
        self.huaxia = FakeHuaXia()
        self.huaxia.add_account("officer_amy", roles=("officer",))
        self.huaxia.sign_in_as("officer_amy")
        self.huaxia_browser = CallbackBrowser(provider=self.huaxia.authorize)
        self.addCleanup(self.huaxia_browser.finish)
        self.huaxia_keyring = MemoryKeyring()
        self.huaxia_auth = HuaXiaAuthService(
            HuaXiaClient(session=self.huaxia), storage=SecureStorage("test-huaxia", backend=self.huaxia_keyring),
            opener=self.huaxia_browser,
        )

    def open_dialog(self) -> CatalogSettingsDialog:
        """開啟譜庫設定，帶 HuaXia 登入服務"""
        dialog = CatalogSettingsDialog(self.auth, self.preferences, huaxia_auth=self.huaxia_auth)
        self.addCleanup(dialog.deleteLater)
        return dialog

    def enable_huaxia(self):
        """社團設定啟用 HuaXia 發譜（這台電腦記住的結果）"""
        self.preferences.set(CLUB_PUBLISHING_PLATFORM_KEY, PublishingPlatform.HUAXIA.value)

    def click(self, dialog: CatalogSettingsDialog, text: str) -> list:
        """按下文字為 text 的按鈕，回傳跳出過的提示框（標題，訊息）"""
        button = next(b for b in dialog.findChildren(QPushButton) if b.text() == text)
        with answering_prompts() as shown:
            button.click()
        return shown

    def test_hidden_when_club_settings_do_not_enable_huaxia(self):
        dialog = self.open_dialog()
        self.assertFalse(self.shows(dialog, t("huaxia.auth.link_discord_first")))
        self.assertFalse(any(b.text() == t("huaxia.auth.connect") for b in dialog.findChildren(QPushButton)))
        self.assertEqual(self.huaxia_keyring.calls, [])

    def test_shown_with_the_reminder_before_signing_in(self):
        self.enable_huaxia()
        dialog = self.open_dialog()
        self.assertTrue(self.shows(dialog, t("huaxia.auth.not_connected")))
        self.assertTrue(self.shows(dialog, t("huaxia.auth.link_discord_first")))

    def test_officer_signs_in_and_sees_the_discord_account(self):
        self.enable_huaxia()
        dialog = self.open_dialog()
        self.assertEqual(self.click(dialog, t("huaxia.auth.connect")), [])
        self.assertTrue(self.shows(dialog, t("huaxia.auth.connected_as", name="officer_amy")))
        self.assertEqual(len(self.huaxia_keyring.stored()), 1)

    def test_not_an_officer_is_told_why_with_the_discord_account(self):
        self.enable_huaxia()
        self.huaxia.add_account("member_mei", roles=("member",))
        self.huaxia.sign_in_as("member_mei")
        dialog = self.open_dialog()
        shown = self.click(dialog, t("huaxia.auth.connect"))
        reason = t("huaxia.not_officer.not_officer")
        self.assertEqual(shown, [(t("huaxia.auth.title"), t("huaxia.not_officer.with_account", name="member_mei", reason=reason))])
        self.assertTrue(self.shows(dialog, t("huaxia.auth.not_connected")))
        self.assertEqual(self.huaxia_keyring.stored(), [])

    def test_without_a_discord_name_the_account_sentence_is_left_out(self):
        self.enable_huaxia()
        self.huaxia.add_account("nameless", roles=("member",), metadata={})
        self.huaxia.sign_in_as("nameless")
        shown = self.click(self.open_dialog(), t("huaxia.auth.connect"))
        self.assertEqual(shown, [(t("huaxia.auth.title"), t("huaxia.not_officer.not_officer"))])
        self.assertNotIn("『』", shown[0][1])

    def test_denied_in_discord_says_so(self):
        self.enable_huaxia()
        self.huaxia_browser.deny()
        dialog = self.open_dialog()
        shown = self.click(dialog, t("huaxia.auth.connect"))
        self.assertEqual(shown, [(t("huaxia.auth.title"), t("huaxia.auth.denied"))])

    def test_logout_ends_only_this_computer_in_the_background(self):
        self.enable_huaxia()
        self.huaxia_auth.authenticate()
        dialog = self.open_dialog()
        self.click(dialog, t("huaxia.auth.disconnect"))
        wait_until(lambda: self.shows(dialog, t("huaxia.auth.not_connected")))
        wait_until(lambda: self.huaxia_keyring.stored() == [])
        self.assertEqual(self.huaxia.logouts(), ["local"])


class TestPublishingPlatformHint(LoginAreaTestCase):
    """寫出這台電腦記住的發譜平台，與到哪裡設定"""

    def test_names_the_remembered_platform_as_written_in_club_settings(self):
        self.preferences.set(CLUB_PUBLISHING_PLATFORM_KEY, PublishingPlatform.HUAXIA.value)
        dialog = self.open_dialog()
        self.assertTrue(self.shows(dialog, "發譜平台：華夏國樂社（在譜庫試算表的「社團設定」工作表設定）"))

    def test_says_not_set_when_nothing_is_remembered(self):
        dialog = self.open_dialog()
        self.assertTrue(self.shows(dialog, "發譜平台：未設定（在譜庫試算表的「社團設定」工作表設定）"))


class CatalogFolderTestCase(LoginAreaTestCase):
    """選資料夾與譜庫試算表的共用骨架：已登入；譜庫服務接假 Google，記下以哪個試算表 ID 建立"""

    def setUp(self):
        super().setUp()
        self.auth.authenticate()
        self.google = FakeGoogle()
        self.folder = self.google.drive.add_folder("譜庫")
        self.connected_to = []
        self.log_dir = os.path.join(self.temp_dir, "logs")

    def catalog(self, spreadsheet_id: str) -> CatalogService:
        """接假 Google 的譜庫服務"""
        self.connected_to.append(spreadsheet_id)
        return CatalogService(self.google.sheets, self.google.drive, sleep=lambda seconds: None)

    def open_dialog(self) -> CatalogSettingsDialog:
        """開啟譜庫設定，譜庫服務接假 Google"""
        dialog = CatalogSettingsDialog(
            self.auth, self.preferences, catalog_factory=self.catalog, log=ActivityLog(FileService(), self.log_dir),
        )
        self.addCleanup(dialog.deleteLater)
        self.addCleanup(dialog.reject)
        return dialog

    def open_with_folder(self) -> CatalogSettingsDialog:
        """資料夾已設定時開啟，等找譜庫試算表的結果（找到、建好、沒有，或提示列出現）"""
        self.preferences.set(CATALOG_ROOT_FOLDER_ID_KEY, self.folder)
        dialog = self.open_dialog()
        wait_until(lambda: not self.bar(dialog).isHidden() or self.settled(dialog))
        return dialog

    def settled(self, dialog: CatalogSettingsDialog) -> bool:
        """資料夾欄位下方寫出找譜庫試算表的結果"""
        found = t("catalog.settings.catalog_found", name="")
        done = (t("catalog.settings.catalog_created"), t("catalog.settings.catalog_not_found"))
        return any(
            label.text().startswith(found) or label.text() in done
            for label in dialog.findChildren(QLabel)
        )

    def save(self, dialog: CatalogSettingsDialog) -> None:
        """按「儲存設定」"""
        next(b for b in dialog.findChildren(QPushButton) if b.text() == t("catalog.settings.save")).click()

    def saved(self) -> PreferencesService:
        """偏好檔裡存的內容（重新載入一份）"""
        reloaded = PreferencesService(self.preferences.path)
        reloaded.load()
        return reloaded

    def log_text(self) -> str:
        """紀錄檔資料夾裡所有檔的內容"""
        if not os.path.isdir(self.log_dir):
            return ""
        texts = []
        for name in sorted(os.listdir(self.log_dir)):
            with open(os.path.join(self.log_dir, name), encoding="utf-8") as f:
                texts.append(f.read())
        return "".join(texts)

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
        sheet = self.add_spreadsheet("譜庫試算表")
        dialog = self.open_with_folder()
        self.assertTrue(self.shows(dialog, t("catalog.settings.catalog_found", name="譜庫試算表")))
        self.save(dialog)
        self.assertEqual(self.preferences.get(CATALOG_SPREADSHEET_ID_KEY), sheet)
        self.assertEqual(self.saved().get(CATALOG_SPREADSHEET_ID_KEY), sheet)

    def test_creates_a_catalog_in_an_empty_folder(self):
        with mock.patch.object(QInputDialog, "getText", return_value=("社團譜庫", True)):
            dialog = self.open_with_folder()
        self.save(dialog)
        sheet = self.preferences.get(CATALOG_SPREADSHEET_ID_KEY)
        self.assertEqual(self.google.drive.file(sheet).parents, (self.folder,))
        self.assertEqual(self.google.drive.file(sheet).name, "社團譜庫")
        self.assertEqual(self.google.sheets.tabs(), list(SHEET_HEADERS))
        self.assertTrue(self.shows(dialog, t("catalog.settings.catalog_created")))

    def test_cancelling_the_name_prompt_says_how_to_create_one(self):
        with mock.patch.object(QInputDialog, "getText", return_value=("", False)):
            dialog = self.open_with_folder()
        self.assertTrue(self.shows(dialog, "此資料夾尚無譜庫試算表。按「瀏覽」再選一次這個資料夾即可建立。"))
        self.assertEqual(self.google.drive.children(self.folder), [])
        self.assertFalse(self.preferences.get(CATALOG_SPREADSHEET_ID_KEY))

    def test_unreadable_folder_shows_the_bar_and_retry_finds_the_catalog(self):
        sheet = self.add_spreadsheet("譜庫試算表")
        self.google.fail_next(CatalogErrorKind.OFFLINE)
        dialog = self.open_with_folder()
        self.assertTrue(self.bar_text(dialog).startswith("讀不到譜庫｜連不上網路："))
        next(b for b in self.bar(dialog).findChildren(QPushButton) if b.text() == "重試").click()
        wait_until(lambda: self.settled(dialog))
        self.assertTrue(self.bar(dialog).isHidden())
        self.save(dialog)
        self.assertEqual(self.preferences.get(CATALOG_SPREADSHEET_ID_KEY), sheet)

    def test_failing_to_create_shows_the_write_bar(self):
        self.google.fail_at(2, CatalogErrorKind.NO_PERMISSION)
        with mock.patch.object(QInputDialog, "getText", return_value=("社團譜庫", True)):
            dialog = self.open_with_folder()
        self.assertTrue(self.bar_text(dialog).startswith("寫不進譜庫｜沒有權限："))
        self.assertFalse(self.preferences.get(CATALOG_SPREADSHEET_ID_KEY))


class TestMyDriveTypedAsRoot(CatalogFolderTestCase):
    """資料夾欄位寫的是 Drive 根資料夾的別名 root 時，存成根資料夾真正的 ID，譜庫的其他地方才比對得到"""

    def test_alias_and_the_my_drive_url_are_saved_as_the_real_id(self):
        sheet = self.google.drive.add_file("譜庫試算表", mime_type=DRIVE_SPREADSHEET_MIME)
        for typed in ("root", "https://drive.google.com/drive/my-drive"):
            with self.subTest(typed=typed):
                self.preferences.set(CATALOG_ROOT_FOLDER_ID_KEY, typed)
                dialog = self.open_dialog()
                wait_until(lambda: not self.bar(dialog).isHidden() or self.settled(dialog))
                self.save(dialog)
                self.assertEqual(self.preferences.get(CATALOG_ROOT_FOLDER_ID_KEY), ROOT)
                self.assertEqual(self.preferences.get(CATALOG_SPREADSHEET_ID_KEY), sheet)


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
        self.add_spreadsheet("譜庫試算表")
        self.google.sheets.create_tab("曲目", [SHEET_HEADERS["曲目"]])
        self.save(self.open_with_folder())
        self.assertIsNone(ClubSettingsService(self.preferences).remembered())
        wait_until(lambda: self.google.requests >= 2)
        self.assertIsNone(ClubSettingsService(self.saved()).remembered())

    def test_a_new_catalog_with_the_platform_keeps_publishing(self):
        self.remember_huaxia()
        sheet = self.add_spreadsheet("譜庫試算表")
        self.google.sheets.create_tab("社團設定", [["鍵", "值"], ["發譜平台", "華夏國樂社"]])
        self.save(self.open_with_folder())
        wait_until(lambda: ClubSettingsService(self.saved()).remembered() is PublishingPlatform.HUAXIA)
        self.assertIn(sheet, self.connected_to)

    def test_cancelling_after_picking_a_catalog_changes_nothing(self):
        self.remember_huaxia()
        self.preferences.save()
        self.add_spreadsheet("譜庫試算表")
        dialog = self.open_with_folder()
        dialog.reject()
        self.assertEqual(self.preferences.get(CATALOG_SPREADSHEET_ID_KEY), "old-sheet")
        self.assertEqual(self.saved().get(CATALOG_SPREADSHEET_ID_KEY), "old-sheet")
        self.assertIs(ClubSettingsService(self.saved()).remembered(), PublishingPlatform.HUAXIA)
        self.assertEqual(self.connected_to, [""])

    def test_unreadable_club_settings_of_the_new_catalog_are_written_to_the_log(self):
        self.remember_huaxia()
        self.add_spreadsheet("譜庫試算表")
        dialog = self.open_with_folder()
        self.google.fail_next(CatalogErrorKind.OFFLINE)
        self.save(dialog)
        wait_until(lambda: "讀取社團設定" in self.log_text())
        self.assertIn("連不上網路", self.log_text())
        self.assertIsNone(ClubSettingsService(self.saved()).remembered())


class TestBrowseFolders(CatalogFolderTestCase):
    """瀏覽 Drive 資料夾：子資料夾經譜庫服務列出，選了就找裡面的譜庫試算表；讀不到時瀏覽視窗出現提示列"""

    def browse(self, dialog: CatalogSettingsDialog, pick: str = ""):
        """按「瀏覽」；瀏覽視窗列出資料夾後選 pick 並按確定（pick 為空時只回傳瀏覽視窗，不選）"""
        seen = []

        def fake_exec(browser):
            seen.append(browser)
            tree = browser.findChild(QTreeWidget)
            bar = browser.findChild(CatalogErrorBar)
            wait_until(lambda: tree.findItems(pick or "*", Qt.MatchWildcard | Qt.MatchRecursive) or not bar.isHidden())
            if not pick:
                return QDialog.Rejected
            tree.setCurrentItem(tree.findItems(pick, Qt.MatchExactly | Qt.MatchRecursive)[0])
            browser.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
            return browser.result()
        button = next(b for b in dialog.findChildren(QPushButton) if b.text() == t("catalog.settings.browse"))
        with mock.patch.object(QDialog, "exec", fake_exec):
            button.click()
        return seen[0]

    def test_picking_a_folder_finds_its_catalog(self):
        sheet = self.add_spreadsheet("譜庫試算表")
        dialog = self.open_dialog()
        self.browse(dialog, "譜庫")
        wait_until(lambda: self.settled(dialog))
        self.save(dialog)
        self.assertEqual(self.preferences.get(CATALOG_SPREADSHEET_ID_KEY), sheet)

    def test_my_drive_itself_can_be_picked_and_is_saved_under_its_real_id(self):
        sheet = self.google.drive.add_file("譜庫試算表", mime_type=DRIVE_SPREADSHEET_MIME)
        dialog = self.open_dialog()
        self.browse(dialog, t("catalog.settings.my_drive"))
        wait_until(lambda: self.settled(dialog))
        self.save(dialog)
        self.assertEqual(self.preferences.get(CATALOG_ROOT_FOLDER_ID_KEY), ROOT)
        self.assertEqual(self.preferences.get(CATALOG_SPREADSHEET_ID_KEY), sheet)

    def test_unreadable_drive_shows_the_bar_in_the_browser(self):
        self.google.fail_next(CatalogErrorKind.RATE_LIMITED, times=6)
        dialog = self.open_dialog()
        browser = self.browse(dialog)
        self.assertTrue(self.bar_text(browser).startswith("讀不到譜庫｜Google 暫時不接受請求："))


if __name__ == "__main__":
    unittest.main()
