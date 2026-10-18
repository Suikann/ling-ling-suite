# -*- coding: utf-8 -*-
"""
聲部名稱自動完成的接線測試（offscreen）

測編制表的輸入欄與改名的編輯欄都掛上自動完成、候選來源與「同一聲部」標記，以及選了候選照樣受同一份樂器表
不重名的規則擋下；譜庫視窗讀到的聲部名稱經主視窗給這幾處共用。分割的分段名稱見 test_split_dialog.py。
不測畫面。以 QT_QPA_PLATFORM=offscreen 執行，不需要顯示器。
"""
import os
import sys
import unittest
from typing import List
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFocusEvent, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QTableWidget

from core.catalog_constants import SHEET_PARTS
from core.constants import CATALOG_SPREADSHEET_ID_KEY
from core.locale import t
from core.models import Group, Project
from core.voice_suggestions import VoiceCandidates
from tests.test_catalog_service import add_row
from tests.test_catalog_window import CatalogWindowTestCase
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.file_service import FileService
from tests.test_main_window import (
    InstrumentListTestCase, MainWindowTestCase, answering_prompts, button_in, wait_until,
)
from ui.catalog_window import CatalogWindow
from ui.instrumentation_table import InstrumentationTable


def type_into(edit: QLineEdit, text: str) -> None:
    """在欄位打字：先讓欄位取得焦點、清掉原本的文字，再逐字送出按鍵

    QTest.keyClicks 只認 ASCII，中文改送帶文字的按鍵事件；offscreen 不會真的切換焦點，所以直接送 FocusIn。
    """
    QApplication.sendEvent(edit, QFocusEvent(QEvent.FocusIn, Qt.OtherFocusReason))
    edit.selectAll()
    QTest.keyClick(edit, Qt.Key_Backspace)
    for char in text:
        QApplication.sendEvent(edit, QKeyEvent(QEvent.KeyPress, 0, Qt.NoModifier, char))


def suggestions(edit: QLineEdit) -> List[str]:
    """欄位的自動完成目前列出的候選（顯示的文字）"""
    completer = edit.completer()
    if completer is None:
        return []
    model = completer.model()
    return [model.index(row, 0).data() for row in range(model.rowCount())]


def choose(edit: QLineEdit, row: int) -> None:
    """在自動完成的選單點第 row 個候選（按 Enter 選的話，Enter 也會送到欄位本身）"""
    completer = edit.completer()
    popup = completer.popup()
    index = completer.completionModel().index(row, 0)
    QTest.mouseClick(popup.viewport(), Qt.LeftButton, pos=popup.visualRect(index).center())


def marked(name: str) -> str:
    """與輸入同一聲部、寫法不同的候選顯示的文字"""
    return t("voice.suggestion.same_voice", name=name)


