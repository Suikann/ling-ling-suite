# -*- coding: utf-8 -*-
"""
存檔時把編制寫回譜庫的測試：連到譜庫的群組改了編制後，經譜庫服務對假 Google 寫回，再把結果交回專案

只送改過的格子；寫入前重讀譜庫，每一格依「等於快照的原值、等於要寫的新值、兩者都不是」分成寫入、已完成、衝突；
衝突選邊後才寫，選「用譜庫的」的格子不寫、快照與群組跟著譜庫；一次存檔的格子一個請求。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.catalog_constants import (
    SHEET_DURATIONS, SHEET_EDITIONS, SHEET_HEADERS, SHEET_INSTRUMENTATION_LOG, SHEET_PARTS, SHEET_PIECES,
    SHEET_SECTIONS,
)
from core.catalog_errors import CatalogError, CatalogErrorKind
from core.catalog_sync import Choice, Decision, SyncField, Verdict, WriteSource, place_writes
from core.models import CatalogChangeKind, FiledVoicesError, Headcount, Project
from test_catalog_link import LinkTestCase, ProjectLinkTestCase, part_file, records
from test_catalog_service import add_row

OPERATOR = "score@example.com"


class SyncTestCase(ProjectLinkTestCase):
    """專案的群組要連到譜庫的第一樂章；寫回與查看分譜工作表的輔助"""

    def sync(self, decisions=None):
        """把專案待寫入的改動寫回譜庫並交回專案，回傳結果"""
        outcome = self.catalog.sync_instrumentation(
            place_writes(self.project), self.project.orchestra_type, OPERATOR, WriteSource.SAVE, decisions or {},
        )
        if outcome.written:
            self.project.mark_catalog_synced(outcome)
        return outcome

    def row(self, voice: str) -> dict:
        """分譜工作表裡第一樂章這個聲部的一列"""
        return next(
            r for r in records(self.google, SHEET_PARTS) if r["instrument_name"] == voice and r["movement_id"] == "m1"
        )


class WriteBackTestCase(SyncTestCase):
    """群組連到譜庫的第一樂章（二胡 I、二胡 II、琵琶、揚琴，時長 7:05）"""

    def setUp(self):
        super().setUp()
        self.fill_catalog()
        self.link()


class TestOnlyChangedCells(WriteBackTestCase):
    """只送改過的格子，一次寫入"""

    def test_a_headcount_change_writes_only_that_cell(self):
        self.project.set_headcount(self.group, 2, Headcount(3))
        outcome = self.sync()
        self.assertTrue(outcome.written)
        self.assertEqual(self.row("琵琶")["headcount_min"], "3")
        part_cells = [cell for write in self.google.sheets.writes for cell, _ in write if SHEET_PARTS in cell]
        self.assertEqual(part_cells, ["'分譜'!J4"])
        self.assertEqual(len(self.google.sheets.writes), 1)
        self.assertEqual(self.project.catalog_changes(self.group), [])


class TestComparison(WriteBackTestCase):
    """寫入前重讀譜庫：等於新值為已完成、等於原值就寫、兩者都不是為衝突"""

    def test_catalog_already_holding_the_new_value_is_done_without_writing(self):
        self.project.set_headcount(self.group, 2, Headcount(3))
        self.google.sheets.set_cell(SHEET_PARTS, "J4", "3")
        outcome = self.sync()
        self.assertTrue(outcome.written)
        self.assertEqual([r.verdict for r in outcome.places[0].items], [Verdict.DONE])
        self.assertEqual(self.google.sheets.writes, [])
        self.assertEqual(self.project.catalog_changes(self.group), [])

    def test_catalog_changed_by_someone_else_is_a_conflict_and_nothing_is_written(self):
        self.project.set_headcount(self.group, 2, Headcount(3))
        self.project.set_duration(self.group, 430)
        self.google.sheets.set_cell(SHEET_PARTS, "J4", "4")
        outcome = self.sync()
        self.assertFalse(outcome.written)
        self.assertEqual(
            [(r.item.field, r.theirs, r.verdict) for r in outcome.places[0].items],
            [(SyncField.HEADCOUNT, Headcount(4), Verdict.CONFLICT), (SyncField.DURATION, 425, Verdict.WRITE)],
        )
        self.assertEqual(self.google.sheets.writes, [])
        self.assertEqual(len(self.project.catalog_changes(self.group)), 2)


class TestChoosingASide(WriteBackTestCase):
    """衝突選邊後再寫：用我的照寫，用譜庫的不寫、快照與群組跟著譜庫"""

    def setUp(self):
        super().setUp()
        self.project.set_headcount(self.group, 2, Headcount(3))
        self.project.set_duration(self.group, 430)
        self.google.sheets.set_cell(SHEET_PARTS, "J4", "4")
        self.conflict = self.sync().conflicts[0][1]

    def decide(self, choice: Choice) -> dict:
        """對琵琶人數那一格的選擇"""
        return {self.group.id: {self.conflict.item.key: Decision(choice, self.conflict.theirs)}}

    def test_taking_the_catalog_value_updates_the_snapshot_without_writing_it(self):
        outcome = self.sync(self.decide(Choice.CATALOG))
        self.assertTrue(outcome.written)
        self.assertEqual(self.row("琵琶")["headcount_min"], "4")
        written = [cell for write in self.google.sheets.writes for cell, _ in write]
        self.assertNotIn("'分譜'!J4", written)
        self.assertEqual(self.group.headcounts[2], Headcount(4))
        self.assertEqual(dict(self.group.catalog_link.snapshot.voices)["琵琶"], Headcount(4))
        self.assertEqual(self.group.catalog_link.snapshot.duration_seconds, 430)
        self.assertEqual(self.project.catalog_changes(self.group), [])

    def test_keeping_mine_writes_it(self):
        self.sync(self.decide(Choice.MINE))
        self.assertEqual(self.row("琵琶")["headcount_min"], "3")
        self.assertEqual(self.group.headcounts[2], Headcount(3))
        self.assertEqual(self.project.catalog_changes(self.group), [])

    def test_another_computer_changing_a_cell_before_the_write_is_a_conflict_not_overwritten(self):
        self.google.before_request(
            self.google.requests + 1, lambda: self.google.sheets.set_cell(SHEET_DURATIONS, "D2", "500"),
        )
        outcome = self.sync(self.decide(Choice.MINE))
        self.assertFalse(outcome.written)
        self.assertEqual(
            [(r.item.field, r.theirs) for _, r in outcome.conflicts], [(SyncField.DURATION, 500)],
        )
        self.assertEqual(self.row("琵琶")["headcount_min"], "4")
        self.assertEqual(records(self.google, SHEET_DURATIONS)[0]["seconds"], "500")

    def test_a_choice_made_on_a_value_that_changed_again_is_asked_again(self):
        self.google.sheets.set_cell(SHEET_PARTS, "J4", "5")
        outcome = self.sync(self.decide(Choice.MINE))
        self.assertFalse(outcome.written)
        self.assertEqual([r.theirs for _, r in outcome.conflicts], [Headcount(5)])


class TestVoiceList(WriteBackTestCase):
    """聲部的增刪與順序：新增的接在分譜工作表最後，刪掉的清空那一列，順序寫進排序欄"""

    def test_added_voice_becomes_a_row_with_its_order_and_headcount(self):
        self.project.add_instrument(self.group, "中阮")
        self.project.set_headcount(self.group, 4, Headcount(2, 3))
        self.sync()
        row = self.row("中阮")
        self.assertEqual(
            (row["edition_id"], row["sort_order"], row["headcount_min"], row["headcount_max"]), ("e1", "5", "2", "3"),
        )
        self.assertEqual(len(row["id"]), 8)
        self.assertEqual(self.project.catalog_changes(self.group), [])

    def test_removed_voice_row_is_cleared_and_the_rest_keep_their_rows(self):
        self.project.remove_instruments(self.group, [1])
        self.sync()
        names = [r["instrument_name"] for r in records(self.google, SHEET_PARTS)]
        self.assertEqual(names, ["二胡 I", "", "琵琶", "揚琴"])
        self.assertEqual([self.row(v)["sort_order"] for v in ("二胡 I", "琵琶", "揚琴")], ["1", "3", "4"])
        self.assertEqual(self.catalog.read_place("e1", "m1", self.project.orchestra_type).voices[1][0], "琵琶")

    def test_moved_voices_rewrite_only_the_order_cells_that_changed(self):
        self.project.move_instruments(self.group, [3], 0)
        self.sync()
        place = self.catalog.read_place("e1", "m1", self.project.orchestra_type)
        self.assertEqual([name for name, _ in place.voices], ["揚琴", "二胡 I", "二胡 II", "琵琶"])
        part_cells = [cell for write in self.google.sheets.writes for cell, _ in write if SHEET_PARTS in cell]
        self.assertEqual(sorted(part_cells), ["'分譜'!E2", "'分譜'!E3", "'分譜'!E4", "'分譜'!E5"])

    def test_renamed_voice_is_removed_and_added(self):
        self.project.rename_instrument(self.group, 3, "中阮")
        self.sync()
        place = self.catalog.read_place("e1", "m1", self.project.orchestra_type)
        self.assertEqual([name for name, _ in place.voices], ["二胡 I", "二胡 II", "琵琶", "中阮"])

    def test_another_spelling_of_the_same_voice_is_not_written(self):
        self.project.rename_instrument(self.group, 0, "二胡1")
        self.assertEqual(place_writes(self.project), [])


class TestStructuralConflicts(WriteBackTestCase):
    """順序與聲部被別人改過時的選邊：用譜庫的就照譜庫調整群組，分譜跟著聲部"""

    def decide_all(self, outcome, choice: Choice) -> dict:
        """所有衝突都選同一邊"""
        return {self.group.id: {r.item.key: Decision(choice, r.theirs) for _, r in outcome.conflicts}}

    def reorder_elsewhere(self):
        """另一台電腦對調了二胡 I 與二胡 II；這邊把揚琴移到最前面"""
        self.google.sheets.set_cell(SHEET_PARTS, "E2", "2")
        self.google.sheets.set_cell(SHEET_PARTS, "E3", "1")
        self.project.move_instruments(self.group, [3], 0)

    def delete_part_elsewhere(self, index: int):
        """另一台電腦刪掉分譜工作表的第 index 列（從 0 起算，含標題列）"""
        rows = self.google.sheets.rows(SHEET_PARTS)
        self.google.sheets.create_tab(SHEET_PARTS, rows[:index] + rows[index + 1:])

    def test_order_changed_on_both_sides_is_a_conflict(self):
        self.reorder_elsewhere()
        outcome = self.sync()
        self.assertEqual([(r.item.field, r.theirs) for _, r in outcome.conflicts],
                         [(SyncField.ORDER, ("二胡 II", "二胡 I", "琵琶", "揚琴"))])

    def test_taking_the_catalog_order_reorders_the_group_and_writes_nothing(self):
        self.reorder_elsewhere()
        self.project.add_files([part_file(f"{n}.pdf") for n in ("揚琴", "二胡1", "二胡2", "琵琶")], self.group)
        outcome = self.sync(self.decide_all(self.sync(), Choice.CATALOG))
        self.assertTrue(outcome.written)
        self.assertEqual(self.google.sheets.writes, [])
        self.assertEqual(self.group.instruments, ["二胡 II", "二胡 I", "琵琶", "揚琴"])
        self.assertEqual(
            [p.file.display_name for p in self.group.pairing()], ["二胡2.pdf", "二胡1.pdf", "琵琶.pdf", "揚琴.pdf"],
        )
        self.assertEqual(self.project.catalog_changes(self.group), [])

    def test_keeping_my_order_writes_it(self):
        self.reorder_elsewhere()
        self.sync(self.decide_all(self.sync(), Choice.MINE))
        place = self.catalog.read_place("e1", "m1", self.project.orchestra_type)
        self.assertEqual([name for name, _ in place.voices], ["揚琴", "二胡 I", "二胡 II", "琵琶"])

    def test_voice_deleted_elsewhere_while_its_headcount_changed_here(self):
        self.delete_part_elsewhere(3)
        self.project.set_headcount(self.group, 2, Headcount(3))
        outcome = self.sync()
        self.assertEqual([(r.item.voice, r.theirs) for _, r in outcome.conflicts], [("琵琶", None)])
        self.sync(self.decide_all(outcome, Choice.MINE))
        self.assertEqual(self.row("琵琶")["headcount_min"], "3")

    def test_taking_the_deletion_removes_the_voice_from_the_group(self):
        self.project.add_files([part_file(f"{n}.pdf") for n in ("二胡1", "二胡2", "琵琶", "揚琴")], self.group)
        self.delete_part_elsewhere(3)
        self.project.set_headcount(self.group, 2, Headcount(3))
        self.sync(self.decide_all(self.sync(), Choice.CATALOG))
        self.assertEqual(self.group.instruments, ["二胡 I", "二胡 II", "揚琴"])
        self.assertEqual([f.display_name for f in self.project.ungrouped_files], ["琵琶.pdf"])
        self.assertEqual(self.project.catalog_changes(self.group), [])


class TestSections(WriteBackTestCase):
    """連到譜庫的群組改的聲部組寫進社團對照；聲部組旁的影響範圍數其他曲目"""

    def test_changed_section_is_written_to_the_club_mapping_keeping_its_spelling(self):
        self.project.set_section("琵琶", "低音")
        self.sync()
        club = [(r["orchestra_type"], r["instrument"], r["section"]) for r in records(self.google, SHEET_SECTIONS)]
        self.assertIn(("chinese", "琵琶", "低音"), club)
        self.assertEqual(len(club), 3)
        self.assertEqual(self.project.catalog_changes(self.group), [])

    def test_instrument_new_to_the_club_is_added_without_its_number(self):
        self.project.add_instrument(self.group, "中阮 I")
        self.project.add_instrument(self.group, "二胡 III")
        self.sync()
        club = [(r["instrument"], r["section"]) for r in records(self.google, SHEET_SECTIONS)]
        self.assertIn(("中阮", "彈撥"), club)
        self.assertEqual(len(club), 4)

    def test_counts_other_pieces_using_each_instrument(self):
        add_row(self.google, SHEET_PIECES, id="p2", title="將軍令")
        add_row(self.google, SHEET_PIECES, id="p3", title="金蛇狂舞")
        add_row(self.google, SHEET_EDITIONS, id="e2", piece_id="p2")
        add_row(self.google, SHEET_EDITIONS, id="e3", piece_id="p3")
        add_row(self.google, SHEET_EDITIONS, id="e1b", piece_id="p1")
        for n, (edition, voice) in enumerate((("e2", "琵琶"), ("e2", "柳琴"), ("e3", "琵琶 II"), ("e1b", "揚琴"))):
            add_row(self.google, SHEET_PARTS, id=f"x{n}", edition_id=edition, instrument_name=voice, sort_order="1")
        self.project.set_section("琵琶", "低音")
        self.sync()
        link = self.group.catalog_link
        self.assertEqual(link.other_pieces_using("琵琶"), 2)
        self.assertEqual(link.other_pieces_using("揚琴"), 0)
        self.assertEqual(link.other_pieces_using("二胡 II"), 0)


class TestVoicesWithDriveFiles(SyncTestCase):
    """已有 Drive 檔案的聲部不能從群組刪除；寫回時要刪掉它就標成無法寫回，其餘照寫"""

    def setUp(self):
        super().setUp()
        self.fill_catalog()

    def test_linking_remembers_the_voices_with_files_and_removing_them_is_refused(self):
        self.google.sheets.set_cell(SHEET_PARTS, "G5", "drive-9")
        self.link()
        self.assertEqual(self.group.catalog_link.snapshot.filed, ["揚琴"])
        with self.assertRaises(FiledVoicesError) as caught:
            self.project.remove_instruments(self.group, [2, 3])
        self.assertEqual(caught.exception.voices, ["揚琴"])
        with self.assertRaises(FiledVoicesError):
            self.project.rename_instrument(self.group, 3, "中阮")
        self.assertEqual(self.group.instruments, ["二胡 I", "二胡 II", "琵琶", "揚琴"])
        self.project.remove_instruments(self.group, [2])
        self.project.rename_instrument(self.group, 2, "揚 琴")

    def test_removing_a_voice_that_got_a_file_since_is_blocked_and_the_rest_is_written(self):
        self.link()
        self.google.sheets.set_cell(SHEET_PARTS, "G5", "drive-9")
        self.project.remove_instruments(self.group, [3])
        self.project.set_headcount(self.group, 2, Headcount(3))
        outcome = self.sync()
        self.assertTrue(outcome.written)
        self.assertEqual([r.item.voice for _, r in outcome.blocked], ["揚琴"])
        self.assertEqual(self.row("揚琴")["drive_file_id"], "drive-9")
        self.assertEqual(self.row("琵琶")["headcount_min"], "3")
        self.assertEqual(self.group.catalog_link.snapshot.filed, ["揚琴"])
        self.assertEqual(
            [c.kind for c in self.project.catalog_changes(self.group)], [CatalogChangeKind.VOICES],
        )


class TestWriteLog(WriteBackTestCase):
    """編制寫入紀錄：每一格改動一列，只新增、不修改"""

    def test_each_written_cell_appends_one_row_and_earlier_rows_stay(self):
        add_row(self.google, SHEET_INSTRUMENTATION_LOG, id="old1", time="2026-01-01 00:00:00", field="duration")
        self.project.set_headcount(self.group, 2, Headcount(3))
        self.project.set_duration(self.group, 430)
        self.sync()
        log = records(self.google, SHEET_INSTRUMENTATION_LOG)
        self.assertEqual(log[0]["id"], "old1")
        self.assertEqual(
            [(r["operator"], r["source"], r["edition_id"], r["movement_id"], r["voice"], r["field"], r["old_value"],
              r["new_value"], r["group"]) for r in log[1:]],
            [(OPERATOR, "save", "e1", "m1", "琵琶", "headcount", "2", "3", "江樓鐘鼓"),
             (OPERATOR, "save", "e1", "m1", "", "duration", "425", "430", "江樓鐘鼓")],
        )
        self.assertTrue(all(r["time"] for r in log[1:]))
        self.project.set_duration(self.group, 440)
        writes_before = len(self.google.sheets.writes)
        self.sync()
        cells = [cell for write in self.google.sheets.writes[writes_before:] for cell, _ in write]
        self.assertEqual(sorted(cells), ["'時長'!D2", "'編制寫入紀錄'!A5"])
        log_after = records(self.google, SHEET_INSTRUMENTATION_LOG)
        self.assertEqual(log_after[:3], log)
        self.assertEqual([(r["old_value"], r["new_value"]) for r in log_after[3:]], [("430", "440")])

    def test_cells_already_done_and_taken_from_the_catalog_are_not_logged(self):
        self.project.set_headcount(self.group, 2, Headcount(3))
        self.project.set_duration(self.group, 430)
        self.google.sheets.set_cell(SHEET_PARTS, "J4", "3")
        self.google.sheets.set_cell(SHEET_DURATIONS, "D2", "500")
        conflict = self.sync().conflicts[0][1]
        self.sync({self.group.id: {conflict.item.key: Decision(Choice.CATALOG, conflict.theirs)}})
        self.assertEqual(records(self.google, SHEET_INSTRUMENTATION_LOG), [])
        self.assertEqual(self.group.duration_seconds, 500)


class TestFirstWrite(SyncTestCase):
    """舊譜庫（沒有新欄與新工作表）第一次寫入時建立它們"""

    def setUp(self):
        super().setUp()
        for sheet in (SHEET_DURATIONS, SHEET_SECTIONS, SHEET_INSTRUMENTATION_LOG):
            self.google.sheets.delete_tab(sheet)
        old_headers = SHEET_HEADERS[SHEET_PARTS][:9]
        self.google.sheets.create_tab(SHEET_PARTS, [old_headers, ["pt1", "e1", "m1", "二胡 I", "1", "ok"]])

    def test_creates_the_new_sheets_and_columns_with_their_headers(self):
        self.link()
        self.project.set_headcount(self.group, 0, Headcount(6))
        self.project.set_duration(self.group, 300)
        outcome = self.sync()
        self.assertTrue(outcome.written)
        self.assertEqual(self.google.sheets.rows(SHEET_PARTS)[0], SHEET_HEADERS[SHEET_PARTS])
        self.assertEqual(self.google.sheets.rows(SHEET_PARTS)[1][5], "ok")
        for sheet in (SHEET_DURATIONS, SHEET_SECTIONS, SHEET_INSTRUMENTATION_LOG):
            self.assertEqual(self.google.sheets.rows(sheet)[0], SHEET_HEADERS[sheet])
        place = self.catalog.read_place("e1", "m1", self.project.orchestra_type)
        self.assertEqual((place.voices, place.duration_seconds), ([("二胡 I", Headcount(6))], 300))
        self.assertEqual(place.sections, {"二胡": "拉弦"})
        self.assertEqual(len(records(self.google, SHEET_INSTRUMENTATION_LOG)), 3)

    def test_sending_again_after_a_failed_write_finishes_without_duplicates(self):
        self.link()
        self.project.set_duration(self.group, 300)
        self.google.fail_at(self.google.requests + 6, CatalogErrorKind.OFFLINE)
        with self.assertRaises(CatalogError):
            self.sync()
        self.sync()
        self.assertEqual(len(records(self.google, SHEET_DURATIONS)), 1)
        self.assertEqual(self.google.sheets.rows(SHEET_DURATIONS)[0], SHEET_HEADERS[SHEET_DURATIONS])
        self.assertEqual(self.project.catalog_changes(self.group), [])


class TestRequests(LinkTestCase):
    """一次存檔的所有格子一個寫入請求；請求數不隨聲部數增加"""

    def requests_to_write(self, count: int, movement_id: str) -> int:
        """把 count 個聲部（都有人數）與時長寫進譜庫空的那處（第一樂章或整首），回傳用了幾次請求"""
        project = Project()
        group = project.add_group("江樓鐘鼓", "總譜")
        project.set_instruments(group, [f"聲部{n}" for n in range(count)])
        for index in range(count):
            project.set_headcount(group, index, Headcount(index + 1))
        project.set_duration(group, 300)
        project.link_group(group, self.catalog.read_place("e1", movement_id, project.orchestra_type))
        before = self.google.requests
        writes_before = len(self.google.sheets.writes)
        outcome = self.catalog.sync_instrumentation(
            place_writes(project), project.orchestra_type, OPERATOR, WriteSource.SAVE, {},
        )
        self.assertTrue(outcome.written)
        self.assertEqual(len(self.google.sheets.writes) - writes_before, 1)
        return self.google.requests - before

    def test_three_and_thirty_voices_take_the_same_number_of_requests(self):
        self.assertEqual(self.requests_to_write(3, "m1"), self.requests_to_write(30, ""))
        self.assertEqual(len(records(self.google, SHEET_PARTS)), 33)


if __name__ == "__main__":
    unittest.main()
