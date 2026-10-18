# -*- coding: utf-8 -*-
"""
群組連到譜庫的接線測試（offscreen）

連線對話框經譜庫服務、對假 Google：依群組的曲名與樂章編號預選、挑到的那處或新建的曲目寫進群組，讀不到時頂端出現提示列；
主視窗按編制表上方的「連到譜庫…」開啟對話框，連上後編制表上方顯示連線。不測畫面。
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QTabWidget

from core.catalog_constants import SHEET_EDITIONS, SHEET_MOVEMENTS, SHEET_PARTS, SHEET_PIECES
from core.catalog_errors import CatalogErrorKind
from core.constants import CATALOG_ROOT_FOLDER_ID_KEY, CATALOG_SPREADSHEET_ID_KEY
from core.locale import t
from core.models import FileInfo, Group, Project
from core.voice_suggestions import VoiceCandidates
from fake_google_catalog import FakeGoogle
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.file_service import FileService
from tests.test_catalog_service import add_row, empty_catalog
from tests.test_main_window import MainWindowTestCase, answering_prompts, button_in, wait_until
from ui.catalog_error_bar import CatalogErrorBar
from ui.catalog_link_dialog import CatalogLinkDialog
from ui.instrumentation_table import InstrumentationTable


def fake_catalog() -> FakeGoogle:
    """假 Google：「春江花月夜」版本 e1，第一樂章 m1 有二胡 I、琵琶兩個聲部"""
    google = FakeGoogle()
    empty_catalog(google)
    google.root = google.drive.add_folder("譜庫")
    add_row(google, SHEET_PIECES, id="p1", title="春江花月夜", active_edition_id="e1")
    add_row(google, SHEET_EDITIONS, id="e1", piece_id="p1", edition_label="彭修文編")
    add_row(google, SHEET_MOVEMENTS, id="m1", piece_id="p1", movement_number="1", name="江樓鐘鼓", sort_order="1")
    add_row(google, SHEET_PARTS, id="pt1", edition_id="e1", movement_id="m1", instrument_name="二胡 I", sort_order="1")
    add_row(google, SHEET_PARTS, id="pt2", edition_id="e1", movement_id="m1", instrument_name="琵琶", sort_order="2")
    return google


class LinkDialogTestCase(unittest.TestCase):
    """專案裡一個「春江花月夜」第一樂章的群組，對話框接假 Google"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.google = fake_catalog()
        self.project = Project()
        self.group = self.project.add_group("江樓鐘鼓", "總譜")
        self.project.update_group(self.group, piece_name="春江花月夜", movement_number="1", movement_name="江樓鐘鼓")
        self.results = []
        self.candidates = VoiceCandidates()

    def open_dialog(self) -> CatalogLinkDialog:
        """開啟連線對話框，等曲目讀進來（或提示列出現）"""
        dialog = CatalogLinkDialog(
            lambda: CatalogService(self.google.sheets, self.google.drive, sleep=lambda s: None),
            self.project, self.group, self.google.root, voice_candidates=self.candidates,
        )
        dialog.linked.connect(self.results.append)
        self.addCleanup(dialog.deleteLater)
        dialog.show()
        wait_until(lambda: self.combo(dialog, 0).count() > 0 or not dialog.findChild(CatalogErrorBar).isHidden())
        return dialog

    @staticmethod
    def combo(dialog: CatalogLinkDialog, index: int) -> QComboBox:
        """第 index 個選單：曲目、版本、樂章"""
        return dialog.findChildren(QComboBox)[index]

    def confirm(self, dialog: CatalogLinkDialog) -> None:
        """按「連線」，等它交回"""
        button_in(dialog, QPushButton, t("catalog.link.confirm")).click()
        wait_until(lambda: not dialog.isVisible() or not dialog.findChild(CatalogErrorBar).isHidden())


