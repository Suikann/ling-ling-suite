# -*- coding: utf-8 -*-
"""
譜庫視窗編輯聲部組對照的接線測試（offscreen）

工具列的「聲部組對照」經譜庫服務讀出全社團的對照，每個聲部組一欄、樂器標出用到幾首；切換樂團類型時欄位換成那一套，
搜尋框篩選每一欄。把樂器移到別的欄（拖拉放下的結果）後按儲存才寫進假 Google；被別人改過時開衝突畫面選邊。不測畫面。
"""
import os
import sys
import unittest
from contextlib import contextmanager
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QGroupBox, QLineEdit, QListWidget, QPushButton, QTableWidget

from core.catalog_constants import SHEET_INSTRUMENTATION_LOG, SHEET_PARTS, SHEET_SECTIONS
from core.locale import t
from test_catalog_link import records
from tests.test_catalog_service import add_row
from tests.test_catalog_window import WritesTestCase
from tests.test_main_window import _settle, wait_until
from ui.catalog_conflict_dialog import CatalogConflictDialog
from ui.section_map_editor import SectionMapEditor


@contextmanager
def answering_conflicts(choice_text: str):
    """模擬使用者在衝突畫面每個衝突都選 choice_text 再按「寫入」

    Yields:
        開啟過的衝突畫面，各自列出的（項目、改成、譜庫現在）
    """
    shown = []

    def fake_exec(dialog):
        table = dialog.findChild(QTableWidget)
        shown.append([
            (table.item(row, 1).text(), table.item(row, 3).text(), table.item(row, 4).text())
            for row in range(table.rowCount())
        ])
        for combo in dialog.findChildren(QComboBox):
            combo.setCurrentIndex(combo.findText(choice_text))
        next(b for b in dialog.findChildren(QPushButton) if b.text() == t("catalog.sections.confirm")).click()
        return dialog.result()
    with mock.patch.object(CatalogConflictDialog, "exec", fake_exec):
        yield shown


class SectionMapWindowTestCase(WritesTestCase):
    """譜庫的「春江花月夜」用到二胡 I、琵琶、柳琴與 Violin；社團對照歸了二胡 I 與琵琶"""

    def setUp(self):
        super().setUp()
        for n, voice in enumerate(("二胡 I", "琵琶", "柳琴", "Violin")):
            add_row(self.google, SHEET_PARTS, id=f"pt{n}", edition_id="e1", instrument_name=voice, sort_order=str(n))
        for n, (instrument, section) in enumerate((("二胡 I", "拉弦"), ("琵琶", "彈撥"))):
            add_row(
                self.google, SHEET_SECTIONS, id=f"s{n}", orchestra_type="chinese", instrument=instrument,
                section=section,
            )

    def open_map(self) -> SectionMapEditor:
        """開啟譜庫視窗，按工具列的「聲部組對照」，等對照讀到"""
        window = self.open_settled()
        self.window = window
        self.button(window, t("catalog.sections.title")).click()
        wait_until(lambda: window.findChild(SectionMapEditor) is not None)
        self.idle()
        return window.findChild(SectionMapEditor)

    @staticmethod
    def visible_columns(editor: SectionMapEditor) -> list:
        """目前這一套的每一欄（欄標題在外框上）"""
        _settle()
        return [box for box in editor.findChildren(QGroupBox) if box.isVisibleTo(editor)]

    def columns(self, editor: SectionMapEditor) -> dict:
        """目前這一套的每一欄：欄標題 → 看得到的樂器項目文字"""
        result = {}
        for box in self.visible_columns(editor):
            column = box.findChild(QListWidget)
            result[box.title()] = [column.item(i).text() for i in range(column.count()) if not column.item(i).isHidden()]
        return result

    def move(self, editor: SectionMapEditor, instrument: str, section_title: str) -> None:
        """把樂器移到標題以 section_title 開頭的那一欄（拖拉放下的結果）"""
        boxes = self.visible_columns(editor)
        lists = [box.findChild(QListWidget) for box in boxes]
        source = next(c for c in lists for i in range(c.count()) if c.item(i).data(Qt.UserRole) == instrument)
        row = next(i for i in range(source.count()) if source.item(i).data(Qt.UserRole) == instrument)
        target = next(box for box in boxes if box.title().startswith(section_title)).findChild(QListWidget)
        target.addItem(source.takeItem(row))

    def club(self) -> list:
        """社團對照工作表的每一列：（樂團類型、樂器、聲部組）"""
        return [(r["orchestra_type"], r["instrument"], r["section"]) for r in records(self.google, SHEET_SECTIONS)]


