# -*- coding: utf-8 -*-
"""
收回的測試

對假 HuaXia、假 Google 與臨時目錄裡的檔，先照常發一次譜（PublishRunner），公演結束後從譜庫的發譜紀錄挑曲目收回
（services/recall_service.py 的 RecallService）：刪掉曲目在 HuaXia 上的所有樂譜，包括別人放的；聲部、曲目、場次與排人
都留著；收回前清掉那首的孤兒檔；還掛在未結束公演上的曲目被標出、預設不勾；收回與不收回各寫一列到發譜紀錄。
"""
import copy
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.publish_log import PublishAction, publish_entries
from services.huaxia_client import lingling_storage_path
from services.recall_service import RecallService
from test_publish_run import TwoPiecesTestCase, sha
from test_publish_retract import RetractTestCase


class RecallTestCase(RetractTestCase):
    """RetractTestCase 的「烽火操」發完、掛的「期末音樂會」結束十天後：

    HuaXia 上那首有泠靈發的總譜、二胡 I、琵琶、排鼓、高胡（高胡排了人），與聲部長在琵琶放的弓法版（網頁上傳的）。
    """

    def setUp(self):
        super().setUp()
        concert = next(event for event in self.huaxia.tables["events"] if event["id"] == self.concert)
        self.now = datetime.datetime.fromisoformat(concert["ends_at"]) + datetime.timedelta(days=10)
        self.recaller = RecallService(self.auth, now=lambda: self.now)

    def piece(self, piece_id: str = ""):
        """收回預覽裡這首（預設烽火操）"""
        piece_id = piece_id or self.fenghuo
        return next(piece for piece in self.recaller.preview(self.catalog).pieces if piece.piece_id == piece_id)

    def listed(self):
        """收回預覽裡的曲目編號"""
        return [piece.piece_id for piece in self.recaller.preview(self.catalog).pieces]

    def still_playing(self, days: int = 5) -> str:
        """幹部把烽火操也掛上 days 天後的「校慶音樂會」：回傳場次編號"""
        start = self.now + datetime.timedelta(days=days)
        event = self.huaxia.add_event(None, "校慶音樂會", start, start + datetime.timedelta(hours=3))
        self.huaxia.attach_piece(event, self.fenghuo)
        return event


class TestRecalling(RecallTestCase):
    """收回刪掉曲目的所有樂譜，包括別人放的；聲部、曲目、場次與排人都留著"""

    def test_every_score_goes_including_others_and_everything_else_stays(self):
        kept = {table: copy.deepcopy(self.huaxia.tables[table]) for table in (
            "pieces", "piece_parts", "part_assignments", "event_pieces", "events",
        )}
        result = self.recaller.recall([self.piece()], self.catalog)
        self.assertIsNone(result.error)
        self.assertEqual(self.scores(self.fenghuo), [])
        self.assertEqual(sorted(self.huaxia.objects), [])
        for table, rows in kept.items():
            self.assertEqual(self.huaxia.tables[table], rows, table)

    def test_a_file_another_piece_still_registers_is_not_deleted(self):
        self.huaxia.add_score(self.moli, None, self.bowing, title="借用的弓法版")
        self.recaller.recall([self.piece()], self.catalog)
        self.assertEqual(self.scores(self.fenghuo), [])
        self.assertEqual(sorted(self.huaxia.objects), [self.bowing])

    def test_one_recall_row_per_piece_is_written_under_a_new_publish_id(self):
        piece = self.piece()
        result = self.recaller.recall([piece], self.catalog)
        [row] = self.log(PublishAction.RECALL)
        self.assertNotEqual(row.publish_id, self.outcome.publish_id)
        self.assertEqual((row.piece_id, row.piece_title, row.operator), (self.fenghuo, "長城隨想－烽火操", "officer_amy"))
        self.assertEqual({s["id"] for s in row.scores_removed}, {s.id for s in piece.scores})
        self.assertIn(self.bowing_score, {s["id"] for s in row.scores_removed})
        self.assertEqual(sorted(row.files_deleted), sorted({s.file_path for s in piece.scores}))
        self.assertEqual([event["id"] for event in row.events], [self.concert])
        self.assertIsNone(row.failure)
        self.assertEqual(result.recalled, ["長城隨想－烽火操"])
        [entry] = [e for e in publish_entries(self.catalog.publish_log()) if e.action is PublishAction.RECALL]
        self.assertEqual([record.piece_id for record in entry.groups], [self.fenghuo])

    def test_someone_no_longer_an_officer_changes_nothing(self):
        piece = self.piece()
        before = self.tables()
        self.huaxia.remove_roles("officer_amy")
        result = self.recaller.recall([piece], self.catalog)
        self.assertIsNotNone(result.not_officer)
        self.assertEqual(self.tables()["scores"], before["scores"])
        self.assertEqual(self.log(PublishAction.RECALL), [])


