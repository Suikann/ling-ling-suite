# -*- coding: utf-8 -*-
"""
旋轉對話框測試（offscreen）

測對話框與存檔對話框、PDF 旋轉的接線，不測畫面；以 QT_QPA_PLATFORM=offscreen 執行，不需要顯示器。
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PyPDF2 import PdfReader
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QFileDialog, QMessageBox, QPushButton

from core.locale import t
from core.models import FileInfo, Project
from ui.rotate_dialog import RotatePdfDialog
from tests.test_main_window import button_in
from tests.test_split_dialog import _make_pdf, _wait_until


class TestRotateSaveAs(unittest.TestCase):
    """旋轉另存：沒打副檔名就補上 .pdf"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.source = _make_pdf(os.path.join(self.temp_dir, "score.pdf"), 1)
        self.dialog = RotatePdfDialog(Project(ungrouped_files=[FileInfo(self.source, "score.pdf")]))
        self.addCleanup(self.dialog.close)

    def test_saved_copy_without_extension_gets_it(self):
        combo = next(c for c in self.dialog.findChildren(QComboBox) if c.findText("score.pdf") >= 0)
        combo.setCurrentIndex(combo.findText("score.pdf"))
        _wait_until(lambda: button_in(self.dialog, QPushButton, t("rotate.execute")).isEnabled())
        button_in(self.dialog, QCheckBox, t("rotate.overwrite")).setChecked(False)
        typed = os.path.join(self.temp_dir, "rotated")
        with mock.patch.object(QFileDialog, "getSaveFileName", return_value=(typed, "")), \
                mock.patch.object(QMessageBox, "information"):
            button_in(self.dialog, QPushButton, t("rotate.execute")).click()
        self.assertFalse(os.path.exists(typed))
        self.assertEqual(len(PdfReader(typed + ".pdf").pages), 1)


if __name__ == "__main__":
    unittest.main()