class TestPickingAPlace(LinkDialogTestCase):
    """依群組的曲名與樂章編號預選；按連線把那處的編制帶進群組"""

    def test_preselects_the_piece_and_movement_of_the_group_and_links_it(self):
        dialog = self.open_dialog()
        self.assertEqual(self.combo(dialog, 0).currentText(), "春江花月夜")
        self.assertEqual(self.combo(dialog, 1).currentText(), "彭修文編")
        self.assertIn("江樓鐘鼓", self.combo(dialog, 2).currentText())
        self.confirm(dialog)
        self.assertEqual((self.group.catalog_link.edition_id, self.group.catalog_link.movement_id), ("e1", "m1"))
        self.assertEqual(self.group.instruments, ["二胡 I", "琵琶"])
        self.assertEqual(len(self.results), 1)

    def test_voice_names_used_in_the_catalog_become_candidates(self):
        self.open_dialog()
        self.assertEqual(self.candidates.for_group(self.project, self.group), ["二胡 I", "琵琶"])

    def test_whole_piece_can_be_picked(self):
        dialog = self.open_dialog()
        self.combo(dialog, 2).setCurrentIndex(0)
        self.assertEqual(self.combo(dialog, 2).currentText(), t("catalog.link.whole"))
        self.confirm(dialog)
        self.assertEqual(self.group.catalog_link.movement_id, "")
        self.assertEqual(self.group.instruments, [])

    def test_piece_not_in_the_catalog_is_created_from_the_group(self):
        self.project.update_group(self.group, piece_name="將軍令", composer="彭修文")
        dialog = self.open_dialog()
        self.assertEqual(self.combo(dialog, 0).currentText(), t("catalog.link.new_piece", title="將軍令"))
        self.confirm(dialog)
        titles = [row[2] for row in self.google.sheets.rows(SHEET_PIECES)[1:]]
        self.assertIn("將軍令", titles)
        self.assertEqual(self.group.catalog_link.piece_title, "將軍令")
        self.assertEqual(self.group.catalog_link.movement_name, "江樓鐘鼓")

    def test_both_sides_with_an_instrumentation_are_not_linked_yet(self):
        self.project.set_instruments(self.group, ["笛子"])
        dialog = self.open_dialog()
        with answering_prompts() as shown:
            self.confirm(dialog)
        self.assertIn((t("catalog.link.title"), t("catalog.link.needs_merge")), shown)
        self.assertIsNone(self.group.catalog_link)
        self.assertEqual(self.results, [])


class TestLinkFailures(LinkDialogTestCase):
    """讀不到譜庫時頂端出現提示列"""

    def test_unreadable_catalog_shows_the_bar(self):
        self.google.fail_next(CatalogErrorKind.OFFLINE)
        dialog = self.open_dialog()
        bar = dialog.findChild(CatalogErrorBar)
        self.assertFalse(bar.isHidden())
        self.assertIn("連不上網路", bar.findChild(QLabel).text())


class TestMainWindowLinksAGroup(MainWindowTestCase):
    """主視窗：按編制表上方的「連到譜庫…」開啟連線對話框，連上後編制表上方顯示連線"""

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
        project = Project(groups=[Group(name="江樓鐘鼓", piece_name="春江花月夜", movement_number="1")])
        self.open_from_menu(project)
        self.window.findChild(QTabWidget).setCurrentIndex(1)

    def table(self) -> InstrumentationTable:
        """左欄的編制表"""
        return self.window.findChild(InstrumentationTable)

    def test_linking_from_the_table_shows_the_link_above_it(self):
        button_in(self.table(), QPushButton, t("catalog.link.button")).click()
        wait_until(lambda: self.window.findChild(CatalogLinkDialog) is not None)
        dialog = self.window.findChild(CatalogLinkDialog)
        wait_until(lambda: dialog.findChildren(QComboBox)[0].count() > 0)
        button_in(dialog, QPushButton, t("catalog.link.confirm")).click()
        wait_until(lambda: self.window.project.groups[0].catalog_link is not None)
        texts = [label.text() for label in self.table().findChildren(QLabel) if label.isVisibleTo(self.table())]
        self.assertIn("春江花月夜 › 彭修文編 › 江樓鐘鼓", texts)
        self.assertEqual(self.window.project.groups[0].instruments, ["二胡 I", "琵琶"])
        self.assertTrue(self.is_marked_unsaved())

    def test_group_with_duplicate_rows_is_refused_with_the_rows(self):
        group = self.window.project.groups[0]
        self.window.project.set_instruments(group, ["二胡1", "琵琶", "二胡 I"])
        self.table().show_group(group)
        with answering_prompts() as shown:
            button_in(self.table(), QPushButton, t("catalog.link.button")).click()
        rows = "\n".join([t("catalog.link.duplicate_row", row=1, voice="二胡1"),
                          t("catalog.link.duplicate_row", row=3, voice="二胡 I")])
        self.assertIn(t("catalog.link.duplicates", rows=rows), [text for _, text in shown])
        self.assertIsNone(self.window.findChild(CatalogLinkDialog))


if __name__ == "__main__":
    unittest.main()
