# -*- coding: utf-8 -*-
"""
壓縮服務測試

不接替身：以 PyMuPDF 產生合成 PDF（見 tests/synthetic_pdfs.py），真的壓縮、檢查。
合成檔只有幾百 KB，以建構時注入的大小上限代替 20MB，讓各級的去留落在想測的地方。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.constants import CompressionLevel, CompressionProblem, CompressionStatus
from services.compression_service import CompressionProgress, CompressionService
from services.file_service import FileService
from tests.synthetic_pdfs import (
    bilevel_pdf, encrypted_pdf, file_digest, image_dpis, image_pixels, mislabeled_jpeg_pdf, scanned_pdf,
    uncompressed_pdf,
)

KB = 1000


class CompressionTestCase(unittest.TestCase):
    """壓縮服務測試的共用骨架：來源與暫存資料夾各在臨時目錄下，每個測試結束時確認原檔沒被改動"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.source_dir = os.path.join(self.temp_dir, "parts")
        os.makedirs(self.source_dir)
        self.scratch_dir = os.path.join(self.temp_dir, "scratch")

    def service(self, size_limit: int) -> CompressionService:
        """以 size_limit 為大小上限的壓縮服務"""
        return CompressionService(FileService(), self.scratch_dir, size_limit=size_limit)

    def source(self, make, name: str = "Flute.pdf", **kwargs) -> str:
        """以 make 在來源資料夾產生合成 PDF，並在測試結束時確認它的內容不變"""
        path = make(os.path.join(self.source_dir, name), **kwargs)
        digest = file_digest(path)
        self.addCleanup(lambda: self.assertEqual(file_digest(path), digest, "原檔被改動"))
        return path


class TestCompressionLevels(CompressionTestCase):
    """依序試無損、300 dpi、200 dpi，到上限以下就停"""

    def test_stops_at_lossless_when_that_is_enough(self):
        source = self.source(uncompressed_pdf, dpi=600)
        result = self.service(size_limit=500 * KB).compress(source)
        self.assertEqual(result.status, CompressionStatus.COMPRESSED)
        self.assertEqual(result.level, CompressionLevel.LOSSLESS)
        self.assertEqual(result.original_size, os.path.getsize(source))
        self.assertEqual(result.compressed_size, os.path.getsize(result.output_path))
        self.assertLessEqual(result.compressed_size, 500 * KB)
        self.assertEqual(image_dpis(result.output_path), [600])

    def test_scan_too_large_after_lossless_is_downsampled_to_exactly_300_dpi(self):
        source = self.source(scanned_pdf, dpi=600, pages=2)
        result = self.service(size_limit=500 * KB).compress(source)
        self.assertEqual(result.status, CompressionStatus.COMPRESSED)
        self.assertEqual(result.level, CompressionLevel.DPI_300)
        self.assertEqual(result.compressed_size, os.path.getsize(result.output_path))
        self.assertLessEqual(result.compressed_size, 500 * KB)
        self.assertEqual(image_dpis(result.output_path), [300, 300])

    def test_scan_still_too_large_at_300_dpi_is_downsampled_to_exactly_200_dpi(self):
        source = self.source(scanned_pdf, dpi=600, pages=2)
        result = self.service(size_limit=50 * KB).compress(source)
        self.assertEqual(result.status, CompressionStatus.COMPRESSED)
        self.assertEqual(result.level, CompressionLevel.DPI_200)
        self.assertLessEqual(result.compressed_size, 50 * KB)
        self.assertEqual(image_dpis(result.output_path), [200, 200])

    def test_jpeg_scan_at_400_dpi_lands_on_exact_dpi_of_each_level(self):
        for size_limit, level, dpi in ((100 * KB, CompressionLevel.DPI_300, 300), (40 * KB, CompressionLevel.DPI_200, 200)):
            with self.subTest(level=level):
                source = self.source(scanned_pdf, name=f"{dpi}.pdf", dpi=400, jpeg=True)
                result = self.service(size_limit=size_limit).compress(source)
                self.assertEqual(result.status, CompressionStatus.COMPRESSED)
                self.assertEqual(result.level, level)
                self.assertEqual(image_dpis(result.output_path), [dpi])


    def test_bilevel_image_is_not_downsampled_and_is_reported_still_too_large(self):
        source = self.source(bilevel_pdf, dpi=600)
        result = self.service(size_limit=1 * KB).compress(source)
        self.assertEqual(result.status, CompressionStatus.STILL_TOO_LARGE)
        self.assertEqual(result.level, CompressionLevel.DPI_200)
        self.assertGreater(result.compressed_size, 1 * KB)
        self.assertEqual(image_pixels(result.output_path), [(1200, 1200, 1)])


    def test_file_already_under_the_limit_is_left_as_is(self):
        source = self.source(scanned_pdf, dpi=300)
        result = self.service(size_limit=os.path.getsize(source)).compress(source)
        self.assertEqual(result.status, CompressionStatus.SMALL_ENOUGH)
        self.assertIsNone(result.output_path)
        self.assertEqual(result.original_size, os.path.getsize(source))
        self.assertEqual(scratch_files(self.scratch_dir), [])


