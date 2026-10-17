# -*- coding: utf-8 -*-
"""
壓縮比較畫面測試（offscreen）

比較畫面直接由壓縮結果開啟（不經工具選單的獨立壓縮），測逐份的決定、開啟與翻頁的接線，不測畫面。
"""
import os
import shutil
import sys
import tempfile
import unittest
from typing import List

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication, QDialog, QListWidget, QPushButton

from core.constants import CompressionProblem, CompressionStatus
from core.locale import t
from services.compression_service import CompressionResult, CompressionService
from services.file_service import FileService
from tests.synthetic_pdfs import scanned_pdf
from tests.test_main_window import button_in
from ui.compression_compare import CompressionCompareDialog

KB = 1000


class RecordingOpener:
    """外部開啟的替身：記下要開的目標，回傳 opens 指定的結果"""

    def __init__(self, opens: bool = True):
        self.opens = opens
        self.opened: List[str] = []

    def open(self, target: str) -> bool:
        self.opened.append(target)
        return self.opens


class CompareTestCase(unittest.TestCase):
    """以真的壓縮服務壓出 Flute、Violin 兩份壓縮檔，另有原樣複製的 Oboe 與壓縮失敗的 Horn"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.compressor = CompressionService(FileService(), os.path.join(self.temp_dir, "scratch"), size_limit=100 * KB)
        self.flute = self.compressed("Flute.pdf")
        self.violin = self.compressed("Violin.pdf")
        self.oboe = CompressionResult(os.path.join(self.temp_dir, "Oboe.pdf"), CompressionStatus.SMALL_ENOUGH, 10 * KB)
        self.horn = CompressionResult(
            os.path.join(self.temp_dir, "Horn.pdf"), CompressionStatus.UNCOMPRESSIBLE, 300 * KB,
            problem=CompressionProblem.ENCRYPTED,
        )
        self.opener = RecordingOpener()

    def compressed(self, name: str, pages: int = 1) -> CompressionResult:
        source = scanned_pdf(os.path.join(self.temp_dir, name), dpi=300, pages=pages)
        result = self.compressor.compress(source)
        self.assertEqual(result.status, CompressionStatus.COMPRESSED)
        return result

    def open_dialog(self, results: List[CompressionResult]) -> CompressionCompareDialog:
        entries = [(os.path.basename(result.source), result) for result in results]
        dialog = CompressionCompareDialog(entries, self.opener)
        self.addCleanup(dialog.close)
        return dialog

    @staticmethod
    def select(dialog: QDialog, row: int):
        dialog.findChild(QListWidget).setCurrentRow(row)

    @staticmethod
    def button(dialog: QDialog, key: str) -> QPushButton:
        return button_in(dialog, QPushButton, t(key))


class TestCompareDecisions(CompareTestCase):
    """逐份決定：全部壓縮檔都決定後才能輸出，交回留下的與原樣複製的"""

    def test_output_waits_for_every_compressed_file_and_returns_kept_and_copied_ones(self):
        dialog = self.open_dialog([self.flute, self.violin, self.oboe, self.horn])
        output = self.button(dialog, "compare.output")
        self.assertFalse(output.isEnabled())
        self.select(dialog, 0)
        self.button(dialog, "compare.keep").click()
        self.assertFalse(output.isEnabled())
        self.select(dialog, 1)
        self.button(dialog, "compare.drop").click()
        self.assertTrue(output.isEnabled())
        output.click()
        self.assertEqual(dialog.result(), QDialog.Accepted)
        self.assertEqual(dialog.kept(), [self.flute, self.oboe])


if __name__ == "__main__":
    unittest.main()
