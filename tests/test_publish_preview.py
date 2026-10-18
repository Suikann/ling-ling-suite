# -*- coding: utf-8 -*-
"""
發譜預覽各步的規則測試（PublishPreview，不碰畫面）

「曲目」步：群組對到可以挑的現有曲目或有曲名的新建曲目才算選好，擋下的群組不算；選回同一首現有曲目時
沿用手動挑的聲部配對，換了曲目就丟掉這次的聲部決定。「聲部」步：有群組還不能發時「下一步」按不下去。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.constants import HuaXiaEnsemble
from core.models import Headcount, HuaXiaLink, NewHuaXiaPiece
from services.file_service import FileService
from services.huaxia_client import HuaXiaPart, HuaXiaPiece
from services.publish_service import PublishPreview, PublishReadiness, PublishStep, check_groups
from test_publish_preflight import renamed_project

MB = 1000 * 1000


def part(part_id: str, name: str) -> HuaXiaPart:
    """曲目 hx-1 上一個一人、拉弦的聲部"""
    return HuaXiaPart(part_id, "hx-1", name, "1", 1, "拉弦", 1)


class PreviewTestCase(unittest.TestCase):
    """已重新命名的「烽火操」（二胡 I、琵琶）與還沒重新命名、被擋下的「春江花月夜」；HuaXia 上可以挑 hx-1

    hx-1 上有「二胡1」「高胡」兩個聲部；已封存的 hx-old 不在可以挑的曲目裡。
    """

    def setUp(self):
        self.project = renamed_project()
        self.group = self.project.groups[0]
        self.blocked = self.project.add_group("春江花月夜", "總譜")
        self.readiness = PublishReadiness(
            pieces=[HuaXiaPiece("hx-1", "長城隨想－烽火操", "劉文金", None, HuaXiaEnsemble.TUTTI)],
            parts={"hx-1": [part("p-erhu", "二胡1"), part("p-gaohu", "高胡")]},
        )

    def preview(self) -> PublishPreview:
        """以專案目前的狀態開啟預覽"""
        return PublishPreview(self.project, check_groups(self.project), self.readiness, FileService(), 20 * MB)


class TestPieceStep(PreviewTestCase):
    """「曲目」步：每個沒擋下的群組都對到曲目才能按「下一步」"""

    def test_a_group_without_a_piece_is_not_ready(self):
        preview = self.preview()
        self.assertFalse(preview.has_piece(self.group))
        self.assertFalse(preview.step_ready(PublishStep.PIECE))

    def test_an_offered_existing_piece_is_ready_and_blocked_groups_do_not_count(self):
        preview = self.preview()
        preview.set_piece(self.group, HuaXiaLink(piece_id="hx-1", piece_title="長城隨想－烽火操"))
        self.assertIsNone(self.blocked.huaxia_link)
        self.assertTrue(preview.step_ready(PublishStep.PIECE))

    def test_a_piece_no_longer_offered_is_not_ready(self):
        self.project.set_huaxia_link(self.group, HuaXiaLink(piece_id="hx-old", piece_title="春節序曲"))
        self.assertFalse(self.preview().has_piece(self.group))

    def test_a_new_piece_needs_a_title(self):
        preview = self.preview()
        blank = NewHuaXiaPiece(" ", "", None, HuaXiaEnsemble.TUTTI)
        preview.set_piece(self.group, HuaXiaLink(piece_title="", new_piece=blank))
        self.assertFalse(preview.has_piece(self.group))
        titled = blank._replace(title="烽火操")
        preview.set_piece(self.group, HuaXiaLink(piece_title="烽火操", new_piece=titled))
        self.assertTrue(preview.has_piece(self.group))

    def test_choosing_the_same_piece_again_keeps_the_chosen_parts(self):
        self.project.set_huaxia_link(
            self.group, HuaXiaLink(piece_id="hx-1", piece_title="長城隨想－烽火操", part_ids={"琵琶": "p-gaohu"}),
        )
        preview = self.preview()
        new = NewHuaXiaPiece("烽火操", "", None, HuaXiaEnsemble.TUTTI)
        preview.set_piece(self.group, HuaXiaLink(piece_title="烽火操", new_piece=new))
        preview.set_piece(self.group, HuaXiaLink(piece_id="hx-1", piece_title="長城隨想－烽火操"))
        self.assertEqual(self.group.huaxia_link.part_ids, {"琵琶": "p-gaohu"})

    def test_changing_the_piece_forgets_this_time_voice_decisions(self):
        preview = self.preview()
        preview.set_piece(self.group, HuaXiaLink(piece_id="hx-1", piece_title="長城隨想－烽火操"))
        preview.set_overwrite(self.group, 0, False)
        preview.set_piece(self.group, HuaXiaLink(piece_id="hx-1", piece_title="長城隨想－烽火操"))
        self.assertEqual(preview.decisions(self.group).keep, {0})
        new = NewHuaXiaPiece("烽火操", "", None, HuaXiaEnsemble.TUTTI)
        preview.set_piece(self.group, HuaXiaLink(piece_title="烽火操", new_piece=new))
        self.assertEqual(preview.decisions(self.group).keep, set())


class TestRerunNewPiece(PreviewTestCase):
    """新建的曲目在發譜一開始產生編號；重發時沿用那個編號，HuaXia 上已經有它就用它的聲部與樂譜"""

    def setUp(self):
        super().setUp()
        self.new = NewHuaXiaPiece("烽火操", "劉文金", 5.0, HuaXiaEnsemble.TUTTI)
        self.project.set_huaxia_link(self.group, HuaXiaLink(
            piece_id="hx-new", piece_title="烽火操", new_piece=self.new, part_ids={"琵琶": "p-new-pipa"},
        ))

    def test_editing_the_new_piece_keeps_its_generated_number_and_chosen_parts(self):
        preview = self.preview()
        edited = self.new._replace(title="長城隨想－烽火操")
        preview.set_piece(self.group, HuaXiaLink(piece_title=edited.title, new_piece=edited))
        link = self.group.huaxia_link
        self.assertEqual((link.piece_id, link.new_piece, link.part_ids), ("hx-new", edited, {"琵琶": "p-new-pipa"}))

    def test_choosing_new_again_after_an_existing_piece_gets_the_generated_number_back(self):
        preview = self.preview()
        preview.set_piece(self.group, HuaXiaLink(piece_id="hx-1", piece_title="長城隨想－烽火操"))
        preview.set_piece(self.group, HuaXiaLink(piece_title="烽火操", new_piece=self.new))
        self.assertEqual(self.group.huaxia_link.piece_id, "hx-new")

    def test_an_existing_piece_number_is_not_taken_into_a_new_piece(self):
        self.project.set_huaxia_link(self.group, HuaXiaLink(piece_id="hx-1", piece_title="長城隨想－烽火操"))
        preview = PublishPreview(self.project, check_groups(self.project), self.readiness, FileService(), 20 * MB)
        preview.set_piece(self.group, HuaXiaLink(piece_title="烽火操", new_piece=self.new))
        self.assertEqual(self.group.huaxia_link.piece_id, "")

    def test_a_new_piece_already_created_on_huaxia_uses_its_parts(self):
        self.readiness.parts["hx-new"] = [
            HuaXiaPart("p-new-erhu", "hx-new", "二胡 I", "6-8", 6, "拉弦", 1),
            HuaXiaPart("p-new-pipa", "hx-new", "琵琶", "2", 2, "彈撥", 2),
        ]
        plan = self.preview().voice_plan(self.group)
        self.assertEqual(plan.creates, [])
        self.assertEqual([row.part.id for row in plan.rows], ["p-new-erhu", "p-new-pipa"])


class TestVoicesStep(PreviewTestCase):
    """「聲部」步：有衝突、沒挑或要建卻沒填最少人數的群組讓「下一步」按不下去"""

    def setUp(self):
        super().setUp()
        self.preview = self.preview()
        self.preview.set_piece(self.group, HuaXiaLink(piece_id="hx-1", piece_title="長城隨想－烽火操"))

    def test_ready_when_every_group_can_publish(self):
        self.assertTrue(self.preview.step_ready(PublishStep.VOICES))

    def test_two_voices_on_one_part_are_not_ready(self):
        for row in self.preview.voice_plan(self.group).rows:
            self.preview.choose_part(self.group, row, "p-gaohu")
        self.assertTrue(self.preview.voice_plan(self.group).conflicts)
        self.assertFalse(self.preview.step_ready(PublishStep.VOICES))
        self.assertFalse(self.preview.can_publish(compressing=False))

    def test_an_absent_voice_to_create_needs_a_minimum_headcount(self):
        self.project.set_instruments(self.group, ["二胡 I", "琵琶", "揚琴"])
        self.project.set_present(self.group, 2, False)
        self.project.set_headcount(self.group, 2, Headcount())
        self.preview.set_create_absent(self.group, 2, True)
        self.assertFalse(self.preview.step_ready(PublishStep.VOICES))


class TestSteps(unittest.TestCase):
    """步驟依「曲目 › 聲部 › 檔案 › 公演 › 確認」的順序"""

    def test_steps_go_in_order(self):
        self.assertEqual(PublishStep.PIECE.offset(1), PublishStep.VOICES)
        self.assertEqual(PublishStep.CONFIRM.offset(-1), PublishStep.EVENTS)
        self.assertIsNone(PublishStep.PIECE.offset(-1))
        self.assertIsNone(PublishStep.CONFIRM.offset(1))


if __name__ == "__main__":
    unittest.main()
