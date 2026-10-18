# -*- coding: utf-8 -*-
"""
開啟專案時從譜庫更新快照的測試：對假 Google 讀連到的各處，再交給專案更新快照

人數與時長的變動直接套用；聲部清單、名稱或順序變了時回報差異、不自動套用，由譜務選「套用譜庫的」
（依名稱配對移動分譜）或「用專案的」（存檔時寫回，已有 Drive 檔案的聲部無法寫回）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.catalog_constants import SHEET_DURATIONS, SHEET_PARTS, SHEET_SECTIONS
from core.catalog_errors import CatalogError, CatalogErrorKind
from core.catalog_sync import SyncField, Verdict, WriteSource, place_writes
from core.constants import OrchestraType
from core.models import CatalogChange, CatalogChangeKind, Headcount
from test_catalog_link import ProjectLinkTestCase, part_file, records
from test_catalog_service import add_row
from test_catalog_write_back import OPERATOR


class TestReadPlaces(ProjectLinkTestCase):
    """開啟專案時一次讀出各群組連到的那處"""

    def setUp(self):
        super().setUp()
        self.fill_catalog()
        self.part("笛子", "1", movement="")

    def test_reads_every_place_with_two_requests(self):
        places = self.catalog.read_places([("e1", "m1"), ("e1", "")], OrchestraType.CHINESE)
        self.assertEqual([[name for name, _ in place.voices] for place in places], [
            ["二胡 I", "二胡 II", "琵琶", "揚琴"], ["笛子"],
        ])
        self.assertEqual(places[0].duration_seconds, 425)
        self.assertEqual(self.google.requests, 2)

    def test_a_place_no_longer_in_the_catalog_is_none_and_the_others_are_read(self):
        places = self.catalog.read_places([("e1", "m1"), ("e9", ""), ("e1", "m9")], OrchestraType.CHINESE)
        self.assertEqual([place.edition_id if place else None for place in places], ["e1", None, None])


class RefreshTestCase(ProjectLinkTestCase):
    """群組連到譜庫的第一樂章（二胡 I、二胡 II、琵琶、揚琴，時長 7:05），之後譜庫被別人改了"""

    def setUp(self):
        super().setUp()
        self.fill_catalog()
        self.link()

    def refresh(self):
        """重讀第一樂章並更新快照（開啟專案時）"""
        place = self.catalog.read_places([("e1", "m1")], self.project.orchestra_type)[0]
        self.project.refresh_catalog(self.group, place)


class TestHeadcountsAndDuration(RefreshTestCase):
    """人數與時長的變動直接套用"""

    def test_catalog_changes_are_applied_to_the_group_and_the_snapshot(self):
        self.google.sheets.set_cell(SHEET_PARTS, "J4", "3")
        self.google.sheets.set_cell(SHEET_DURATIONS, "D2", "430")
        self.refresh()
        self.assertEqual(self.group.headcounts[2], Headcount(3))
        self.assertEqual(self.group.duration_seconds, 430)
        self.assertEqual(self.project.catalog_changes(self.group), [])
        self.assertIsNone(self.project.catalog_list_change(self.group))

    def test_a_value_the_group_has_not_written_back_yet_is_kept_for_the_conflict_check(self):
        self.project.set_headcount(self.group, 2, Headcount(5))
        self.google.sheets.set_cell(SHEET_PARTS, "J4", "3")
        self.refresh()
        self.assertEqual(self.group.headcounts[2], Headcount(5))
        self.assertEqual(
            self.project.catalog_changes(self.group),
            [CatalogChange(CatalogChangeKind.HEADCOUNT, "琵琶", Headcount(2), Headcount(5))],
        )
        outcome = self.catalog.sync_instrumentation(
            place_writes(self.project), self.project.orchestra_type, OPERATOR, WriteSource.SAVE, {},
        )
        self.assertEqual([review.verdict for _, review in outcome.conflicts], [Verdict.CONFLICT])

    def test_an_unchanged_catalog_leaves_the_project_saved(self):
        self.project.mark_saved()
        self.refresh()
        self.assertFalse(self.project.is_modified())


class ListChangeTestCase(RefreshTestCase):
    """群組的四份分譜依序配好；譜庫刪了二胡 II、新增排鼓（已有 Drive 檔案，社團對照歸低音）、琵琶移到最後"""

    def setUp(self):
        super().setUp()
        self.project.add_files([part_file(n) for n in ("a.pdf", "b.pdf", "c.pdf", "d.pdf")], self.group)
        self.google.sheets.set_cell(SHEET_PARTS, "D3", "")
        self.part("排鼓", "5", drive_file_id="f-drum")
        add_row(self.google, SHEET_SECTIONS, id="s9", orchestra_type="chinese", instrument="排鼓", section="低音")
        self.google.sheets.set_cell(SHEET_PARTS, "E4", "6")
        self.project.mark_saved()
        self.refresh()

    def pairs(self):
        """群組目前每個聲部配到的分譜檔名"""
        return [(pair.voice, pair.file.display_name if pair.file else None) for pair in self.group.pairing()]

    def catalog_voices(self) -> list:
        """分譜工作表裡第一樂章的聲部名稱，依排序欄"""
        rows = [r for r in records(self.google, SHEET_PARTS) if r["movement_id"] == "m1" and r["instrument_name"]]
        return [r["instrument_name"] for r in sorted(rows, key=lambda r: int(r["sort_order"]))]


class TestListChangeOnOpen(ListChangeTestCase):
    """聲部清單變了：回報差異，不自動套用；還沒決定時配對與命名照專案的清單，專案不算未存檔"""

    def test_reports_added_removed_and_moved_voices(self):
        change = self.project.catalog_list_change(self.group)
        self.assertEqual(change.added, ["排鼓"])
        self.assertEqual(change.removed, ["二胡 II"])
        self.assertEqual(change.moved, ["揚琴"])
        self.assertEqual([name for name, _ in change.voices], ["二胡 I", "揚琴", "排鼓", "琵琶"])

    def test_undecided_keeps_the_project_list_and_pairing(self):
        self.assertEqual(self.pairs(), [("二胡 I", "a.pdf"), ("二胡 II", "b.pdf"), ("琵琶", "c.pdf"), ("揚琴", "d.pdf")])
        self.assertEqual(self.project.catalog_changes(self.group), [])
        self.assertEqual(place_writes(self.project), [])


class TestTakeCatalogList(ListChangeTestCase):
    """「套用譜庫的」：分譜依名稱配對，對不到的移到未分組"""

    def test_parts_follow_their_voice_names_and_unmatched_parts_move_to_ungrouped(self):
        moved = self.project.take_catalog_list(self.group)
        self.assertEqual(self.group.instruments, ["二胡 I", "揚琴", "排鼓", "琵琶"])
        self.assertEqual(self.pairs(), [("二胡 I", "a.pdf"), ("揚琴", "d.pdf"), ("琵琶", "c.pdf")])
        self.assertEqual(self.group.present, [True, True, False, True])
        self.assertEqual([f.display_name for f in moved], ["b.pdf"])
        self.assertEqual([f.display_name for f in self.project.ungrouped_files], ["b.pdf"])
        self.assertEqual(self.project.section_of("排鼓"), "低音")
        self.assertEqual(self.project.catalog_changes(self.group), [])
        self.assertIsNone(self.project.catalog_list_change(self.group))


class TestKeepProjectList(ListChangeTestCase):
    """「用專案的（存檔時寫回）」：群組不變，存檔時先確認再寫回；已有 Drive 檔案的聲部無法刪除，其餘照寫"""

    def setUp(self):
        super().setUp()
        self.project.keep_project_list(self.group)

    def sync(self, review_only=False):
        """把待寫入的改動寫回譜庫；寫入時交回專案"""
        outcome = self.catalog.sync_instrumentation(
            place_writes(self.project), self.project.orchestra_type, OPERATOR, WriteSource.SAVE, {},
            review_only=review_only,
        )
        if outcome.written:
            self.project.mark_catalog_synced(outcome)
        return outcome

    def test_the_group_is_unchanged_and_its_list_becomes_a_change_to_confirm(self):
        self.assertEqual(self.group.instruments, ["二胡 I", "二胡 II", "琵琶", "揚琴"])
        self.assertIsNone(self.project.catalog_list_change(self.group))
        writes = place_writes(self.project)
        self.assertTrue(writes[0].confirm)
        self.assertEqual(
            sorted((item.field, item.voice, item.new) for item in writes[0].items if item.field is SyncField.VOICE),
            [(SyncField.VOICE, "二胡 II", True), (SyncField.VOICE, "排鼓", False)],
        )

    def test_reviewing_first_writes_nothing(self):
        before = self.catalog_voices()
        outcome = self.sync(review_only=True)
        self.assertFalse(outcome.written)
        self.assertEqual(outcome.conflicts, [])
        self.assertEqual(self.catalog_voices(), before)

    def test_saving_writes_the_project_list_except_deleting_a_voice_with_a_drive_file(self):
        outcome = self.sync()
        self.assertTrue(outcome.written)
        self.assertEqual([review.item.voice for _, review in outcome.blocked], ["排鼓"])
        self.assertEqual(self.catalog_voices(), ["二胡 I", "二胡 II", "琵琶", "揚琴", "排鼓"])
        self.assertFalse(self.group.catalog_link.confirm_voices)
        self.assertEqual(place_writes(self.project)[0].items[0].voice, "排鼓")


if __name__ == "__main__":
    unittest.main()
