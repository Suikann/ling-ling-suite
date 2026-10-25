# -*- coding: utf-8 -*-
"""
發譜預覽「聲部」步的規則測試

對專案模型與假 HuaXia 測：按下發譜時讀出可以挑的曲目的聲部；每個泠靈聲部依序以記住的配對、
泠靈的「同一聲部」規則對到 HuaXia 聲部，對不上的預設新建；名稱規則對出多個時不自動選；
兩個泠靈聲部指到同一個時擋下；手動挑的配對記進專案；算出要寫的內容（人數記法、聲部組、覆寫、
「本專案沒有」的聲部預設不建），永遠不刪 HuaXia 的聲部。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.models import Headcount, HuaXiaLink, NewHuaXiaPiece, Project
from core.constants import HuaXiaEnsemble
from services.huaxia_client import HuaXiaPart
from services.project_service import ProjectService
from services.publish_service import PartCreate, PartMatch, PartUpdate, VoiceDecisions, plan_voices
from test_publish_preflight import PrepareTestCase


class TestPrepareReadsParts(PrepareTestCase):
    """按下發譜時一起讀出可以挑的曲目的聲部（已封存的曲目不讀）"""

    def test_parts_of_unarchived_pieces_ordered_like_huaxia(self):
        erhu = self.huaxia.add_part(self.fenghuo, "二胡1", needed="6-8", needed_min=6, section="拉弦", sort=2)
        di = self.huaxia.add_part(self.fenghuo, "梆笛", sort=1)
        archived = self.huaxia.add_piece("十面埋伏", archived=True)
        self.huaxia.add_part(archived, "琵琶")
        readiness = self.service.prepare()
        self.assertEqual(readiness.parts, {
            self.fenghuo: [
                HuaXiaPart(di, self.fenghuo, "梆笛", "1", 1, "吹管", 1),
                HuaXiaPart(erhu, self.fenghuo, "二胡1", "6-8", 6, "拉弦", 2),
            ],
        })


class TestChosenPartsAreSaved(unittest.TestCase):
    """手動挑的配對以 HuaXia 的聲部編號記在群組對到的曲目，存進專案檔"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.project = Project()
        self.group = self.project.add_group("烽火操", "總譜")
        self.project.set_instruments(self.group, ["二胡 I", "琵琶"])
        self.project.set_huaxia_link(self.group, HuaXiaLink(piece_id="hx-1", piece_title="長城隨想－烽火操"))
        self.project.mark_saved()

    def reopened(self) -> Project:
        """存成專案檔再開啟"""
        path = os.path.join(self.temp_dir, "concert.llproj")
        ProjectService().save_project(self.project, path)
        return ProjectService().load_project(path)

    def test_a_chosen_part_survives_reopening(self):
        self.project.choose_huaxia_part(self.group, "二胡 I", "part-erhu")
        self.assertTrue(self.project.is_modified())
        link = self.reopened().groups[0].huaxia_link
        self.assertEqual(link.part_ids, {"二胡 I": "part-erhu"})
        self.assertEqual(link.piece_id, "hx-1")

    def test_choosing_new_forgets_the_chosen_part(self):
        self.project.choose_huaxia_part(self.group, "二胡 I", "part-erhu")
        self.project.choose_huaxia_part(self.group, "二胡1", None)
        self.assertEqual(self.reopened().groups[0].huaxia_link.part_ids, {})

    def test_the_chosen_part_is_found_under_another_spelling_of_the_voice(self):
        self.project.choose_huaxia_part(self.group, "二胡 I", "part-erhu")
        self.project.choose_huaxia_part(self.group, "二胡1", "part-other")
        self.assertEqual(self.group.huaxia_link.part_ids, {"二胡1": "part-other"})
        self.assertEqual(self.group.huaxia_link.part_for("二胡一"), "part-other")


def part(part_id: str, name: str, needed: str = "1", needed_min: int = 1, section: str = "", sort: int = 0):
    """曲目 hx-1 上的一個 HuaXia 聲部"""
    return HuaXiaPart(part_id, "hx-1", name, needed, needed_min, section, sort)


