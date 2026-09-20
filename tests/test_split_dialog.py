# -*- coding: utf-8 -*-
"""
分割對話框測試

只測訊息組裝，不建立對話框；匯入 ui.split_dialog 需要 PySide6，但不需要 QApplication。
"""
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.locale import get_locale, set_locale
from core.models import WorkspaceOwner
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


if __name__ == '__main__':
    unittest.main()
