# -*- coding: utf-8 -*-
"""
撤回的測試

對假 HuaXia、假 Google 與臨時目錄裡的檔，先照常發一次譜（PublishRunner），再從譜庫的發譜紀錄撤回那次發譜選定的群組
（services/retract_service.py 的 RetractService）：只撤那次登記的分譜與總譜，連同那次新建、還沒排人的聲部；
被取代的舊檔不會回來，人數與聲部組不改回，場次不動；撤回後沒有譜的聲部由預覽推算出來；中斷後重做會收斂；
撤回寫一列「撤回」到發譜紀錄。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.models import FileInfo, Headcount
from core.publish_log import PublishAction, publish_entries
from services.retract_service import RetractEffect, RetractReason, RetractService
from core.constants import HuaXiaEnsemble
from core.models import HuaXiaLink, NewHuaXiaPiece
from test_publish_run import RunTestCase, TwoPiecesTestCase


class RetractTestCase(RunTestCase):
    """「烽火操」（總譜、二胡 I、琵琶、排鼓、高胡）發到 HuaXia 的「長城隨想－烽火操」並掛上公演：

    二胡 I 取代了泠靈上次發的舊檔，排鼓與高胡是這次新建的聲部。發完之後幹部在網頁上把一位團員排進高胡，
    聲部長在琵琶放了一份弓法版。
    """

    def setUp(self):
        super().setUp()
        self.project.set_instruments(self.group, ["二胡 I", "琵琶", "排鼓", "高胡"])
        self.add_voice_files(self.group, ["排鼓", "高胡"], b"fenghuo")
        self.outcome = self.run_publish(self.press_publish(events=[self.concert]))
        self.assertEqual(self.outcome.failed, [])
        created = {part["name"]: part["id"] for part in self.outcome.groups[0].result["created_parts"]}
        self.paigu, self.gaohu = created["排鼓"], created["高胡"]
        member = self.huaxia.add_account("member_bob", roles=("member",))
        self.huaxia.assign(self.gaohu, member)
        self.bowing = "%s/%s/1700000000_pipa_bowing.pdf" % (self.fenghuo, self.pipa)
        self.huaxia.put_object(self.bowing, b"bowing")
        self.bowing_score = self.huaxia.add_score(self.fenghuo, self.pipa, self.bowing, title="琵琶_弓法")
        self.retracter = RetractService(self.auth)

    def add_voice_files(self, group, voices, content: bytes):
        """幫群組裡這幾個聲部各放一份已重新命名的分譜（檔的內容是 content 加上檔名）"""
        paths = []
        for voice in voices:
            index = group.instruments.index(voice) + 1
            path = os.path.join(self.out, f"{group.name}-{index:02d}-{voice}.pdf")
            with open(path, "wb") as f:
                f.write(content + os.path.basename(path).encode("utf-8"))
            paths.append(path)
        self.project.add_files([FileInfo(path, os.path.basename(path)) for path in paths], group)
        for voice in voices:
            self.project.set_headcount(group, group.instruments.index(voice), Headcount(2))

    def entry(self):
        """發譜紀錄裡這次發譜的那一項"""
        return next(
            entry for entry in publish_entries(self.catalog.publish_log())
            if entry.publish_id == self.outcome.publish_id and entry.action is PublishAction.START
        )

    def parts(self):
        """這首曲目現在的聲部：名稱對應聲部那一列"""
        return {row["name"]: row for row in self.huaxia.tables["piece_parts"] if row["piece_id"] == self.fenghuo}


class TestRetracting(RetractTestCase):
    """撤回只撤那次的檔與那次新建、沒排人的聲部"""

    def test_only_that_publishs_files_and_its_unassigned_new_parts_are_removed(self):
        result = self.retracter.retract(self.entry(), [self.group.id], self.catalog)
        self.assertIsNone(result.error)
        self.assertEqual(self.scores(self.fenghuo), [(self.pipa, self.bowing)])
        self.assertEqual(sorted(self.parts()), ["二胡1", "琵琶", "高胡"])
        self.assertEqual([row["part_id"] for row in self.huaxia.tables["part_assignments"]], [self.gaohu])
        self.assertEqual(sorted(self.huaxia.objects), [self.bowing])

    def test_replaced_files_do_not_come_back_and_nothing_is_reverted(self):
        self.retracter.retract(self.entry(), [self.group.id], self.catalog)
        self.assertNotIn(self.old, self.huaxia.objects)
        self.assertNotIn(self.old_score, {row["id"] for row in self.huaxia.tables["scores"]})
        erhu = self.parts()["二胡1"]
        self.assertEqual((erhu["needed"], erhu["needed_min"]), ("6-8", 6))
        self.assertIn({"event_id": self.concert, "piece_id": self.fenghuo}, self.huaxia.tables["event_pieces"])

    def test_a_retract_row_is_written_under_the_same_publish_id(self):
        result = self.retracter.retract(self.entry(), [self.group.id], self.catalog)
        [row] = self.log(PublishAction.RETRACT)
        self.assertEqual((row.publish_id, row.group_id, row.operator), (self.outcome.publish_id, self.group.id, "officer_amy"))
        self.assertEqual(row.parts_deleted, [{"id": self.paigu, "name": "排鼓"}])
        added = {s["id"] for s in self.outcome.groups[0].result["added_scores"]}
        self.assertEqual({s["id"] for s in row.scores_removed}, added)
        self.assertEqual(sorted(row.files_deleted), sorted({s["file_path"] for s in self.outcome.groups[0].result["added_scores"]}))
        self.assertIsNone(row.failure)
        self.assertEqual([record.group_id for record in result.records], [self.group.id])
        [entry] = [e for e in publish_entries(self.catalog.publish_log()) if e.action is PublishAction.RETRACT]
        self.assertEqual((entry.publish_id, [r.group_id for r in entry.groups]), (self.outcome.publish_id, [self.group.id]))

    def test_a_file_another_score_still_points_to_is_not_deleted(self):
        pipa = next(s["file_path"] for s in self.outcome.groups[0].result["added_scores"] if s["part_id"] == self.pipa)
        self.huaxia.add_score(self.fenghuo, self.erhu, pipa, title="之後又登記的")
        self.retracter.retract(self.entry(), [self.group.id], self.catalog)
        self.assertIn(pipa, self.huaxia.objects)

    def test_someone_no_longer_an_officer_changes_nothing(self):
        before = self.tables()
        self.huaxia.remove_roles("officer_amy")
        result = self.retracter.retract(self.entry(), [self.group.id], self.catalog)
        self.assertIsNotNone(result.not_officer)
        self.assertEqual(self.tables()["scores"], before["scores"])
        self.assertEqual(self.log(PublishAction.RETRACT), [])


class TestInterrupted(RetractTestCase):
    """撤回中途中斷：剩下的成為孤兒，重做一次撤回收斂到一次做完的結果"""

    def assert_retracted(self):
        """與一次做完相同：只剩聲部長的弓法版、排鼓刪掉、這次的檔都刪掉"""
        self.assertEqual(self.scores(self.fenghuo), [(self.pipa, self.bowing)])
        self.assertEqual(sorted(self.parts()), ["二胡1", "琵琶", "高胡"])
        self.assertEqual(sorted(self.huaxia.objects), [self.bowing])

    def test_stopping_before_the_files_are_deleted_converges_on_retrying(self):
        self.huaxia.drop_requests("DELETE", "/storage/v1/object/scores")
        first = self.retracter.retract(self.entry(), [self.group.id], self.catalog)
        self.assertIsNotNone(first.error)
        self.assertEqual(self.scores(self.fenghuo), [(self.pipa, self.bowing)])
        self.assertGreater(len(self.huaxia.objects), 1)
        [stopped] = self.log(PublishAction.RETRACT)
        self.assertTrue(stopped.failure)
        [group] = self.retracter.preview(self.entry()).groups
        self.assertTrue(group.actionable)
        self.retracter.retract(self.entry(), [self.group.id], self.catalog)
        self.assert_retracted()

    def test_stopping_before_the_parts_are_deleted_converges_on_retrying(self):
        self.huaxia.drop_requests("DELETE", "/rest/v1/piece_parts")
        self.retracter.retract(self.entry(), [self.group.id], self.catalog)
        self.assertIn("排鼓", self.parts())
        self.retracter.retract(self.entry(), [self.group.id], self.catalog)
        self.assert_retracted()

    def test_after_retracting_there_is_nothing_left_to_retract(self):
        self.retracter.retract(self.entry(), [self.group.id], self.catalog)
        [group] = self.retracter.preview(self.entry()).groups
        self.assertFalse(group.actionable)
        self.assertEqual({row.effect for row in group.rows}, {RetractEffect.UNTOUCHED})


class TestPreview(RetractTestCase):
    """預覽向 HuaXia 查現況，把每個聲部歸類；撤回後沒有譜的聲部是推算出來的"""

    def test_each_part_is_classified_by_what_retracting_leaves(self):
        [group] = self.retracter.preview(self.entry()).groups
        effects = {row.name: (row.effect, row.reason) for row in group.rows}
        self.assertEqual(effects, {
            "二胡1": (RetractEffect.NO_SCORE, RetractReason.REPLACED),
            "": (RetractEffect.NO_SCORE, RetractReason.ONLY_THIS),
            "排鼓": (RetractEffect.DELETE_PART, RetractReason.NEW_UNASSIGNED),
            "高胡": (RetractEffect.KEEP_PART, RetractReason.ASSIGNED),
            "琵琶": (RetractEffect.PARTIAL, RetractReason.OTHERS_KEPT),
        })
        self.assertEqual(group.missing, 3)
        self.assertEqual([row.effect for row in group.rows], sorted(
            (row.effect for row in group.rows), key=list(RetractEffect).index,
        ))

    def test_overwritten_headcounts_are_shown_as_not_reverted(self):
        [group] = self.retracter.preview(self.entry()).groups
        erhu = next(row for row in group.rows if row.name == "二胡1")
        self.assertEqual(erhu.kept, {"needed": ["6", "6-8"], "needed_min": [6, 6]})

    def test_a_part_whose_file_is_already_gone_is_untouched(self):
        erhu_score = next(row["id"] for row in self.huaxia.tables["scores"] if row["part_id"] == self.erhu)
        self.huaxia.tables["scores"] = [row for row in self.huaxia.tables["scores"] if row["id"] != erhu_score]
        [group] = self.retracter.preview(self.entry()).groups
        erhu = next(row for row in group.rows if row.name == "二胡1")
        self.assertEqual((erhu.effect, erhu.reason), (RetractEffect.UNTOUCHED, RetractReason.GONE))

    def test_a_new_part_someone_else_put_a_file_on_is_kept(self):
        self.huaxia.add_score(self.fenghuo, self.paigu, "%s/%s/1700000000_x.pdf" % (self.fenghuo, self.paigu))
        [group] = self.retracter.preview(self.entry()).groups
        paigu = next(row for row in group.rows if row.name == "排鼓")
        self.assertEqual((paigu.effect, paigu.reason), (RetractEffect.PARTIAL, RetractReason.NEW_WITH_OTHERS))
        self.assertEqual(group.parts, [])


class TestOtherGroups(TwoPiecesTestCase):
    """同一次發譜的其他群組：沒發成的沒有可撤的，沒勾的不動"""

    def setUp(self):
        super().setUp()
        self.retracter = RetractService(self.auth)

    def entry(self, publish_id: str):
        """發譜紀錄裡這次發譜的那一項"""
        return next(
            entry for entry in publish_entries(self.catalog.publish_log())
            if entry.publish_id == publish_id and entry.action is PublishAction.START
        )

    def test_a_group_that_was_not_published_has_nothing_to_retract(self):
        self.huaxia.fail_switch(self.moli)
        outcome = self.run_publish(self.press_publish())
        groups = {group.name: group for group in self.retracter.preview(self.entry(outcome.publish_id)).groups}
        self.assertTrue(groups["茉莉花"].failed)
        self.assertFalse(groups["茉莉花"].actionable)
        self.assertEqual(groups["茉莉花"].rows, [])
        self.assertTrue(groups["烽火操"].actionable)

    def test_a_group_left_unchecked_is_not_touched(self):
        outcome = self.run_publish(self.press_publish())
        moli = self.scores(self.moli)
        self.retracter.retract(self.entry(outcome.publish_id), [self.group.id], self.catalog)
        self.assertEqual(self.scores(self.fenghuo), [])
        self.assertEqual(self.scores(self.moli), moli)
        self.assertTrue(all(path in self.huaxia.objects for _, path in moli))

    def test_a_publish_without_a_result_has_nothing_to_retract(self):
        publish_id = self.catalog.begin_publish([
            group.start_record() for group in self.press_publish().groups
        ], "officer_amy")
        entry = self.entry(publish_id)
        self.assertFalse(entry.finished)
        [group] = self.retracter.preview(entry).groups[:1]
        self.assertFalse(group.actionable)
        self.assertTrue(group.unfinished)
        before = self.tables()
        self.retracter.retract(entry, [group.group_id], self.catalog)
        self.assertEqual(self.tables(), before)


class TestNewPiece(RunTestCase):
    """連錯的那次新建了曲目：撤回後留下一首空曲目，由幹部在網頁刪除"""

    def test_the_new_piece_is_left_empty_and_named_in_the_result(self):
        chunjiang = self.add_group("春江花月夜", ["簫"], b"chunjiang")
        new = NewHuaXiaPiece("春江花月夜", "", 7.5, HuaXiaEnsemble.SIZHU)
        self.project.set_huaxia_link(chunjiang, HuaXiaLink(piece_title="春江花月夜", new_piece=new))
        outcome = self.run_publish(self.press_publish())
        entry = next(e for e in publish_entries(self.catalog.publish_log()) if e.publish_id == outcome.publish_id)
        retracter = RetractService(self.auth)
        result = retracter.retract(entry, [chunjiang.id], self.catalog)
        piece_id = chunjiang.huaxia_link.piece_id
        self.assertIn(piece_id, {row["id"] for row in self.huaxia.tables["pieces"]})
        self.assertEqual(self.scores(piece_id), [])
        self.assertEqual([row for row in self.huaxia.tables["piece_parts"] if row["piece_id"] == piece_id], [])
        self.assertEqual(result.left_pieces, ["春江花月夜"])


if __name__ == "__main__":
    unittest.main()
