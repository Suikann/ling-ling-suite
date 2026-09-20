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
from core.models import SplitEntry
from services.pdf_service import build_split_plan, extract_pages, get_page_count


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


class TestBuildSplitPlan(unittest.TestCase):
    """build_split_plan：區段對應頁面與輸出路徑，對話框只提供 UI 狀態"""

    OUT = os.path.join("out", "dir")

    def test_names_each_section_and_appends_pdf(self):
        plan = build_split_plan([(0, 1), (2, 2)], ["Flute", "Oboe"], set(), self.OUT)
        self.assertEqual(plan, [
            SplitEntry([0, 1], "Flute", os.path.join(self.OUT, "Flute.pdf")),
            SplitEntry([2], "Oboe", os.path.join(self.OUT, "Oboe.pdf")),
        ])

    def test_keeps_existing_pdf_extension_case_insensitively(self):
        plan = build_split_plan([(0, 0)], ["Flute.PDF"], set(), self.OUT)
        self.assertEqual(plan[0].output_path, os.path.join(self.OUT, "Flute.PDF"))

    def test_excludes_deleted_pages(self):
        plan = build_split_plan([(0, 3)], ["Flute"], {1, 2}, self.OUT)
        self.assertEqual(plan[0].pages, [0, 3])

    def test_skips_section_with_no_pages_left(self):
        plan = build_split_plan([(0, 0), (1, 2)], ["Flute", "Oboe"], {0}, self.OUT)
        self.assertEqual([entry.display_name for entry in plan], ["Oboe"])

    def test_empty_name_falls_back_to_part_for_the_file_only(self):
        plan = build_split_plan([(0, 0)], ["   "], set(), self.OUT)
        self.assertEqual(plan, [SplitEntry([0], "", os.path.join(self.OUT, "Part.pdf"))])

    def test_sanitizes_file_name_but_keeps_display_name(self):
        plan = build_split_plan([(0, 0)], [" Violin I/II "], set(), self.OUT)
        self.assertEqual(plan, [SplitEntry([0], "Violin I/II", os.path.join(self.OUT, "Violin I_II.pdf"))])


if __name__ == '__main__':
    unittest.main()
