# -*- coding: utf-8 -*-
"""
存入譜庫的接線測試（offscreen）

主視窗「譜庫」選單的「存入譜庫…」讀出計畫後開預覽：專案有未存檔的改動時「存入譜庫」按不下去；
按下後在背景存入假 Google，期間專案編輯與會動到檔案的操作都停用，做完把指紋存進專案檔；
中途失敗時頂端提示列寫出已完成幾份。存入後本機改過的檔（未存入）在群組面板標出、關閉時提醒一次；
復原存入過的重新命名照常完成，並提示譜庫裡的那份維持原樣。不測畫面。
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

from PySide6.QtCore import QEvent
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton

from core.catalog_errors import CatalogErrorKind
from core.constants import CATALOG_SPREADSHEET_ID_KEY
from core.locale import t
from core.models import RenameEntry
from services.activity_log import ActivityLog
from services.file_service import FileService
from services.move_history import RenameVerdict
from services.project_service import ProjectService
from test_deposit import DepositFixture, md5
from tests.test_main_window import MainWindowTestCase, answering_prompts, opening, wait_until
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

    def test_a_restarted_program_still_shows_that_the_last_deposit_stopped(self):
        self.google.drive.fail_upload(2, CatalogErrorKind.OFFLINE)
        self.archive()
        self.finish()
        with answering_prompts(t("dialog.discard_btn")):
            self.window.close()
        self.window = MainWindow(
            self.preferences, history=self.history, opener=self.opener, auth_service=self.auth,
            compressor=self.compressor(), log=ActivityLog(FileService(), self.log_dir),
            catalog_factory=lambda: self.catalog,
        )
        with answering_prompts(), opening(self.path):
            self.trigger_menu(t("menu.file.open"))
        notices = []

        def look(dialog):
            notices.append(dialog.interrupted_notice.text() if dialog.interrupted_notice else "")
            dialog.reject()
            return dialog.result()
        with mock.patch.object(DepositDialog, "exec", look), answering_prompts():
            self.trigger_menu(t("menu.catalog.deposit"))
        self.assertEqual(notices, [t("deposit.interrupted", count=1)])

    def action(self, text: str):
        """主視窗選單裡文字相符的動作"""
        return next(a for a in self.window.findChildren(QAction) if a.text() == text)



class TestUndeposited(DepositWindowTestCase):
    """存入後本機的琵琶分譜又被改過：群組面板標出、關閉專案時提醒一次"""

    def setUp(self):
        super().setUp()
        self.archive()
        self.finish()
        self.pipa = self.window.project.groups[0].files[1]

    def tearDown(self):
        """收尾時未存入的提醒按「是」"""
        with answering_prompts(QMessageBox.Yes, QMessageBox.Yes, QMessageBox.Yes):
            self.window.close()

    def change_pipa(self, content: bytes = b"new pipa") -> None:
        """改寫本機的琵琶分譜"""
        with open(self.pipa.original_path, "wb") as f:
            f.write(content)

    def undeposited_bar(self) -> QLabel:
        """切到別的分頁再切回群組，回傳群組面板的未存入提示"""
        self.window._tab_widget.setCurrentIndex(0)
        self.window._tab_widget.setCurrentIndex(1)
        return self.window._tab_widget.widget(1).findChild(QLabel, "undeposited_bar")

    def test_the_group_panel_marks_the_changed_file(self):
        self.assertTrue(self.undeposited_bar().isHidden())
        self.change_pipa()
        bar = self.undeposited_bar()
        self.assertFalse(bar.isHidden())
        self.assertEqual(bar.text(), t("deposit.undeposited.bar", files="琵琶.pdf"))

    def test_coming_back_to_the_window_checks_the_files_again(self):
        self.assertTrue(self.undeposited_bar().isHidden())
        self.change_pipa()
        bar = self.window._tab_widget.widget(1).findChild(QLabel, "undeposited_bar")
        self.assertTrue(bar.isHidden())
        with mock.patch.object(self.window, "isActiveWindow", return_value=True):
            self.window.changeEvent(QEvent(QEvent.ActivationChange))
        self.assertFalse(bar.isHidden())

    def test_closing_reminds_once_and_can_be_cancelled(self):
        self.change_pipa()
        reminder = (t("deposit.undeposited.title"), t("deposit.undeposited.close", groups="江樓鐘鼓"))
        with answering_prompts(QMessageBox.No) as shown:
            self.assertFalse(self.window.close())
        self.assertEqual(shown, [reminder])
        with answering_prompts(QMessageBox.Yes) as shown:
            self.assertTrue(self.window.close())
        self.assertEqual(shown, [reminder])

    def test_closing_with_the_content_changed_back_does_not_remind(self):
        self.change_pipa()
        self.change_pipa("琵琶".encode())
        with answering_prompts() as shown:
            self.assertTrue(self.window.close())
        self.assertEqual(shown, [])


class TestUndoArchivedRename(DepositWindowTestCase):
    """琵琶分譜是重新命名過來的，存入譜庫後再復原那次重新命名"""

    def setUp(self):
        super().setUp()
        self.pipa = self.window.project.groups[0].files[1]
        self.raw = os.path.join(self.temp, "pipa.pdf")
        os.rename(self.pipa.original_path, self.raw)
        self.history.rename(RenameVerdict([RenameEntry(self.raw, self.pipa.original_path)]))

    def test_undo_completes_and_says_the_catalog_copy_stays(self):
        self.archive()
        self.finish()
        with answering_prompts(QMessageBox.Yes) as shown:
            self.trigger_menu(t("menu.edit.undo"))
        self.assertIn((t("deposit.undo_archived.title"), t("deposit.undo_archived")), shown)
        self.assertTrue(os.path.isfile(self.raw))
        self.assertEqual(self.pipa.original_path, self.raw)
        self.assertTrue(self.status_shows(t("status.undone")))

    def test_undoing_a_rename_never_archived_does_not_mention_the_catalog(self):
        with answering_prompts(QMessageBox.Yes) as shown:
            self.trigger_menu(t("menu.edit.undo"))
        self.assertNotIn((t("deposit.undo_archived.title"), t("deposit.undo_archived")), shown)
        self.assertEqual(self.pipa.original_path, self.raw)


if __name__ == "__main__":
    unittest.main()
