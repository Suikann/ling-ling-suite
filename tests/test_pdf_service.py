# -*- coding: utf-8 -*-
"""
PDF 工具服務單元測試
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PyPDF2 import PdfWriter
from services.pdf_service import extract_pages, get_page_count


class TestExtractPages(unittest.TestCase):
    """extract_pages 測試"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.source = os.path.join(self.temp_dir, "source.pdf")
        writer = PdfWriter()
        for _ in range(3):
            writer.add_blank_page(width=100, height=100)
        with open(self.source, "wb") as f:
            writer.write(f)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_extracts_selected_pages(self):
        out = os.path.join(self.temp_dir, "part.pdf")
        extract_pages(self.source, [0, 2], out)
        self.assertEqual(get_page_count(out), 2)
        self.assertEqual(get_page_count(self.source), 3)

    def test_refuses_to_overwrite_source(self):
        with self.assertRaises(ValueError):
            extract_pages(self.source, [0], self.source)
        self.assertEqual(get_page_count(self.source), 3)

    def test_refuses_source_via_different_spelling(self):
        alias = os.path.join(self.temp_dir, ".", "source.pdf")
        with self.assertRaises(ValueError):
            extract_pages(self.source, [0], alias)
        self.assertEqual(get_page_count(self.source), 3)


if __name__ == '__main__':
    unittest.main()
