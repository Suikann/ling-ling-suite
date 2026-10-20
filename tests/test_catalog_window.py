# -*- coding: utf-8 -*-
"""
譜庫視窗的接線測試

讀寫經譜庫服務、對假 Google；只測接線：讀到的東西出現在樹狀清單與詳細面板，寫入後假 Google 的試算表與 Drive 內容正確，
讀不到時頂端出現提示列（標題與按鈕對應錯誤類別）、內容區寫「讀不到」，寫不進去時提示列以「寫不進譜庫」開頭，
紀錄檔寫一筆與詳細資料相同的內容。不測畫面。
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox,
    QPushButton, QTableWidget, QTabWidget, QTextEdit, QTreeWidget, QWidget,
)

from core.catalog_constants import (
    SHEET_COMPOSERS, SHEET_INSTRUMENTATION_LOG, SHEET_EDITIONS, SHEET_MOVEMENTS, SHEET_PARTS, SHEET_PERFORMANCES, SHEET_PIECES, PublishingPlatform,
)
from core.catalog_errors import CatalogErrorKind
from core.constants import (
    CATALOG_ROOT_FOLDER_ID_KEY, CATALOG_SPREADSHEET_ID_KEY, CLUB_PUBLISHING_PLATFORM_KEY, OrchestraType,
)
from core.locale import t
from fake_google_catalog import FakeGoogle
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.club_settings_service import ClubSettingsService
from services.download_service import DownloadService
from services.file_service import FileService
from services.google_auth_service import GoogleAuthService
from services.preferences_service import PreferencesService
from services.secure_storage import SecureStorage
from tests.fake_browser import CallbackBrowser
from tests.fake_google import FakeTokenEndpoint, write_client_secrets
from tests.memory_keyring import MemoryKeyring
from tests.test_catalog_service import add_row, empty_catalog
from tests.test_main_window import _settle, answering_prompts, wait_until
from ui.catalog_error_bar import CatalogErrorBar
from ui.catalog_window import CatalogWindow
from ui.instrumentation_table import InstrumentationTable

# 各錯誤類別在提示列上的標題與處理按鈕（spec #81「錯誤呈現」）
EXPECTED = {
    CatalogErrorKind.OFFLINE: ("連不上網路", "重試"),
    CatalogErrorKind.SIGN_IN: ("Google 登入已失效", "重新登入 Google"),
    CatalogErrorKind.NO_PERMISSION: ("沒有權限", "重新登入 Google"),
    CatalogErrorKind.NOT_FOUND: ("找不到譜庫", "開啟譜庫設定"),
    CatalogErrorKind.RATE_LIMITED: ("Google 暫時不接受請求", "重試"),
    CatalogErrorKind.OTHER: ("譜庫讀寫失敗", "重試"),
}
REMEDY_BUTTONS = {"重試", "重新登入 Google", "開啟譜庫設定", "重新讀取"}


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
        self.preferences.set(CATALOG_SPREADSHEET_ID_KEY, "sheet-1")
        self.preferences.set(CATALOG_ROOT_FOLDER_ID_KEY, self.root)
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
            downloads=DownloadService(FileService(), temp_dir=os.path.join(self.temp_dir, "download-temp")),
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
        files = tabs.widget(1).findChild(QListWidget)
        wait_until(lambda: files.count() > 0)
        self.assertEqual(files.item(0).text(), "總譜.pdf")


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

    def test_unreadable_club_settings_are_written_to_the_log(self):
        self.google.sheets.create_tab("社團設定", [["鍵", "值"]])
        self.google.fail_at(3, CatalogErrorKind.OFFLINE, "club settings unreachable")
        self.open_window()
        wait_until(lambda: "club settings unreachable" in self.log_text())
        self.assertIn("讀取社團設定", self.log_text())

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



class WritesTestCase(CatalogWindowTestCase):
    """寫入測試的共用工具：等背景讀取都交回、打開曲目的頁籤、找按鈕、看工作表的列"""

    def idle(self) -> None:
        """等背景讀取都交回：假 Google 一段時間沒有新請求"""
        count = -1
        while count != self.google.requests:
            count = self.google.requests
            for _ in range(20):
                QApplication.processEvents()
                time.sleep(0.005)

    def open_settled(self) -> CatalogWindow:
        """開啟譜庫視窗，等連上譜庫後的讀取都交回"""
        window = self.open_window()
        self.idle()
        return window

    def select(self, window: CatalogWindow, name: str) -> None:
        """在樹狀清單選取最上層名稱為 name 的項目，等讀取交回"""
        tree = self.tree(window)
        tree.setCurrentItem(next(
            tree.topLevelItem(i) for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).text(0) == name
        ))
        self.idle()

    def open_piece(self, window: CatalogWindow) -> QTabWidget:
        """選取標記過的曲目資料夾，等曲目的頁籤出現"""
        self.select(window, "春江花月夜  *")
        wait_until(lambda: window.findChild(QTabWidget) is not None)
        self.idle()
        return window.findChild(QTabWidget)

    def tab(self, window: CatalogWindow, index: int, button_text: str) -> QWidget:
        """等曲目重新顯示到第 index 個頁籤裡有 button_text 的按鈕，回傳那個頁籤"""
        def page():
            """目前的頁籤（還沒顯示時為 None）"""
            tabs = window.findChild(QTabWidget)
            return tabs.widget(index) if tabs is not None else None
        wait_until(lambda: page() is not None and any(b.text() == button_text for b in page().findChildren(QPushButton)))
        self.idle()
        return page()

    @staticmethod
    def button(parent: QWidget, text: str) -> QPushButton:
        """parent 裡文字為 text 的第一顆按鈕"""
        return next(b for b in parent.findChildren(QPushButton) if b.text() == text)

    def rows(self, sheet: str) -> list:
        """工作表的資料列（依欄位名稱）"""
        rows = self.google.sheets.rows(sheet)
        return [dict(zip(rows[0], row + [""] * (len(rows[0]) - len(row)))) for row in rows[1:]]

    def wait_for_rows(self, sheet: str, count: int) -> list:
        """等工作表有 count 列，回傳它們"""
        wait_until(lambda: len(self.rows(sheet)) == count)
        self.idle()
        return self.rows(sheet)

    def subfolder(self, parent: str, name: str):
        """parent 裡名稱為 name 的資料夾（不算請求次數的讀法：直接看假 Drive）"""
        catalog = CatalogService(self.google.sheets, self.google.drive)
        requests = self.google.requests
        found = next((f for f in catalog.subfolders(parent) if f.name == name), None)
        self.google.requests = requests
        return found

    def pick_files(self, *names: str, before_ok=lambda: None):
        """替換「從其他位置移入檔案」的挑選視窗：勾選最上層名稱在 names 裡的檔，做 before_ok 後按確定"""
        def pick(picker):
            """勾選後按確定"""
            tree = picker.findChild(QTreeWidget)
            wait_until(lambda: tree.topLevelItemCount() > 0)
            self.idle()
            for i in range(tree.topLevelItemCount()):
                if tree.topLevelItem(i).text(0) in names:
                    tree.topLevelItem(i).setCheckState(0, Qt.Checked)
            before_ok()
            picker.findChild(QDialogButtonBox).button(QDialogButtonBox.Ok).click()
            return picker.result()
        return mock.patch.object(QDialog, "exec", pick)


class TestWrites(WritesTestCase):
    """譜庫視窗的增刪改經譜庫服務寫進假 Google"""

    def test_tagging_a_folder_adds_a_piece_and_marks_the_folder(self):
        window = self.open_settled()
        self.select(window, "未標記")
        self.button(window, t("catalog.drive.tag_as_piece")).click()
        wait_until(lambda: "未標記  *" in self.tree_names(window))
        piece = next(p for p in self.rows(SHEET_PIECES) if p["title"] == "未標記")
        edition = next(e for e in self.rows(SHEET_EDITIONS) if e["id"] == piece["active_edition_id"])
        self.assertEqual(edition["piece_id"], piece["id"])
        self.assertEqual(edition["drive_folder_id"], self.subfolder(self.root, "未標記").id)

    def test_saving_a_piece_writes_its_fields_and_adds_a_new_composer(self):
        window = self.open_settled()
        info = self.open_piece(window).widget(0)
        next(e for e in info.findChildren(QLineEdit) if e.text() == "春江花月夜").setText("=春江")
        info.findChild(QComboBox).setEditText("彭修文")
        self.button(info, t("catalog.save")).click()
        composers = self.wait_for_rows(SHEET_COMPOSERS, 1)
        wait_until(lambda: self.rows(SHEET_PIECES)[0]["title"] == "=春江")
        self.assertEqual(composers[0]["name"], "彭修文")
        self.assertEqual(self.rows(SHEET_PIECES)[0]["composer_id"], composers[0]["id"])
        self.assertEqual(self.rows(SHEET_PIECES)[0]["active_edition_id"], "e1")

    def test_deleting_a_piece_removes_it_with_its_editions_and_performances(self):
        add_row(self.google, SHEET_PERFORMANCES, id="r1", piece_id="p1")
        window = self.open_settled()
        info = self.open_piece(window).widget(0)
        with answering_prompts(QMessageBox.Yes):
            self.button(info, t("catalog.piece.delete")).click()
            self.wait_for_rows(SHEET_PIECES, 0)
        self.assertEqual((self.rows(SHEET_EDITIONS), self.rows(SHEET_PERFORMANCES)), ([], []))

    def test_adding_an_edition_and_setting_it_active(self):
        window = self.open_settled()
        editions = self.open_piece(window).widget(4)
        with mock.patch.object(QInputDialog, "getText", return_value=("人民音樂", True)):
            self.button(editions, t("catalog.edition.add")).click()
        added = next(e for e in self.wait_for_rows(SHEET_EDITIONS, 2) if e["id"] != "e1")
        self.assertEqual((added["piece_id"], added["publisher"]), ("p1", "人民音樂"))
        self.button(self.tab(window, 4, t("catalog.edition.set_active")), t("catalog.edition.set_active")).click()
        wait_until(lambda: self.rows(SHEET_PIECES)[0]["active_edition_id"] == added["id"])

    def test_adding_and_deleting_a_movement_with_its_parts(self):
        window = self.open_settled()
        movements = self.open_piece(window).widget(3)
        self.button(movements, t("catalog.movement.add")).click()
        added = self.wait_for_rows(SHEET_MOVEMENTS, 1)[0]
        self.assertEqual((added["piece_id"], added["movement_number"]), ("p1", "1"))
        add_row(self.google, SHEET_PARTS, id="t1", edition_id="e1", movement_id=added["id"])
        page = self.tab(window, 3, t("catalog.movement.delete"))
        with answering_prompts(QMessageBox.Yes):
            self.button(page, t("catalog.movement.delete")).click()
            self.wait_for_rows(SHEET_MOVEMENTS, 0)
        self.assertEqual(self.rows(SHEET_PARTS), [])

    def test_a_new_movement_is_numbered_after_the_movements_read_just_before_writing(self):
        window = self.open_settled()
        movements = self.open_piece(window).widget(3)
        # 畫面顯示之後，另一台電腦新增了第 1 樂章
        add_row(self.google, SHEET_MOVEMENTS, id="m1", piece_id="p1", movement_number="1", sort_order="1")
        self.button(movements, t("catalog.movement.add")).click()
        added = next(m for m in self.wait_for_rows(SHEET_MOVEMENTS, 2) if m["id"] != "m1")
        self.assertEqual((added["movement_number"], added["sort_order"]), ("2", "2"))

    def test_adding_saving_and_deleting_a_performance(self):
        window = self.open_settled()
        performances = self.open_piece(window).widget(2)
        self.button(performances, t("catalog.performance.add")).click()
        added = self.wait_for_rows(SHEET_PERFORMANCES, 1)[0]
        self.assertEqual((added["piece_id"], added["status"]), ("p1", "planned"))
        page = self.tab(window, 2, t("catalog.performance.delete"))
        page.findChild(QTableWidget).item(0, 1).setText("中山堂")
        self.button(page, t("catalog.save")).click()
        wait_until(lambda: self.rows(SHEET_PERFORMANCES)[0]["venue"] == "中山堂")
        page = self.tab(window, 2, t("catalog.performance.delete"))
        with answering_prompts(QMessageBox.Yes):
            self.button(page, t("catalog.performance.delete")).click()
            self.wait_for_rows(SHEET_PERFORMANCES, 0)

    def test_creating_a_subfolder_in_the_selected_folder(self):
        window = self.open_settled()
        self.select(window, "未標記")
        with mock.patch.object(QInputDialog, "getText", return_value=("新資料夾", True)):
            self.button(window, t("catalog.toolbar.new_folder")).click()
        unmarked = self.subfolder(self.root, "未標記")
        wait_until(lambda: self.subfolder(unmarked.id, "新資料夾") is not None)

    def test_collecting_files_moves_the_picked_ones_into_the_folder(self):
        stray = self.google.drive.add_file("散落.pdf", self.root)
        window = self.open_settled()
        self.select(window, "未標記")
        with self.pick_files("散落.pdf"), answering_prompts() as shown:
            self.button(window, t("catalog.drive.collect_files")).click()
            wait_until(lambda: shown)
        self.assertEqual(self.google.drive.file(stray).parents, (self.subfolder(self.root, "未標記").id,))
        self.assertEqual(shown, [(t("catalog.drive.collect_files"), t("catalog.drive.collect_result", moved=1, total=1))])

    def test_applying_a_template_opens_the_drive_rename_with_the_edition_files(self):
        window = self.open_settled()
        parts = self.open_piece(window).widget(1)
        opened = []

        def look(dialog):
            """記下 Drive 重新命名對話框的群組清單後取消"""
            groups = dialog.findChild(QListWidget)
            opened.append([groups.item(i).text() for i in range(groups.count())])
            return QDialog.Rejected
        with mock.patch.object(QDialog, "exec", look):
            self.button(parts, t("catalog.drive.apply_template")).click()
            wait_until(lambda: opened)
        self.assertEqual(opened, [["春江花月夜  (1)"]])


class TestWriteFailures(WritesTestCase):
    """寫不進去時，頂端的提示列以「寫不進譜庫」開頭並寫進紀錄檔；重試補上，不重複新增"""

    def test_a_failed_save_shows_the_write_bar_and_retry_saves(self):
        window = self.open_settled()
        info = self.open_piece(window).widget(0)
        next(e for e in info.findChildren(QLineEdit) if e.text() == "春江花月夜").setText("春江")
        self.google.fail_next(CatalogErrorKind.OFFLINE)
        self.button(info, t("catalog.save")).click()
        wait_until(lambda: not self.bar(window).isHidden())
        self.assertTrue(self.bar_text(window).startswith("寫不進譜庫｜連不上網路："))
        self.assertEqual(self.log_text().strip(), self.details_text(window))
        self.assertIn("春江花月夜", self.log_text())
        self.assertEqual(self.rows(SHEET_PIECES)[0]["title"], "春江花月夜")
        self.button(self.bar(window), "重試").click()
        wait_until(lambda: self.rows(SHEET_PIECES)[0]["title"] == "春江")
        self.assertTrue(self.bar(window).isHidden())

    def test_retrying_an_add_whose_response_was_lost_adds_one_row(self):
        window = self.open_settled()
        performances = self.open_piece(window).widget(2)
        # 第 1 次請求讀 ID，第 2 次新增：Google 做完了，回應卻沒收到
        self.google.fail_at(self.google.requests + 2, CatalogErrorKind.OFFLINE, applied=True)
        self.button(performances, t("catalog.performance.add")).click()
        wait_until(lambda: not self.bar(window).isHidden())
        self.button(self.bar(window), "重試").click()
        wait_until(lambda: self.bar(window).isHidden())
        self.idle()
        self.assertEqual(len(self.rows(SHEET_PERFORMANCES)), 1)

    def test_saving_a_record_another_computer_deleted_offers_to_reload_instead_of_retrying(self):
        add_row(self.google, SHEET_MOVEMENTS, id="m1", piece_id="p1", movement_number="1")
        window = self.open_settled()
        self.open_piece(window)
        page = self.tab(window, 3, t("catalog.movement.delete"))
        # 畫面顯示之後，另一台電腦刪掉了這個樂章
        self.google.sheets.create_tab(SHEET_MOVEMENTS, self.google.sheets.rows(SHEET_MOVEMENTS)[:1])
        page.findChild(QTableWidget).item(0, 1).setText("慢板")
        self.button(page, t("catalog.save")).click()
        wait_until(lambda: not self.bar(window).isHidden())
        self.assertTrue(self.bar_text(window).startswith("寫不進譜庫｜這筆已經不在譜庫："))
        self.assertEqual(self.remedy_buttons(window), {"重新讀取"})
        self.button(self.bar(window), "重新讀取").click()

        def movements_shown():
            """樂章頁籤重新顯示，表格裡沒有樂章（換掉的頁籤等事件迴圈才刪掉，所以看每一組）"""
            return any(tabs.widget(3).findChild(QTableWidget).rowCount() == 0 for tabs in window.findChildren(QTabWidget))
        wait_until(movements_shown)
        self.assertTrue(self.bar(window).isHidden())
        self.assertEqual(self.rows(SHEET_MOVEMENTS), [])

    def test_a_failed_move_names_the_file_and_retry_moves_the_rest(self):
        first = self.google.drive.add_file("乙.pdf", self.root)
        second = self.google.drive.add_file("甲.pdf", self.root)
        window = self.open_settled()
        self.select(window, "未標記")
        target = self.subfolder(self.root, "未標記").id
        # 依名稱排序先搬乙、再搬甲：搬甲時連不上
        fail_second = lambda: self.google.fail_at(self.google.requests + 2, CatalogErrorKind.OFFLINE)
        with self.pick_files("乙.pdf", "甲.pdf", before_ok=fail_second):
            self.button(window, t("catalog.drive.collect_files")).click()
            wait_until(lambda: not self.bar(window).isHidden())
        self.assertTrue(self.bar_text(window).startswith("寫不進譜庫｜連不上網路："))
        self.assertIn("甲.pdf", self.details_text(window))
        self.assertEqual((self.google.drive.file(first).parents, self.google.drive.file(second).parents),
                         ((target,), (self.root,)))
        requests = self.google.requests
        with answering_prompts() as shown:
            self.button(self.bar(window), "重試").click()
            wait_until(lambda: shown)
        self.assertEqual(self.google.drive.file(second).parents, (target,))
        self.assertGreaterEqual(self.google.requests, requests + 1)


if __name__ == '__main__':
    unittest.main()


class TestEditionPage(WritesTestCase):
    """版本頁：主視窗同一個編制表，狀態由登記推算；沒登記的檔指定給聲部，按儲存才寫進譜庫"""

    def setUp(self):
        super().setUp()
        self.old = self.google.drive.add_file("二胡 I 舊.pdf", self.piece_folder)
        strings = self.google.drive.add_folder("拉弦", self.piece_folder)
        self.erhu = self.google.drive.add_file("二胡 I.pdf", strings)
        self.pipa = self.google.drive.add_file("琵琶.pdf", self.piece_folder)
        add_row(self.google, SHEET_PARTS, id="pt1", edition_id="e1", instrument_name="二胡 I", sort_order="1",
                drive_file_id=self.old, file_name="二胡 I 舊.pdf")
        add_row(self.google, SHEET_PARTS, id="pt2", edition_id="e1", instrument_name="琵琶", sort_order="2")

    def open_page(self):
        """開啟曲目的版本頁，等它讀到那一處；回傳（版本頁、編制表、聲部表）"""
        window = self.open_settled()
        page = self.open_piece(window).widget(1)
        editor = page.findChild(InstrumentationTable)
        wait_until(lambda: editor.isEnabled())
        self.idle()
        table = next(table for table in editor.findChildren(QTableWidget) if table.objectName() != "score_row")
        return page, editor, table

    def assign(self, page, table, file_name: str, row: int) -> None:
        """在右欄選檔、在編制表選聲部，按指定"""
        files = page.findChild(QListWidget)
        files.setCurrentItem(next(files.item(i) for i in range(files.count()) if files.item(i).text() == file_name))
        table.selectRow(row)
        self.button(page, t("catalog.page.assign")).click()

    def save(self, page) -> None:
        """按版本頁的儲存，等寫完、重新讀取交回"""
        writes = len(self.google.sheets.writes)
        self.button(page, t("catalog.save")).click()
        wait_until(lambda: len(self.google.sheets.writes) > writes)
        self.idle()

    def part(self, voice: str) -> dict:
        """分譜工作表裡這個聲部的一列"""
        return next(r for r in self.rows(SHEET_PARTS) if r["instrument_name"] == voice)

    def test_the_main_window_table_shows_derived_status_without_a_status_dropdown(self):
        page, editor, table = self.open_page()
        self.assertIs(type(editor), InstrumentationTable)
        status = editor.column("status")
        self.assertEqual([table.item(row, status).text() for row in range(table.rowCount())],
                         [t("catalog.page.present"), t("catalog.page.missing")])
        for row in range(table.rowCount()):
            self.assertIsNone(table.cellWidget(row, status))
            self.assertFalse(table.item(row, status).flags() & Qt.ItemIsEditable)
        files = page.findChild(QListWidget)
        self.assertEqual([files.item(i).text() for i in range(files.count())],
                         ["拉弦/二胡 I.pdf", "琵琶.pdf", "總譜.pdf"])

    def test_an_assigned_file_is_written_only_when_saved(self):
        page, editor, table = self.open_page()
        self.assign(page, table, "琵琶.pdf", 1)
        self.idle()
        self.assertEqual(self.google.sheets.writes, [])
        self.assertEqual(self.part("琵琶")["drive_file_id"], "")
        self.assertEqual(table.item(1, editor.column("status")).text(), t("catalog.page.pending"))
        self.save(page)
        self.assertEqual((self.part("琵琶")["drive_file_id"], self.part("琵琶")["file_name"]), (self.pipa, "琵琶.pdf"))
        wait_until(lambda: table.item(1, editor.column("status")).text() == t("catalog.page.present"))

    def test_assigning_over_a_registered_file_asks_and_trashes_the_old_one_on_save(self):
        page, editor, table = self.open_page()
        with answering_prompts(QMessageBox.Yes) as shown:
            self.assign(page, table, "拉弦/二胡 I.pdf", 0)
        self.assertIn("30 天", shown[0][1])
        self.assertFalse(self.google.drive.is_trashed(self.old))
        self.save(page)
        wait_until(lambda: self.google.drive.is_trashed(self.old))
        self.assertEqual(self.part("二胡 I")["drive_file_id"], self.erhu)

    def test_declining_the_overwrite_keeps_the_registration(self):
        page, editor, table = self.open_page()
        with answering_prompts(QMessageBox.No):
            self.assign(page, table, "拉弦/二胡 I.pdf", 0)
        self.assertEqual(table.item(0, editor.column("status")).text(), t("catalog.page.present"))

    def test_the_score_row_takes_a_file(self):
        page, editor, table = self.open_page()
        files = page.findChild(QListWidget)
        files.setCurrentItem(files.findItems("總譜.pdf", Qt.MatchExactly)[0])
        next(t for t in editor.findChildren(QTableWidget) if t.objectName() == "score_row").selectRow(0)
        self.button(page, t("catalog.page.assign")).click()
        self.save(page)
        score = next(r for r in self.rows(SHEET_PARTS) if r["is_score"] == "TRUE")
        self.assertEqual((score["edition_id"], score["movement_id"], score["file_name"]), ("e1", "", "總譜.pdf"))

    def test_instrumentation_edits_go_through_the_write_back_and_are_logged_as_the_catalog_window(self):
        page, editor, table = self.open_page()
        table.cellWidget(1, editor.column("minimum")).setValue(3)
        self.idle()
        self.assertEqual(self.google.sheets.writes, [])
        self.assertEqual(self.part("琵琶")["headcount_min"], "")
        self.save(page)
        self.assertEqual(self.part("琵琶")["headcount_min"], "3")
        log = [r for r in self.rows(SHEET_INSTRUMENTATION_LOG) if r["field"] == "headcount"]
        self.assertEqual([(r["voice"], r["source"], r["new_value"]) for r in log], [("琵琶", "catalog_window", "3")])

    def test_a_cell_changed_elsewhere_opens_the_conflict_screen_and_cancelling_writes_nothing(self):
        page, editor, table = self.open_page()
        table.cellWidget(1, editor.column("minimum")).setValue(3)
        self.assign(page, table, "琵琶.pdf", 1)
        self.google.sheets.set_cell(SHEET_PARTS, "J3", "5")
        opened = []

        def cancel(dialog):
            """記下開了衝突畫面後取消"""
            opened.append(type(dialog).__name__)
            return QDialog.Rejected
        with mock.patch.object(QDialog, "exec", cancel):
            self.button(page, t("catalog.save")).click()
            wait_until(lambda: opened)
        self.idle()
        self.assertEqual(opened, ["CatalogConflictDialog"])
        self.assertEqual(self.google.sheets.writes, [])
        self.assertEqual(self.part("琵琶")["drive_file_id"], "")

    def test_the_conflict_screen_button_says_write(self):
        page, editor, table = self.open_page()
        table.cellWidget(1, editor.column("minimum")).setValue(3)
        self.google.sheets.set_cell(SHEET_PARTS, "J3", "5")
        buttons = []

        def look(dialog):
            """記下確定按鈕的文字後取消"""
            buttons.append(dialog._confirm.text())
            return QDialog.Rejected
        with mock.patch.object(QDialog, "exec", look):
            self.button(page, t("catalog.save")).click()
            wait_until(lambda: buttons)
        self.assertEqual(buttons, [t("catalog.sections.confirm")])

    def test_removing_a_voice_with_a_drive_file_explains_it_for_the_catalog_window(self):
        page, editor, table = self.open_page()
        table.selectRow(0)
        with answering_prompts(QMessageBox.Yes) as shown:
            editor._remove_selected()
        self.assertIn(t("catalog.page.filed_voices", voices="二胡 I"), [text for _, text in shown])

    def test_discarding_unsaved_edits_asks_first(self):
        page, editor, table = self.open_page()
        table.cellWidget(1, editor.column("minimum")).setValue(3)
        self.idle()
        with answering_prompts(QMessageBox.No) as shown:
            self.button(page, t("catalog.page.discard")).click()
        self.assertEqual([text for _, text in shown], [t("catalog.page.discard_confirm")])
        self.assertEqual(table.cellWidget(1, editor.column("minimum")).value(), 3)
        with answering_prompts(QMessageBox.Yes):
            self.button(page, t("catalog.page.discard")).click()
        wait_until(lambda: editor.isEnabled())
        self.idle()
        table = next(t_ for t_ in editor.findChildren(QTableWidget) if t_.objectName() != "score_row")
        self.assertEqual(table.cellWidget(1, editor.column("minimum")).value(), 0)

    def test_switching_the_movement_with_unsaved_edits_asks_and_can_stay(self):
        add_row(self.google, SHEET_MOVEMENTS, id="m1", piece_id="p1", movement_number="1")
        page, editor, table = self.open_page()
        table.cellWidget(1, editor.column("minimum")).setValue(3)
        self.idle()
        movement = page._movement_combo
        with answering_prompts(QMessageBox.No) as shown:
            movement.setCurrentIndex(1)
        self.assertEqual([text for _, text in shown], [t("catalog.page.discard_confirm")])
        self.assertEqual(movement.currentIndex(), 0)
        self.assertEqual(table.cellWidget(1, editor.column("minimum")).value(), 3)

    def test_the_orchestra_type_starts_as_the_open_projects_and_can_be_switched(self):
        window = CatalogWindow(
            self.auth, self.preferences, log=ActivityLog(FileService(), self.log_dir),
            catalog_factory=lambda: CatalogService(self.google.sheets, self.google.drive, sleep=lambda s: None),
            orchestra_type=OrchestraType.WESTERN,
        )
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        wait_until(lambda: self.tree(window).topLevelItemCount() > 0)
        self.idle()
        page = self.open_piece(window).widget(1)
        editor = page.findChild(InstrumentationTable)
        wait_until(lambda: editor.isEnabled())
        self.assertEqual(page._orchestra_combo.currentData(), OrchestraType.WESTERN.value)
        self.assertIs(page._project.orchestra_type, OrchestraType.WESTERN)
        page._orchestra_combo.setCurrentIndex(page._orchestra_combo.findData(OrchestraType.CHINESE.value))
        wait_until(lambda: page._project.orchestra_type is OrchestraType.CHINESE and editor.isEnabled())
