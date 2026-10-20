# -*- coding: utf-8 -*-
"""
譜庫標題列的語言：各工作表的中英文欄名，與從標題列認出語言
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from core.catalog_constants import SHEET_CLUB_SETTINGS, SHEET_HEADERS, SHEET_PARTS, SHEET_PIECES
from core.catalog_headers import catalog_header_locale, header_locale, header_row
from core.constants import LOCALE_EN, LOCALE_ZH_TW


class TestHeaderRow(unittest.TestCase):
    """每張工作表的標題列依語言寫成中文或英文；欄的位置與程式的欄位常數一一對應"""

    def test_english_header_row_is_the_field_keys(self):
        self.assertEqual(header_row(SHEET_PARTS, LOCALE_EN), SHEET_HEADERS[SHEET_PARTS])

    def test_chinese_header_row_names_each_field(self):
        row = header_row(SHEET_PARTS, LOCALE_ZH_TW)
        self.assertEqual(row[:4], ["編號", "版本編號", "樂章編號", "聲部"])
        self.assertEqual(row[SHEET_HEADERS[SHEET_PARTS].index("headcount_min")], "最少人數")

    def test_every_sheet_has_a_distinct_chinese_name_for_every_field(self):
        for sheet, keys in SHEET_HEADERS.items():
            with self.subTest(sheet=sheet):
                row = header_row(sheet, LOCALE_ZH_TW)
                self.assertEqual(len(row), len(keys))
                self.assertEqual(len(set(row)), len(row))
                self.assertTrue(all(name.strip() for name in row))

    def test_club_settings_keep_the_same_header_in_both_languages(self):
        self.assertEqual(header_row(SHEET_CLUB_SETTINGS, LOCALE_EN), ["鍵", "值"])
        self.assertEqual(header_row(SHEET_CLUB_SETTINGS, LOCALE_ZH_TW), ["鍵", "值"])


class TestHeaderLocale(unittest.TestCase):
    """從工作表現有的標題列認出語言：第一欄是 id 為英文，其他為中文；沒有標題列時認不出"""

    def test_first_column_id_is_english(self):
        self.assertEqual(header_locale(SHEET_PIECES, ["id", "composer_id"]), LOCALE_EN)

    def test_other_first_column_is_chinese(self):
        self.assertEqual(header_locale(SHEET_PIECES, ["編號", "作曲家編號"]), LOCALE_ZH_TW)

    def test_blank_header_row_has_no_language(self):
        self.assertIsNone(header_locale(SHEET_PIECES, []))
        self.assertIsNone(header_locale(SHEET_PIECES, ["", "title"]))

    def test_club_settings_have_no_language(self):
        self.assertIsNone(header_locale(SHEET_CLUB_SETTINGS, ["鍵", "值"]))

    def test_catalog_language_is_the_one_every_sheet_agrees_on(self):
        english = {SHEET_PIECES: ["id"], SHEET_PARTS: ["id"], SHEET_CLUB_SETTINGS: ["鍵", "值"]}
        self.assertEqual(catalog_header_locale(english), LOCALE_EN)
        chinese = {SHEET_PIECES: ["編號"], SHEET_PARTS: ["編號"], SHEET_CLUB_SETTINGS: ["鍵", "值"]}
        self.assertEqual(catalog_header_locale(chinese), LOCALE_ZH_TW)

    def test_mixed_or_unknown_catalog_has_no_language(self):
        self.assertIsNone(catalog_header_locale({SHEET_PIECES: ["id"], SHEET_PARTS: ["編號"]}))
        self.assertIsNone(catalog_header_locale({SHEET_PIECES: [], SHEET_CLUB_SETTINGS: ["鍵", "值"]}))


if __name__ == "__main__":
    unittest.main()
