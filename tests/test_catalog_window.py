# -*- coding: utf-8 -*-
"""
譜庫視窗的接線測試

讀取經譜庫服務、對假 Google；只測接線：讀到的東西出現在樹狀清單與詳細面板，
讀不到時頂端出現提示列（標題與按鈕對應錯誤類別）、內容區寫「讀不到」，紀錄檔寫一筆與詳細資料相同的內容。不測畫面。
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QTableWidget, QTabWidget, QTextEdit, QTreeWidget

from core.catalog_constants import SHEET_EDITIONS, SHEET_PIECES, PublishingPlatform
from core.catalog_errors import CatalogErrorKind
from core.constants import CLUB_PUBLISHING_PLATFORM_KEY
from core.locale import t
from fake_google_catalog import FakeGoogle
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.club_settings_service import ClubSettingsService
from services.file_service import FileService
from services.google_auth_service import GoogleAuthService
from services.preferences_service import PreferencesService
from services.secure_storage import SecureStorage
from tests.fake_browser import CallbackBrowser
from tests.fake_google import FakeTokenEndpoint, write_client_secrets
from tests.memory_keyring import MemoryKeyring
from tests.test_catalog_service import add_row, empty_catalog
from tests.test_main_window import _settle, wait_until
from ui.catalog_error_bar import CatalogErrorBar
from ui.catalog_window import CatalogWindow

# 各錯誤類別在提示列上的標題與處理按鈕（spec #81「錯誤呈現」）
EXPECTED = {
    CatalogErrorKind.OFFLINE: ("連不上網路", "重試"),
    CatalogErrorKind.SIGN_IN: ("Google 登入已失效", "重新登入 Google"),
    CatalogErrorKind.NO_PERMISSION: ("沒有權限", "重新登入 Google"),
    CatalogErrorKind.NOT_FOUND: ("找不到譜庫", "開啟譜庫設定"),
    CatalogErrorKind.RATE_LIMITED: ("Google 暫時不接受請求", "重試"),
    CatalogErrorKind.OTHER: ("譜庫讀寫失敗", "重試"),
}
REMEDY_BUTTONS = {"重試", "重新登入 Google", "開啟譜庫設定"}


class CatalogWindowTestCase(unittest.TestCase):
    """譜庫視窗測試的共用骨架：假 Google 裡有一個譜庫根資料夾、一首標記過的曲目與一個沒標記的資料夾"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.google = FakeGoogle()
        empty_catalog(self.google)
        self.root = self.google.drive.add_folder("譜庫")
        self.piece_folder = self.google.drive.add_folder("春江花月夜", self.root)
        self.google.drive.add_folder("未標記", self.root)
        self.google.drive.add_file("總譜.pdf", self.piece_folder)
        add_row(self.google, SHEET_PIECES, id="p1", title="春江花月夜", active_edition_id="e1")
        add_row(self.google, SHEET_EDITIONS, id="e1", piece_id="p1", drive_folder_id=self.piece_folder)
        self.log_dir = os.path.join(self.temp_dir, "logs")
        self.preferences = PreferencesService(os.path.join(self.temp_dir, "preferences.json"))
        self.preferences.set("catalog_spreadsheet_id", "sheet-1")
        self.preferences.set("catalog_root_folder_id", self.root)
        browser = CallbackBrowser()
        self.addCleanup(browser.finish)
        self.auth = GoogleAuthService(
            storage=SecureStorage("test-google", backend=MemoryKeyring()),
            request=FakeTokenEndpoint(),
            opener=browser,
            preferences=self.preferences,
            client_secrets_path=write_client_secrets(os.path.join(self.temp_dir, "client_secret.json")),
            legacy_token_path=os.path.join(self.temp_dir, "google_token.json"),
        )

    def open_window(self, use_fake: bool = True) -> CatalogWindow:
        """開啟譜庫視窗，等第一次讀取交回；use_fake 為 False 時照常以登入服務接 Google"""
        factory = (lambda: CatalogService(self.google.sheets, self.google.drive, sleep=lambda s: None)) if use_fake else None
        window = CatalogWindow(
            self.auth, self.preferences, catalog_factory=factory, log=ActivityLog(FileService(), self.log_dir),
        )
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        wait_until(lambda: self.tree(window).topLevelItemCount() > 0 or not self.bar(window).isHidden())
        return window

    def tree(self, window: CatalogWindow) -> QTreeWidget:
        """左邊的樹狀清單"""
        return window.findChild(QTreeWidget)

    def bar(self, window: CatalogWindow) -> CatalogErrorBar:
        """頂端的錯誤提示列"""
        return window.findChild(CatalogErrorBar)

    def bar_text(self, window: CatalogWindow) -> str:
        """提示列上的訊息"""
        return self.bar(window).findChild(QLabel).text()

    def remedy_buttons(self, window: CatalogWindow) -> set:
        """提示列上顯示的處理按鈕"""
        return {
            b.text() for b in self.bar(window).findChildren(QPushButton)
            if b.text() in REMEDY_BUTTONS and not b.isHidden()
        }

    def shows_unreadable(self, window: CatalogWindow) -> bool:
        """內容區有一段寫「讀不到」的文字正在顯示"""
        _settle()
        return any(
            label.text() == t("catalog.error.unreadable") and label.isVisibleTo(window)
            for label in window.findChildren(QLabel)
        )

    def log_text(self) -> str:
        """紀錄檔資料夾裡所有檔的內容"""
        if not os.path.isdir(self.log_dir):
            return ""
        texts = []
        for name in sorted(os.listdir(self.log_dir)):
            with open(os.path.join(self.log_dir, name), encoding="utf-8") as f:
                texts.append(f.read())
        return "".join(texts)

    def details_text(self, window: CatalogWindow) -> str:
        """提示列「詳細資料」的內容"""
        return self.bar(window).findChild(QTextEdit).toPlainText()

    def tree_names(self, window: CatalogWindow) -> list:
        """樹狀清單最上層的名稱"""
        tree = self.tree(window)
        return [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]


