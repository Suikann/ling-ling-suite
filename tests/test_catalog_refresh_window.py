# -*- coding: utf-8 -*-
"""
開啟專案時從譜庫更新快照的接線測試（offscreen）

開啟專案時讀連到的那處：人數與時長的變動直接套用並存回專案檔；聲部清單變了時編制表頂端出現提示列、
標出增刪與移動，兩顆按鈕各呼叫「套用譜庫的」「用專案的（存檔時寫回）」；讀不到譜庫時照快照開啟、顯示提示列。
不測畫面。
"""
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton, QTableWidget, QWidget

from core.catalog_constants import SHEET_DURATIONS, SHEET_EDITIONS, SHEET_PARTS
from core.catalog_errors import CatalogErrorKind
from core.locale import t
from core.models import Headcount
from services.project_service import ProjectService
from test_catalog_link import records
from test_catalog_service import add_row
from test_catalog_write_back_window import WriteBackWindowTestCase, answering_conflicts
from tests.test_main_window import answering_prompts, opening
from ui.catalog_error_bar import CatalogErrorBar


class RefreshWindowTestCase(WriteBackWindowTestCase):
    """群組連到第一樂章（二胡 I、琵琶，時長 3:00），兩份分譜已配好並存檔"""

    def setUp(self):
        super().setUp()
        files = self.create_files("二胡I.pdf", "琵琶.pdf")
        self.window.project.add_files(files, self.group)
        self.save()

    def tearDown(self):
        """收尾時「是否儲存」按「不儲存」，不論跳出幾個"""
        def discard(box):
            """按下「不儲存」"""
            next(b for b in box.buttons() if b.text() == t("dialog.discard_btn")).click()
            return box.result()
        with mock.patch.object(QMessageBox, "exec", discard), \
                mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            self.window.close()

    def reopen(self):
        """從選單重新開啟同一個專案檔"""
        with answering_prompts(), opening(self.path):
            self.trigger_menu(t("menu.file.open"))
        self.group = self.window.project.groups[0]

    def list_bar(self) -> QWidget:
        """編制表頂端「譜庫的聲部清單變了」的提示列"""
        return self.table().findChild(QWidget, "catalog_list_bar")

    def voice_cells(self) -> list:
        """編制表聲部欄每一列的文字"""
        grid = self.table().findChild(QTableWidget)
        return [grid.item(row, 1).text() for row in range(grid.rowCount())]

    def pairs(self) -> list:
        """群組目前每個聲部配到的分譜檔名"""
        return [(pair.voice, pair.file.display_name if pair.file else None) for pair in self.group.pairing()]


class TestHeadcountsAndDurationOnOpen(RefreshWindowTestCase):
    """人數與時長的變動開啟時直接套用，存回專案檔"""

    def test_catalog_changes_are_applied_and_saved(self):
        self.google.sheets.set_cell(SHEET_PARTS, "J3", "4")
        self.google.sheets.set_cell(SHEET_DURATIONS, "D2", "200")
        self.reopen()
        self.assertEqual(self.group.headcounts[1], Headcount(4))
        self.assertEqual(self.group.duration_seconds, 200)
        self.assertFalse(self.is_marked_unsaved())
        saved = ProjectService().load_project(self.path)
        self.assertEqual(saved.groups[0].duration_seconds, 200)
        self.assertTrue(self.list_bar().isHidden())


class ListChangeWindowTestCase(RefreshWindowTestCase):
    """譜庫刪了琵琶、新增揚琴"""

    def setUp(self):
        super().setUp()
        self.google.sheets.set_cell(SHEET_PARTS, "D3", "")
        add_row(self.google, SHEET_PARTS, id="pt3", edition_id="e1", movement_id="m1", instrument_name="揚琴", sort_order="3")
        self.reopen()

    def press(self, text: str):
        """按下提示列上的按鈕"""
        next(b for b in self.list_bar().findChildren(QPushButton) if b.text() == text).click()


