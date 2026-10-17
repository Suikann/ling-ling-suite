# -*- coding: utf-8 -*-
"""
Drive 重新命名測試

計畫與撞名偵測經過命名模組，檔名與本機重新命名一致；不需要 Google 用戶端程式庫。
讀群組與改名經譜庫服務、對假 Google；對話框以 QT_QPA_PLATFORM=offscreen 執行，不需要顯示器。
"""
import os
import subprocess
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, SRC_DIR)

from PySide6.QtWidgets import (
    QApplication, QDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTextEdit,
)

from core.catalog_errors import CatalogError, CatalogErrorKind
from core.catalog_models import Piece, PieceDetail
from core.constants import DEFAULT_MASTER_TEMPLATE
from core.locale import get_locale, set_locale, t
from core.models import FileInfo, Group, Project
from services.catalog_service import CatalogService
from services.drive_rename_service import build_groups_from_drive, execute_drive_rename, generate_drive_rename_plan
from services.rename_service import generate_rename_plan
from tests.fake_google_catalog import FakeGoogle
from tests.test_main_window import answering_prompts, wait_until
from ui.catalog_error_bar import CatalogErrorBar
from ui.drive_rename_dialog import DriveRenameDialog


def _drive_files(*names):
    """Drive 上的 PDF：檔案 ID 為 id-<檔名>"""
    return [FileInfo(original_path=f"id-{name}", display_name=name) for name in names]


def _new_names(plan):
    return [entry.new_name for entry in plan.entries]


class TestSameNamesAsLocal(unittest.TestCase):
    """同一群組、同一命名格式，Drive 計畫與本機計畫的檔名一致"""

    CASES = {
        "非法字元": (
            Group(name="g", files=_drive_files("a.pdf", "b.pdf"), instruments=["Fl: 1", "Hn/2"], piece_name='Sym "5"?'),
            "{樂器} - {曲名}.pdf",
        ),
        "命名格式不含 .pdf": (
            Group(name="g", files=_drive_files("a.pdf", "b.pdf"), instruments=["Flute", "Horn"]),
            "{序號} {樂器}",
        ),
        "檔案多於聲部": (
            Group(name="g", files=_drive_files("a.pdf", "b.pdf", "c.pdf"), instruments=["Flute", "Horn"]),
            "{樂器}.pdf",
        ),
        "群組沒有聲部": (
            Group(name="g", files=_drive_files("a.pdf", "b.pdf")),
            "{樂器}.pdf",
        ),
    }

    def test_drive_names_match_the_local_plan(self):
        for case, (group, template) in self.CASES.items():
            with self.subTest(case):
                local = generate_rename_plan(Project(master_template=template, groups=[group])).entries
                drive = generate_drive_rename_plan([group], template)
                self.assertEqual(
                    [(e.file_id, e.new_name) for e in drive.entries],
                    [(e.original_path, os.path.basename(e.new_path)) for e in local],
                )


class TestVoices(unittest.TestCase):
    """明確輸入的樂器表優先；產生計畫不改寫群組"""

    def _group(self):
        return Group(name="g", files=_drive_files("a.pdf", "b.pdf"), instruments=["Flute", "Horn"])

    def test_explicit_voices_take_precedence_over_the_group_voices(self):
        plan = generate_drive_rename_plan([self._group()], "{樂器}.pdf", ["Oboe", "Bassoon"])
        self.assertEqual(_new_names(plan), ["Oboe.pdf", "Bassoon.pdf"])

    def test_empty_voices_after_explicit_ones_use_the_group_voices_and_leave_the_group_untouched(self):
        group = self._group()
        generate_drive_rename_plan([group], "{樂器}.pdf", ["Oboe", "Bassoon"])
        self.assertEqual(_new_names(generate_drive_rename_plan([group], "{樂器}.pdf", [])), ["Flute.pdf", "Horn.pdf"])
        self.assertEqual(group.instruments, ["Flute", "Horn"])