class TestReadsThroughCatalogService(CatalogWindowTestCase):
    """讀得到時，樹狀清單與詳細面板顯示譜庫服務讀到的內容"""

    def test_tree_lists_the_root_folder_and_marks_folders_with_a_piece(self):
        window = self.open_window()
        self.assertEqual(self.tree_names(window), ["春江花月夜  *", "未標記"])
        self.assertTrue(self.bar(window).isHidden())

    def test_selecting_a_piece_folder_shows_its_detail_and_edition_files(self):
        window = self.open_window()
        self.tree(window).setCurrentItem(self.tree(window).topLevelItem(0))
        wait_until(lambda: window.findChild(QTabWidget) is not None)
        tabs = window.findChild(QTabWidget)
        self.assertEqual(tabs.tabText(0), t("catalog.piece.title"))
        _settle()
        self.assertIn("總譜.pdf", self.table_texts(window))

    def table_texts(self, window: CatalogWindow) -> list:
        """詳細面板裡所有表格的文字"""
        texts = []
        for table in window.findChildren(QTableWidget):
            for row in range(table.rowCount()):
                for col in range(table.columnCount()):
                    item = table.item(row, col)
                    if item is not None:
                        texts.append(item.text())
        return texts


class TestErrorBar(CatalogWindowTestCase):
    """讀不到時，頂端的提示列寫出類別與怎麼辦，給對應的按鈕，內容區寫「讀不到」"""

    def test_each_kind_shows_its_title_and_remedy(self):
        for kind, (title, button) in EXPECTED.items():
            with self.subTest(kind=kind):
                self.google.fail_next(kind, times=6)
                window = self.open_window()
                self.assertFalse(self.bar(window).isHidden())
                self.assertTrue(self.bar_text(window).startswith(f"讀不到譜庫｜{title}："))
                self.assertEqual(self.remedy_buttons(window), {button})
                self.assertTrue(self.shows_unreadable(window))
                self.google.requests = 0

    def test_details_show_the_action_and_original_message(self):
        self.google.fail_at(1, CatalogErrorKind.NO_PERMISSION, "The caller does not have permission")
        window = self.open_window()
        details = self.bar(window).findChild(QTextEdit)
        self.assertTrue(details.isHidden())
        next(b for b in self.bar(window).findChildren(QPushButton) if b.text() == "詳細資料").click()
        self.assertFalse(details.isHidden())
        self.assertIn("讀取譜庫", details.toPlainText())
        self.assertIn("The caller does not have permission", details.toPlainText())

    def test_retry_reads_again_and_hides_the_bar(self):
        self.google.fail_at(1, CatalogErrorKind.OFFLINE)
        window = self.open_window()
        next(b for b in self.bar(window).findChildren(QPushButton) if b.text() == "重試").click()
        wait_until(lambda: self.tree(window).topLevelItemCount() > 0)
        self.assertTrue(self.bar(window).isHidden())
        self.assertFalse(self.shows_unreadable(window))

    def test_not_signed_in_asks_to_sign_in_again(self):
        window = self.open_window(use_fake=False)
        self.assertTrue(self.bar_text(window).startswith("讀不到譜庫｜Google 登入已失效："))
        self.assertEqual(self.remedy_buttons(window), {"重新登入 Google"})

    def test_a_failed_piece_detail_says_unreadable(self):
        window = self.open_window()
        self.google.fail_next(CatalogErrorKind.OFFLINE)
        self.tree(window).setCurrentItem(self.tree(window).topLevelItem(0))
        wait_until(lambda: not self.bar(window).isHidden())
        self.assertTrue(self.bar_text(window).startswith("讀不到譜庫｜連不上網路："))
        self.assertTrue(self.shows_unreadable(window))
        self.assertIsNone(window.findChild(QTabWidget))


