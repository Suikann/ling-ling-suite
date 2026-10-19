# -*- coding: utf-8 -*-
"""
譜庫的發譜紀錄測試

對假 Google 測：每次發譜先寫「開始發譜」、結束後補「發譜結果」，兩者以同一個發譜編號串起來，每個群組一列；
只新增、不修改，每次寫入的請求數固定；寫不進去時一列都沒寫，讀回來與寫進去的相同。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.catalog_constants import SHEET_HEADERS, SHEET_PUBLISH_LOG
from core.catalog_errors import CatalogError, CatalogErrorKind
from core.publish_log import PublishAction, PublishRecord, publish_entries
from fake_google_catalog import FakeGoogle
from services.catalog_service import CatalogService
from test_catalog_service import empty_catalog


def start(group: str, piece: str = "hx-1") -> PublishRecord:
    """一個群組的「開始發譜」：要傳一份、要刪一份、要掛一場"""
    return PublishRecord(
        PublishAction.START, group_id=f"g-{group}", group=group, piece_id=piece, piece_title="長城隨想－烽火操",
        parts_created=[{"name": "琵琶", "needed": "2", "needed_min": 2, "section": "彈撥", "sort": 3}],
        parts_updated=[{"id": "p-1", "name": "二胡1", "needed": ["6", "6-8"], "needed_min": [6, 6]}],
        files_uploaded=[f"lingling/{piece}/{'a' * 64}.pdf"],
        scores_removed=[{"id": "s-1", "part_id": "p-1", "file_path": f"lingling/{piece}/{'b' * 64}.pdf"}],
        files_deleted=[f"lingling/{piece}/{'b' * 64}.pdf"],
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


if __name__ == '__main__':
    unittest.main()