class TestOrphans(RecallTestCase):
    """收回前以孤兒檔清理清一次那首的：只清沒有登記的泠靈檔，與「收回」一起寫進發譜紀錄"""

    def test_the_pieces_orphans_are_cleared_before_recalling(self):
        orphan = lingling_storage_path(self.fenghuo, sha(b"orphan"), "score")
        self.huaxia.put_object(orphan, b"orphan")
        self.recaller.recall([self.piece()], self.catalog)
        self.assertEqual(sorted(self.huaxia.objects), [])
        [cleared] = self.log(PublishAction.CLEAR_ORPHANS)
        self.assertEqual((cleared.piece_id, cleared.files_deleted), (self.fenghuo, [orphan]))
        [row] = self.log(PublishAction.RECALL)
        self.assertEqual(cleared.publish_id, row.publish_id)
        self.assertNotIn(orphan, row.files_deleted)


class TestInterrupted(RecallTestCase):
    """收回中途停下：已撤的不會回來，沒刪成的檔記在那列，再收回一次補刪"""

    def test_stopping_before_the_files_are_deleted_converges_on_recalling_again(self):
        self.huaxia.drop_requests("DELETE", "/storage/v1/object/scores")
        first = self.recaller.recall([self.piece()], self.catalog)
        self.assertIsNotNone(first.error)
        self.assertEqual(self.scores(self.fenghuo), [])
        self.assertIn(self.bowing, self.huaxia.objects)
        [stopped] = self.log(PublishAction.RECALL)
        self.assertTrue(stopped.failure)
        self.assertEqual(stopped.files_deleted, [])
        piece = self.piece()
        self.assertIn(self.bowing, piece.leftovers)
        again = self.recaller.recall([piece], self.catalog)
        self.assertIsNone(again.error)
        self.assertEqual(sorted(self.huaxia.objects), [])
        self.assertNotIn(self.fenghuo, self.listed())


class TestRefusedDeletion(RecallTestCase):
    """Storage 刪不掉的檔（回應照樣成功）不算刪掉：記在撤掉的登記而不在刪掉的檔，再收回一次補刪"""

    def test_a_file_storage_refuses_to_delete_is_left_for_the_next_recall(self):
        self.huaxia.refuse_storage_deletes(self.bowing)
        self.recaller.recall([self.piece()], self.catalog)
        [row] = self.log(PublishAction.RECALL)
        self.assertNotIn(self.bowing, row.files_deleted)
        self.assertIn(self.bowing, self.piece().leftovers)


