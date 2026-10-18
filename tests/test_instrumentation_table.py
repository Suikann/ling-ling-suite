# -*- coding: utf-8 -*-
"""
編制表元件測試（offscreen）

不經主視窗，直接帶入專案與群組使用編制表，測它的拖動、改名、勾選、人數、時長、複製編制與聲部組各呼叫對的編輯操作，
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
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QLabel, QMessageBox, QPushButton, QSpinBox, QTableWidget,
)

from core.locale import get_keys, t
from core.models import FileInfo, Group, Headcount, Project
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

    def shown_texts(self) -> List[str]:
        """編制表目前顯示的說明文字"""
        return [label.text() for label in self.editor.findChildren(QLabel) if label.isVisibleTo(self.editor)]

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



class TestHeadcountColumns(InstrumentationTableTestCase):
    """最少、最多、以上三欄：改值寫進那一列的人數範圍"""

    def spin(self, row: int, column: str) -> QSpinBox:
        return self.table.cellWidget(row, self.editor.column(column))

    def open_ended(self, row: int):
        return self.table.item(row, self.editor.column("open_ended"))

    def test_headers_say_minimum_maximum_and_open_ended(self):
        headers = [self.table.horizontalHeaderItem(i).text() for i in range(self.table.columnCount())]
        self.assertEqual(headers[:5], [t("instrumentation.present"), t("instrumentation.voice"), "最少", "最多", "以上"])

    def test_minimum_and_maximum_write_the_range(self):
        self.spin(0, "minimum").setValue(6)
        self.spin(0, "maximum").setValue(8)
        self.spin(1, "minimum").setValue(4)
        self.assertEqual(self.group.headcounts, [Headcount(6, 8), Headcount(4), Headcount()])
        self.assertTrue(self.project.is_modified())

    def test_open_ended_clears_and_disables_the_maximum(self):
        self.spin(0, "minimum").setValue(2)
        self.spin(0, "maximum").setValue(4)
        self.open_ended(0).setCheckState(Qt.Checked)
        self.assertEqual(self.group.headcounts[0], Headcount(2, open_ended=True))
        self.assertEqual(self.spin(0, "maximum").value(), 0)
        self.assertFalse(self.spin(0, "maximum").isEnabled())

    def test_shows_the_stored_headcounts_and_blank_for_unfilled(self):
        self.project.set_headcount(self.group, 2, Headcount(4, 6))
        self.editor.show_group(self.group)
        self.assertEqual((self.spin(2, "minimum").value(), self.spin(2, "maximum").value()), (4, 6))
        self.assertEqual(self.spin(0, "minimum").text().strip(), "")


class TestDuration(InstrumentationTableTestCase):
    """上方的時長（分、秒）以秒寫進群組"""

    def spins(self):
        return self.editor.findChild(QSpinBox, "duration_minutes"), self.editor.findChild(QSpinBox, "duration_seconds")

    def test_minutes_and_seconds_are_stored_as_seconds(self):
        minutes, seconds = self.spins()
        minutes.setValue(7)
        seconds.setValue(45)
        self.assertEqual(self.group.duration_seconds, 465)
        self.assertTrue(self.project.is_modified())

    def test_shows_the_stored_duration(self):
        self.project.set_duration(self.group, 125)
        self.editor.show_group(self.group)
        self.assertEqual([spin.value() for spin in self.spins()], [2, 5])


class TestCopyInstrumentation(InstrumentationTableTestCase):
    """從其他群組複製編制：依同一聲部對應，表格下方寫出對應不到、已留空的聲部"""

    def test_choosing_another_group_copies_its_headcounts(self):
        source = Group(name="第一樂章", instruments=["flute", "Horn"], headcounts=[Headcount(2), Headcount(4, 6)])
        self.project.groups.append(source)
        self.editor.show_group(self.group)
        combo = next(c for c in self.editor.findChildren(QComboBox) if c.itemText(0) == t("instrumentation.copy_from"))
        self.assertEqual([combo.itemText(i) for i in range(1, combo.count())], ["第一樂章"])
        combo.setCurrentIndex(1)
        self.assertEqual([h.text() for h in self.group.headcounts], ["2", "", "4-6"])
        self.assertIn(t("instrumentation.copy_unmatched", voices="Oboe"), self.shown_texts())
        self.assertEqual(combo.currentIndex(), 0)


class TestOldHeadcountDialogIsGone(InstrumentationTableTestCase):
    """人數改在編制表裡填：舊的「編輯人數」對話框拿掉，介面用詞照詞彙表"""

    def test_there_is_no_edit_headcount_button(self):
        texts = [b.text() for b in self.editor.findChildren(QPushButton)]
        self.assertNotIn("編輯人數", texts)
        self.assertFalse(any(isinstance(w, QDialog) for w in QApplication.topLevelWidgets() if w.isVisible()))

    def test_wording(self):
        self.assertEqual(t("instrument.export"), "匯出樂器表")
        self.assertEqual(t("filedialog.export_instruments"), "匯出樂器表")
        self.assertEqual(t("instrument.load_preset"), "載入預設樂器表")
        self.assertEqual(t("split.load_names"), "載入預設樂器表：")
        for key in get_keys("zh_TW"):
            with self.subTest(key):
                for avoided in ("建議人數", "編制設定", "匯出編制表", "載入編制表"):
                    self.assertNotIn(avoided, t(key))

if __name__ == "__main__":
    unittest.main()
