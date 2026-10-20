# -*- coding: utf-8 -*-
"""
壓縮檢查測試

以合成 PDF 壓出正常的壓縮檔，再手工做壞（截斷、串流損壞、宣告灰階的三通道 JPEG、畫面不同），
確認 check_compressed 都判為沒通過，而正常的壓縮檔通過。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pymupdf
from core.constants import CompressionProblem
from services.pdf_service import check_compressed, save_downsampled, save_lossless
from tests.synthetic_pdfs import mislabeled_jpeg_pdf, scanned_pdf

PSNR_THRESHOLD = 30
RENDER_DPI = 150


class TestCompressionCheck(unittest.TestCase):
    """三層檢查：結構、影像通道、畫面"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.original = scanned_pdf(self.path("original.pdf"), dpi=600, pages=2)

    def path(self, name: str) -> str:
        """臨時目錄裡的檔案路徑"""
        return os.path.join(self.temp_dir, name)

    def check(self, compressed: str, original: str = None):
        """以測試的門檻與 dpi 檢查壓縮檔"""
        return check_compressed(original or self.original, compressed, PSNR_THRESHOLD, RENDER_DPI)

    def test_properly_downsampled_file_passes(self):
        compressed = self.path("compressed.pdf")
        save_downsampled(self.original, compressed, 200, 75)
        self.assertIsNone(self.check(compressed))

    def test_truncated_file_fails(self):
        compressed = self.path("compressed.pdf")
        save_downsampled(self.original, compressed, 300, 75)
        with open(compressed, "rb") as f:
            data = f.read()
        with open(compressed, "wb") as f:
            f.write(data[:len(data) * 2 // 3])
        defect = self.check(compressed)
        self.assertIsNotNone(defect)
        self.assertIn(defect.problem, (
            CompressionProblem.OUTPUT_UNREADABLE, CompressionProblem.REPAIRED, CompressionProblem.PAGE_COUNT,
        ))

    def test_file_with_a_damaged_image_stream_fails(self):
        compressed = self.path("compressed.pdf")
        save_lossless(self.original, compressed)
        with pymupdf.open(compressed) as doc:
            raw = doc.xref_stream_raw(doc[1].get_images(full=True)[0][0])
        with open(compressed, "rb") as f:
            data = f.read()
        start = data.index(raw) + len(raw) // 2
        damaged = data[:start] + bytes(len(raw) // 4) + data[start + len(raw) // 4:]
        with open(compressed, "wb") as f:
            f.write(damaged)
        defect = self.check(compressed)
        self.assertIsNotNone(defect)
        self.assertIn(defect.problem, (
            CompressionProblem.RENDER_ERROR, CompressionProblem.WARNINGS, CompressionProblem.REPAIRED,
        ))

    def test_jpeg_with_three_channels_declared_as_grayscale_fails(self):
        original = self.path("color.pdf")
        compressed = mislabeled_jpeg_pdf(self.path("compressed.pdf"), correct_path=original)
        defect = self.check(compressed, original)
        self.assertIsNotNone(defect)
        self.assertEqual(defect.problem, CompressionProblem.CHANNELS)

    def test_file_whose_pages_look_different_fails(self):
        other = scanned_pdf(self.path("other.pdf"), dpi=200, pages=2)
        with pymupdf.open(other) as doc:
            for page in doc:
                page.draw_rect(page.rect, fill=(0, 0, 0))
            compressed = self.path("compressed.pdf")
            doc.save(compressed)
        defect = self.check(compressed)
        self.assertIsNotNone(defect)
        self.assertEqual(defect.problem, CompressionProblem.PSNR)

    def test_file_with_a_different_page_count_fails(self):
        compressed = self.path("compressed.pdf")
        with pymupdf.open(self.original) as doc:
            doc.delete_page(1)
            doc.save(compressed)
        defect = self.check(compressed)
        self.assertEqual(defect.problem, CompressionProblem.PAGE_COUNT)


if __name__ == '__main__':
    unittest.main()
