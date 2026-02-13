# -*- coding: utf-8 -*-
"""
模板引擎單元測試
"""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.template_engine import (
    substitute_template,
    build_variables_for_file,
    detect_piece_name,
    validate_template,
)
from core.models import Group


class TestSubstituteTemplate(unittest.TestCase):
    """substitute_template 測試"""

    def test_basic_substitution(self):
        result = substitute_template(
            "{序號}. {樂器} - {曲名}.pdf",
            {"序號": "01", "樂器": "Flute", "曲名": "Beethoven Sym.5"},
        )
        self.assertEqual(result, "01. Flute - Beethoven Sym.5.pdf")

    def test_empty_variables(self):
        result = substitute_template("{序號}. {樂器}.pdf", {})
        self.assertEqual(result, "{序號}. {樂器}.pdf")

    def test_partial_substitution(self):
        result = substitute_template(
            "{序號}. {樂器} - {曲名}.pdf",
            {"序號": "01", "樂器": "Oboe"},
        )
        self.assertEqual(result, "01. Oboe - {曲名}.pdf")

    def test_no_variables_in_template(self):
        result = substitute_template("plain.pdf", {"序號": "01"})
        self.assertEqual(result, "plain.pdf")

    def test_repeated_variable(self):
        result = substitute_template("{曲名} - {曲名}.pdf", {"曲名": "Test"})
        self.assertEqual(result, "Test - Test.pdf")


class TestBuildVariablesForFile(unittest.TestCase):
    """build_variables_for_file 測試"""

    def test_basic_build(self):
        group = Group(
            selected_instruments=[0, 1, 2],
            piece_name="Beethoven Sym.5",
            movement_number="1",
            movement_name="Allegro",
        )
        instruments = ["Flute", "Oboe", "Clarinet"]
        result = build_variables_for_file(0, group, instruments)
        self.assertEqual(result["序號"], "1")
        self.assertEqual(result["樂器"], "Flute")
        self.assertEqual(result["曲名"], "Beethoven Sym.5")
        self.assertEqual(result["樂章編號"], "1")
        self.assertEqual(result["樂章名稱"], "Allegro")

    def test_zero_padding(self):
        group = Group(
            selected_instruments=list(range(12)),
            piece_name="Test",
        )
        instruments = [f"Inst{i}" for i in range(12)]
        result = build_variables_for_file(0, group, instruments)
        self.assertEqual(result["序號"], "01")
        result = build_variables_for_file(11, group, instruments)
        self.assertEqual(result["序號"], "12")

    def test_selected_instruments_subset(self):
        group = Group(
            selected_instruments=[1, 3],
            piece_name="Test",
        )
        instruments = ["Flute", "Oboe", "Clarinet", "Bassoon"]
        result = build_variables_for_file(0, group, instruments)
        self.assertEqual(result["序號"], "2")
        self.assertEqual(result["樂器"], "Oboe")
        result = build_variables_for_file(1, group, instruments)
        self.assertEqual(result["序號"], "4")
        self.assertEqual(result["樂器"], "Bassoon")


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


class TestValidateTemplate(unittest.TestCase):
    """validate_template 測試"""

    def test_valid_template(self):
        result = validate_template("{序號}. {樂器} - {曲名}.pdf")
        self.assertEqual(result, [])

    def test_unknown_variable(self):
        result = validate_template("{序號}. {未知變數}.pdf")
        self.assertEqual(result, ["未知變數"])

    def test_no_variables(self):
        result = validate_template("plain.pdf")
        self.assertEqual(result, [])

    def test_all_variables(self):
        template = "{序號}{樂器}{曲名}{樂章編號}{樂章名稱}"
        result = validate_template(template)
        self.assertEqual(result, [])


if __name__ == '__main__':
    unittest.main()
