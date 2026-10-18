# -*- coding: utf-8 -*-
"""
存檔時寫回譜庫的接線測試（offscreen）

主視窗按儲存時，連到譜庫的群組的改動經譜庫服務寫進假 Google，再存專案檔；譜庫被別人改過時開衝突畫面，
列出這次會寫進譜庫的全部改動、衝突的標紅要選邊；寫不進去時頂端出現提示列。編制表在連到譜庫的群組刪除聲部時
確認框寫明從樂譜刪除，已有 Drive 檔案的聲部擋下；聲部組旁寫出其他曲目的影響範圍。不測畫面。
"""
import os
import sys
import unittest
from contextlib import contextmanager
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QComboBox, QLabel, QMessageBox, QPushButton, QTableWidget, QTabWidget

from core.catalog_constants import SHEET_DURATIONS, SHEET_EDITIONS, SHEET_PARTS, SHEET_PIECES, SHEET_SECTIONS
from core.catalog_errors import CatalogErrorKind
from core.constants import CATALOG_ROOT_FOLDER_ID_KEY, CATALOG_SPREADSHEET_ID_KEY
from core.locale import t
from core.models import Headcount, Project
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.file_service import FileService
from services.project_service import ProjectService
from test_catalog_link import records
from test_catalog_link_dialog import fake_catalog
from tests.test_catalog_service import add_row
from tests.test_main_window import MainWindowTestCase, answering_prompts
from ui.catalog_conflict_dialog import CatalogConflictDialog
from ui.catalog_error_bar import CatalogErrorBar
from ui.instrumentation_table import InstrumentationTable


@contextmanager
def answering_conflicts(choice_text: str = "", accept: bool = True):
    """模擬使用者在衝突畫面每個衝突都選 choice_text（空字串時不選），再按「寫入並存檔」或取消

    Yields:
        開啟過的衝突畫面，各自在開啟當下列出的（項目、原本、改成、譜庫現在、是否標紅）
    """
    shown = []

    def fake_exec(dialog):
        table = dialog.findChild(QTableWidget)
        rows = []
        for row in range(table.rowCount()):
            texts = tuple(table.item(row, column).text() for column in range(1, 5))
            rows.append(texts + (table.item(row, 1).foreground().color().name() == "#e74c3c",))
        shown.append(rows)
        for combo in dialog.findChildren(QComboBox):
            if choice_text:
                combo.setCurrentIndex(combo.findText(choice_text))
        if accept:
            button = next(b for b in dialog.findChildren(QPushButton) if b.text() == t("catalog.sync.confirm"))
            if button.isEnabled():
                button.click()
                return dialog.result()
        dialog.reject()
        return dialog.result()

    with mock.patch.object(CatalogConflictDialog, "exec", fake_exec):
        yield shown


class WriteBackWindowTestCase(MainWindowTestCase):
    """主視窗開著一個專案：群組連到假 Google 的「春江花月夜」第一樂章（二胡 I、琵琶，時長 3:00，社團對照有兩種樂器）"""

    def setUp(self):
        super().setUp()
        self.auth.authenticate()
        self.google = fake_catalog()
        add_row(self.google, SHEET_DURATIONS, id="d1", edition_id="e1", movement_id="m1", seconds="180")
        for n, (instrument, section) in enumerate((("二胡", "拉弦"), ("琵琶", "彈撥"))):
            add_row(
                self.google, SHEET_SECTIONS, id=f"s{n}", orchestra_type="chinese", instrument=instrument,
                section=section,
            )
        self.preferences.set(CATALOG_SPREADSHEET_ID_KEY, "sheet-1")
        self.preferences.set(CATALOG_ROOT_FOLDER_ID_KEY, self.google.root)
        with answering_prompts(t("dialog.discard_btn")):
            self.window.close()
        from ui.main_window import MainWindow
        connect = self.connect
        self.window = MainWindow(
            self.preferences, history=self.history, opener=self.opener, auth_service=self.auth,
            compressor=self.compressor(), log=ActivityLog(FileService(), self.log_dir), catalog_factory=connect,
        )
        project = Project()
        group = project.add_group("江樓鐘鼓", "總譜")
        project.link_group(group, self.connect().read_place("e1", "m1", project.orchestra_type))
        self.path = self.open_from_menu(project)
        self.window.findChild(QTabWidget).setCurrentIndex(1)
        self.group = self.window.project.groups[0]

    def connect(self) -> CatalogService:
        """接假 Google 的譜庫服務"""
        return CatalogService(self.google.sheets, self.google.drive, sleep=lambda s: None)

    def save(self):
        """從選單儲存專案"""
        with answering_prompts() as shown:
            self.trigger_menu(t("menu.file.save"))
        return shown

    def table(self) -> InstrumentationTable:
        """左欄的編制表"""
        return self.window.findChild(InstrumentationTable)

    def headcount_in_catalog(self, voice: str) -> str:
        """分譜工作表裡第一樂章這個聲部的最少人數"""
        return next(r["headcount_min"] for r in records(self.google, SHEET_PARTS) if r["instrument_name"] == voice)


