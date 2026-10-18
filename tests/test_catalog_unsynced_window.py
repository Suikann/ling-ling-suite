# -*- coding: utf-8 -*-
"""
未同步的接線測試（offscreen）

寫不進譜庫（斷網、API 失敗）時專案照樣存檔，改動標未同步並存進專案檔；編制表頂端出現未同步的提示列，
時長旁寫出譜庫現在的值。下次存檔或開啟專案時重試，重試遇到別人改過的格子照樣進衝突畫面，成功後清掉標記。
關閉還有未同步改動的專案時提醒一次。不測畫面。
"""
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QLabel, QMessageBox

from core.catalog_constants import SHEET_DURATIONS, SHEET_PARTS
from core.catalog_errors import CatalogErrorKind
from core.locale import t
from core.models import Headcount
from services.project_service import ProjectService
from test_catalog_link import records
from test_catalog_write_back_window import WriteBackWindowTestCase, answering_conflicts
from tests.test_main_window import answering_prompts, opening
from ui.catalog_error_bar import CatalogErrorBar


class UnsyncedWindowTestCase(WriteBackWindowTestCase):
    """群組的琵琶人數改成 3、時長改成 3:20，斷網時存檔"""

    def setUp(self):
        super().setUp()
        self.window.project.set_headcount(self.group, 1, Headcount(3))
        self.window.project.set_duration(self.group, 200)
        self.google.fail_next(CatalogErrorKind.OFFLINE)
        self.save()

    def tearDown(self):
        """收尾時「是否儲存」按「不儲存」、未同步的提醒按「是」，不論跳出哪幾個"""
        def discard(box):
            """按下「不儲存」"""
            next(b for b in box.buttons() if b.text() == t("dialog.discard_btn")).click()
            return box.result()
        with mock.patch.object(QMessageBox, "exec", discard), \
                mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.window.close()

    def reopen(self, *answers):
        """從選單重新開啟同一個專案檔，提示框依序按 answers"""
        with answering_prompts(*answers), opening(self.path):
            self.trigger_menu(t("menu.file.open"))
        self.group = self.window.project.groups[0]

    def saved_unsynced(self) -> list:
        """磁碟上的專案檔裡標未同步的群組名稱"""
        return [group.name for group in ProjectService().load_project(self.path).unsynced_groups()]

    def unsynced_bar(self) -> QLabel:
        """編制表頂端的未同步提示列"""
        return self.table().findChild(QLabel, "unsynced_bar")

    def catalog_now(self) -> QLabel:
        """時長旁的「譜庫現在是…」"""
        return self.table().findChild(QLabel, "duration_catalog_now")


class TestSavingOffline(UnsyncedWindowTestCase):
    """寫不進譜庫：專案照樣存檔，改動標未同步並記在專案檔"""

    def test_the_project_is_saved_and_the_changes_are_marked_unsynced(self):
        self.assertFalse(self.is_marked_unsaved())
        self.assertEqual(self.saved_unsynced(), ["江樓鐘鼓"])
        self.assertEqual(self.headcount_in_catalog("琵琶"), "")
        bar = self.window.findChild(CatalogErrorBar)
        self.assertFalse(bar.isHidden())
        self.assertIn(t("catalog.unsynced.heading"), bar.findChild(QLabel).text())

    def test_the_instrumentation_table_shows_what_is_unsynced_and_the_catalog_value(self):
        items = "、".join([t("catalog.sync.headcount", voice="琵琶"), t("catalog.sync.duration")])
        self.assertFalse(self.unsynced_bar().isHidden())
        self.assertEqual(self.unsynced_bar().text(), t("catalog.unsynced.bar", items=items))
        self.assertFalse(self.catalog_now().isHidden())
        self.assertEqual(self.catalog_now().text(), t("catalog.unsynced.catalog_now", value="3:00"))


class TestRetryingOnSave(UnsyncedWindowTestCase):
    """下次存檔時重試，成功後清掉標記"""

    def test_next_save_writes_the_changes_and_clears_the_mark(self):
        self.save()
        self.assertEqual(self.headcount_in_catalog("琵琶"), "3")
        self.assertEqual(records(self.google, SHEET_DURATIONS)[0]["seconds"], "200")
        self.assertEqual(self.saved_unsynced(), [])
        self.assertTrue(self.unsynced_bar().isHidden())
        self.assertTrue(self.catalog_now().isHidden())
        self.assertTrue(self.window.findChild(CatalogErrorBar).isHidden())

    def test_a_cell_changed_by_someone_else_meanwhile_goes_to_the_conflict_screen(self):
        self.google.sheets.set_cell(SHEET_PARTS, "J3", "4")
        with answering_conflicts(t("catalog.sync.use_mine")) as shown:
            self.save()
        self.assertEqual(len(shown), 1)
        self.assertEqual(self.headcount_in_catalog("琵琶"), "3")
        self.assertEqual(self.saved_unsynced(), [])


class TestRetryingOnOpen(UnsyncedWindowTestCase):
    """開啟專案時重試"""

    def test_opening_writes_the_changes_and_saves_the_cleared_mark(self):
        self.reopen(QMessageBox.Yes)
        self.assertEqual(self.headcount_in_catalog("琵琶"), "3")
        self.assertEqual(self.saved_unsynced(), [])
        self.assertFalse(self.is_marked_unsaved())
        self.assertTrue(self.unsynced_bar().isHidden())

    def test_a_cell_changed_by_someone_else_goes_to_the_conflict_screen(self):
        self.google.sheets.set_cell(SHEET_PARTS, "J3", "4")
        with answering_conflicts(t("catalog.sync.use_catalog")) as shown:
            self.reopen(QMessageBox.Yes)
        self.assertEqual(len(shown), 1)
        self.assertEqual(self.headcount_in_catalog("琵琶"), "4")
        self.assertEqual(records(self.google, SHEET_DURATIONS)[0]["seconds"], "200")
        self.assertEqual(self.group.headcounts[1], Headcount(4))
        self.assertEqual(self.saved_unsynced(), [])

    def test_still_offline_keeps_the_mark_and_shows_the_bar(self):
        self.google.fail_next(CatalogErrorKind.OFFLINE)
        self.reopen(QMessageBox.Yes)
        self.assertEqual(self.saved_unsynced(), ["江樓鐘鼓"])
        self.assertFalse(self.is_marked_unsaved())
        self.assertFalse(self.unsynced_bar().isHidden())
        self.assertFalse(self.window.findChild(CatalogErrorBar).isHidden())


class TestClosingWithUnsyncedChanges(UnsyncedWindowTestCase):
    """關閉還有未同步改動的專案時提醒一次"""

    def test_closing_reminds_once_and_can_be_cancelled(self):
        reminder = (t("catalog.unsynced.title"), t("catalog.unsynced.close", groups="江樓鐘鼓"))
        with answering_prompts(QMessageBox.No) as shown:
            self.assertFalse(self.window.close())
        self.assertEqual(shown, [reminder])
        with answering_prompts(QMessageBox.Yes) as shown:
            self.assertTrue(self.window.close())
        self.assertEqual(shown, [reminder])

    def test_closing_after_the_changes_are_synced_does_not_remind(self):
        self.save()
        with answering_prompts() as shown:
            self.assertTrue(self.window.close())
        self.assertEqual(shown, [])


if __name__ == "__main__":
    unittest.main()
