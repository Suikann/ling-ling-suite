# -*- coding: utf-8 -*-
"""
從譜庫下載回原位的接線測試（offscreen）

群組有只在譜庫的檔時，群組分頁出現「從譜庫下載回來」，按下後在背景下載回原位，群組恢復正常；
重新命名的預覽說明跳過只在譜庫的檔，旁邊也有這個按鈕。斷網時頂端顯示提示列。不測畫面。
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QLabel, QPushButton

from core.catalog_constants import SHEET_PARTS
from core.catalog_errors import CatalogErrorKind
from core.locale import t
from services.project_service import ProjectService
from test_catalog_link import records
from test_clear_local_window import ClearWindowTestCase
from tests.test_main_window import answering_prompts, previewing, wait_until
from ui.catalog_error_bar import CatalogErrorBar
from ui.group_panel import GroupTab


def read(path: str) -> bytes:
    """檔案內容"""
    with open(path, "rb") as f:
        return f.read()


class DownloadBackTestCase(ClearWindowTestCase):
    """主視窗開著已存入、已清除本機檔案的專案（情境見 DepositFixture）"""

    def setUp(self):
        super().setUp()
        self.clear(t("clear.confirm.delete"))

    def tab(self) -> GroupTab:
        """群組分頁"""
        return self.window.findChild(GroupTab)

    def download_button(self) -> QPushButton:
        """群組分頁的「從譜庫下載回來」"""
        return next(b for b in self.tab().findChildren(QPushButton) if b.text() == t("download.back"))

    def download_back(self, *answers):
        """按群組分頁的「從譜庫下載回來」，等背景的下載做完，回傳跳出的提示框"""
        with answering_prompts(*answers) as prompts:
            self.download_button().click()
            wait_until(lambda: self.window._task is None)
        return prompts


class TestDownloadBack(DownloadBackTestCase):
    """群組有只在譜庫的檔時出現「從譜庫下載回來」，下載後群組恢復正常"""

    def test_the_button_appears_and_downloading_brings_the_group_back_to_normal(self):
        self.assertFalse(self.download_button().isHidden())
        self.download_back()
        self.assertEqual([read(path) for path in self.paths()], [b"score", "二胡 I".encode(), "琵琶".encode()])
        tab = self.tab()
        self.assertTrue(self.download_button().isHidden())
        self.assertFalse(any(
            label.text() == t("catalog_only.group", count=3) and not label.isHidden()
            for label in tab.findChildren(QLabel)
        ))

    def test_a_newer_catalog_copy_comes_back_and_its_fingerprint_is_saved_to_the_project_file(self):
        pipa = next(r["drive_file_id"] for r in records(self.google, SHEET_PARTS) if r["instrument_name"] == "琵琶")
        self.google.drive.set_content(pipa, b"new pipa")
        self.download_back()
        self.assertEqual(read(self.parts[1].original_path), b"new pipa")
        saved = ProjectService().load_project(self.path)
        self.assertEqual(saved.groups[0].files[1].deposit_md5, self.google.drive.file(pipa).md5)
        self.assertFalse(self.window.project.is_modified())

    def test_files_that_could_not_come_back_are_listed(self):
        self.google.drive.corrupt_downloads(
            next(r["drive_file_id"] for r in records(self.google, SHEET_PARTS) if r["instrument_name"] == "二胡 I"),
        )
        prompts = self.download_back()
        self.assertFalse(os.path.exists(self.parts[0].original_path))
        self.assertTrue(any(self.parts[0].original_path in message for _, message in prompts), prompts)

    def test_without_network_the_error_bar_shows_and_nothing_is_written(self):
        self.google.fail_next(CatalogErrorKind.OFFLINE, times=20)
        self.download_back()
        self.assertFalse(self.window.findChild(CatalogErrorBar).isHidden())
        self.assertFalse(any(os.path.exists(path) for path in self.paths()))


class TestDownloadBackFromRenamePreview(DownloadBackTestCase):
    """重新命名的預覽說明跳過只在譜庫的檔，旁邊的「從譜庫下載回來」關掉預覽、下載回原位"""

    def test_the_rename_preview_offers_downloading_back(self):
        def press(dialog):
            button = next(b for b in dialog.findChildren(QPushButton) if b.text() == t("download.back"))
            self.assertFalse(button.isHidden())
            button.click()

        with answering_prompts(), previewing(press):
            self.click_button(t("panel.preview_rename"))
        wait_until(lambda: self.window._task is None)
        self.assertEqual([read(path) for path in self.paths()], [b"score", "二胡 I".encode(), "琵琶".encode()])


if __name__ == "__main__":
    unittest.main()
