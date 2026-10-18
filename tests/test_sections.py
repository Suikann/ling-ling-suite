# -*- coding: utf-8 -*-
"""
聲部組測試

純 Python：依樂團類型偵測聲部組的表格，以及以樂器為鍵的聲部組對照。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.constants import OrchestraType
from core.sections import detect_section, section_for, section_options


class TestDetectSectionForChineseOrchestra(unittest.TestCase):
    """國樂團的偵測：胡類歸拉弦，大提琴、低音提琴、革胡歸低音，偵測不到的歸「其他」"""

    EXPECTED = [
        ("梆笛", "吹管"), ("新笛Ⅰ、Ⅱ", "吹管"), ("高音笙", "吹管"), ("低音笙", "吹管"), ("中音嗩吶", "吹管"),
        ("簫", "吹管"),
        ("高胡", "拉弦"), ("二胡 I", "拉弦"), ("二胡 II", "拉弦"), ("中胡", "拉弦"), ("板胡", "拉弦"),
        ("柳琴", "彈撥"), ("琵琶", "彈撥"), ("中阮", "彈撥"), ("揚琴", "彈撥"), ("古箏", "彈撥"), ("三弦", "彈撥"),
        ("打擊 I", "打擊"), ("定音鼓", "打擊"), ("Timpani", "打擊"),
        ("大提琴", "低音"), ("低音提琴", "低音"), ("低音大提琴", "低音"), ("革胡", "低音"), ("Cello", "低音"),
        ("Contrabass", "低音"),
        ("Di II", "其他"), ("Flute 1", "其他"), ("人聲", "其他"),
    ]

    def test_detects_each_voice_into_the_chinese_orchestra_sections(self):
        for voice, section in self.EXPECTED:
            with self.subTest(voice):
                self.assertEqual(detect_section(voice, OrchestraType.CHINESE), section)

    def test_options_are_the_five_sections_and_other(self):
        self.assertEqual(section_options(OrchestraType.CHINESE), ("吹管", "拉弦", "彈撥", "打擊", "低音", "其他"))


class TestDetectSectionForWesternOrchestra(unittest.TestCase):
    """管弦樂團的偵測：木管、銅管、打擊、鍵盤、弦樂，偵測不到的歸「其他」"""

    EXPECTED = [
        ("Flute 1", "木管"), ("English Horn", "木管"), ("Clarinet in Bb 2", "木管"), ("Alto Saxophone 1", "木管"),
        ("Horn in F 1", "銅管"), ("Trumpet in Bb 1", "銅管"), ("Tuba", "銅管"),
        ("Timpani", "打擊"), ("Percussion 2", "打擊"),
        ("Piano", "鍵盤"), ("Harp", "鍵盤"),
        ("Violin I", "弦樂"), ("Viola", "弦樂"), ("Violoncello", "弦樂"), ("Contrabass", "弦樂"),
        ("Theremin", "其他"), ("琵琶", "其他"),
    ]

    def test_detects_each_voice_into_the_western_orchestra_sections(self):
        for voice, section in self.EXPECTED:
            with self.subTest(voice):
                self.assertEqual(detect_section(voice, OrchestraType.WESTERN), section)

    def test_options_are_the_five_sections_and_other(self):
        self.assertEqual(section_options(OrchestraType.WESTERN), ("木管", "銅管", "打擊", "鍵盤", "弦樂", "其他"))


class TestSectionFor(unittest.TestCase):
    """聲部的聲部組：它的樂器存了（不是空白）就用存的，否則依樂團類型偵測"""

    def test_value_stored_for_the_instrument_wins_for_every_number(self):
        for voice in ("二胡 I", "二胡2", "二胡"):
            with self.subTest(voice):
                self.assertEqual(section_for(voice, {"二胡 II": "低音"}, OrchestraType.CHINESE), "低音")

    def test_missing_or_blank_value_falls_back_to_detection(self):
        for sections in ({}, {"二胡 I": "  "}, {"中胡": "低音"}):
            with self.subTest(sections=sections):
                self.assertEqual(section_for("二胡 I", sections, OrchestraType.CHINESE), "拉弦")


if __name__ == "__main__":
    unittest.main()