class TestConflicts(unittest.TestCase):
    """撞名：重新命名後同一群組（同一個 Drive 資料夾）內有兩個檔案同名，不分大小寫"""

    def test_names_differing_only_in_case_conflict(self):
        group = Group(name="g", files=_drive_files("a.pdf", "b.pdf"))
        plan = generate_drive_rename_plan([group], "{Instrument}.pdf", ["Flute", "FLUTE"])
        self.assertEqual(plan.conflicts, [["id-a.pdf", "id-b.pdf"]])

    def test_same_name_in_different_groups_does_not_conflict(self):
        groups = [Group(name="I", files=_drive_files("a.pdf")), Group(name="II", files=_drive_files("b.pdf"))]
        plan = generate_drive_rename_plan(groups, "{樂器}.pdf", ["Flute"])
        self.assertEqual(plan.conflicts, [])

    def test_new_name_taken_by_a_file_left_unrenamed_conflicts(self):
        group = Group(name="g", files=_drive_files("a.pdf", "flute.pdf"))
        plan = generate_drive_rename_plan([group], "{樂器}.pdf", ["Flute"])
        self.assertEqual(plan.conflicts, [["id-a.pdf", "id-flute.pdf"]])

    def test_files_left_unrenamed_do_not_conflict_among_themselves(self):
        group = Group(name="g", files=_drive_files("a.pdf", "x.pdf", "X.pdf"))
        plan = generate_drive_rename_plan([group], "{樂器}.pdf", ["Flute"])
        self.assertEqual(plan.conflicts, [])


class DriveRenameTestCase(unittest.TestCase):
    """假 Google 裡有一個版本資料夾，放著 a.pdf、b.pdf；譜庫服務接假 Google"""

    def setUp(self):
        self.google = FakeGoogle()
        self.catalog = CatalogService(self.google.sheets, self.google.drive, sleep=lambda seconds: None)
        self.folder = self.google.drive.add_folder("Sym")
        self.a = self.google.drive.add_file("a.pdf", self.folder)
        self.b = self.google.drive.add_file("b.pdf", self.folder)

    def names(self) -> list:
        """版本資料夾裡兩份檔目前的名稱"""
        return [self.google.drive.file(file_id).name for file_id in (self.a, self.b)]


class TestRenameOnDrive(DriveRenameTestCase):
    """群組從 Drive 資料夾經譜庫服務讀出；改名經譜庫服務，失敗時留下還沒改的，以同一份清單再呼叫即補齊"""

    def test_groups_follow_subfolders_and_root_pdfs(self):
        first = self.google.drive.add_folder("I", self.folder)
        self.google.drive.add_file("c.pdf", first)
        groups = build_groups_from_drive(self.catalog, self.folder, PieceDetail(piece=Piece(title="Sym")))
        self.assertEqual(
            [(g.name, g.movement_number, [f.display_name for f in g.files]) for g in groups],
            [("I", "1", ["c.pdf"]), ("Sym", "", ["a.pdf", "b.pdf"])],
        )

    def test_a_failed_rename_leaves_the_rest_pending_and_calling_again_finishes(self):
        groups = build_groups_from_drive(self.catalog, self.folder, PieceDetail(piece=Piece(title="Sym")))
        pending = list(generate_drive_rename_plan(groups, "{Instrument}.pdf", ["Flute", "Horn"]).entries)
        # 第一次呼叫在 a 就失敗；第二次改好 a、在 b 失敗
        self.google.fail_next(CatalogErrorKind.OFFLINE)
        self.google.fail_at(self.google.requests + 3, CatalogErrorKind.OFFLINE)
        with self.assertRaises(CatalogError):
            execute_drive_rename(self.catalog, pending)
        with self.assertRaises(CatalogError):
            execute_drive_rename(self.catalog, pending)
        self.assertEqual([e.original_name for e in pending], ["b.pdf"])
        self.assertEqual(self.names(), ["Flute.pdf", "b.pdf"])
        execute_drive_rename(self.catalog, pending)
        self.assertEqual((pending, self.names()), ([], ["Flute.pdf", "Horn.pdf"]))