class TestListChangeOnOpen(ListChangeWindowTestCase):
    """聲部清單變了：提示列出現、編制表標出增刪，不自動套用，專案不算未存檔"""

    def test_bar_and_marks_without_applying(self):
        self.assertFalse(self.list_bar().isHidden())
        self.assertEqual(self.voice_cells(), ["二胡 I", "琵琶", t("catalog.refresh.added", voice="揚琴")])
        self.assertEqual(self.pairs(), [("二胡 I", "二胡I.pdf"), ("琵琶", "琵琶.pdf")])
        self.assertFalse(self.is_marked_unsaved())


class TestListBarButtons(ListChangeWindowTestCase):
    """提示列的兩顆按鈕各呼叫對的操作"""

    def test_take_catalog_list_moves_unmatched_parts_to_ungrouped(self):
        self.press(t("catalog.refresh.take"))
        self.assertEqual(self.group.instruments, ["二胡 I", "揚琴"])
        self.assertEqual([f.display_name for f in self.window.project.ungrouped_files], ["琵琶.pdf"])
        self.assertTrue(self.list_bar().isHidden())
        self.assertEqual(self.voice_cells(), ["二胡 I", "揚琴"])
        self.assertTrue(self.is_marked_unsaved())

    def test_keep_project_list_writes_it_back_after_confirming_on_save(self):
        self.press(t("catalog.refresh.keep"))
        self.assertEqual(self.group.instruments, ["二胡 I", "琵琶"])
        self.assertTrue(self.list_bar().isHidden())
        self.assertFalse(self.table().findChild(QLabel, "catalog_keep_note").isHidden())
        with answering_conflicts() as shown:
            self.save()
        self.assertEqual(len(shown), 1)
        self.assertIn(t("catalog.sync.add_voice", voice="琵琶"), [row[0] for row in shown[0]])
        voices = [r["instrument_name"] for r in records(self.google, SHEET_PARTS) if r["instrument_name"]]
        self.assertEqual(sorted(voices), sorted(["二胡 I", "琵琶"]))
        self.assertFalse(self.is_marked_unsaved())
        self.assertTrue(self.table().findChild(QLabel, "catalog_keep_note").isHidden())


class TestPlaceGoneOnOpen(RefreshWindowTestCase):
    """一個群組連到的那處已不在譜庫：其他群組照常更新，那個群組照快照，提示列寫出是哪個群組"""

    def test_other_groups_still_refresh_and_the_gone_group_is_named(self):
        add_row(self.google, SHEET_EDITIONS, id="e2", piece_id="p1")
        other = self.window.project.add_group("別處", "總譜")
        self.window.project.link_group(other, self.connect().read_place("e2", "", self.window.project.orchestra_type))
        self.save()
        rows = self.google.sheets.rows(SHEET_EDITIONS)
        self.google.sheets.set_cell(SHEET_EDITIONS, f"A{next(i + 1 for i, r in enumerate(rows) if r[:1] == ['e2'])}", "x")
        self.google.sheets.set_cell(SHEET_PARTS, "J3", "4")
        self.reopen()
        self.assertEqual(self.group.headcounts[1], Headcount(4))
        bar = self.window.findChild(CatalogErrorBar)
        self.assertFalse(bar.isHidden())
        self.assertIn(t("catalog.refresh.gone", groups="別處"), bar.findChild(QLabel).text())


class TestCatalogUnreachableOnOpen(RefreshWindowTestCase):
    """讀不到譜庫：照快照開啟，顯示分類提示列，不擋編輯"""

    def test_opens_from_the_snapshot_with_the_error_bar(self):
        self.google.fail_next(CatalogErrorKind.OFFLINE)
        self.reopen()
        bar = self.window.findChild(CatalogErrorBar)
        self.assertFalse(bar.isHidden())
        self.assertIn(t("catalog.refresh.heading"), bar.findChild(QLabel).text())
        self.assertEqual(self.group.instruments, ["二胡 I", "琵琶"])
        self.assertTrue(self.table().isEnabled())
        self.assertFalse(self.is_marked_unsaved())


if __name__ == "__main__":
    unittest.main()
