# -*- coding: utf-8 -*-
"""
撤銷存入的接線測試（offscreen）

撤銷的預覽對話框照服務算出的預覽顯示：各下場的份數、逐聲部的對照表、本機已清除時的下載提示與一起撤掉編制的勾選。
譜庫視窗版本頁的「存入紀錄…」列出那次存入，選了按「撤銷存入…」開預覽，按「撤銷存入」後交給主視窗在背景撤銷：
期間鎖住專案編輯，做完把撤掉的檔的指紋拿掉並存進專案檔。不測畫面。
"""
import os
import sys
import threading
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QTabWidget, QTreeWidget

from core.constants import CATALOG_ROOT_FOLDER_ID_KEY
from core.deposit_revert import LocalProject, RevertAction, RevertBlock, RevertChoice, deposits
from core.models import Project
from core.locale import t
from fake_google_catalog import ROOT
from services.activity_log import ActivityLog
from services.deposit_revert_service import DepositRevertService
from services.download_service import DownloadService
from services.file_service import FileService
from services.project_service import ProjectService
from test_deposit_revert import ClearedTestCase
from test_deposit_window import DepositWindowTestCase
from tests.test_main_window import answering_prompts, wait_until
from ui.catalog_window import CatalogWindow
from ui.deposit_revert_dialog import DepositLogDialog, DepositRevertDialog
from ui.instrumentation_table import InstrumentationTable


class TestRevertDialog(ClearedTestCase):
    """預覽對話框的份數、對照表與下載提示跟服務算出的預覽一致（兩個群組都已存入並清除本機檔案）"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        pipa = self.registered_ids()["琵琶"]
        self.google.drive.set_content(pipa, b"edited on the web")
        self.revert_preview = self.preview(self.deposit_id, self.cleared)
        self.dialog = DepositRevertDialog(self.revert_preview)
        self.addCleanup(self.dialog.deleteLater)

    def test_counts_and_rows_match_the_preview(self):
        first, _ = self.revert_preview.groups
        self.assertEqual(first.counts(), {RevertAction.TRASH: 2, RevertAction.KEEP: 1})
        for group, page in zip(self.revert_preview.groups, self.dialog.pages):
            self.assertEqual(sorted(label.text() for label in page.counts), sorted(
                t("revert.count", kind=t(f"revert.action.{action.value}"), count=count)
                for action, count in group.counts().items()
            ))
            table = page.table
            self.assertEqual(table.rowCount(), len(group.files))
            for row, file in enumerate(group.files):
                self.assertEqual(table.item(row, 0).text(), file.entry.voice)
                self.assertTrue(table.item(row, 3).text().startswith(t(f"revert.action.{file.action.value}")))
        pipa_row = [f.entry.voice for f in first.files].index("琵琶")
        self.assertEqual(self.dialog.pages[0].table.item(pipa_row, 3).text(), t(
            "revert.reason", kind=t("revert.action.keep"), why=t("revert.reason.changed"),
        ))

    def test_the_download_notice_counts_the_cleared_files_to_revert(self):
        notices = [page.download_notice.text() for page in self.dialog.pages]
        self.assertEqual(notices, [
            t("revert.download", group="江樓鐘鼓", count=2), t("revert.download", group="將軍令", count=2),
        ])

    def test_checked_groups_and_instrumentation_become_the_choices(self):
        self.assertFalse(self.dialog.confirm.isEnabled())
        self.dialog.groups.item(1).setCheckState(Qt.Checked)
        self.assertTrue(self.dialog.confirm.isEnabled())
        self.assertEqual(self.dialog.choices(), [RevertChoice(self.revert_preview.groups[1], False)])
        self.assertFalse(self.dialog.pages[1].instrumentation.isEnabled())


class TestRefusedGroupInDialog(ClearedTestCase):
    """撤銷會毀掉唯一一份的群組：不能勾，頁面寫出原因"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_a_group_of_a_project_that_is_not_open_cannot_be_checked(self):
        preview = self.preview(self.deposit_id, local=LocalProject.of(Project(), ()))
        dialog = DepositRevertDialog(preview)
        self.addCleanup(dialog.deleteLater)
        self.assertEqual(preview.groups[0].block, RevertBlock.NOT_OPEN)
        self.assertFalse(dialog.groups.item(0).flags() & Qt.ItemIsUserCheckable)
        self.assertEqual(dialog.pages[0].block_notice.text(), t("revert.block.not_open"))
        self.assertEqual(dialog.choices(), [])
        self.assertFalse(dialog.confirm.isEnabled())


