# -*- coding: utf-8 -*-
"""
檔名清理單元測試
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.filename import ensure_pdf_extension, sanitize_filename


class TestSanitizeFilename(unittest.TestCase):
    """sanitize_filename：非法字元換底線、去頭尾空白、空名回退"""

    def test_replaces_each_illegal_char_with_underscore(self):
        self.assertEqual(
            sanitize_filename('a<b>c:d"e/f\\g|h?i*j'),
            "a_b_c_d_e_f_g_h_i_j",
        )

    def test_strips_surrounding_whitespace(self):
        self.assertEqual(sanitize_filename("  Flute 1  "), "Flute 1")

    def test_empty_name_returns_empty_without_fallback(self):
        self.assertEqual(sanitize_filename("   "), "")

    def test_empty_name_returns_fallback(self):
        self.assertEqual(sanitize_filename("   ", fallback="Part"), "Part")

    def test_name_made_of_illegal_chars_only_keeps_underscores(self):
        self.assertEqual(sanitize_filename("?*", fallback="Part"), "__")


class TestEnsurePdfExtension(unittest.TestCase):
    """ensure_pdf_extension：沒有 .pdf 就補上，已有者不分大小寫保留"""

    def test_appends_pdf(self):
        self.assertEqual(ensure_pdf_extension("Flute"), "Flute.pdf")

    def test_keeps_existing_extension_case_insensitively(self):
        self.assertEqual(ensure_pdf_extension("Flute.PDF"), "Flute.PDF")
        self.assertEqual(ensure_pdf_extension("Flute.pdf"), "Flute.pdf")


if __name__ == '__main__':
    unittest.main()
