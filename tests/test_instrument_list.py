# -*- coding: utf-8 -*-
"""
匯出樂器表的文字內容測試

依聲部組分段、聲部後面寫人數、上方寫時長、最後寫聲部數與人數合計；介面為英文時聲部組換成英文名稱。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.constants import LOCALE_EN, LOCALE_ZH_TW
from core.instrument_list import instrument_list_text
from core.locale import set_locale
from core.models import Group, Headcount, Project


class TestInstrumentListText(unittest.TestCase):
    """國樂團的群組：高胡、二胡填了人數，琵琶未填，時長 7:05"""

    def setUp(self):
        set_locale(LOCALE_ZH_TW)
        self.addCleanup(set_locale, LOCALE_ZH_TW)
        self.group = Group(
            name="g", instruments=["高胡", "琵琶", "二胡 I"],
            headcounts=[Headcount(2), Headcount(), Headcount(6, 8)], duration_seconds=425,
        )
        self.project = Project(groups=[self.group])

    def test_voices_are_listed_by_section_with_duration_and_totals(self):
        self.assertEqual(instrument_list_text(self.project, self.group).split("\n"), [
            "樂器表", "=" * 40, "",
            "時長：7:05", "",
            "拉弦：", f"  {'高胡':<30s} x 2", f"  {'二胡 I':<30s} x 6-8", "",
            "彈撥：", "  琵琶", "",
            "=" * 40, "共 3 個聲部、8-10 人",
        ])

    def test_english_interface_shows_english_section_names(self):
        set_locale(LOCALE_EN)
        lines = instrument_list_text(self.project, self.group).split("\n")
        self.assertIn("Bowed Strings：", lines)
        self.assertEqual(lines[-1], "3 parts / 8-10 people")


if __name__ == "__main__":
    unittest.main()