class TestUncompressible(CompressionTestCase):
    """原檔打不開：無法壓縮，寫出原因"""

    def test_password_protected_file_cannot_be_compressed(self):
        source = self.source(encrypted_pdf, dpi=300)
        result = self.service(size_limit=1 * KB).compress(source)
        self.assertEqual(result.status, CompressionStatus.UNCOMPRESSIBLE)
        self.assertEqual(result.problem, CompressionProblem.ENCRYPTED)
        self.assertIsNone(result.output_path)
        self.assertEqual(scratch_files(self.scratch_dir), [])

    def test_file_that_is_not_a_pdf_cannot_be_compressed_and_says_why(self):
        def not_a_pdf(path):
            """寫出一個不是 PDF 的檔案"""
            with open(path, "wb") as f:
                f.write(b"not a pdf" * 1000)
            return path
        source = self.source(not_a_pdf)
        result = self.service(size_limit=1 * KB).compress(source)
        self.assertEqual(result.status, CompressionStatus.UNCOMPRESSIBLE)
        self.assertEqual(result.problem, CompressionProblem.UNREADABLE)
        self.assertTrue(result.detail)
        self.assertEqual(scratch_files(self.scratch_dir), [])


class TestProgressAndCancel(CompressionTestCase):
    """進度逐頁回報；取消時在頁與頁之間停下，什麼都不留"""

    def test_progress_reports_each_page_of_each_level_tried_and_of_the_check(self):
        source = self.source(scanned_pdf, dpi=600, pages=2)
        events = []
        self.service(size_limit=500 * KB).compress(source, on_progress=events.append)
        lossless, dpi_300 = CompressionLevel.LOSSLESS, CompressionLevel.DPI_300
        self.assertEqual(events, [
            CompressionProgress(lossless, False, 0, 2),
            CompressionProgress(dpi_300, False, 0, 2),
            CompressionProgress(dpi_300, False, 1, 2),
            CompressionProgress(dpi_300, False, 2, 2),
            CompressionProgress(dpi_300, True, 0, 2),
            CompressionProgress(dpi_300, True, 1, 2),
            CompressionProgress(dpi_300, True, 2, 2),
        ])

    def test_cancelling_while_downsampling_leaves_no_output(self):
        source = self.source(scanned_pdf, dpi=600, pages=3)
        events = []
        first_page_done = CompressionProgress(CompressionLevel.DPI_300, False, 1, 3)
        result = self.service(size_limit=50 * KB).compress(
            source, should_cancel=lambda: first_page_done in events, on_progress=events.append,
        )
        self.assertEqual(result.status, CompressionStatus.CANCELLED)
        self.assertIsNone(result.output_path)
        self.assertEqual(scratch_files(self.scratch_dir), [])
        self.assertEqual(events[-1], first_page_done)

    def test_cancelling_while_checking_leaves_no_output(self):
        source = self.source(scanned_pdf, dpi=600, pages=2)
        events = []
        result = self.service(size_limit=500 * KB).compress(
            source, should_cancel=lambda: any(e.checking for e in events), on_progress=events.append,
        )
        self.assertEqual(result.status, CompressionStatus.CANCELLED)
        self.assertIsNone(result.output_path)
        self.assertEqual(scratch_files(self.scratch_dir), [])


class TestFailedCheck(CompressionTestCase):
    """壓縮檔沒通過檢查：壓縮失敗，丟掉壓縮檔，不改試下一級"""

    def test_output_that_fails_the_check_is_discarded_without_trying_the_next_level(self):
        source = self.source(mislabeled_jpeg_pdf)
        result = self.service(size_limit=os.path.getsize(source) - 1).compress(source)
        self.assertEqual(result.status, CompressionStatus.FAILED)
        self.assertEqual(result.level, CompressionLevel.LOSSLESS)
        self.assertEqual(result.problem, CompressionProblem.CHANNELS)
        self.assertIsNone(result.output_path)
        self.assertEqual(scratch_files(self.scratch_dir), [])


class TestTempFolder(CompressionTestCase):
    """暫存資料夾的殘檔"""

    def test_clearing_leftovers_removes_everything_in_the_temp_folder(self):
        leftover = os.path.join(self.scratch_dir, "0123abcd", "Flute.pdf")
        os.makedirs(os.path.dirname(leftover))
        with open(leftover, "wb") as f:
            f.write(b"half written")
        self.service(size_limit=1 * KB).clear_leftovers()
        self.assertEqual(scratch_files(self.scratch_dir), [])

    def test_clearing_leftovers_without_a_temp_folder_does_nothing(self):
        self.service(size_limit=1 * KB).clear_leftovers()
        self.assertFalse(os.path.exists(self.scratch_dir))


def scratch_files(folder: str) -> list:
    """暫存資料夾裡留下的檔案（資料夾不存在時為空）"""
    return [os.path.join(root, name) for root, _, names in os.walk(folder) for name in names]


if __name__ == '__main__':
    unittest.main()
