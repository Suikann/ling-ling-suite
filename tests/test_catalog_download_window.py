# -*- coding: utf-8 -*-
"""
從譜庫下載的接線測試（offscreen）

群組有只在譜庫的檔時，群組分頁出現「從譜庫下載回來」，按下後在背景下載回原位，群組恢復正常；
重新命名的預覽說明跳過只在譜庫的檔，旁邊也有這個按鈕。譜庫視窗的版本頁按「下載…」勾選檔或整個版本，
下載到自選的資料夾。斷網時頂端顯示提示列。不測畫面。
"""
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QFileDialog, QLabel, QListWidget, QPushButton

from core.catalog_constants import SHEET_PARTS
from core.catalog_errors import CatalogErrorKind
from core.locale import t
from services.project_service import ProjectService
from test_catalog_link import records
from test_clear_local_window import ClearWindowTestCase
from tests.test_main_window import answering_prompts, previewing, wait_until
from tests.test_catalog_window import WritesTestCase
from ui.catalog_download_dialog import CatalogDownloadDialog
from ui.catalog_error_bar import CatalogErrorBar
from ui.group_panel import GroupTab
from ui.instrumentation_table import InstrumentationTable


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
        """切到群組分頁按「從譜庫下載回來」，等背景的下載做完，回傳跳出的提示框"""
        self.window._tab_widget.setCurrentWidget(self.tab())
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
        self.assertIs(self.window._tab_widget.currentWidget(), tab)
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


class TestDownloadFromEditionPage(WritesTestCase):
    """版本頁的「下載…」：勾選幾份檔或整個版本，下載到自選的資料夾"""

    def setUp(self):
        super().setUp()
        strings = self.google.drive.add_folder("拉弦", self.piece_folder)
        self.google.drive.add_file("二胡 I.pdf", strings, b"erhu")
        self.destination = os.path.join(self.temp_dir, "下載")
        os.makedirs(self.destination)

    def download(self, choose, *answers):
        """開版本頁按「下載…」，在挑選視窗做 choose 後按下載、資料夾選 self.destination；等下載做完，回傳（視窗，提示框）"""
        window = self.open_settled()
        page = self.open_piece(window).widget(1)
        wait_until(lambda: page.findChild(InstrumentationTable).isEnabled())
        self.idle()

        def pick(dialog):
            choose(dialog)
            self.button(dialog, t("catalog.download.start")).click()
            return dialog.result()

        with mock.patch.object(CatalogDownloadDialog, "exec", pick), \
                mock.patch.object(QFileDialog, "getExistingDirectory", return_value=self.destination), \
                answering_prompts(*answers) as prompts:
            self.button(page, t("catalog.download.button")).click()
            wait_until(lambda: window._download_task is None)
        return window, prompts

    def downloaded(self) -> dict:
        """自選的資料夾裡的檔：相對路徑（以 / 分隔）→ 內容"""
        found = {}
        for folder, _, names in os.walk(self.destination):
            for name in names:
                path = os.path.join(folder, name)
                found[os.path.relpath(path, self.destination).replace(os.sep, "/")] = read(path)
        return found

    def test_the_whole_edition_downloads_with_its_subfolders(self):
        def whole(dialog):
            dialog.findChild(QCheckBox).setChecked(True)

        self.download(whole)
        self.assertEqual(self.downloaded(), {"總譜.pdf": b"%PDF", "拉弦/二胡 I.pdf": b"erhu"})

    def test_chosen_files_download_into_the_folder(self):
        def only_erhu(dialog):
            dialog.findChild(QCheckBox).setChecked(False)
            files = dialog.findChild(QListWidget)
            for i in range(files.count()):
                files.item(i).setCheckState(Qt.Checked if files.item(i).text() == "拉弦/二胡 I.pdf" else Qt.Unchecked)

        self.download(only_erhu)
        self.assertEqual(self.downloaded(), {"二胡 I.pdf": b"erhu"})

    def test_a_failed_download_shows_the_error_bar_and_leaves_no_file(self):
        def whole(dialog):
            self.google.fail_next(CatalogErrorKind.OFFLINE, times=20)

        window, _ = self.download(whole)
        self.assertFalse(self.bar(window).isHidden())
        self.assertEqual(self.downloaded(), {})


if __name__ == "__main__":
    unittest.main()
