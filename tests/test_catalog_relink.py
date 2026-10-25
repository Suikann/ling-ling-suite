# -*- coding: utf-8 -*-
"""
改連後撤掉原處編制的測試：對假 Google 讀出這個群組在原處寫過的編制，分成刪掉、改回原值與不動，選「撤掉」後存檔時寫進譜庫

群組先連到第一樂章並存檔寫回（新增笛子、改琵琶與揚琴的人數），之後別的群組改了揚琴；改連時依編制寫入紀錄判斷哪些
是這個群組寫的、之後沒人改過的。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.catalog_constants import SHEET_HEADERS, SHEET_INSTRUMENTATION_LOG, SHEET_PARTS
from core.catalog_revert import KeepReason, RevertOutcome, pending_revert, revert_count
from core.catalog_sync import WriteSource, place_writes
from core.models import Headcount, LinkChoice, Project
from test_catalog_link import records
from test_catalog_write_back import OPERATOR, SyncTestCase

OTHER = "other@example.com"


class RelinkTestCase(SyncTestCase):
    """群組連到第一樂章，存檔時新增笛子（2 人）、琵琶改成 3 人、揚琴改成 2 人；之後別的群組把揚琴改成 3 人"""

    def setUp(self):
        super().setUp()
        self.fill_catalog()
        self.link()
        self.project.add_instrument(self.group, "笛子")
        self.project.set_headcount(self.group, 4, Headcount(2))
        self.project.set_headcount(self.group, 2, Headcount(3))
        self.project.set_headcount(self.group, 3, Headcount(2))
        self.assertTrue(self.sync().written)
        self.other_writes("揚琴", Headcount(3))

    def other_writes(self, voice: str, headcount: Headcount) -> None:
        """別的專案的「別的群組」也連到第一樂章，改了一個聲部的人數並存檔寫回"""
        project = Project()
        group = project.add_group("別的群組", "總譜")
        project.link_group(group, self.catalog.read_place("e1", "m1", project.orchestra_type))
        project.set_headcount(group, group.instruments.index(voice), headcount)
        outcome = self.catalog.sync_instrumentation(
            place_writes(project), project.orchestra_type, OTHER, WriteSource.SAVE, {},
        )
        self.assertTrue(outcome.written)

    def revert_rows(self):
        """讀出這個群組在第一樂章寫過的編制"""
        return self.catalog.revert_rows("e1", "m1", self.group.id, self.group.name, self.project.orchestra_type)


class TestRevertRows(RelinkTestCase):
    """只列這個群組寫過的聲部，依譜庫的順序；之後沒人改過的才撤得掉"""

    def test_added_voice_is_removed_changed_headcount_restored_and_later_change_kept(self):
        rows = self.revert_rows()
        self.assertEqual([row.voice for row in rows], ["琵琶", "揚琴", "笛子"])
        pipa, yangqin, dizi = rows
        self.assertEqual(
            (pipa.outcome, pipa.written, pipa.current, pipa.original), (RevertOutcome.RESTORE, Headcount(3),
                                                                        Headcount(3), Headcount(2)),
        )
        other_time = next(
            r["time"] for r in records(self.google, SHEET_INSTRUMENTATION_LOG) if r["operator"] == OTHER
        )
        self.assertEqual(
            (yangqin.outcome, yangqin.keep_reason, yangqin.written, yangqin.current, yangqin.changed_by,
             yangqin.changed_at),
            (RevertOutcome.KEEP, KeepReason.CHANGED, Headcount(2), Headcount(3), OTHER, other_time),
        )
        self.assertEqual((dizi.outcome, dizi.added, dizi.written), (RevertOutcome.REMOVE, True, Headcount(2)))

    def test_a_written_lone_zero_is_logged_as_zero_and_can_be_restored(self):
        self.project.set_headcount(self.group, 2, Headcount(0))
        self.assertTrue(self.sync().written)
        log = [r for r in records(self.google, SHEET_INSTRUMENTATION_LOG) if r["voice"] == "琵琶"]
        self.assertEqual(log[-1]["new_value"], "0")
        pipa = self.revert_rows()[0]
        self.assertEqual((pipa.outcome, pipa.written, pipa.current), (RevertOutcome.RESTORE, Headcount(0), Headcount(0)))

    def test_value_changed_on_the_sheet_without_a_log_entry_is_kept(self):
        self.google.sheets.set_cell(SHEET_PARTS, f"J{self.sheet_row('琵琶')}", "5")
        pipa = self.revert_rows()[0]
        self.assertEqual(
            (pipa.outcome, pipa.keep_reason, pipa.current, pipa.changed_by),
            (RevertOutcome.KEEP, KeepReason.EDITED, Headcount(5), ""),
        )

    def test_added_voice_with_a_drive_file_is_kept(self):
        self.google.sheets.set_cell(SHEET_PARTS, f"G{self.sheet_row('笛子')}", "file-1")
        dizi = self.revert_rows()[-1]
        self.assertEqual((dizi.outcome, dizi.keep_reason), (RevertOutcome.KEEP, KeepReason.FILED))

    def test_voice_the_group_deleted_is_not_listed(self):
        self.project.remove_instruments(self.group, [2])
        self.assertTrue(self.sync().written)
        self.assertEqual([row.voice for row in self.revert_rows()], ["揚琴", "笛子"])

    def test_other_places_list_nothing(self):
        self.part("二胡 I", "1", movement="")
        self.assertEqual(
            self.catalog.revert_rows("e1", "", self.group.id, self.group.name, self.project.orchestra_type), [],
        )

    def test_a_renamed_group_still_finds_its_writes(self):
        self.project.update_group(self.group, name="改過名字的群組")
        self.assertEqual([row.voice for row in self.revert_rows()], ["琵琶", "揚琴", "笛子"])

    def test_another_group_with_the_same_name_counts_as_someone_else(self):
        project = Project()
        group = project.add_group(self.group.name, "總譜")
        project.link_group(group, self.catalog.read_place("e1", "m1", project.orchestra_type))
        project.set_headcount(group, group.instruments.index("琵琶"), Headcount(4))
        outcome = self.catalog.sync_instrumentation(
            place_writes(project), project.orchestra_type, OTHER, WriteSource.SAVE, {},
        )
        self.assertTrue(outcome.written)
        pipa = self.revert_rows()[0]
        self.assertEqual((pipa.voice, pipa.keep_reason, pipa.changed_by), ("琵琶", KeepReason.CHANGED, OTHER))

    def test_rows_written_before_group_ids_are_matched_by_name(self):
        column = SHEET_HEADERS[SHEET_INSTRUMENTATION_LOG].index("group_id")
        rows = self.google.sheets.rows(SHEET_INSTRUMENTATION_LOG)
        for number in range(2, len(rows) + 1):
            self.google.sheets.set_cell(SHEET_INSTRUMENTATION_LOG, f"{chr(ord('A') + column)}{number}", "")
        self.assertEqual([row.voice for row in self.revert_rows()], ["琵琶", "揚琴", "笛子"])

    def sheet_row(self, voice: str) -> int:
        """分譜工作表裡第一樂章這個聲部所在的列號（從 1 起算）"""
        rows = self.google.sheets.rows(SHEET_PARTS)
        return next(i + 1 for i, row in enumerate(rows) if row[2:4] == ["m1", voice])



class RevertOnSaveTestCase(RelinkTestCase):
    """群組改連到整首，選了撤掉第一樂章的編制"""

    def setUp(self):
        super().setUp()
        rows = self.revert_rows()
        self.project.link_group(self.group, self.catalog.read_place("e1", "", self.project.orchestra_type))
        self.project.queue_catalog_revert(self.group, pending_revert("e1", "m1", rows))

    def first_movement(self) -> dict:
        """分譜工作表裡第一樂章的聲部與最少人數"""
        return {
            r["instrument_name"]: r["headcount_min"] for r in records(self.google, SHEET_PARTS)
            if r["movement_id"] == "m1" and r["instrument_name"]
        }


class TestRevertOnSave(RevertOnSaveTestCase):
    """存檔時把撤掉寫進原處：新增的聲部刪掉、人數改回原值，之後被改過的不動；寫編制寫入紀錄"""

    def test_saving_removes_the_added_voice_and_restores_the_headcount(self):
        outcome = self.sync()
        self.assertTrue(outcome.written)
        self.assertEqual(self.first_movement(), {"二胡 I": "6", "二胡 II": "4", "琵琶": "2", "揚琴": "3"})
        undo = [r for r in records(self.google, SHEET_INSTRUMENTATION_LOG) if r["source"] == WriteSource.UNDO.value]
        self.assertEqual(
            [(r["movement_id"], r["voice"], r["field"], r["old_value"], r["new_value"], r["group"]) for r in undo],
            [("m1", "笛子", "voice", "笛子", "", "江樓鐘鼓"), ("m1", "琵琶", "headcount", "3", "2", "江樓鐘鼓")],
        )
        self.assertEqual(self.group.catalog_link.reverts, [])
        self.assertEqual(self.group.catalog_link.movement_id, "")

    def test_value_changed_after_choosing_is_left_alone_without_a_conflict(self):
        self.other_writes("琵琶", Headcount(4))
        outcome = self.sync()
        self.assertTrue(outcome.written)
        self.assertEqual(outcome.conflicts, [])
        self.assertEqual(self.first_movement()["琵琶"], "4")
        self.assertNotIn("笛子", self.first_movement())

    def test_place_no_longer_in_the_catalog_has_nothing_to_revert(self):
        self.catalog.delete_movement("m1")
        outcome = self.sync()
        self.assertTrue(outcome.written)
        self.assertEqual(self.group.catalog_link.reverts, [])

    def test_nothing_is_left_to_revert_afterwards(self):
        self.sync()
        self.assertEqual(revert_count(self.revert_rows()), 0)

    def test_pending_revert_is_kept_in_the_project_file(self):
        restored = Project.from_data(self.project.to_data(), "總譜").groups[0].catalog_link.reverts
        self.assertEqual(restored, self.group.catalog_link.reverts)
        self.assertEqual(restored[0].remove, ["笛子"])
        self.assertEqual(restored[0].restore, [("琵琶", Headcount(3), Headcount(2))])

    def test_linking_back_to_the_place_drops_its_pending_revert(self):
        place = self.catalog.read_place("e1", "m1", self.project.orchestra_type)
        self.project.link_group(self.group, place, LinkChoice.CATALOG)
        self.assertEqual(self.group.catalog_link.reverts, [])



class TestUnsyncedRevert(RelinkTestCase):
    """撤掉寫不進去時跟編制一樣標未同步，寫進去才清掉"""

    def test_group_with_only_a_pending_revert_is_unsynced_until_it_is_written(self):
        rows = self.revert_rows()
        self.project.link_group(self.group, self.catalog.read_place("e1", "", self.project.orchestra_type))
        self.sync()
        self.project.queue_catalog_revert(self.group, pending_revert("e1", "m1", rows))
        self.assertEqual(self.project.catalog_changes(self.group), [])
        self.project.mark_catalog_unsynced([self.group.id])
        self.assertEqual(self.project.unsynced_groups(), [self.group])
        self.assertTrue(self.sync().written)
        self.assertEqual(self.project.unsynced_groups(), [])
        first = [r["instrument_name"] for r in records(self.google, SHEET_PARTS) if r["movement_id"] == "m1"]
        self.assertNotIn("笛子", first)


if __name__ == "__main__":
    unittest.main()