class RevertWindowTestCase(DepositWindowTestCase):
    """主視窗開著已存入譜庫的專案（情境見 DepositFixture）"""

    def setUp(self):
        super().setUp()
        self.archive()
        self.finish()
        self.deposit_id = deposits(self.catalog.deposit_log())[0].deposit_id

    def preview(self):
        """撤銷那次存入的預覽"""
        service = DepositRevertService(
            FileService(), DownloadService(FileService(), temp_dir=os.path.join(self.temp, "t")),
        )
        local = LocalProject.of(self.window.project, [])
        return service.preview(self.deposit_id, self.catalog, self.window.project.orchestra_type, local)


class TestRevertInMainWindow(RevertWindowTestCase):
    """主視窗在背景撤銷：期間鎖住專案編輯，做完拿掉撤掉的檔的指紋並存進專案檔"""

    def test_editing_is_locked_and_fingerprints_are_saved_afterwards(self):
        preview = self.preview()
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.google.before_request(self.google.requests + 1, lambda: gate.wait(10))
        results = []
        self.assertTrue(self.window.revert_deposit(preview, [RevertChoice(preview.groups[0])], results.append))
        self.assertFalse(self.window.findChild(InstrumentationTable).isEnabled())
        self.assertFalse(self.window.revert_deposit(preview, [RevertChoice(preview.groups[0])], results.append))
        gate.set()
        self.finish()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].completed, 3)
        self.assertTrue(self.window.findChild(InstrumentationTable).isEnabled())
        self.assertFalse(self.window.project.is_modified())
        saved = ProjectService().load_project(self.path)
        self.assertEqual([f.deposit_md5 for f in saved.groups[0].files], ["", ""])
        self.assertTrue(self.status_shows(t("revert.done", count=3)))


class TestRevertFromCatalogWindow(RevertWindowTestCase):
    """譜庫視窗版本頁的「存入紀錄…」選那次存入，按「撤銷存入…」開預覽，按「撤銷存入」後交給主視窗撤銷"""

    def open_catalog(self) -> CatalogWindow:
        """開譜庫視窗（接假 Google、撤銷交給主視窗），打開春江花月夜的版本頁"""
        self.preferences.set(CATALOG_ROOT_FOLDER_ID_KEY, ROOT)
        window = CatalogWindow(
            self.auth, self.preferences, catalog_factory=lambda: self.catalog,
            log=ActivityLog(FileService(), self.log_dir), reverter=self.window,
        )
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        tree = window.findChild(QTreeWidget)
        wait_until(lambda: tree.topLevelItemCount() > 0)
        tree.setCurrentItem(next(
            tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
            if tree.topLevelItem(i).text(0).startswith("春江花月夜")
        ))
        wait_until(lambda: window.findChild(QTabWidget) is not None)
        tabs = window.findChild(QTabWidget)
        tabs.setCurrentIndex(1)
        wait_until(lambda: tabs.widget(1).findChild(InstrumentationTable).isEnabled())
        return window

    def test_choosing_the_deposit_reverts_it_in_the_main_window(self):
        window = self.open_catalog()
        log_shown, revert_shown = [], []

        def pick_deposit(dialog):
            log_shown.append(dialog.table.rowCount())
            dialog.table.selectRow(0)
            dialog.revert_button.click()
            return dialog.result()

        def confirm(dialog):
            revert_shown.append([group.name for group in dialog._preview.groups])
            dialog.confirm.click()
            return dialog.result()

        page = window.findChild(QTabWidget).widget(1)
        button = next(b for b in page.findChildren(QPushButton) if b.text() == t("deposit_log.button"))
        with mock.patch.object(DepositLogDialog, "exec", pick_deposit), \
                mock.patch.object(DepositRevertDialog, "exec", confirm), answering_prompts():
            button.click()
            wait_until(lambda: bool(revert_shown))
            self.finish()
        self.assertEqual(log_shown, [1])
        self.assertEqual(revert_shown, [["江樓鐘鼓"]])
        self.assertEqual(set(self.registered()), set())
        self.assertTrue(self.status_shows(t("revert.done", count=3)))


if __name__ == "__main__":
    unittest.main()