class TestShowingTheMap(SectionMapWindowTestCase):
    """每個聲部組一欄，樂器標出用到幾首；切換樂團類型換成那一套；搜尋篩選每一欄"""

    def test_each_section_is_a_column_with_unclassified_first(self):
        columns = self.columns(self.open_map())
        self.assertEqual(columns, {
            "未歸類（2）": ["柳琴（1 首）", "Violin（1 首）"],
            "吹管（0）": [], "拉弦（1）": ["二胡 I（1 首）"], "彈撥（1）": ["琵琶（1 首）"],
            "打擊（0）": [], "低音（0）": [], "其他（0）": [],
        })

    def test_switching_the_orchestra_type_shows_that_set(self):
        editor = self.open_map()
        combo = editor.findChild(QComboBox)
        combo.setCurrentIndex(combo.findText(t("orchestra.western")))
        self.assertEqual(list(self.columns(editor)), [
            "未歸類（4）", "木管（0）", "銅管（0）", "打擊（0）", "鍵盤（0）", "弦樂（0）", "其他（0）",
        ])

    def test_search_filters_every_column(self):
        editor = self.open_map()
        editor.findChild(QLineEdit).setText("琴")
        columns = self.columns(editor)
        self.assertEqual(columns["未歸類（2）"], ["柳琴（1 首）"])
        self.assertEqual(columns["拉弦（1）"], [])
        self.assertEqual(columns["彈撥（1）"], [])

    def test_moving_updates_the_column_counts(self):
        editor = self.open_map()
        self.move(editor, "柳琴", "彈撥")
        self.assertEqual(self.columns(editor)["彈撥（2）"], ["琵琶（1 首）", "柳琴（1 首）"])


class TestSaving(SectionMapWindowTestCase):
    """按儲存才寫入，只寫改過的樂器；被別人改過時開衝突畫面"""

    def test_nothing_is_written_until_saved(self):
        editor = self.open_map()
        self.move(editor, "柳琴", "彈撥")
        self.move(editor, "琵琶", "低音")
        self.idle()
        self.window.close()
        self.assertEqual(self.google.sheets.writes, [])

    def test_saving_writes_only_the_moved_instruments(self):
        editor = self.open_map()
        self.move(editor, "柳琴", "彈撥")
        self.button(editor, t("catalog.sections.save")).click()
        wait_until(lambda: len(self.club()) == 3)
        self.idle()
        self.assertEqual(self.club(), [
            ("chinese", "二胡 I", "拉弦"), ("chinese", "琵琶", "彈撥"), ("chinese", "柳琴", "彈撥"),
        ])
        sources = {r["source"] for r in records(self.google, SHEET_INSTRUMENTATION_LOG)}
        self.assertEqual(sources, {"catalog_window"})
        reloaded = self.window.findChild(SectionMapEditor)
        self.assertEqual(self.columns(reloaded)["彈撥（2）"], ["琵琶（1 首）", "柳琴（1 首）"])

    def test_an_instrument_someone_else_changed_opens_the_conflict_dialog(self):
        editor = self.open_map()
        self.move(editor, "琵琶", "低音")
        self.google.sheets.set_cell(SHEET_SECTIONS, "D3", "打擊")
        with answering_conflicts(t("catalog.sync.use_mine")) as shown:
            self.button(editor, t("catalog.sections.save")).click()
            wait_until(lambda: ("chinese", "琵琶", "低音") in self.club())
            self.idle()
        self.assertEqual(shown, [[(t("catalog.sync.section", instrument="琵琶"), "低音", "打擊")]])


if __name__ == "__main__":
    unittest.main()