class PlanTestCase(unittest.TestCase):
    """國樂團專案的一個群組「烽火操」，對到 HuaXia 的現有曲目 hx-1；樂器表與人數由各測試給"""

    def setUp(self):
        self.project = Project()
        self.group = self.project.add_group("烽火操", "總譜")
        self.project.set_huaxia_link(self.group, HuaXiaLink(piece_id="hx-1", piece_title="長城隨想－烽火操"))

    def voices(self, *rows):
        """樂器表依序為 rows：（聲部，人數）或（聲部，人數，本專案有）"""
        self.project.set_instruments(self.group, [row[0] for row in rows])
        for index, row in enumerate(rows):
            self.project.set_headcount(self.group, index, row[1])
            if len(row) > 2:
                self.project.set_present(self.group, index, row[2])

    def plan(self, parts, decisions=None):
        """這個群組對 parts 的配對結果"""
        return plan_voices(self.project, self.group, parts, decisions or VoiceDecisions())

    def row(self, plan, voice):
        """配對結果裡這個聲部那一列"""
        return next(row for row in plan.rows if row.voice == voice)


class TestMatchingOrder(PlanTestCase):
    """配對順序：記住的配對，記住的已刪除就退回名稱規則，再沒有才算沒對上"""

    def setUp(self):
        super().setUp()
        self.voices(("二胡 I", Headcount(6, 8)))

    def test_the_name_rule_matches_another_spelling_and_is_not_remembered(self):
        erhu = part("p1", "二胡1", "6-8", 6, "拉弦")
        row = self.row(self.plan([erhu]), "二胡 I")
        self.assertIs(row.match, PartMatch.NAME)
        self.assertEqual(row.part, erhu)
        self.assertEqual(self.group.huaxia_link.part_ids, {})

    def test_a_remembered_part_comes_before_the_name_rule(self):
        same_name, web = part("p1", "二胡1"), part("p2", "二胡")
        self.project.choose_huaxia_part(self.group, "二胡 I", "p2")
        plan = self.plan([same_name, web])
        row = self.row(plan, "二胡 I")
        self.assertIs(row.match, PartMatch.CHOSEN)
        self.assertEqual(row.part, web)
        self.assertEqual(plan.huaxia_only, [same_name])

    def test_a_deleted_remembered_part_falls_back_to_the_name_rule(self):
        erhu = part("p1", "二胡1")
        self.project.choose_huaxia_part(self.group, "二胡 I", "p-deleted")
        row = self.row(self.plan([erhu]), "二胡 I")
        self.assertIs(row.match, PartMatch.NAME)
        self.assertEqual(row.part, erhu)

    def test_nothing_matching_defaults_to_new_and_offers_the_unpaired_parts(self):
        web, pipa = part("p1", "二胡"), part("p2", "琵琶")
        self.voices(("二胡 I", Headcount(6, 8)), ("琵琶", Headcount(2)))
        row = self.row(self.plan([web, pipa]), "二胡 I")
        self.assertIs(row.match, PartMatch.NONE)
        self.assertIsNone(row.part)
        self.assertTrue(row.create)
        self.assertEqual(row.options, [web])

    def test_several_name_matches_are_not_chosen_automatically(self):
        a, b, other = part("p1", "二胡I"), part("p2", "二胡 1"), part("p3", "二胡")
        plan = self.plan([a, b, other])
        row = self.row(plan, "二胡 I")
        self.assertIs(row.match, PartMatch.AMBIGUOUS)
        self.assertIsNone(row.part)
        self.assertFalse(row.create)
        self.assertEqual(row.options, [a, b])
        self.assertEqual(plan.undecided, ["二胡 I"])
        self.assertTrue(plan.blocked)

    def test_choosing_among_several_name_matches_settles_it(self):
        a, b = part("p1", "二胡I"), part("p2", "二胡 1")
        self.project.choose_huaxia_part(self.group, "二胡 I", "p2")
        plan = self.plan([a, b])
        self.assertEqual(self.row(plan, "二胡 I").part, b)
        self.assertEqual(self.row(plan, "二胡 I").options, [a, b])
        self.assertFalse(plan.blocked)

    def test_choosing_new_this_time_overrides_the_name_rule(self):
        row = self.row(self.plan([part("p1", "二胡1")], VoiceDecisions(new={0})), "二胡 I")
        self.assertIsNone(row.part)
        self.assertTrue(row.create)