class TestSavingWritesBack(WriteBackWindowTestCase):
    """按儲存時同時寫進譜庫與專案檔"""

    def test_saving_writes_the_changed_cells_and_the_project_file(self):
        self.window.project.set_headcount(self.group, 1, Headcount(3))
        self.save()
        self.assertEqual(self.headcount_in_catalog("琵琶"), "3")
        self.assertFalse(self.is_marked_unsaved())
        saved = ProjectService().load_project(self.path)
        self.assertEqual(saved.catalog_changes(saved.groups[0]), [])

    def test_without_changes_the_catalog_is_not_contacted(self):
        self.window.project.update_group(self.group, composer="張三")
        before = self.google.requests
        self.save()
        self.assertEqual(self.google.requests, before)
        self.assertFalse(self.is_marked_unsaved())

    def test_unwritable_catalog_shows_the_bar_and_keeps_the_project_unsaved(self):
        self.window.project.set_headcount(self.group, 1, Headcount(3))
        self.google.fail_next(CatalogErrorKind.OFFLINE)
        self.save()
        bar = self.window.findChild(CatalogErrorBar)
        self.assertFalse(bar.isHidden())
        self.assertIn(t("catalog.error.cannot_write"), bar.findChild(QLabel).text())
        self.assertTrue(self.is_marked_unsaved())
        self.assertEqual(self.headcount_in_catalog("琵琶"), "")


class TestConflictScreen(WriteBackWindowTestCase):
    """譜庫被別人改過：衝突畫面列出這次會寫進譜庫的全部改動，衝突的標紅要選邊"""

    def setUp(self):
        super().setUp()
        self.window.project.set_headcount(self.group, 1, Headcount(3))
        self.window.project.set_duration(self.group, 200)
        self.google.sheets.set_cell(SHEET_PARTS, "J3", "4")

    def test_lists_every_change_and_marks_the_conflict_red(self):
        with answering_conflicts(t("catalog.sync.use_catalog")) as shown:
            self.save()
        self.assertEqual(shown, [[
            (t("catalog.sync.headcount", voice="琵琶"), "", "3", "4", True),
            (t("catalog.sync.duration"), "3:00", "3:20", "3:00", False),
        ]])

    def test_taking_the_catalog_value_saves_without_writing_it(self):
        with answering_conflicts(t("catalog.sync.use_catalog")):
            self.save()
        self.assertEqual(self.headcount_in_catalog("琵琶"), "4")
        self.assertEqual(records(self.google, SHEET_DURATIONS)[0]["seconds"], "200")
        self.assertEqual(self.group.headcounts[1], Headcount(4))
        self.assertFalse(self.is_marked_unsaved())

    def test_keeping_mine_writes_it(self):
        with answering_conflicts(t("catalog.sync.use_mine")):
            self.save()
        self.assertEqual(self.headcount_in_catalog("琵琶"), "3")

    def test_without_choosing_a_side_nothing_is_written_or_saved(self):
        with answering_conflicts():
            self.save()
        self.assertEqual(records(self.google, SHEET_DURATIONS)[0]["seconds"], "180")
        self.assertTrue(self.is_marked_unsaved())


class TestDeletingVoices(WriteBackWindowTestCase):
    """在連到譜庫的群組刪除聲部：確認框寫明從樂譜刪除；已有 Drive 檔案的聲部擋下"""

    def remove_row(self, row: int):
        """在編制表選取一列後按「移除」"""
        table = self.table().findChild(QTableWidget)
        table.clearSelection()
        table.selectRow(row)
        next(b for b in self.table().findChildren(QPushButton) if b.text() == t("instrument.remove")).click()

    def test_confirmation_says_it_deletes_the_voice_from_the_score(self):
        with answering_prompts(QMessageBox.No) as shown:
            self.remove_row(1)
        self.assertEqual(shown, [(t("dialog.warning"), t("catalog.sync.delete_voices", voices="琵琶"))])
        self.assertEqual(self.group.instruments, ["二胡 I", "琵琶"])
        with answering_prompts(QMessageBox.Yes):
            self.remove_row(1)
        self.assertEqual(self.group.instruments, ["二胡 I"])

    def test_voice_with_a_drive_file_cannot_be_deleted(self):
        self.google.sheets.set_cell(SHEET_PARTS, "G3", "drive-9")
        project = Project()
        group = project.add_group("江樓鐘鼓", "總譜")
        project.link_group(group, self.connect().read_place("e1", "m1", project.orchestra_type))
        self.open_from_menu(project)
        self.window.findChild(QTabWidget).setCurrentIndex(1)
        with answering_prompts() as shown:
            self.remove_row(1)
        self.assertEqual(shown, [(t("dialog.warning"), t("catalog.sync.filed_voices", voices="琵琶"))])
        self.assertEqual(self.window.project.groups[0].instruments, ["二胡 I", "琵琶"])


class TestSectionImpact(WriteBackWindowTestCase):
    """連到譜庫的群組，聲部組旁寫出其他曲目也會跟著改"""

    def test_names_how_many_other_pieces_use_the_instrument(self):
        add_row(self.google, SHEET_PIECES, id="p2", title="將軍令")
        add_row(self.google, SHEET_EDITIONS, id="e2", piece_id="p2")
        add_row(self.google, SHEET_PARTS, id="x1", edition_id="e2", instrument_name="琵琶 II", sort_order="1")
        self.window.project.set_headcount(self.group, 1, Headcount(3))
        self.save()
        self.table().refresh()
        table = self.table().findChild(QTableWidget)
        follows = table.item(1, InstrumentationTable.column("follows")).text()
        self.assertEqual(follows, t("catalog.sync.other_pieces", count=1, instrument="琵琶"))


if __name__ == "__main__":
    unittest.main()
