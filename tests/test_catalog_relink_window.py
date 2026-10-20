# -*- coding: utf-8 -*-
"""
改連的接線測試（offscreen）

群組連到假 Google 的「春江花月夜」第一樂章，之前存檔時新增了笛子、把琵琶改成 3 人。改連到別處後跳出確認框，
四欄表列出撤掉後的結果，「留著」「撤掉」各呼叫對的操作；在合併差異表那步取消時不問、連線不變。
編制表上方連線旁有「改連…」，按下開啟連線對話框。不測畫面。
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QLabel, QPushButton, QTableWidget, QTabWidget

from core.catalog_constants import SHEET_MOVEMENTS, SHEET_PARTS
from core.catalog_revert import KeepReason, RevertOutcome, RevertRow
from core.catalog_sync import WriteSource, place_writes
from core.constants import CATALOG_ROOT_FOLDER_ID_KEY, CATALOG_SPREADSHEET_ID_KEY
from core.locale import t
from core.models import Group, Headcount, Project
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.file_service import FileService
from test_catalog_link_dialog import fake_catalog
from tests.test_catalog_service import add_row
from tests.test_main_window import MainWindowTestCase, answering_prompts, button_in, wait_until
from ui.catalog_link_dialog import CatalogLinkDialog
from ui.catalog_merge_dialog import CatalogMergeDialog
from ui.catalog_revert_dialog import CatalogRevertDialog
from ui.instrumentation_table import InstrumentationTable


def linked_and_saved(google) -> Project:
    """專案的群組連到第一樂章，新增笛子（2 人）、琵琶改成 3 人後寫回譜庫（像之前存過檔）"""
    catalog = CatalogService(google.sheets, google.drive, sleep=lambda s: None)
    project = Project()
    group = project.add_group("江樓鐘鼓", "總譜")
    project.update_group(group, piece_name="春江花月夜", movement_number="1", movement_name="江樓鐘鼓")
    project.link_group(group, catalog.read_place("e1", "m1", project.orchestra_type))
    project.add_instrument(group, "笛子")
    project.set_headcount(group, 2, Headcount(2))
    project.set_headcount(group, 1, Headcount(3))
    outcome = catalog.sync_instrumentation(
        place_writes(project), project.orchestra_type, "score@example.com", WriteSource.SAVE, {},
    )
    project.mark_catalog_synced(outcome)
    return project


class RelinkDialogTestCase(unittest.TestCase):
    """連線對話框以改連開啟：群組已連到第一樂章並寫過編制；譜庫另有第二樂章（有二胡 I）"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.google = fake_catalog()
        add_row(self.google, SHEET_MOVEMENTS, id="m2", piece_id="p1", movement_number="2", name="月上東山", sort_order="2")
        add_row(self.google, SHEET_PARTS, id="pt3", edition_id="e1", movement_id="m2", instrument_name="二胡 I",
                sort_order="1")
        self.project = linked_and_saved(self.google)
        self.group = self.project.groups[0]
        self.results = []

    def tearDown(self):
        """關掉還開著的對話框，不留給之後的測試"""
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, QDialog) and widget.isVisible():
                widget.reject()

    def open_dialog(self) -> CatalogLinkDialog:
        """開啟連線對話框，等曲目讀進來"""
        dialog = CatalogLinkDialog(
            lambda: CatalogService(self.google.sheets, self.google.drive, sleep=lambda s: None),
            self.project, self.group, self.google.root,
        )
        dialog.linked.connect(self.results.append)
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        wait_until(lambda: dialog.findChildren(QComboBox)[0].count() > 0)
        return dialog

    def relink_to_whole_piece(self) -> CatalogRevertDialog:
        """改連到整首（還沒有編制），等確認框出現"""
        self.dialog = self.open_dialog()
        self.dialog.findChildren(QComboBox)[2].setCurrentIndex(0)
        button_in(self.dialog, QPushButton, t("catalog.link.confirm")).click()
        wait_until(lambda: self.dialog.findChild(CatalogRevertDialog) is not None)
        return self.dialog.findChild(CatalogRevertDialog)


class TestRevertQuestion(RelinkDialogTestCase):
    """改連完成後問要不要撤掉原處的編制：撤掉就記下、存檔時寫入；留著就不記"""

    def test_dialog_is_titled_relink(self):
        self.assertEqual(self.open_dialog().windowTitle(), t("catalog.relink.title"))

    def test_question_counts_what_can_be_reverted(self):
        question = self.relink_to_whole_piece()
        texts = [label.text() for label in question.findChildren(QLabel)]
        self.assertIn(t("catalog.revert.question", count=2), texts)
        self.assertEqual(self.group.catalog_link.movement_id, "")

    def test_reverting_queues_it_for_the_next_save(self):
        button_in(self.relink_to_whole_piece(), QPushButton, t("catalog.revert.revert")).click()
        wait_until(lambda: not self.dialog.isVisible())
        [revert] = self.group.catalog_link.reverts
        self.assertEqual((revert.movement_id, revert.remove), ("m1", ["笛子"]))
        self.assertEqual(revert.restore, [("琵琶", Headcount(3), Headcount())])
        self.assertEqual(len(self.results), 1)

    def test_keeping_queues_nothing(self):
        button_in(self.relink_to_whole_piece(), QPushButton, t("catalog.revert.keep")).click()
        wait_until(lambda: not self.dialog.isVisible())
        self.assertEqual(self.group.catalog_link.reverts, [])
        self.assertEqual(self.group.catalog_link.movement_id, "")
        self.assertEqual(len(self.results), 1)

    def test_cancelling_the_merge_table_neither_asks_nor_relinks(self):
        dialog = self.open_dialog()
        dialog.findChildren(QComboBox)[2].setCurrentIndex(2)
        button_in(dialog, QPushButton, t("catalog.link.confirm")).click()
        wait_until(lambda: dialog.findChild(CatalogMergeDialog) is not None)
        button_in(dialog.findChild(CatalogMergeDialog), QPushButton, t("dialog.cancel_btn")).click()
        self.assertEqual(self.group.catalog_link.movement_id, "m1")
        self.assertIsNone(dialog.findChild(CatalogRevertDialog))
        self.assertEqual(self.results, [])


