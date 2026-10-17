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
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication, QDialog, QLabel, QListWidget, QMessageBox, QPushButton

from core.constants import CompressionProblem, CompressionStatus
from core.locale import t
from services.compression_service import CompressionResult, CompressionService
from services.file_service import FileService
from tests.synthetic_pdfs import scanned_pdf
from tests.test_main_window import button_in, wait_until
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

    def test_cancelling_returns_rejected(self):
        dialog = self.open_dialog([self.flute])
        self.button(dialog, "compare.cancel").click()
        self.assertEqual(dialog.result(), QDialog.Rejected)


class TestCompareOpen(CompareTestCase):
    """開啟：以預設的 PDF 閱讀器開啟目前這份的壓縮檔"""

    def test_open_shows_the_selected_compressed_file_in_the_default_reader(self):
        dialog = self.open_dialog([self.flute, self.violin])
        self.select(dialog, 1)
        self.button(dialog, "compare.open").click()
        self.assertEqual(self.opener.opened, [self.violin.output_path])

    def test_reader_that_does_not_start_is_reported(self):
        self.opener.opens = False
        dialog = self.open_dialog([self.flute])
        with mock.patch.object(QMessageBox, "warning") as warning:
            self.button(dialog, "compare.open").click()
        self.assertEqual(warning.call_args.args[2], t("compare.open_failed"))


if __name__ == "__main__":
    unittest.main()


class TestComparePages(CompareTestCase):
    """翻頁：載入後顯示第幾頁、共幾頁，換一份從第一頁開始"""

    def shows(self, dialog: QDialog, text: str) -> bool:
        return any(label.text() == text for label in dialog.findChildren(QLabel))

    def test_pages_turn_within_the_file_and_restart_for_another_file(self):
        score = self.compressed("Score.pdf", pages=3)
        dialog = self.open_dialog([score, self.flute])
        wait_until(lambda: self.shows(dialog, t("compare.page", page=1, count=3)))
        self.assertFalse(self.button(dialog, "compare.prev").isEnabled())
        self.button(dialog, "compare.next").click()
        wait_until(lambda: self.shows(dialog, t("compare.page", page=2, count=3)))
        self.button(dialog, "compare.next").click()
        wait_until(lambda: self.shows(dialog, t("compare.page", page=3, count=3)))
        self.assertFalse(self.button(dialog, "compare.next").isEnabled())
        self.select(dialog, 1)
        wait_until(lambda: self.shows(dialog, t("compare.page", page=1, count=1)))
