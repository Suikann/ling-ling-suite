# -*- coding: utf-8 -*-
"""
譜庫的發譜紀錄測試

對假 Google 測：每次發譜先寫「開始發譜」、結束後補「發譜結果」，兩者以同一個發譜編號串起來，每個群組一列；
只新增、不修改，每次寫入的請求數固定；寫不進去時一列都沒寫，讀回來與寫進去的相同。
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.catalog_constants import SHEET_HEADERS, SHEET_PUBLISH_LOG
from core.catalog_errors import CatalogError, CatalogErrorKind
from core.publish_log import (
    PublishAction, PublishRecord, due_reminders, publish_entries, published_pieces, reminder_summary,
)
from fake_google_catalog import FakeGoogle
from services.catalog_service import CatalogService
from test_catalog_service import empty_catalog


def start(group: str, piece: str = "hx-1") -> PublishRecord:
    """一個群組的「開始發譜」：要傳一份、要刪一份、要掛一場"""
    return PublishRecord(
        PublishAction.START, group_id=f"g-{group}", group=group, piece_id=piece, piece_title="長城隨想－烽火操",
        parts_created=[{"name": "琵琶", "needed": "2", "needed_min": 2, "section": "彈撥", "sort": 3}],
        parts_updated=[{"id": "p-1", "name": "二胡1", "needed": ["6", "6-8"], "needed_min": [6, 6]}],
        files_uploaded=[f"lingling/{piece}/{'a' * 64}-score.pdf"],
        scores_removed=[{"id": "s-1", "part_id": "p-1", "file_path": f"lingling/{piece}/{'b' * 64}-score.pdf"}],
        files_deleted=[f"lingling/{piece}/{'b' * 64}-score.pdf"],
        events=[{"id": "e-1", "title": "期末音樂會", "ends_at": "2026-12-20T21:00:00+08:00"}],
    )


class PublishLogTestCase(unittest.TestCase):
    """假 Google 上只有標題列的譜庫試算表"""

    def setUp(self):
        self.google = FakeGoogle()
        empty_catalog(self.google)
        self.catalog = CatalogService(self.google.sheets, self.google.drive, sleep=lambda seconds: None)

    def rows(self):
        """發譜紀錄的資料列（以欄位名稱為鍵）"""
        headers = SHEET_HEADERS[SHEET_PUBLISH_LOG]
        return [dict(zip(headers, row + [""] * len(headers))) for row in self.google.sheets.rows(SHEET_PUBLISH_LOG)[1:]]


class TestStartAndResult(PublishLogTestCase):
    """「開始發譜」與「發譜結果」以發譜編號串起來，每個群組一列"""

    def test_start_and_result_rows_share_the_publish_id(self):
        publish_id = self.catalog.begin_publish([start("烽火操"), start("春江花月夜", "hx-2")], "officer_amy")
        result = PublishRecord(
            PublishAction.RESULT, group_id="g-烽火操", group="烽火操", piece_id="hx-1",
            failure={"stage": "switch", "code": "PT404", "detail": "找不到聲部"},
        )
        self.catalog.record_publish(publish_id, [result], "officer_amy")
        rows = self.rows()
        self.assertEqual([row["publish_id"] for row in rows], [publish_id] * 3)
        self.assertEqual([row["action"] for row in rows], ["start", "start", "result"])
        self.assertEqual([row["operator"] for row in rows], ["officer_amy"] * 3)
        self.assertEqual(len({row["id"] for row in rows}), 3)

    def test_each_publish_gets_its_own_id_and_rows_are_only_appended(self):
        first = self.catalog.begin_publish([start("烽火操")], "officer_amy")
        before = self.google.sheets.rows(SHEET_PUBLISH_LOG)
        second = self.catalog.begin_publish([start("烽火操")], "officer_amy")
        self.assertNotEqual(first, second)
        self.assertEqual(self.google.sheets.rows(SHEET_PUBLISH_LOG)[:len(before)], before)

    def test_what_is_written_reads_back(self):
        publish_id = self.catalog.begin_publish([start("烽火操")], "officer_amy")
        [entry] = self.catalog.publish_log()
        self.assertEqual((entry.publish_id, entry.operator), (publish_id, "officer_amy"))
        self.assertTrue(entry.time)
        expected = start("烽火操")
        self.assertEqual(
            entry, PublishRecord(**{**expected.__dict__, "publish_id": publish_id, "time": entry.time,
                                    "operator": "officer_amy"}),
        )

    def test_the_sheet_is_created_when_missing(self):
        self.google.sheets.delete_tab(SHEET_PUBLISH_LOG)
        self.assertEqual(self.catalog.publish_log(), [])
        self.catalog.begin_publish([start("烽火操")], "officer_amy")
        self.assertEqual(self.google.sheets.rows(SHEET_PUBLISH_LOG)[0], SHEET_HEADERS[SHEET_PUBLISH_LOG])
        self.assertEqual(len(self.rows()), 1)


class TestAnotherComputerWritingAtTheSameTime(PublishLogTestCase):
    """發譜紀錄只新增：別台電腦（或背景的發譜與譜庫視窗的撤回）在讀與寫之間新增的列不會被蓋掉"""

    def test_rows_added_between_reading_and_writing_are_kept(self):
        other = ["x-1", "p-other", "2026-10-19 10:00:00", "officer_bob", "start"]
        self.google.sheets.before_each_write(
            lambda: self.google.sheets.append_by_another_computer(SHEET_PUBLISH_LOG, other),
        )
        publish_id = self.catalog.begin_publish([start("烽火操")], "officer_amy")
        self.catalog.record_publish(publish_id, [PublishRecord(PublishAction.RESULT, group="烽火操")], "officer_amy")
        rows = self.rows()
        self.assertEqual([row["publish_id"] for row in rows if row["id"] == "x-1"], ["p-other", "p-other"])
        self.assertEqual([row["action"] for row in rows if row["publish_id"] == publish_id], ["start", "result"])


class TestFixedRequests(PublishLogTestCase):
    """寫入的請求數不隨群組數增加；寫不進去時一列都沒寫"""

    def test_requests_are_the_same_for_one_and_ten_groups(self):
        counts = []
        for size in (1, 10):
            before = self.google.sheets_requests
            publish_id = self.catalog.begin_publish([start(f"群組{n}") for n in range(size)], "officer_amy")
            self.catalog.record_publish(
                publish_id, [PublishRecord(PublishAction.RESULT, group=f"群組{n}") for n in range(size)], "officer_amy",
            )
            counts.append(self.google.sheets_requests - before)
        self.assertEqual(counts[0], counts[1])

    def test_a_failed_start_writes_nothing(self):
        # 第 3 個請求是寫入（之前是列出工作表名稱與讀取）
        self.google.fail_at(self.google.requests + 3, CatalogErrorKind.OFFLINE)
        with self.assertRaises(CatalogError):
            self.catalog.begin_publish([start("烽火操")], "officer_amy")
        self.assertEqual(self.rows(), [])


class TestEntries(unittest.TestCase):
    """發譜紀錄的列表：一次發譜一項（有結果時列結果），同一次寫入的撤回一項，新的在前"""

    @staticmethod
    def row(action: PublishAction, publish_id: str, time: str, group: str, failure=None) -> PublishRecord:
        """讀回來的一列"""
        return PublishRecord(
            action, group_id=f"g-{group}", group=group, publish_id=publish_id, time=time, operator="officer_amy",
            failure=failure,
        )

    def test_publishes_and_retracts_are_listed_newest_first(self):
        rows = [
            self.row(PublishAction.START, "P1", "10/05 22:10", "烽火操"),
            self.row(PublishAction.START, "P1", "10/05 22:10", "茉莉花"),
            self.row(PublishAction.RESULT, "P1", "10/05 22:12", "烽火操"),
            self.row(PublishAction.RESULT, "P1", "10/05 22:12", "茉莉花", failure={"stage": "switch"}),
            self.row(PublishAction.START, "P2", "10/06 09:00", "春江花月夜"),
            self.row(PublishAction.RETRACT, "P1", "10/07 08:00", "烽火操"),
        ]
        entries = publish_entries(rows)
        self.assertEqual(
            [(e.publish_id, e.action, e.time, e.finished) for e in entries],
            [("P1", PublishAction.RETRACT, "10/07 08:00", True), ("P2", PublishAction.START, "10/06 09:00", False),
             ("P1", PublishAction.START, "10/05 22:10", True)],
        )
        self.assertEqual([r.action for r in entries[2].groups], [PublishAction.RESULT, PublishAction.RESULT])
        self.assertEqual([r.action for r in entries[1].groups], [PublishAction.START])

    def test_orphan_cleanups_are_not_listed_as_entries(self):
        rows = [
            self.row(PublishAction.START, "P1", "10/05 22:10", "烽火操"),
            self.row(PublishAction.CLEAR_ORPHANS, "P1", "10/05 22:12", "", failure={"code": "x"}),
            self.row(PublishAction.RESULT, "P1", "10/05 22:12", "烽火操"),
        ]
        [entry] = publish_entries(rows)
        self.assertEqual((entry.action, [r.action for r in entry.groups]), (PublishAction.START, [PublishAction.RESULT]))

    def test_retracts_written_at_different_times_are_separate(self):
        rows = [
            self.row(PublishAction.START, "P1", "10/05 22:10", "烽火操"),
            self.row(PublishAction.RETRACT, "P1", "10/07 08:00", "烽火操"),
            self.row(PublishAction.RETRACT, "P1", "10/08 08:00", "烽火操"),
        ]
        self.assertEqual([e.action for e in publish_entries(rows)], [PublishAction.RETRACT] * 2 + [PublishAction.START])

    def test_a_retract_row_reads_back(self):
        google = FakeGoogle()
        empty_catalog(google)
        catalog = CatalogService(google.sheets, google.drive, sleep=lambda seconds: None)
        publish_id = catalog.begin_publish([start("烽火操")], "officer_amy")
        retract = PublishRecord(
            PublishAction.RETRACT, group_id="g-烽火操", group="烽火操", piece_id="hx-1",
            parts_deleted=[{"id": "p-9", "name": "排鼓"}],
        )
        catalog.record_publish(publish_id, [retract], "officer_amy")
        [_, row] = catalog.publish_log()
        self.assertEqual((row.action, row.publish_id, row.parts_deleted), (
            PublishAction.RETRACT, publish_id, [{"id": "p-9", "name": "排鼓"}],
        ))



class TestPublishedPieces(unittest.TestCase):
    """發過哪些曲目：整次發譜的群組都撤回了的那首，HuaXia 上已經沒有泠靈的譜，不算發過"""

    @staticmethod
    def row(action: PublishAction, publish_id: str, group: str, piece: str = "hx-1", events=(), failure=None):
        """讀回來的一列（曲目 piece，events 是掛的公演的結束時間）"""
        return PublishRecord(
            action, group_id=f"g-{group}", group=group, piece_id=piece, piece_title=f"曲目{piece}",
            events=[{"id": f"e-{end}", "title": "公演", "ends_at": end} for end in events],
            publish_id=publish_id, failure=failure,
        )

    def published(self, rows):
        """發過的曲目編號"""
        return [piece.piece_id for piece in published_pieces(rows)]

    def test_a_piece_whose_every_group_was_retracted_is_not_published(self):
        rows = [
            self.row(PublishAction.START, "P1", "烽火操"),
            self.row(PublishAction.START, "P1", "烽火操二"),
            self.row(PublishAction.RESULT, "P1", "烽火操"),
            self.row(PublishAction.RESULT, "P1", "烽火操二"),
            self.row(PublishAction.RETRACT, "P1", "烽火操"),
        ]
        self.assertEqual(self.published(rows), ["hx-1"])
        rows.append(self.row(PublishAction.RETRACT, "P1", "烽火操二"))
        self.assertEqual(self.published(rows), [])

    def test_a_retract_that_stopped_partway_leaves_the_piece_published(self):
        rows = [
            self.row(PublishAction.START, "P1", "烽火操"),
            self.row(PublishAction.RESULT, "P1", "烽火操"),
            self.row(PublishAction.RETRACT, "P1", "烽火操", failure={"code": "network"}),
        ]
        self.assertEqual(self.published(rows), ["hx-1"])

    def test_an_earlier_publish_still_counts_after_the_last_one_is_retracted(self):
        rows = [
            self.row(PublishAction.START, "P1", "烽火操"),
            self.row(PublishAction.RESULT, "P1", "烽火操"),
            self.row(PublishAction.KEEP, "K1", "", events=["2026-12-20T21:00:00+08:00"]),
            self.row(PublishAction.START, "P2", "烽火操"),
            self.row(PublishAction.RESULT, "P2", "烽火操"),
            self.row(PublishAction.RETRACT, "P2", "烽火操"),
        ]
        [piece] = published_pieces(rows)
        self.assertTrue(piece.kept)



ENDED = "2026-06-20T21:00:00+08:00"
LATER = "2026-12-20T21:00:00+08:00"
NOW = datetime.datetime(2026, 10, 19, tzinfo=datetime.timezone.utc)


class TestReminder(unittest.TestCase):
    """收回提醒：依本機摘要，掛的公演都結束、沒收回、沒標不收回的曲目才提醒"""

    row = staticmethod(TestPublishedPieces.row)

    def publish(self, publish_id: str, piece: str = "hx-1", planned=(), attached=(), failure=None):
        """一次發譜的兩列：開始發譜要掛 planned，發譜結果新掛上 attached"""
        group = f"群組{piece}"
        return [
            self.row(PublishAction.START, publish_id, group, piece, events=planned),
            self.row(PublishAction.RESULT, publish_id, group, piece, events=attached, failure=failure),
        ]

    def due(self, rows):
        """以摘要判斷現在要提醒的曲名"""
        return due_reminders(reminder_summary(rows), NOW)

    def test_a_piece_whose_concerts_are_all_over_is_reminded(self):
        self.assertEqual(self.due(self.publish("P1", planned=[ENDED], attached=[ENDED])), ["曲目hx-1"])

    def test_a_piece_still_on_a_concert_to_come_is_not_reminded(self):
        rows = self.publish("P1", planned=[ENDED], attached=[ENDED]) + self.publish("P2", planned=[LATER])
        self.assertEqual(self.due(rows), [])

    def test_a_piece_on_no_concert_is_not_reminded(self):
        self.assertEqual(self.due(self.publish("P1")), [])

    def test_kept_or_recalled_pieces_are_not_reminded(self):
        rows = self.publish("P1", planned=[ENDED], attached=[ENDED])
        self.assertEqual(self.due(rows + [self.row(PublishAction.KEEP, "K1", "", events=[ENDED])]), [])
        self.assertEqual(self.due(rows + [self.row(PublishAction.RECALL, "R1", "", events=[ENDED])]), [])

    def test_a_retracted_piece_is_not_reminded(self):
        rows = self.publish("P1", planned=[ENDED], attached=[ENDED]) + [
            self.row(PublishAction.RETRACT, "P1", "群組hx-1"),
        ]
        self.assertEqual(self.due(rows), [])

    def test_a_concert_the_piece_was_already_on_counts(self):
        rows = self.publish("P1", planned=[ENDED], attached=[ENDED]) + self.publish("P2", planned=[LATER], attached=[])
        self.assertEqual(self.due(rows), [])

    def test_a_failed_groups_concerts_do_not_count(self):
        rows = self.publish("P1", planned=[ENDED], attached=[ENDED]) + self.publish(
            "P2", planned=[LATER], failure={"stage": "switch"},
        )
        self.assertEqual(self.due(rows), ["曲目hx-1"])

    def test_concerts_seen_when_recalling_or_keeping_count(self):
        rows = self.publish("P1", planned=[ENDED], attached=[ENDED]) + [
            self.row(PublishAction.KEEP, "K1", "", events=[ENDED, LATER]),
        ] + self.publish("P2")
        self.assertEqual(self.due(rows), [])

    def test_the_summary_holds_only_pieces_still_to_recall(self):
        rows = self.publish("P1", "hx-1", planned=[ENDED]) + self.publish("P2", "hx-2") + [
            self.row(PublishAction.KEEP, "K1", "", "hx-2"),
        ]
        self.assertEqual(reminder_summary(rows), [{"piece_id": "hx-1", "piece_title": "曲目hx-1", "ends_at": [ENDED]}])

    def test_a_damaged_summary_reminds_nothing(self):
        summary = [
            "壞掉", {"piece_title": "沒有時間"}, {"piece_title": "時間壞掉", "ends_at": ["明天"]},
            {"piece_title": "沒有時區", "ends_at": ["2026-06-20T21:00:00"]}, {"ends_at": [ENDED]},
        ]
        self.assertEqual(due_reminders(summary, NOW), [])
        self.assertEqual(due_reminders("壞掉", NOW), [])


if __name__ == '__main__':
    unittest.main()