class TestRememberedOnHuaXia(PrepareTestCase):
    """手動挑的配對記的是 HuaXia 的聲部編號：幹部在網頁上改了名稱、重開專案照樣對得到"""

    def test_a_part_renamed_on_huaxia_is_still_matched(self):
        web = self.huaxia.add_part(self.fenghuo, "二胡", needed="6-8", needed_min=6)
        self.huaxia.add_part(self.fenghuo, "二胡1")
        project = Project()
        group = project.add_group("烽火操", "總譜")
        project.set_instruments(group, ["二胡 I"])
        project.set_headcount(group, 0, Headcount(6, 8))
        project.set_huaxia_link(group, HuaXiaLink(piece_id=self.fenghuo, piece_title="長城隨想－烽火操"))
        project.choose_huaxia_part(group, "二胡 I", web)
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, True)
        path = os.path.join(temp_dir, "concert.llproj")
        ProjectService().save_project(project, path)
        reopened = ProjectService().load_project(path)
        self.huaxia.rename_part(web, "二胡（網頁）")
        parts = self.service.prepare().parts[self.fenghuo]
        row = plan_voices(reopened, reopened.groups[0], parts).rows[0]
        self.assertIs(row.match, PartMatch.CHOSEN)
        self.assertEqual((row.part.id, row.part.name), (web, "二胡（網頁）"))

    def test_a_part_deleted_on_huaxia_falls_back_to_the_name_rule(self):
        web = self.huaxia.add_part(self.fenghuo, "二胡")
        same = self.huaxia.add_part(self.fenghuo, "二胡1")
        project = Project()
        group = project.add_group("烽火操", "總譜")
        project.set_instruments(group, ["二胡 I"])
        project.set_huaxia_link(group, HuaXiaLink(piece_id=self.fenghuo, piece_title="長城隨想－烽火操"))
        project.choose_huaxia_part(group, "二胡 I", web)
        self.huaxia.remove_part(web)
        row = plan_voices(project, group, self.service.prepare().parts[self.fenghuo]).rows[0]
        self.assertIs(row.match, PartMatch.NAME)
        self.assertEqual(row.part.id, same)


class TestConflicts(PlanTestCase):
    """兩個泠靈聲部不能指到同一個 HuaXia 聲部"""

    def test_two_voices_on_one_part_are_blocked(self):
        self.voices(("二胡 I", Headcount(6)), ("二胡 II", Headcount(6)))
        web = part("p1", "二胡")
        self.project.choose_huaxia_part(self.group, "二胡 I", "p1")
        self.project.choose_huaxia_part(self.group, "二胡 II", "p1")
        plan = self.plan([web])
        self.assertEqual(plan.conflicts, [(web, ["二胡 I", "二胡 II"])])
        self.assertTrue(plan.blocked)

    def test_a_part_chosen_by_another_voice_is_not_offered(self):
        self.voices(("二胡 I", Headcount(6)), ("二胡 II", Headcount(6)))
        web, gaohu = part("p1", "二胡"), part("p2", "高胡")
        self.project.choose_huaxia_part(self.group, "二胡 I", "p1")
        plan = self.plan([web, gaohu])
        self.assertEqual(self.row(plan, "二胡 II").options, [gaohu])
        self.assertEqual(self.row(plan, "二胡 I").options, [web, gaohu])

    def test_a_remembered_part_is_not_offered_to_the_name_rule_again(self):
        self.voices(("二胡 I", Headcount(6)), ("二胡", Headcount(2)))
        web = part("p1", "二胡")
        self.project.choose_huaxia_part(self.group, "二胡 I", "p1")
        plan = self.plan([web])
        self.assertEqual(self.row(plan, "二胡").match, PartMatch.NONE)
        self.assertEqual(plan.conflicts, [])


