# -*- coding: utf-8 -*-
"""
壓縮輸出位置的規則測試（臨時目錄）

預設位置是原資料夾旁的「<原資料夾名>（壓縮）」、檔名不變；原檔與原資料夾內的位置都擋下；
不到上限的檔原樣複製，壓縮過的交出暫存的壓縮檔。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.constants import CompressionStatus
from services.compression_output_service import CompressionOutputService
from services.compression_service import CompressionService
from services.file_service import FileService
from tests.path_spellings import spellings
from tests.synthetic_pdfs import file_digest, scanned_pdf

KB = 1000


class TestCompressionOutput(unittest.TestCase):
    """單一檔的另存位置與交出"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.parts = os.path.join(self.temp_dir, "Symphony")
        os.makedirs(self.parts)
        self.source = scanned_pdf(os.path.join(self.parts, "Flute.pdf"), dpi=600)
        self.output = CompressionOutputService(FileService())

    def compressor(self, size_limit: int) -> CompressionService:
        return CompressionService(FileService(), os.path.join(self.temp_dir, "scratch"), size_limit=size_limit)

    def test_default_target_is_beside_the_original_folder_with_the_same_file_name(self):
        self.assertEqual(
            self.output.default_target(self.source),
            os.path.join(self.temp_dir, "Symphony（壓縮）", "Flute.pdf"),
        )

    def test_original_file_and_anywhere_inside_its_folder_are_blocked(self):
        for target in (
            self.source,
            os.path.join(self.parts, "Flute small.pdf"),
            os.path.join(self.parts, "sub", "Flute.pdf"),
            *spellings(self.source).values(),
        ):
            with self.subTest(target=target):
                self.assertTrue(self.output.is_blocked(self.source, target))

    def test_default_target_and_a_similarly_named_sibling_folder_are_allowed(self):
        for target in (
            self.output.default_target(self.source),
            os.path.join(self.temp_dir, "Symphony2", "Flute.pdf"),
            os.path.join(self.temp_dir, "Flute.pdf"),
        ):
            with self.subTest(target=target):
                self.assertFalse(self.output.is_blocked(self.source, target))

    def test_file_under_the_limit_is_copied_as_is(self):
        result = self.compressor(size_limit=os.path.getsize(self.source)).compress(self.source)
        self.assertEqual(result.status, CompressionStatus.SMALL_ENOUGH)
        target = self.output.default_target(self.source)
        self.output.deliver(result, target)
        self.assertEqual(file_digest(target), file_digest(self.source))

    def test_compressed_file_is_delivered_from_the_temp_folder(self):
        compressor = self.compressor(size_limit=500 * KB)
        result = compressor.compress(self.source)
        self.assertEqual(result.status, CompressionStatus.COMPRESSED)
        target = os.path.join(self.temp_dir, "elsewhere", "Flute.pdf")
        self.output.deliver(result, target)
        self.assertEqual(file_digest(target), file_digest(result.output_path))
        compressor.discard(result)
        self.assertFalse(os.path.exists(result.output_path))
        self.assertTrue(os.path.isfile(target))

    def test_delivering_to_a_blocked_target_is_refused(self):
        result = self.compressor(size_limit=500 * KB).compress(self.source)
        digest = file_digest(self.source)
        with self.assertRaises(ValueError):
            self.output.deliver(result, self.source)
        self.assertEqual(file_digest(self.source), digest)


if __name__ == '__main__':
    unittest.main()
