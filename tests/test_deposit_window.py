# -*- coding: utf-8 -*-
"""
存入譜庫的接線測試（offscreen）

主視窗「譜庫」選單的「存入譜庫…」讀出計畫後開預覽：專案有未存檔的改動時「存入譜庫」按不下去；
按下後在背景存入假 Google，期間專案編輯與會動到檔案的操作都停用，做完把指紋存進專案檔；
中途失敗時頂端提示列寫出已完成幾份。不測畫面。
"""
import os
import sys
import threading
import unittest
from contextlib import contextmanager
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QPushButton

from core.catalog_errors import CatalogErrorKind
from core.constants import CATALOG_SPREADSHEET_ID_KEY
from core.locale import t
from services.activity_log import ActivityLog
from services.file_service import FileService
from services.project_service import ProjectService
from test_deposit import DepositFixture, md5
from tests.test_main_window import MainWindowTestCase, answering_prompts, wait_until
from ui.catalog_error_bar import CatalogErrorBar
from ui.deposit_dialog import DepositDialog
from ui.instrumentation_table import InstrumentationTable
from ui.main_window import MainWindow


@contextmanager
def answering_preview(accept: bool = True, before_accept=lambda: None):
    """模擬存入預覽：記下開啟當下「存入譜庫」能不能按、未存檔提示有沒有顯示，accept 時（按得下去才）按它

    Args:
        accept: 是否按「存入譜庫」；否則取消
        before_accept: 按下之前呼叫（例如排定讓背景存入卡住）

    Yields:
        開啟過的預覽，各自是（「存入譜庫」能不能按，未存檔提示是否顯示）
    """
    shown = []

    def fake_exec(dialog):
        confirm = next(b for b in dialog.findChildren(QPushButton) if b.text() == t("deposit.confirm"))
        banner = next(label for label in dialog.findChildren(QLabel) if label.text() == t("deposit.unsaved"))
        shown.append((confirm.isEnabled(), not banner.isHidden()))
        if accept and confirm.isEnabled():
            before_accept()
            confirm.click()
        else:
            dialog.reject()
        return dialog.result()

    with mock.patch.object(DepositDialog, "exec", fake_exec):
        yield shown


class DepositWindowTestCase(DepositFixture, MainWindowTestCase):
    """主視窗開著已重新命名、連到假 Google 的專案（情境見 DepositFixture）"""

    def setUp(self):
        MainWindowTestCase.setUp(self)
        self.temp = self.temp_dir
        self.build(self.VOICES)
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

    def archive(self, accept: bool = True, before_accept=lambda: None):
        """從選單開存入預覽，回傳開啟過的預覽（見 answering_preview）"""
        with answering_preview(accept, before_accept) as shown, answering_prompts():
            self.trigger_menu(t("menu.catalog.deposit"))
        return shown

    def finish(self):
        """等背景的存入做完"""
        wait_until(lambda: self.window._task is None)


class TestArchivePreview(DepositWindowTestCase):
    """預覽：專案有未存檔的改動時不能存入"""

    def test_unsaved_changes_disable_archiving_and_show_why(self):
        self.window.project.update_group(self.window.project.groups[0], composer="秦鵬章")
        self.assertEqual(self.archive(), [(False, True)])
        self.assertEqual(self.google.drive.children(self.folder), [])

    def test_a_saved_project_can_be_archived(self):
        self.assertEqual(self.archive(accept=False), [(True, False)])


class TestArchiving(DepositWindowTestCase):
    """按下「存入譜庫」：背景存入，期間鎖住專案編輯與檔案操作，做完自動存檔記下指紋"""

    def test_editing_and_file_operations_are_locked_while_archiving(self):
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.archive(before_accept=lambda: self.google.before_request(self.google.requests + 1, lambda: gate.wait(10)))
        self.assertIsNotNone(self.window._task)
        self.assertFalse(self.window.findChild(InstrumentationTable).isEnabled())
        self.assertFalse(self.window._rename_btn.isEnabled())
        self.assertFalse(self.window._tab_widget.widget(1).isEnabled())
        for text in (t("menu.edit.undo"), t("menu.edit.redo"), t("menu.tools.split_pdf"), t("menu.file.save"),
                     t("menu.import.files"), t("menu.catalog.deposit")):
            self.assertFalse(self.action(text).isEnabled(), text)
        gate.set()
        self.finish()
        self.assertTrue(self.window.findChild(InstrumentationTable).isEnabled())
        self.assertTrue(self.action(t("menu.edit.undo")).isEnabled())

    def test_fingerprints_are_saved_into_the_project_file_after_archiving(self):
        self.archive()
        self.finish()
        self.assertEqual(set(self.registered()), {"總譜", "二胡 I", "琵琶"})
        self.assertFalse(self.window.project.is_modified())
        saved = ProjectService().load_project(self.path)
        self.assertEqual(saved.groups[0].files[1].deposit_md5, md5("琵琶".encode()))
        self.assertTrue(self.status_shows(t("deposit.done", count=3)))

    def test_a_failure_halfway_shows_how_many_were_done_and_pressing_again_completes(self):
        self.google.drive.fail_upload(2, CatalogErrorKind.OFFLINE)
        self.archive()
        self.finish()
        bar = self.window.findChild(CatalogErrorBar)
        self.assertFalse(bar.isHidden())
        self.assertIn(t("deposit.stopped", count=1), bar.findChild(QLabel).text())
        saved = ProjectService().load_project(self.path)
        self.assertEqual(saved.groups[0].score_file.deposit_md5, md5(b"score"))
        self.archive()
        self.finish()
        self.assertTrue(bar.isHidden())
        self.assertEqual(set(self.registered()), {"總譜", "二胡 I", "琵琶"})

    def action(self, text: str):
        """主視窗選單裡文字相符的動作"""
        return next(a for a in self.window.findChildren(QAction) if a.text() == text)


if __name__ == "__main__":
    unittest.main()