class TestWrites(PlanTestCase):
    """算出要寫的內容：HuaXia 的名稱、人數記法、聲部組；覆寫預設勾選；永遠不刪 HuaXia 的聲部"""

    def test_new_parts_use_huaxia_headcount_notation_after_the_existing_ones(self):
        self.voices(("梆笛", Headcount(6)), ("二胡 I", Headcount(6, 8)), ("琵琶", Headcount(2, open_ended=True)))
        plan = self.plan([part("p1", "雲鑼", sort=4)])
        self.assertEqual(plan.creates, [
            PartCreate("梆笛", "6", 6, "吹管", 5),
            PartCreate("二胡 I", "6-8", 6, "拉弦", 6),
            PartCreate("琵琶", "2↑", 2, "彈撥", 7),
        ])

    def test_other_section_is_written_blank_for_huaxia_to_guess(self):
        self.voices(("口琴", Headcount(1)))
        self.assertEqual(self.project.section_of("口琴"), "其他")
        self.assertEqual(self.plan([]).creates, [PartCreate("口琴", "1", 1, "", 1)])

    def test_other_section_is_not_a_difference(self):
        self.voices(("口琴", Headcount(1)))
        row = self.row(self.plan([part("p1", "口琴", section="打擊")]), "口琴")
        self.assertFalse(row.section_differs)

    def test_differences_are_overwritten_by_default_keeping_the_huaxia_name(self):
        self.voices(("二胡 I", Headcount(5, 8)), ("大提琴", Headcount(4)))
        erhu = part("p1", "二胡1", "6-8", 6, "拉弦")
        cello = part("p2", "大提琴", "4", 4, "拉弦")
        plan = self.plan([erhu, cello])
        self.assertTrue(self.row(plan, "二胡 I").headcount_differs)
        self.assertTrue(self.row(plan, "大提琴").section_differs)
        self.assertEqual(plan.updates, [
            PartUpdate("p1", "二胡1", "5-8", 5, None),
            PartUpdate("p2", "大提琴", None, None, "低音"),
        ])
        self.assertEqual(plan.creates, [])

    def test_an_optional_voice_differs_from_a_huaxia_zero_and_is_written_with_minimum_zero(self):
        self.voices(("二胡 I", Headcount(0, 1)))
        plan = self.plan([part("p1", "二胡1", "0", 0, "拉弦")])
        row = self.row(plan, "二胡 I")
        self.assertTrue(row.headcount_differs)
        self.assertTrue(row.overwrite)
        self.assertEqual(plan.updates, [PartUpdate("p1", "二胡1", "0-1", 0, None)])

    def test_an_optional_voice_already_on_huaxia_is_the_same(self):
        self.voices(("二胡 I", Headcount(0, 1)))
        plan = self.plan([part("p1", "二胡1", "0-1", 0, "拉弦")])
        self.assertFalse(self.row(plan, "二胡 I").headcount_differs)
        self.assertEqual(plan.updates, [])

    def test_a_new_optional_voice_is_created_with_minimum_zero(self):
        self.voices(("二胡 I", Headcount(0, 1)))
        plan = self.plan([])
        self.assertEqual(plan.creates, [PartCreate("二胡 I", "0-1", 0, "拉弦", 1)])
        self.assertEqual(plan.unfilled, [])

    def test_unchecking_overwrite_keeps_huaxia(self):
        self.voices(("二胡 I", Headcount(5, 8)))
        plan = self.plan([part("p1", "二胡1", "6-8", 6, "拉弦")], VoiceDecisions(keep={0}))
        self.assertFalse(self.row(plan, "二胡 I").overwrite)
        self.assertEqual(plan.updates, [])

    def test_a_matching_part_is_left_alone(self):
        self.voices(("二胡 I", Headcount(6, 8)))
        plan = self.plan([part("p1", "二胡1", "6-8", 6, "拉弦")])
        self.assertEqual((plan.creates, plan.updates), ([], []))

    def test_parts_only_on_huaxia_are_never_deleted(self):
        self.voices(("二胡 I", Headcount(6, 8)))
        erhu, yunluo = part("p1", "二胡1", "6-8", 6, "拉弦"), part("p2", "雲鑼", section="打擊")
        plan = self.plan([erhu, yunluo])
        self.assertEqual(plan.huaxia_only, [yunluo])
        self.assertEqual(plan.updates, [])
        self.assertFalse(hasattr(plan, "deletes"))

    def test_a_new_piece_creates_every_present_voice(self):
        self.project.set_huaxia_link(self.group, HuaXiaLink(
            piece_title="烽火操", new_piece=NewHuaXiaPiece("烽火操", "劉文金", None, HuaXiaEnsemble.TUTTI),
        ))
        self.voices(("二胡 I", Headcount(6, 8)), ("琵琶", Headcount(2)))
        self.assertEqual([c.name for c in self.plan([]).creates], ["二胡 I", "琵琶"])


