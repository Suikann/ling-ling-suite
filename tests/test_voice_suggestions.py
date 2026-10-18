# -*- coding: utf-8 -*-
"""
聲部名稱自動完成的候選測試：候選來源、排序與「同一聲部」標記
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from core.constants import INSTRUMENT_PRESETS
from core.models import Group, Project
from core.voice_suggestions import VoiceCandidates, VoiceSuggestion, ranked_by_usage, suggest


class TestRankedByUsage(unittest.TestCase):
    """依使用次數排序，各種寫法各算各的"""

    def test_more_used_names_come_first_and_ties_keep_first_appearance(self):
        names = ["中胡", "二胡 I", "琵琶", "二胡 I", "琵琶", "二胡 I", "二胡1"]
        self.assertEqual(ranked_by_usage(names), ["二胡 I", "琵琶", "中胡", "二胡1"])

    def test_blank_names_are_skipped_and_surrounding_spaces_ignored(self):
        self.assertEqual(ranked_by_usage(["", "  ", " 琵琶", "琵琶 "]), ["琵琶"])


class TestSuggest(unittest.TestCase):
    """輸入時列出的候選"""

    CANDIDATES = ["二胡 I", "二胡 II", "琵琶", "高胡", "Flute 1"]

    def test_candidates_containing_the_typed_text_keep_their_order(self):
        self.assertEqual(suggest("胡", self.CANDIDATES), [
            VoiceSuggestion("二胡 I"), VoiceSuggestion("二胡 II"), VoiceSuggestion("高胡"),
        ])

    def test_the_same_voice_written_differently_is_marked(self):
        self.assertEqual(suggest("二胡1", self.CANDIDATES), [VoiceSuggestion("二胡 I", same_voice=True)])

    def test_matching_ignores_case_and_full_width(self):
        self.assertEqual(suggest("ｆｌｕ", self.CANDIDATES), [VoiceSuggestion("Flute 1")])

    def test_a_candidate_written_exactly_as_typed_is_not_listed(self):
        self.assertEqual(suggest("琵琶", self.CANDIDATES), [])

    def test_nothing_typed_lists_nothing(self):
        self.assertEqual(suggest("  ", self.CANDIDATES), [])


class TestVoiceCandidates(unittest.TestCase):
    """候選來源：讀過譜庫用譜庫的，沒讀過用專案其他群組與預設樂器表"""

    def setUp(self):
        self.current = Group(name="一", instruments=["笛"])
        self.project = Project(groups=[
            self.current,
            Group(name="二", instruments=["中胡", "二胡 I"]),
            Group(name="三", instruments=["二胡 I", "大阮"]),
        ])
        self.candidates = VoiceCandidates()

    def test_without_the_catalog_other_groups_come_first_then_the_presets(self):
        names = self.candidates.for_group(self.project, self.current)
        self.assertEqual(names[:3], ["二胡 I", "中胡", "大阮"])
        self.assertNotIn("笛", names)
        self.assertIn("Violin I", names)
        self.assertIn("柳琴", names)
        preset_names = {voice for preset in INSTRUMENT_PRESETS for voice in preset.instruments}
        self.assertEqual(set(names[3:]), preset_names - {"二胡 I", "中胡", "大阮"})
        self.assertEqual(len(names), len(set(names)))

    def test_without_a_group_every_group_counts(self):
        names = self.candidates.for_group(self.project, None)
        self.assertEqual(names[:4], ["二胡 I", "笛", "中胡", "大阮"])

    def test_names_read_from_the_catalog_replace_the_project_and_presets(self):
        self.candidates.remember_catalog(["二胡 I", "琵琶"])
        self.assertEqual(self.candidates.for_group(self.project, self.current), ["二胡 I", "琵琶"])


if __name__ == "__main__":
    unittest.main()
