# -*- coding: utf-8 -*-
"""
聲部組對照的編輯：譜庫服務對假 Google 讀出全社團的對照、寫回改過的樂器

讀：每個樂團類型一套，社團對照裡的樂器連同譜庫裡出現、還沒歸類的樂器，各標出譜庫裡用到幾首。
寫：只寫改過的樂器，走存檔寫回的同一套比對（別人同時改過同一個樂器就是衝突），樂器的寫法保留第一次寫入的，
編制寫入紀錄的來源記「譜庫視窗」。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.catalog_constants import (
    SHEET_EDITIONS, SHEET_INSTRUMENTATION_LOG, SHEET_PARTS, SHEET_PIECES, SHEET_SECTIONS,
)
from core.catalog_models import SectionMapEntry
from core.catalog_sync import Choice, Decision, SyncField, Verdict, section_map_items
from core.constants import OrchestraType
from fake_google_catalog import FakeGoogle
from services.catalog_service import CatalogService
from test_catalog_link import records
from test_catalog_service import add_row, empty_catalog

OPERATOR = "score@example.com"


class SectionMapTestCase(unittest.TestCase):
    """假 Google 的譜庫：三首曲目用到二胡、琵琶、柳琴、排鼓與 Violin；社團對照歸了二胡 I、琵琶與 Violin"""

    def setUp(self):
        self.google = FakeGoogle()
        empty_catalog(self.google)
        self.catalog = CatalogService(self.google.sheets, self.google.drive, sleep=lambda seconds: None)
        for piece, edition in (("p1", "e1"), ("p2", "e2"), ("p3", "e3")):
            add_row(self.google, SHEET_PIECES, id=piece, title=piece, active_edition_id=edition)
            add_row(self.google, SHEET_EDITIONS, id=edition, piece_id=piece)
        add_row(self.google, SHEET_EDITIONS, id="e1b", piece_id="p1")
        parts = (
            ("e1", "二胡 I"), ("e1", "二胡 II"), ("e1", "琵琶"), ("e1b", "二胡1"),
            ("e2", "二胡 I"), ("e2", "柳琴"), ("e2", "排鼓"),
            ("e3", "琵琶 II"), ("e3", "Violin I"),
        )
        for n, (edition, voice) in enumerate(parts):
            add_row(self.google, SHEET_PARTS, id=f"pt{n}", edition_id=edition, instrument_name=voice, sort_order="1")
        add_row(self.google, SHEET_PARTS, id="score", edition_id="e3", instrument_name="總譜", is_score="TRUE")
        club = (("chinese", "二胡 I", "拉弦"), ("chinese", "琵琶", "彈撥"), ("western", "Violin", "弦樂"))
        for n, (orchestra, instrument, section) in enumerate(club):
            add_row(
                self.google, SHEET_SECTIONS, id=f"s{n}", orchestra_type=orchestra, instrument=instrument,
                section=section,
            )

    def club(self) -> list:
        """社團對照工作表的每一列：（樂團類型、樂器、聲部組）"""
        return [(r["orchestra_type"], r["instrument"], r["section"]) for r in records(self.google, SHEET_SECTIONS)]

    def save(self, chosen: dict, decisions=None):
        """把 chosen（樂團類型 → {樂器: 聲部組}）相對於現在讀到的對照的改動寫回譜庫，回傳結果"""
        entries = self.catalog.section_map()
        items = {orchestra: section_map_items(entries[orchestra], sections) for orchestra, sections in chosen.items()}
        return self.catalog.sync_section_map(items, OPERATOR, decisions or {})


class TestReadingTheMap(SectionMapTestCase):
    """每個樂團類型一套：社團對照的樂器在它的聲部組，譜庫裡出現、還沒歸類的樂器聲部組空白；各標出用到幾首"""

    def test_lists_each_orchestra_type_with_unclassified_instruments_and_piece_counts(self):
        sections = self.catalog.section_map()
        self.assertEqual(sections[OrchestraType.CHINESE], [
            SectionMapEntry("二胡 I", "拉弦", 2),
            SectionMapEntry("琵琶", "彈撥", 2),
            SectionMapEntry("柳琴", "", 1),
            SectionMapEntry("排鼓", "", 1),
            SectionMapEntry("Violin", "", 1),
        ])
        self.assertEqual(sections[OrchestraType.WESTERN], [
            SectionMapEntry("Violin", "弦樂", 1),
            SectionMapEntry("二胡", "", 2),
            SectionMapEntry("琵琶", "", 2),
            SectionMapEntry("柳琴", "", 1),
            SectionMapEntry("排鼓", "", 1),
        ])

    def test_takes_two_requests(self):
        self.catalog.section_map()
        self.assertEqual(self.google.requests, 2)


class TestSavingTheMap(SectionMapTestCase):
    """只寫改過的樂器；樂器的寫法保留第一次寫入的；編制寫入紀錄的來源記譜庫視窗"""

    def test_writes_only_the_moved_instruments_in_one_request_keeping_their_spelling(self):
        outcome = self.save({OrchestraType.CHINESE: {"二胡 I": "低音", "柳琴": "彈撥", "琵琶": "彈撥"}})
        self.assertTrue(outcome.written)
        self.assertEqual(self.club(), [
            ("chinese", "二胡 I", "低音"), ("chinese", "琵琶", "彈撥"), ("western", "Violin", "弦樂"),
            ("chinese", "柳琴", "彈撥"),
        ])
        self.assertEqual(len(self.google.sheets.writes), 1)

    def test_each_written_instrument_logs_one_row_from_the_catalog_window(self):
        self.save({OrchestraType.CHINESE: {"二胡 I": "低音"}, OrchestraType.WESTERN: {"二胡": "弦樂"}})
        log = [
            (r["source"], r["voice"], r["field"], r["old_value"], r["new_value"], r["operator"])
            for r in records(self.google, SHEET_INSTRUMENTATION_LOG)
        ]
        self.assertEqual(log, [
            ("catalog_window", "二胡 I", "section", "拉弦", "低音", OPERATOR),
            ("catalog_window", "二胡", "section", "", "弦樂", OPERATOR),
        ])
        self.assertIn(("western", "二胡", "弦樂"), self.club())

    def test_moving_an_instrument_back_to_unclassified_blanks_its_section(self):
        self.save({OrchestraType.CHINESE: {"琵琶": ""}})
        self.assertIn(("chinese", "琵琶", ""), self.club())
        chinese = self.catalog.section_map()[OrchestraType.CHINESE]
        self.assertIn(SectionMapEntry("琵琶", "", 2), chinese)

    def test_nothing_moved_writes_nothing(self):
        outcome = self.save({OrchestraType.CHINESE: {"二胡 I": "拉弦"}})
        self.assertTrue(outcome.written)
        self.assertEqual(self.google.sheets.writes, [])


class TestSomeoneElseChangedTheSameInstrument(SectionMapTestCase):
    """寫入前重讀社團對照：別人同時改過同一個樂器就是衝突，選邊後才寫"""

    def save_after_someone_else_sets(self, section: str, mine: str, decisions=None):
        """讀到對照之後、寫入之前，另一台電腦把琵琶改成 section；我把琵琶改成 mine"""
        entries = self.catalog.section_map()
        self.google.sheets.set_cell(SHEET_SECTIONS, "D3", section)
        items = {OrchestraType.CHINESE: section_map_items(entries[OrchestraType.CHINESE], {"琵琶": mine})}
        return self.catalog.sync_section_map(items, OPERATOR, decisions or {})

    def test_a_different_value_is_a_conflict_and_nothing_is_written(self):
        outcome = self.save_after_someone_else_sets("低音", "打擊")
        self.assertFalse(outcome.written)
        (_, review), = outcome.conflicts
        self.assertEqual((review.item.voice, review.item.new, review.theirs), ("琵琶", "打擊", "低音"))
        self.assertEqual(self.google.sheets.writes, [])

    def test_keeping_mine_after_the_conflict_writes_it(self):
        decisions = {OrchestraType.CHINESE.value: {(SyncField.SECTION, "琵琶"): Decision(Choice.MINE, "低音")}}
        outcome = self.save_after_someone_else_sets("低音", "打擊", decisions)
        self.assertTrue(outcome.written)
        self.assertIn(("chinese", "琵琶", "打擊"), self.club())

    def test_the_same_value_is_already_done(self):
        outcome = self.save_after_someone_else_sets("低音", "低音")
        self.assertTrue(outcome.written)
        self.assertEqual([r.verdict for r in outcome.places[0].items], [Verdict.DONE])
        self.assertEqual(self.google.sheets.writes, [])


if __name__ == "__main__":
    unittest.main()