class TestPreview(RecallTestCase):
    """預覽讀發譜紀錄裡發過的曲目，再向 HuaXia 查樂譜與掛的公演"""

    def test_a_piece_whose_concerts_are_over_is_suggested_under_that_concert(self):
        preview = self.recaller.preview(self.catalog)
        [(concert, pieces)] = preview.groups()
        self.assertEqual((concert.id, [piece.piece_id for piece in pieces]), (self.concert, [self.fenghuo]))
        [piece] = pieces
        self.assertTrue(piece.suggested)
        self.assertEqual(piece.upcoming, [])

    def test_scores_others_put_come_first(self):
        piece = self.piece()
        self.assertEqual(piece.scores[0].id, self.bowing_score)
        self.assertEqual([score.id for score in piece.others], [self.bowing_score])
        self.assertEqual(piece.voice(piece.scores[0]), "琵琶")
        self.assertEqual(len(piece.scores), 6)

    def test_a_piece_still_on_a_concert_to_come_is_marked_and_not_suggested(self):
        later = self.still_playing(days=5)
        piece = self.piece()
        self.assertEqual([event.id for event in piece.upcoming], [later])
        self.assertEqual(piece.concert.id, self.concert)
        self.assertFalse(piece.suggested)

    def test_a_piece_on_no_concert_is_listed_last_and_not_suggested(self):
        self.huaxia.tables["event_pieces"] = []
        preview = self.recaller.preview(self.catalog)
        self.assertEqual(preview.groups(), [(None, preview.pieces)])
        self.assertFalse(preview.pieces[0].suggested)

    def test_a_rehearsal_is_not_a_concert(self):
        start = self.now + datetime.timedelta(days=3)
        rehearsal = self.huaxia.add_event(None, "排練", start, start, kinds=("rehearsal",))
        self.huaxia.attach_piece(rehearsal, self.fenghuo)
        self.assertTrue(self.piece().suggested)

    def test_a_piece_whose_publish_was_retracted_is_not_listed(self):
        self.retracter.retract(self.entry(), [self.group.id], self.catalog)
        self.assertNotIn(self.fenghuo, self.listed())

    def test_a_recalled_piece_is_not_listed_until_it_is_published_again(self):
        self.recaller.recall([self.piece()], self.catalog)
        self.assertNotIn(self.fenghuo, self.listed())
        self.run_publish(self.press_publish())
        self.assertIn(self.fenghuo, self.listed())


class TestKeep(RecallTestCase):
    """「勾選的不收回」寫進發譜紀錄，之後不再建議收回；不動 HuaXia"""

    def test_keeping_writes_a_row_and_the_piece_is_no_longer_suggested(self):
        before = self.tables()
        self.recaller.keep([self.piece()], self.catalog)
        self.assertEqual(self.tables(), before)
        [row] = self.log(PublishAction.KEEP)
        self.assertEqual((row.piece_id, row.piece_title, row.operator), (self.fenghuo, "長城隨想－烽火操", "officer_amy"))
        self.assertEqual([event["id"] for event in row.events], [self.concert])
        piece = self.piece()
        self.assertTrue(piece.kept)
        self.assertFalse(piece.suggested)

    def test_publishing_again_after_keeping_suggests_it_again(self):
        self.recaller.keep([self.piece()], self.catalog)
        self.run_publish(self.press_publish())
        self.assertFalse(self.piece().kept)


class TestTwoPieces(TwoPiecesTestCase):
    """兩首一起發到期末音樂會，之後只有茉莉花也掛上還沒到的校慶音樂會"""

    def test_each_piece_is_judged_by_its_own_concerts(self):
        self.run_publish(self.press_publish(events=[self.concert]))
        concert = next(event for event in self.huaxia.tables["events"] if event["id"] == self.concert)
        now = datetime.datetime.fromisoformat(concert["ends_at"]) + datetime.timedelta(days=1)
        later = self.huaxia.add_event(None, "校慶音樂會", now + datetime.timedelta(days=9), now + datetime.timedelta(days=9))
        self.huaxia.attach_piece(later, self.moli)
        preview = RecallService(self.auth, now=lambda: now).preview(self.catalog)
        suggested = {piece.piece_title: piece.suggested for piece in preview.pieces}
        self.assertEqual(suggested, {"長城隨想－烽火操": True, "茉莉花": False})
        [(event, pieces)] = preview.groups()
        self.assertEqual(event.id, self.concert)
        self.assertEqual(len(pieces), 2)


if __name__ == "__main__":
    unittest.main()
