# -*- coding: utf-8 -*-
"""
編制表元件測試（offscreen）

不經主視窗，直接帶入專案與群組使用編制表，測它的拖動、改名、勾選與聲部組各呼叫對的編輯操作，
結果從專案讀回；不測畫面。以 QT_QPA_PLATFORM=offscreen 執行，不需要顯示器。
"""
import os
import sys
import unittest
from typing import List
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QMessageBox, QPushButton, QTableWidget

from core.locale import t
from core.models import FileInfo, Group, Project
from ui.instrumentation_table import InstrumentationTable
from tests.test_main_window import button_in

# 重名的列的文字顏色
DUPLICATE_RED = "#e74c3c"


def _files(*names: str) -> List[FileInfo]:
    return [FileInfo(os.path.join(os.getcwd(), "scores", name), name) for name in names]


class InstrumentationTableTestCase(unittest.TestCase):
    """帶入一個專案與它的一個群組（聲部 Flute、Oboe、Horn，各有一份分譜）"""

    VOICES = ["Flute", "Oboe", "Horn"]

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.group = Group(name="g", instruments=list(self.VOICES), files=_files("fl.pdf", "ob.pdf", "hn.pdf"))
        self.project = Project(groups=[self.group])
        self.editor = InstrumentationTable(self.project)
        self.addCleanup(self.editor.deleteLater)
        self.editor.show_group(self.group)
        self.table = self.editor.findChild(QTableWidget)

    def pairs(self):
        """（聲部、配到的分譜檔名），依本專案有的聲部的順序"""
        return [(p.voice, p.file.display_name if p.file else None) for p in self.group.pairing()]

    def shown_voices(self) -> List[str]:
        column = self.editor.column("voice")
        return [self.table.item(row, column).text() for row in range(self.table.rowCount())]


class TestDraggingVoices(InstrumentationTableTestCase):
    """拖動列：聲部連同分譜一起移動"""

    def test_dropping_a_row_moves_the_voice_with_its_part(self):
        self.table.rows_dropped.emit([2], 0)
        self.assertEqual(self.pairs(), [("Horn", "hn.pdf"), ("Flute", "fl.pdf"), ("Oboe", "ob.pdf")])
        self.assertEqual(self.shown_voices(), ["Horn", "Flute", "Oboe"])
        self.assertTrue(self.project.is_modified())


class TestRenamingVoices(InstrumentationTableTestCase):
    """改聲部名稱：分譜留在原位；與另一列同一聲部時說明原因並還原"""

    def test_renaming_keeps_the_part_in_place(self):
        self.table.item(1, self.editor.column("voice")).setText("English Horn")
        self.assertEqual(self.pairs()[1], ("English Horn", "ob.pdf"))

    def test_renaming_to_another_row_s_voice_is_refused_and_the_name_restored(self):
        with mock.patch.object(QMessageBox, "warning") as warned:
            self.table.item(0, self.editor.column("voice")).setText("horn")
        warned.assert_called_once()
        self.assertEqual(self.group.instruments, self.VOICES)
        self.assertEqual(self.shown_voices(), self.VOICES)


class TestPresentInThisProject(InstrumentationTableTestCase):
    """取消勾選「本專案有」：那份分譜移到未分組，表格下方寫出移了哪幾份"""

    def present_item(self, row: int):
        return self.table.item(row, self.editor.column("present"))

    def test_unchecking_moves_the_part_to_ungrouped_and_says_which(self):
        self.present_item(1).setCheckState(Qt.Unchecked)
        self.assertEqual(self.group.present, [True, False, True])
        self.assertEqual(self.pairs(), [("Flute", "fl.pdf"), ("Horn", "hn.pdf")])
        self.assertEqual([f.display_name for f in self.project.ungrouped_files], ["ob.pdf"])
        self.assertIn(t("instrumentation.moved_to_ungrouped", files="ob.pdf"), self.shown_texts())
        self.assertEqual(self.present_item(1).checkState(), Qt.Unchecked)

    def test_removing_selected_rows_moves_their_parts_to_ungrouped_and_says_which(self):
        self.table.selectRow(0)
        button_in(self.editor, QPushButton, t("instrument.remove")).click()
        self.assertEqual(self.shown_voices(), ["Oboe", "Horn"])
        self.assertEqual(self.pairs(), [("Oboe", "ob.pdf"), ("Horn", "hn.pdf")])
        self.assertIn(t("instrumentation.moved_to_ungrouped", files="fl.pdf"), self.shown_texts())

    def shown_texts(self) -> List[str]:
        return [label.text() for label in self.editor.findChildren(QLabel) if label.isVisibleTo(self.editor)]


class TestDuplicateRowsOfOldProjects(InstrumentationTableTestCase):
    """舊專案留下的重名原樣保留，與另一列同一聲部的列標紅"""

    VOICES = ["二胡 I", "二胡1", "中胡"]

    def test_rows_of_the_same_voice_are_marked_red(self):
        column = self.editor.column("voice")
        colors = [self.table.item(row, column).foreground().color().name() for row in range(3)]
        self.assertEqual(colors[:2], [DUPLICATE_RED, DUPLICATE_RED])
        self.assertNotEqual(colors[2], DUPLICATE_RED)
        self.assertEqual(self.group.instruments, self.VOICES)


class TestSectionsByInstrument(InstrumentationTableTestCase):
    """聲部組選單：改一列時同一樂器的其他編號跟著改，旁邊寫出會跟著改的編號"""

    VOICES = ["二胡 I", "二胡 II", "中胡"]

    def combos(self) -> List[QComboBox]:
        return [self.table.cellWidget(row, self.editor.column("section")) for row in range(self.table.rowCount())]

    def test_options_follow_the_orchestra_type(self):
        combo = self.combos()[0]
        self.assertEqual([combo.itemData(i) for i in range(combo.count())], ["吹管", "拉弦", "彈撥", "打擊", "低音", "其他"])

    def test_changing_one_number_changes_the_others_and_says_so(self):
        column = self.editor.column("follows")
        self.assertEqual([self.table.item(row, column).text() for row in range(3)], [
            t("instrument.section_follows", voices="二胡 II"), t("instrument.section_follows", voices="二胡 I"), "",
        ])
        combo = self.combos()[0]
        combo.setCurrentIndex(combo.findData("低音"))
        self.assertEqual([c.currentData() for c in self.combos()], ["低音", "低音", "拉弦"])
        self.assertEqual([self.project.section_of(v) for v in self.VOICES], ["低音", "低音", "拉弦"])


if __name__ == "__main__":
    unittest.main()
