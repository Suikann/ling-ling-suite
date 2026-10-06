# -*- coding: utf-8 -*-
"""
Drive 重新命名測試

計畫與撞名偵測經過命名模組，檔名與本機重新命名一致；不需要 Google 用戶端程式庫。
對話框以假的 Drive 服務驅動，以 QT_QPA_PLATFORM=offscreen 執行，不需要顯示器。
"""
import os
import subprocess
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, SRC_DIR)

from PySide6.QtWidgets import QApplication, QLineEdit, QMessageBox, QPushButton, QTableWidget, QTextEdit

from core.catalog_models import Piece, PieceDetail
from core.constants import DEFAULT_MASTER_TEMPLATE
from core.locale import get_locale, set_locale, t
from core.models import FileInfo, Group, Project
from services.drive_rename_service import build_groups_from_drive, generate_drive_rename_plan
from services.file_service import FileService
from services.rename_service import RenameService
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
        local_service = RenameService(FileService())
        for case, (group, template) in self.CASES.items():
            with self.subTest(case):
                local = local_service.generate_rename_plan(Project(master_template=template, groups=[group]))
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


class FakeDrive:
    """假的 Drive 服務：版本資料夾沒有子資料夾、只有這些 PDF；記下重新命名的呼叫"""

    def __init__(self, *pdf_names):
        self._pdfs = [{"id": f"id-{name}", "name": name} for name in pdf_names]
        self.renamed = []

    def list_subfolders(self, folder_id):
        return []

    def list_pdfs_in_folder(self, folder_id):
        return list(self._pdfs)

    def rename_file(self, file_id, new_name):
        self.renamed.append((file_id, new_name))
        return True


class TestDriveRenameDialog(unittest.TestCase):
    """對話框接線（以假的 Drive 服務驅動）：預覽有撞名時停用執行，執行的是預覽的計畫"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self._locale = get_locale()
        set_locale("zh_TW")
        self.drive = FakeDrive("a.pdf", "b.pdf")
        groups = build_groups_from_drive(self.drive, "folder", PieceDetail(piece=Piece(title="Sym")))
        self.dialog = DriveRenameDialog(self.drive, groups)
        self.template = next(e for e in self.dialog.findChildren(QLineEdit) if e.text() == DEFAULT_MASTER_TEMPLATE)
        self.template.setText("{Instrument}.pdf")
        self.voices = self.dialog.findChild(QTextEdit)
        self.execute = next(
            b for b in self.dialog.findChildren(QPushButton) if b.text() == t("catalog.rename.execute")
        )

    def tearDown(self):
        self.dialog.close()
        set_locale(self._locale)

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
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
                mock.patch.object(QMessageBox, "information", return_value=QMessageBox.Ok):
            self.execute.click()
        self.assertEqual(self.drive.renamed, [("id-a.pdf", "Flute.pdf"), ("id-b.pdf", "Horn.pdf")])


class TestWithoutGoogleClient(unittest.TestCase):
    """計畫、撞名偵測與對話框不載入 Google 用戶端程式庫（只有 Drive 的 adapter 會）"""

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