class TestDriveRenameDialog(DriveRenameTestCase):
    """對話框接線（以假 Google 驅動）：預覽有撞名時停用執行，執行的是預覽的計畫，失敗時提示列出現"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self._locale = get_locale()
        set_locale("zh_TW")
        groups = build_groups_from_drive(self.catalog, self.folder, PieceDetail(piece=Piece(title="Sym")))
        self.dialog = DriveRenameDialog(lambda: self.catalog, groups)
        self.template = next(e for e in self.dialog.findChildren(QLineEdit) if e.text() == DEFAULT_MASTER_TEMPLATE)
        self.template.setText("{Instrument}.pdf")
        self.voices = next(
            e for e in self.dialog.findChildren(QTextEdit) if e.placeholderText() == t("catalog.rename.instruments_hint")
        )
        self.execute = next(
            b for b in self.dialog.findChildren(QPushButton) if b.text() == t("catalog.rename.execute")
        )

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        set_locale(self._locale)

    def bar(self) -> CatalogErrorBar:
        """對話框頂端的錯誤提示列"""
        return self.dialog.findChild(CatalogErrorBar)

    def test_execute_is_disabled_while_the_preview_has_conflicts_and_enabled_once_resolved(self):
        self.voices.setPlainText("Flute\nFLUTE")
        self.assertFalse(self.execute.isEnabled())
        self.voices.setPlainText("Flute\nHorn")
        self.assertTrue(self.execute.isEnabled())

    def _previewed_names(self):
        table = self.dialog.findChild(QTableWidget)
        return [table.item(row, 2).text() for row in range(table.rowCount())]

    def test_editing_the_piece_name_updates_the_preview(self):
        self.voices.setPlainText("Flute\nHorn")
        self.template.setText("{PieceName} {Instrument}.pdf")
        piece_name = next(e for e in self.dialog.findChildren(QLineEdit) if e.text() == "Sym")
        piece_name.setText("Sym 5")
        self.assertEqual(self._previewed_names(), ["Sym 5 Flute.pdf", "Sym 5 Horn.pdf"])

    def test_executing_renames_on_drive_what_the_preview_shows(self):
        self.voices.setPlainText("Flute\nHorn")
        with answering_prompts(QMessageBox.Yes) as shown:
            self.execute.click()
            wait_until(lambda: self.dialog.result() == QDialog.Accepted)
        self.assertEqual(self.names(), ["Flute.pdf", "Horn.pdf"])
        self.assertIn((t("catalog.rename.title"), t("catalog.rename.success", success=2, total=2)), shown)

    def test_a_failed_rename_shows_the_bar_and_retry_renames_the_rest(self):
        self.voices.setPlainText("Flute\nHorn")
        self.google.fail_at(self.google.requests + 2, CatalogErrorKind.NO_PERMISSION)
        with answering_prompts(QMessageBox.Yes):
            self.execute.click()
            wait_until(lambda: not self.bar().isHidden())
        self.assertTrue(self.bar().findChild(QLabel).text().startswith("寫不進譜庫｜沒有權限："))
        self.assertIn("b.pdf", self.bar().findChild(QTextEdit).toPlainText())
        self.assertEqual(self.names(), ["Flute.pdf", "b.pdf"])
        requests = self.google.requests
        with answering_prompts() as shown:
            next(b for b in self.bar().findChildren(QPushButton) if b.text() == "重新登入 Google").click()
            wait_until(lambda: self.dialog.result() == QDialog.Accepted)
        self.assertEqual(self.names(), ["Flute.pdf", "Horn.pdf"])
        self.assertEqual(self.google.requests, requests + 1)
        self.assertIn((t("catalog.rename.title"), t("catalog.rename.success", success=2, total=2)), shown)


class TestWithoutGoogleClient(unittest.TestCase):
    """計畫、撞名偵測與對話框不載入 Google 用戶端程式庫（只有譜庫的通事會）"""

    def test_plan_and_dialog_modules_load_no_google_library(self):
        code = (
            f"import sys; sys.path.insert(0, {SRC_DIR!r}); "
            "import services.drive_rename_service, ui.drive_rename_dialog; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('google', 'googleapiclient')))"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[]")


if __name__ == '__main__':
    unittest.main()