class TestRevertTable(unittest.TestCase):
    """確認框的四欄表：聲部、那次寫的、譜庫現在、撤掉後；撤掉後分刪掉、改回原值、不動三種"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_each_outcome_is_written_in_the_last_column(self):
        rows = [
            RevertRow("笛子", True, Headcount(2), Headcount(2), RevertOutcome.REMOVE),
            RevertRow("琵琶", False, Headcount(3), Headcount(3), RevertOutcome.RESTORE, original=Headcount(2)),
            RevertRow("揚琴", False, Headcount(2), Headcount(4), RevertOutcome.KEEP, keep_reason=KeepReason.CHANGED,
                      changed_by="other@example.com", changed_at="2026-10-01 09:30:00"),
            RevertRow("笙", True, Headcount(), None, RevertOutcome.KEEP, keep_reason=KeepReason.EDITED),
            RevertRow("中阮", True, Headcount(1), Headcount(1), RevertOutcome.KEEP, keep_reason=KeepReason.FILED),
        ]
        dialog = CatalogRevertDialog(rows, "春江花月夜 › 彭修文編 › 江樓鐘鼓")
        self.addCleanup(dialog.deleteLater)
        table = dialog.findChild(QTableWidget)
        self.assertEqual(
            [table.horizontalHeaderItem(c).text() for c in range(4)],
            [t("catalog.revert.column.voice"), t("catalog.revert.column.written"),
             t("catalog.revert.column.current"), t("catalog.revert.column.after")],
        )
        cells = [[table.item(r, c).text() for c in range(4)] for r in range(table.rowCount())]
        self.assertEqual(cells, [
            ["笛子", t("catalog.revert.added", headcount="2"), "2", t("catalog.revert.remove")],
            ["琵琶", "3", "3", t("catalog.revert.restore", headcount="2")],
            ["揚琴", "2", "4", t("catalog.revert.keep_changed", who="other@example.com", when="2026-10-01 09:30:00")],
            ["笙", t("catalog.revert.added_blank"), t("catalog.revert.gone"), t("catalog.revert.keep_edited")],
            ["中阮", t("catalog.revert.added", headcount="1"), "1", t("catalog.revert.keep_filed")],
        ])
        self.assertIn(t("catalog.revert.question", count=2), [label.text() for label in dialog.findChildren(QLabel)])


class TestMainWindowRelinks(MainWindowTestCase):
    """主視窗：連到譜庫的群組在編制表上方連線旁有「改連…」，按下開啟連線對話框"""

    def setUp(self):
        super().setUp()
        self.auth.authenticate()
        self.google = fake_catalog()
        self.preferences.set(CATALOG_SPREADSHEET_ID_KEY, "sheet-1")
        self.preferences.set(CATALOG_ROOT_FOLDER_ID_KEY, self.google.root)
        with answering_prompts(t("dialog.discard_btn")):
            self.window.close()
        from ui.main_window import MainWindow
        self.window = MainWindow(
            self.preferences, history=self.history, opener=self.opener, auth_service=self.auth,
            compressor=self.compressor(), log=ActivityLog(FileService(), self.log_dir),
            catalog_factory=lambda: CatalogService(self.google.sheets, self.google.drive, sleep=lambda s: None),
        )
        self.open_from_menu(linked_and_saved(self.google))
        self.window.findChild(QTabWidget).setCurrentIndex(1)

    def test_relink_button_opens_the_link_dialog(self):
        table = self.window.findChild(InstrumentationTable)
        self.assertFalse(button_in(table, QPushButton, t("catalog.link.button")).isVisibleTo(table))
        relink = button_in(table, QPushButton, t("catalog.relink.button"))
        self.assertTrue(relink.isVisibleTo(table))
        relink.click()
        wait_until(lambda: self.window.findChild(CatalogLinkDialog) is not None)
        dialog = self.window.findChild(CatalogLinkDialog)
        self.addCleanup(dialog.reject)
        self.assertEqual(dialog.windowTitle(), t("catalog.relink.title"))

    def test_unlinked_group_has_no_relink_button(self):
        self.window.project.groups.append(Group(name="另一首"))
        table = self.window.findChild(InstrumentationTable)
        table.show_group(self.window.project.groups[1])
        self.assertFalse(button_in(table, QPushButton, t("catalog.relink.button")).isVisibleTo(table))


if __name__ == "__main__":
    unittest.main()