class TestAbsentVoices(PlanTestCase):
    """「本專案沒有」的聲部：HuaXia 沒有時逐個問要不要建，預設不建；要建的也要填最少人數"""

    def setUp(self):
        super().setUp()
        self.voices(("二胡 I", Headcount(6, 8)), ("豎琴", Headcount(), False))

    def test_not_created_by_default(self):
        plan = self.plan([])
        row = self.row(plan, "豎琴")
        self.assertFalse(row.create)
        self.assertEqual([c.name for c in plan.creates], ["二胡 I"])
        self.assertFalse(plan.blocked)

    def test_creating_one_needs_a_minimum_headcount(self):
        plan = self.plan([], VoiceDecisions(create_absent={1}))
        self.assertEqual(plan.unfilled, ["豎琴"])
        self.assertTrue(plan.blocked)

    def test_creating_one_with_a_minimum(self):
        self.project.set_headcount(self.group, 1, Headcount(1))
        plan = self.plan([], VoiceDecisions(create_absent={1}))
        self.assertEqual([c.name for c in plan.creates], ["二胡 I", "豎琴"])
        self.assertFalse(plan.blocked)

    def test_one_already_on_huaxia_is_left_alone(self):
        harp = part("p1", "豎琴", "2", 2)
        plan = self.plan([harp])
        self.assertEqual(self.row(plan, "豎琴").part, harp)
        self.assertEqual(plan.updates, [])
        self.assertEqual(plan.huaxia_only, [])


class TestAbsentVoicesDoNotTakeParts(PlanTestCase):
    """這次不發的聲部（本專案沒有、也不在 HuaXia 建）不占 HuaXia 的聲部：網頁上建的「二胡」可以給泠靈的「二胡 I」挑"""

    def setUp(self):
        super().setUp()
        self.voices(("二胡", Headcount(), False), ("二胡 I", Headcount(6, 8)))
        self.erhu = part("p1", "二胡", "6", 6)

    def test_the_part_is_offered_to_the_voice_that_is_published(self):
        plan = self.plan([self.erhu])
        self.assertIn(self.erhu, self.row(plan, "二胡 I").options)
        self.assertEqual(self.row(plan, "二胡").part, self.erhu)

    def test_choosing_it_goes_through_and_the_unchecked_voice_lets_go(self):
        self.project.choose_huaxia_part(self.group, "二胡 I", "p1")
        plan = self.plan([self.erhu])
        self.assertEqual(self.row(plan, "二胡 I").part, self.erhu)
        self.assertIsNone(self.row(plan, "二胡").part)
        self.assertEqual(plan.conflicts, [])
        self.assertFalse(plan.blocked)

    def test_an_unchecked_voice_put_on_huaxia_takes_its_part_again(self):
        plan = self.plan([self.erhu], VoiceDecisions(create_absent={0}))
        self.assertEqual(self.row(plan, "二胡").part, self.erhu)
        self.assertNotIn(self.erhu, self.row(plan, "二胡 I").options)


if __name__ == "__main__":
    unittest.main()