class TestRemembersClubSettings(CatalogWindowTestCase):
    """連上譜庫時讀社團設定，把啟用的發譜平台記在偏好設定；讀不到時沿用記住的，不另外顯示提示列"""

    def remembered(self):
        """偏好設定裡記住的發譜平台"""
        return ClubSettingsService(self.preferences).remembered()

    def test_opening_the_window_remembers_the_publishing_platform(self):
        self.google.sheets.create_tab("社團設定", [["鍵", "值"], ["發譜平台", "華夏國樂社"]])
        self.open_window()
        wait_until(lambda: self.remembered() is PublishingPlatform.HUAXIA)
        reloaded = PreferencesService(self.preferences.path)
        reloaded.load()
        self.assertIs(ClubSettingsService(reloaded).remembered(), PublishingPlatform.HUAXIA)

    def test_a_removed_row_is_remembered_as_none(self):
        self.preferences.set(CLUB_PUBLISHING_PLATFORM_KEY, PublishingPlatform.HUAXIA.value)
        self.open_window()
        wait_until(lambda: self.remembered() is None)

    def test_unreadable_club_settings_keep_the_remembered_result_without_a_bar(self):
        self.preferences.set(CLUB_PUBLISHING_PLATFORM_KEY, PublishingPlatform.HUAXIA.value)
        self.google.sheets.create_tab("社團設定", [["鍵", "值"]])
        self.google.fail_at(3, CatalogErrorKind.OFFLINE)
        window = self.open_window()
        wait_until(lambda: self.google.requests >= 3)
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            QApplication.processEvents()
        self.assertEqual(self.google.requests, 3)
        self.assertTrue(self.bar(window).isHidden())
        self.assertIs(self.remembered(), PublishingPlatform.HUAXIA)

    def test_nothing_is_read_when_the_catalog_is_unreadable(self):
        self.preferences.set(CLUB_PUBLISHING_PLATFORM_KEY, PublishingPlatform.HUAXIA.value)
        self.google.fail_at(1, CatalogErrorKind.OFFLINE)
        self.open_window()
        self.assertEqual(self.google.requests, 1)
        self.assertIs(self.remembered(), PublishingPlatform.HUAXIA)


class TestFailuresAreLogged(CatalogWindowTestCase):
    """讀不到時紀錄檔寫一筆，與提示列的詳細資料相同；讀得到時不寫"""

    def test_a_failed_read_writes_the_banner_details_to_the_log(self):
        self.google.fail_at(1, CatalogErrorKind.NO_PERMISSION, "The caller does not have permission")
        window = self.open_window()
        self.assertEqual(self.log_text().strip(), self.details_text(window))
        self.assertIn("讀取譜庫", self.log_text())
        self.assertIn("沒有權限", self.log_text())
        self.assertIn("The caller does not have permission", self.log_text())

    def test_a_failed_piece_detail_logs_the_piece_title(self):
        window = self.open_window()
        self.google.fail_next(CatalogErrorKind.OFFLINE)
        self.tree(window).setCurrentItem(self.tree(window).topLevelItem(0))
        wait_until(lambda: not self.bar(window).isHidden())
        self.assertIn("春江花月夜", self.log_text())
        self.assertIn("連不上網路", self.log_text())

    def test_successful_reads_write_nothing(self):
        self.open_window()
        self.assertEqual(self.log_text(), "")


if __name__ == '__main__':
    unittest.main()
