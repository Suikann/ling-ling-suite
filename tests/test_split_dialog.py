# -*- coding: utf-8 -*-
"""
分割對話框測試（offscreen）

測訊息組裝與對話框到服務層的接線，不測畫面；以 QT_QPA_PLATFORM=offscreen 執行，不需要顯示器。
"""
import os
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication

from core.locale import get_locale, set_locale
from core.models import Project, SplitEntry, WorkspaceOwner
from services.file_service import FileService
from services.workspace_service import WorkspaceService
from ui.split_dialog import SplitPdfDialog


class TestResplitMessage(unittest.TestCase):
    """重新分割確認訊息：同專案不點名；他專案點名，找不到時加註"""

    def setUp(self):
        self._locale = get_locale()
        set_locale("zh_TW")

    def tearDown(self):
        set_locale(self._locale)

    def test_same_project_does_not_name_an_owner(self):
        text = SplitPdfDialog._resplit_message(3, None)
        self.assertIn("3 個分譜", text)
        self.assertNotIn("屬於專案", text)

    def test_other_project_is_named(self):
        text = SplitPdfDialog._resplit_message(3, WorkspaceOwner("/proj/冬季.llproj", exists=True))
        self.assertIn("屬於專案「/proj/冬季.llproj」", text)
        self.assertNotIn("找不到", text)

    def test_missing_other_project_is_flagged(self):
        text = SplitPdfDialog._resplit_message(3, WorkspaceOwner("/proj/冬季.llproj", exists=False))
        self.assertIn("屬於專案「/proj/冬季.llproj」（找不到）", text)


class TestSplitPlanWiring(unittest.TestCase):
    """分割計畫由服務組出，對話框只交出分割點、名稱欄位與已刪頁面"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        workspace = WorkspaceService(FileService(), os.path.join(self.temp_dir, "workspace"))
        self.dialog = SplitPdfDialog(Project(instruments=["Flute", "Oboe"]), workspace_service=workspace)

    def tearDown(self):
        self.dialog.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_plan_reflects_split_points_name_entries_and_deleted_pages(self):
        self.dialog._page_count = 4
        self.dialog._split_starts = {0, 2}
        self.dialog._deleted_pages = {3}
        self.dialog._update_assignments()
        self.dialog._section_name_entries[1].setText("Oboe: d'amore")
        plan = self.dialog._build_split_plan(self.temp_dir)
        self.assertEqual(plan, [
            SplitEntry([0, 1], "Flute", os.path.join(self.temp_dir, "Flute.pdf")),
            SplitEntry([2], "Oboe: d'amore", os.path.join(self.temp_dir, "Oboe_ d'amore.pdf")),
        ])


if __name__ == '__main__':
    unittest.main()
