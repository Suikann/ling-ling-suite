# -*- coding: utf-8 -*-
"""
清除本機檔案的接線測試（offscreen）

主視窗「譜庫」選單的「清除本機檔案…」讀出計畫後開預覽，按下後先跳確認框寫出要永久刪除幾份，
按「永久刪除」才在背景刪掉，做完把指紋存進專案檔；斷網時頂端提示列、不開預覽。
清除後群組分頁標出只在譜庫的檔，重新命名的預覽說明跳過它們。不測畫面。
"""
import os
import sys
import unittest
from contextlib import contextmanager
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QLabel, QListWidget, QPushButton

from core.catalog_errors import CatalogErrorKind
from core.constants import CATALOG_SPREADSHEET_ID_KEY
from core.locale import t
from services.activity_log import ActivityLog
from services.deposit_service import DepositService
from services.file_service import FileService
from services.project_service import ProjectService
from test_deposit import DepositFixture
from tests.test_main_window import MainWindowTestCase, answering_prompts, opening, previewing, wait_until
from ui.catalog_error_bar import CatalogErrorBar
from ui.clear_dialog import ClearDialog
from ui.group_panel import GroupTab
from ui.main_window import MainWindow


@contextmanager
def answering_preview(accept: bool = True):
    """模擬清除預覽：記下開啟當下「清除本機檔案」能不能按，accept 時（按得下去才）按它

    Yields:
        開啟過的預覽，各自是「清除本機檔案」能不能按
    """
    shown = []

    def fake_exec(dialog):
        confirm = next(b for b in dialog.findChildren(QPushButton) if b.text() == t("clear.confirm"))
        shown.append(confirm.isEnabled())
        if accept and confirm.isEnabled():
            confirm.click()
        else:
            dialog.reject()
        return dialog.result()

    with mock.patch.object(ClearDialog, "exec", fake_exec):
        yield shown


class ClearWindowTestCase(DepositFixture, MainWindowTestCase):
    """主視窗開著已存進假 Google 的專案（情境見 DepositFixture），指紋已記進專案檔"""

    def setUp(self):
        MainWindowTestCase.setUp(self)
        self.temp = self.temp_dir
        self.build(self.VOICES)
        service = DepositService(FileService())
        plan = service.preview(self.project, self.catalog)
        outcome = service.deposit(plan, self.catalog, "office@example.com", lambda: False, lambda progress: None)
        self.project.record_deposit(outcome.records)
        self.project.mark_saved()
        self.auth.authenticate()
        self.preferences.set(CATALOG_SPREADSHEET_ID_KEY, "sheet-1")
        with answering_prompts(t("dialog.discard_btn")):
            self.window.close()
        self.window = MainWindow(
            self.preferences, history=self.history, opener=self.opener, auth_service=self.auth,
            compressor=self.compressor(), log=ActivityLog(FileService(), self.log_dir),
            catalog_factory=lambda: self.catalog,
        )
        self.path = self.open_from_menu(self.project)

    def paths(self) -> list:
        """群組的總譜與分譜的路徑"""
        return [self.score.original_path] + [part.original_path for part in self.parts]

    def clear(self, *answers, accept: bool = True):
        """從選單開清除預覽，確認框依序按 answers；等背景的清除做完，回傳（預覽，跳過的提示框）"""
        with answering_preview(accept) as previews, answering_prompts(*answers) as prompts:
            self.trigger_menu(t("menu.catalog.clear"))
        wait_until(lambda: self.window._task is None)
        return previews, prompts


class TestClearing(ClearWindowTestCase):
    """確認框寫出永久刪除幾份；按「永久刪除」才刪，做完把指紋存進專案檔"""

    def test_the_confirm_box_names_how_many_files_and_deleting_clears_them(self):
        previews, prompts = self.clear(t("clear.confirm.delete"))
        self.assertEqual(previews, [True])
        self.assertEqual(prompts[0][0], t("clear.confirm.title", count=3))
        self.assertFalse(any(os.path.exists(path) for path in self.paths()))
        self.assertFalse(os.path.exists(self.out))
        self.assertFalse(self.window.project.is_modified())
        saved = ProjectService().load_project(self.path)
        self.assertEqual(saved.groups[0].score_file.deposit_md5, self.project.groups[0].score_file.deposit_md5)

    def test_cancelling_the_confirm_box_deletes_nothing(self):
        self.clear(t("dialog.cancel_btn"))
        self.assertTrue(all(os.path.exists(path) for path in self.paths()))

    def test_unsaved_changes_disable_clearing(self):
        self.window.project.update_group(self.window.project.groups[0], composer="秦鵬章")
        previews, _ = self.clear()
        self.assertEqual(previews, [False])
        self.assertTrue(all(os.path.exists(path) for path in self.paths()))

    def test_without_network_the_preview_does_not_open_and_the_error_bar_shows(self):
        self.google.fail_next(CatalogErrorKind.OFFLINE, times=20)
        previews, _ = self.clear()
        self.assertEqual(previews, [])
        self.assertFalse(self.window.findChild(CatalogErrorBar).isHidden())
        self.assertTrue(all(os.path.exists(path) for path in self.paths()))


class TestOnlyInCatalogAfterClearing(ClearWindowTestCase):
    """清除後：群組分頁標出只在譜庫的檔，重新命名跳過它們並說明原因"""

    def setUp(self):
        super().setUp()
        self.clear(t("clear.confirm.delete"))

    def test_the_group_tab_marks_the_files_only_in_the_catalog(self):
        tab = self.window.findChild(GroupTab)
        self.assertTrue(any(label.text() == t("catalog_only.group", count=3) for label in tab.findChildren(QLabel)))
        names = [tab.findChild(QListWidget).item(i).text() for i in range(2)]
        self.assertTrue(all(name.endswith(t("catalog_only.file", name="").strip()) for name in names), names)

    def test_reopening_the_project_does_not_warn_about_missing_files(self):
        with answering_prompts() as prompts, opening(self.path):
            self.trigger_menu(t("menu.file.open"))
        self.assertEqual(prompts, [])

    def test_rename_skips_the_files_only_in_the_catalog_and_says_why(self):
        notes = []

        def read_notes(dialog):
            notes.extend(label.text() for label in dialog.findChildren(QLabel) if not label.isHidden())

        with previewing(read_notes):
            self.click_button(t("panel.preview_rename"))
        expected = t("preview.catalog_only_note", count=3, files="\n".join(self.paths()))
        self.assertTrue(any(expected in text for text in notes), notes)
        self.assertFalse(any(t("preview.missing_warning", count=3, files="\n".join(self.paths())) in text for text in notes))


if __name__ == "__main__":
    unittest.main()
