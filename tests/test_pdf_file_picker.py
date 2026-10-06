# -*- coding: utf-8 -*-
"""
分割與旋轉對話框的選檔清單測試（offscreen）

兩個對話框從專案的同一個檔案走訪取得清單：群組列總譜再列分譜，未分組只列未分組檔案，只列 PDF。
"""
import os
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication

from core.models import FileInfo, Group, Project
from services.file_service import FileService
from services.split_service import SplitService
from services.workspace_service import WorkspaceService
from ui.rotate_dialog import RotatePdfDialog
from ui.split_dialog import SplitPdfDialog


def _info(name):
    return FileInfo(os.path.join(os.getcwd(), name), name)


class TestPdfFilePicker(unittest.TestCase):
    """選檔清單的內容與順序"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.group = Group(
            name="g",
            files=[_info("Flute.pdf"), _info("notes.txt"), _info("Oboe.PDF")],
            score_file=_info("Score.pdf"),
        )
        self.other = Group(name="h", files=[_info("Horn.pdf")])
        self.project = Project(
            groups=[self.group, self.other],
            ungrouped_files=[_info("Loose.pdf"), _info("readme.txt")],
        )

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _dialogs(self, initial_group):
        files = FileService()
        splitter = SplitService(files, WorkspaceService(files, os.path.join(self.temp_dir, "workspace")))
        yield "分割", SplitPdfDialog(self.project, None, initial_group, splitter=splitter)
        yield "旋轉", RotatePdfDialog(self.project, None, initial_group)

    @staticmethod
    def _choices(dialog):
        """選檔清單中的檔案（略過最前面的「請選擇」）：顯示名稱、路徑、所屬群組"""
        combo = dialog._file_combo
        return [
            (combo.itemText(i), *combo.itemData(i))
            for i in range(combo.count()) if combo.itemData(i)
        ]

    def test_group_lists_score_then_pdf_parts(self):
        for label, dialog in self._dialogs(self.group):
            with self.subTest(label):
                self.assertEqual(self._choices(dialog), [
                    (f.display_name, f.original_path, self.group)
                    for f in (self.group.score_file, self.group.files[0], self.group.files[2])
                ])
                dialog.close()

    def test_ungrouped_lists_only_ungrouped_pdfs(self):
        for label, dialog in self._dialogs(None):
            with self.subTest(label):
                loose = self.project.ungrouped_files[0]
                self.assertEqual(self._choices(dialog), [(loose.display_name, loose.original_path, None)])
                dialog.close()


if __name__ == '__main__':
    unittest.main()
