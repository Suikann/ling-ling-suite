# -*- coding: utf-8 -*-
"""
模板引擎單元測試
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.template_engine import detect_piece_name


class TestDetectPieceName(unittest.TestCase):
    """detect_piece_name 測試"""

    def test_common_prefix(self):
        filenames = [
            "Beethoven Sym5 - Flute.pdf",
            "Beethoven Sym5 - Oboe.pdf",
            "Beethoven Sym5 - Clarinet.pdf",
        ]
        result = detect_piece_name(filenames)
        self.assertEqual(result, "Beethoven Sym5")

    def test_single_file(self):
        result = detect_piece_name(["Beethoven Sym5.pdf"])
        self.assertEqual(result, "Beethoven Sym5")

    def test_empty_list(self):
        result = detect_piece_name([])
        self.assertEqual(result, "")

    def test_no_common_prefix(self):
        filenames = ["Alpha.pdf", "Beta.pdf", "Gamma.pdf"]
        result = detect_piece_name(filenames)
        self.assertEqual(result, "")

    def test_strip_trailing_separators(self):
        filenames = ["Song_01.pdf", "Song_02.pdf"]
        result = detect_piece_name(filenames)
        self.assertEqual(result, "Song")

    def test_common_tokens_fallback(self):
        """前綴法失敗時，共同詞彙法應能偵測"""
        filenames = [
            "Flute - Beethoven Sym5.pdf",
            "Oboe - Beethoven Sym5.pdf",
            "Clarinet - Beethoven Sym5.pdf",
        ]
        result = detect_piece_name(filenames)
        self.assertEqual(result, "Beethoven Sym5")

    def test_common_tokens_no_match(self):
        """所有詞彙皆不同時回傳空字串"""
        filenames = ["Flute.pdf", "Oboe.pdf", "Clarinet.pdf"]
        result = detect_piece_name(filenames)
        self.assertEqual(result, "")

    def test_common_tokens_ignores_pure_numbers(self):
        """共同詞彙法應忽略純數字詞彙"""
        filenames = ["01 Flute.pdf", "01 Oboe.pdf"]
        result = detect_piece_name(filenames)
        self.assertEqual(result, "")

    def test_common_tokens_mixed(self):
        """前綴法失敗，共同詞彙法偵測多個共同詞"""
        filenames = [
            "Fl - Mozart PC21 Mvt1.pdf",
            "Ob - Mozart PC21 Mvt1.pdf",
        ]
        result = detect_piece_name(filenames)
        self.assertEqual(result, "Mozart PC21 Mvt1")


if __name__ == '__main__':
    unittest.main()