class CompleterTestCase(unittest.TestCase):
    """一個專案：目前的群組（聲部 二胡 I、琵琶）與另一個群組（中胡、大阮）"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.group = Group(name="g", instruments=["二胡 I", "琵琶"])
        self.other = Group(name="h", instruments=["中胡", "大阮"])
        self.project = Project(groups=[self.group, self.other])
        self.candidates = VoiceCandidates()


class TestInstrumentationTableEntry(CompleterTestCase):
    """編制表的輸入欄"""

    def setUp(self):
        super().setUp()
        self.editor = InstrumentationTable(self.project, voice_candidates=self.candidates)
        self.addCleanup(self.editor.deleteLater)
        self.editor.show_group(self.group)
        self.editor.show()
        self.entry = next(
            e for e in self.editor.findChildren(QLineEdit) if e.placeholderText() == t("instrument.placeholder")
        )

    def test_catalog_names_are_listed_in_order_and_the_same_voice_is_marked(self):
        self.candidates.remember_catalog(["二胡 II", "二胡 I", "中胡"])
        type_into(self.entry, "二胡2")
        self.assertEqual(suggestions(self.entry), [marked("二胡 II")])
        type_into(self.entry, "胡")
        self.assertEqual(suggestions(self.entry), ["二胡 II", "二胡 I", "中胡"])

    def test_without_the_catalog_other_groups_and_presets_are_listed(self):
        type_into(self.entry, "阮")
        listed = suggestions(self.entry)
        self.assertEqual(listed[0], "大阮")
        self.assertIn("中阮", listed)

    def test_choosing_a_candidate_fills_in_the_name_without_the_mark(self):
        self.candidates.remember_catalog(["二胡 II"])
        type_into(self.entry, "二胡2")
        choose(self.entry, 0)
        self.assertEqual(self.entry.text(), "二胡 II")

    def test_typing_alone_never_rewrites_the_text(self):
        self.candidates.remember_catalog(["二胡 II"])
        type_into(self.entry, "二胡2")
        self.assertEqual(self.entry.text(), "二胡2")

    def test_a_chosen_candidate_is_still_refused_as_a_duplicate(self):
        self.candidates.remember_catalog(["二胡1"])
        type_into(self.entry, "二胡")
        choose(self.entry, 0)
        with answering_prompts() as prompts:
            button_in(self.editor, QPushButton, t("instrument.add")).click()
        self.assertEqual(self.group.instruments, ["二胡 I", "琵琶"])
        self.assertEqual(len(prompts), 1)


class TestInstrumentationTableRename(CompleterTestCase):
    """編制表雙擊改名的編輯欄"""

    def setUp(self):
        super().setUp()
        self.editor = InstrumentationTable(self.project, voice_candidates=self.candidates)
        self.addCleanup(self.editor.deleteLater)
        self.editor.show_group(self.group)
        self.editor.show()
        self.table = self.editor.findChild(QTableWidget)

    def open_editor(self, row: int) -> QLineEdit:
        """開始改第 row 列的聲部名稱，回傳編輯欄"""
        item = self.table.item(row, self.editor.column("voice"))
        self.table.editItem(item)
        return self.table.indexWidget(self.table.indexFromItem(item))

    def commit(self, edit: QLineEdit) -> None:
        """收下編輯欄的內容，如同按 Enter（offscreen 下送 Enter 時委派認不出編輯欄，改發出它按 Enter 時發的訊號）"""
        delegate = self.table.itemDelegateForColumn(self.editor.column("voice")) or self.table.itemDelegate()
        delegate.commitData.emit(edit)

    def test_the_rename_editor_lists_candidates_and_marks_the_same_voice(self):
        self.candidates.remember_catalog(["琵琶 I", "柳琴"])
        edit = self.open_editor(1)
        type_into(edit, "琵琶1")
        self.assertEqual(suggestions(edit), [marked("琵琶 I")])

    def test_a_chosen_candidate_is_still_refused_as_a_duplicate(self):
        self.candidates.remember_catalog(["二胡1"])
        edit = self.open_editor(1)
        type_into(edit, "二胡")
        choose(edit, 0)
        with answering_prompts() as prompts:
            self.commit(edit)
        self.assertEqual(self.group.instruments, ["二胡 I", "琵琶"])
        self.assertEqual(len(prompts), 1)


class TestCatalogWindowFeedsCandidates(CatalogWindowTestCase):
    """讀譜庫總覽時一起取得的聲部名稱交給候選來源"""

    def test_reading_the_catalog_remembers_its_voice_names(self):
        add_row(self.google, SHEET_PARTS, id="t1", edition_id="e1", instrument_name="琵琶")
        add_row(self.google, SHEET_PARTS, id="t2", edition_id="e1", instrument_name="二胡 I")
        add_row(self.google, SHEET_PARTS, id="t3", edition_id="e1", instrument_name="二胡 I")
        candidates = VoiceCandidates()
        window = CatalogWindow(
            self.auth, self.preferences,
            catalog_factory=lambda: CatalogService(self.google.sheets, self.google.drive, sleep=lambda s: None),
            log=ActivityLog(FileService(), self.log_dir), voice_candidates=candidates,
        )
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        wait_until(lambda: self.tree(window).topLevelItemCount() > 0)
        self.assertEqual(candidates.for_group(None, None), ["二胡 I", "琵琶"])


def catalog_candidates(case: MainWindowTestCase) -> VoiceCandidates:
    """從主視窗的選單開譜庫視窗（以替身代替），回傳主視窗交給它的候選來源"""
    case.preferences.set(CATALOG_SPREADSHEET_ID_KEY, "sheet-1")
    with mock.patch.object(case.auth, "get_credentials", return_value=object()), \
            mock.patch("ui.catalog_window.CatalogWindow") as window_class:
        case.trigger_menu(t("menu.catalog.open"))
    return window_class.call_args.kwargs["voice_candidates"]


class TestMainWindowSharesCandidates(InstrumentListTestCase):
    """主視窗把同一份候選來源交給譜庫視窗與編制表（分割視窗見 test_split_dialog.py）"""

    def test_names_read_by_the_catalog_window_are_listed_in_the_instrumentation_table(self):
        self.open_from_menu(Project(groups=[Group(name="g", instruments=["琵琶"])]))
        catalog_candidates(self).remember_catalog(["二胡 II"])
        entry = self._instrument_entry()
        type_into(entry, "二胡2")
        self.assertEqual(suggestions(entry), [marked("二胡 II")])


if __name__ == "__main__":
    unittest.main()
