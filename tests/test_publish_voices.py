# -*- coding: utf-8 -*-
"""
發譜預覽「聲部」步的規則測試

對專案模型與假 HuaXia 測：按下發譜時讀出可以挑的曲目的聲部；每個泠靈聲部依序以記住的配對、
泠靈的「同一聲部」規則對到 HuaXia 聲部，對不上的預設新建；名稱規則對出多個時不自動選；
兩個泠靈聲部指到同一個時擋下；手動挑的配對記進專案；算出要寫的內容（人數記法、聲部組、覆寫、
「本專案沒有」的聲部預設不建），永遠不刪 HuaXia 的聲部。HuaXia 上分好分部的聲部：配對只在聲部之間，分部列在
所屬聲部底下、不算沒對到的聲部，泠靈聲部的名稱對到分部時提示、不配對，人數與聲部組只寫聲部。要新建的聲部
可以勾選 HuaXia 聲部收成分部：只列可以收的，被收的聲部上泠靈發的樂譜一起撤掉，別人放的擋下群組。
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
from services.huaxia_client import HuaXiaPart, HuaXiaScore, lingling_storage_path
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

    def test_divisi_carry_the_voice_they_belong_to(self):
        percussion = self.huaxia.add_part(self.fenghuo, "擊樂2", needed="4", needed_min=4, section="打擊", sort=1)
        shaker = self.huaxia.add_part(self.fenghuo, "沙球", sort=2, parent_id=percussion)
        self.assertEqual(self.service.prepare().parts[self.fenghuo], [
            HuaXiaPart(percussion, self.fenghuo, "擊樂2", "4", 4, "打擊", 1),
            HuaXiaPart(shaker, self.fenghuo, "沙球", "1", 1, "打擊", 2, parent_id=percussion),
        ])


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

    def plan(self, parts, decisions=None, scores=()):
        """這個群組對 parts（曲目上登記著 scores）的配對結果"""
        return plan_voices(self.project, self.group, parts, decisions or VoiceDecisions(), list(scores))

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
        erhu = part("p1", "二胡1", "6-8", 6, "拉弦", sort=1)
        cello = part("p2", "大提琴", "4", 4, "拉弦", sort=2)
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
        erhu, yunluo = part("p1", "二胡1", "6-8", 6, "拉弦", sort=1), part("p2", "雲鑼", section="打擊", sort=2)
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
        self.assertEqual([update for update in plan.updates if update.overwrites], [])
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


class TestAbsorbing(PlanTestCase):
    """收成分部：沒對上的泠靈聲部「擊樂2」勾選 HuaXia 上的「風鈴」「鈴鼓」，發譜時新建「擊樂2」把它們收成分部

    HuaXia 上有「風鈴」「鈴鼓」「琵琶」，以及分好分部的「二胡」（分部「二胡1」）。
    """

    def setUp(self):
        super().setUp()
        self.voices(("擊樂2", Headcount(3)), ("琵琶", Headcount(2)))
        self.chimes, self.tambourine = part("p1", "風鈴", sort=1), part("p2", "鈴鼓", sort=2)
        self.pipa, self.erhu = part("p3", "琵琶", "2", 2, sort=3), part("p4", "二胡", sort=4)
        self.erhu_one = HuaXiaPart("p5", "hx-1", "二胡1", "1", 1, "", 5, parent_id="p4")
        self.parts = [self.chimes, self.tambourine, self.pipa, self.erhu, self.erhu_one]

    def test_the_new_voice_absorbs_the_ticked_parts(self):
        plan = self.plan(self.parts, VoiceDecisions(absorb={0: {"p2", "p1"}}))
        row = self.row(plan, "擊樂2")
        self.assertTrue(row.create)
        self.assertEqual(row.absorbs, [self.chimes, self.tambourine])
        [create] = plan.creates
        self.assertEqual((create.name, create.needed, create.needed_min, create.absorb), ("擊樂2", "3", 3, ("p1", "p2")))
        self.assertEqual(plan.huaxia_only, [self.erhu])
        self.assertNotIn(self.chimes, self.row(plan, "琵琶").options)

    def test_only_parentless_parts_without_divisi_not_paired_by_another_voice_can_be_absorbed(self):
        row = self.row(self.plan(self.parts), "擊樂2")
        self.assertEqual(row.absorb_options, [self.chimes, self.tambourine])
        self.assertEqual(row.absorbs, [])
        self.assertEqual(self.plan(self.parts).creates[0].absorb, ())

    def test_a_part_paired_by_another_voice_is_not_absorbed(self):
        plan = self.plan(self.parts, VoiceDecisions(absorb={0: {"p3"}}))
        self.assertEqual(self.row(plan, "擊樂2").absorbs, [])
        self.assertEqual(self.row(plan, "琵琶").part, self.pipa)

    def test_a_voice_paired_with_an_existing_part_absorbs_nothing(self):
        self.project.choose_huaxia_part(self.group, "擊樂2", "p4")
        plan = self.plan(self.parts, VoiceDecisions(absorb={0: {"p1"}}))
        self.assertEqual(self.row(plan, "擊樂2").absorbs, [])
        self.assertEqual(plan.creates, [])
        self.assertIn(self.chimes, plan.huaxia_only)

    def test_a_part_absorbed_by_one_voice_is_not_offered_to_another(self):
        self.voices(("擊樂2", Headcount(3)), ("擊樂3", Headcount(2)))
        plan = self.plan(self.parts, VoiceDecisions(absorb={0: {"p1"}, 1: {"p1", "p2"}}))
        self.assertEqual(self.row(plan, "擊樂2").absorbs, [self.chimes])
        self.assertEqual(self.row(plan, "擊樂3").absorbs, [self.tambourine])
        self.assertNotIn(self.chimes, self.row(plan, "擊樂3").absorb_options)
        self.assertNotIn(self.chimes, self.row(plan, "擊樂3").options)

    def test_lingling_copies_on_absorbed_parts_are_removed_in_the_same_publish(self):
        copy = HuaXiaScore("s1", "hx-1", "p1", "風鈴", lingling_storage_path("hx-1", "a" * 64, "chimes"), "")
        elsewhere = HuaXiaScore("s2", "hx-1", "p3", "琵琶", lingling_storage_path("hx-1", "b" * 64, "pipa"), "")
        plan = self.plan(self.parts, VoiceDecisions(absorb={0: {"p1"}}), [copy, elsewhere])
        self.assertEqual(plan.absorb_replaces, [copy])
        self.assertFalse(plan.blocked)

    def test_scores_put_there_by_others_block_the_group_and_are_listed(self):
        bowing = HuaXiaScore("s1", "hx-1", "p2", "鈴鼓弓法", "hx-1/p2/1700000000_bowing.pdf", "")
        plan = self.plan(self.parts, VoiceDecisions(absorb={0: {"p1", "p2"}}), [bowing])
        self.assertEqual(plan.absorb_others, [(self.tambourine, [bowing])])
        self.assertTrue(plan.blocked)

    def test_an_absent_voice_does_not_take_an_absorbed_part(self):
        self.voices(("擊樂2", Headcount(3)), ("風鈴", Headcount(), False))
        plan = self.plan(self.parts, VoiceDecisions(absorb={0: {"p1"}}))
        self.assertIsNone(self.row(plan, "風鈴").part)
        self.assertEqual(self.row(plan, "擊樂2").absorbs, [self.chimes])


class TestDivisi(PrepareTestCase):
    """HuaXia 上分好分部的曲目：泠靈只對到聲部本身，分部列在聲部底下；名稱對到分部時提示、不配對

    「長城隨想－烽火操」有「二胡」（分部「二胡1」「二胡2」）、「擊樂2」（分部「風鈴」）與「琵琶」。
    """

    def setUp(self):
        super().setUp()
        self.erhu = self.huaxia.add_part(self.fenghuo, "二胡", needed="8", needed_min=8, section="拉弦", sort=1)
        self.erhu_one = self.huaxia.add_part(self.fenghuo, "二胡1", needed="4", needed_min=4, sort=2, parent_id=self.erhu)
        self.erhu_two = self.huaxia.add_part(self.fenghuo, "二胡2", needed="4", needed_min=4, sort=3, parent_id=self.erhu)
        self.percussion = self.huaxia.add_part(self.fenghuo, "擊樂2", needed="3", needed_min=3, section="打擊", sort=4)
        self.chimes = self.huaxia.add_part(self.fenghuo, "風鈴", sort=5, parent_id=self.percussion)
        self.pipa = self.huaxia.add_part(self.fenghuo, "琵琶", needed="2", needed_min=2, sort=6)
        self.project = Project()
        self.group = self.project.add_group("烽火操", "總譜")
        self.project.set_huaxia_link(self.group, HuaXiaLink(piece_id=self.fenghuo, piece_title="長城隨想－烽火操"))

    def voices(self, *rows):
        """樂器表依序為 rows：（聲部，人數）"""
        self.project.set_instruments(self.group, [voice for voice, _headcount in rows])
        for index, (_voice, headcount) in enumerate(rows):
            self.project.set_headcount(self.group, index, headcount)

    def plan(self, decisions=None):
        """按下發譜讀出 HuaXia 的聲部後，這個群組的配對結果"""
        parts = self.service.prepare().parts[self.fenghuo]
        return plan_voices(self.project, self.group, parts, decisions or VoiceDecisions())

    def ids(self, parts):
        """這幾個 HuaXia 聲部的編號"""
        return [part.id for part in parts]

    def row(self, plan, voice):
        """配對結果裡這個聲部那一列"""
        return next(row for row in plan.rows if row.voice == voice)

    def test_voices_pair_with_voices_and_divisi_are_listed_under_them(self):
        self.voices(("二胡", Headcount(8)), ("擊樂2", Headcount(3)))
        plan = self.plan()
        self.assertEqual([(row.match, row.part.id) for row in plan.rows], [
            (PartMatch.NAME, self.erhu), (PartMatch.NAME, self.percussion),
        ])
        self.assertEqual(self.ids(plan.huaxia_only), [self.pipa])
        self.assertEqual({voice: self.ids(divisi) for voice, divisi in plan.divisi.items()}, {
            self.erhu: [self.erhu_one, self.erhu_two], self.percussion: [self.chimes],
        })

    def test_a_divisi_does_not_make_the_name_rule_match_several(self):
        erhu_i = self.huaxia.add_part(self.fenghuo, "二胡一", sort=7)
        self.voices(("二胡 I", Headcount(4)),)
        plan = self.plan()
        row = self.row(plan, "二胡 I")
        self.assertIs(row.match, PartMatch.NAME)
        self.assertEqual(row.part.id, erhu_i)
        self.assertIsNone(row.divisi_match)
        self.assertFalse(plan.blocked)

    def test_only_voices_are_offered_to_choose(self):
        self.voices(("高胡", Headcount(2)),)
        self.assertEqual(self.ids(self.row(self.plan(), "高胡").options), [self.erhu, self.percussion, self.pipa])

    def test_a_voice_whose_name_matches_a_divisi_is_hinted_and_not_paired(self):
        self.voices(("風鈴", Headcount(1)),)
        plan = self.plan()
        row = self.row(plan, "風鈴")
        self.assertIs(row.match, PartMatch.NONE)
        self.assertIsNone(row.part)
        self.assertEqual((row.divisi_match.divisi.id, row.divisi_match.voice.id), (self.chimes, self.percussion))
        self.assertEqual(plan.updates, [])

    def test_a_voice_whose_name_matches_a_divisi_blocks_the_group_until_a_voice_is_picked(self):
        self.voices(("風鈴", Headcount(1)),)
        plan = self.plan()
        self.assertTrue(plan.blocked)
        self.assertEqual([(voice, match.divisi.id) for voice, match in plan.divisi_named], [("風鈴", self.chimes)])
        self.project.choose_huaxia_part(self.group, "風鈴", self.percussion)
        plan = self.plan()
        self.assertEqual(plan.divisi_named, [])
        self.assertFalse(plan.blocked)

    def test_ticking_voices_to_absorb_does_not_clear_the_divisi_block(self):
        self.voices(("風鈴", Headcount(1)),)
        plan = self.plan(VoiceDecisions(absorb={0: {self.pipa}}))
        self.assertEqual([voice for voice, _match in plan.divisi_named], ["風鈴"])
        self.assertTrue(plan.blocked)

    def test_an_absent_voice_whose_name_matches_a_divisi_does_not_block(self):
        self.voices(("二胡", Headcount(8)), ("風鈴", Headcount(1)))
        self.project.set_present(self.group, 1, False)
        plan = self.plan()
        self.assertIsNotNone(self.row(plan, "風鈴").divisi_match)
        self.assertEqual(plan.divisi_named, [])
        self.assertFalse(plan.blocked)

    def test_a_remembered_part_since_made_a_divisi_is_not_paired(self):
        self.voices(("擊樂2 風鈴", Headcount(2)),)
        self.project.choose_huaxia_part(self.group, "擊樂2 風鈴", self.chimes)
        plan = self.plan()
        row = self.row(plan, "擊樂2 風鈴")
        self.assertIsNone(row.part)
        self.assertNotIn(self.chimes, self.ids(row.options))
        self.assertEqual(plan.updates, [])

    def test_headcounts_are_written_to_the_voice_only(self):
        self.voices(("二胡", Headcount(6, 8)),)
        plan = self.plan()
        self.assertEqual([update.part_id for update in plan.updates], [self.erhu])


class TestOrder(PlanTestCase):
    """照樂器表重排這首曲目在 HuaXia 上的聲部（不含分部）：泠靈的聲部照樂器表，只在 HuaXia 的跟著它前一個聲部；預設重排

    HuaXia 上的聲部人數與聲部組都和泠靈一致，要寫的只有順序。
    """

    def flute(self, sort):
        """HuaXia 上的「笛子」"""
        return part("p-flute", "笛子", section="吹管", sort=sort)

    def erhu(self, sort):
        """HuaXia 上的「二胡」"""
        return part("p-erhu", "二胡", section="拉弦", sort=sort)

    def sorts(self, plan):
        """要寫的順序：聲部編號對應新的 sort（只列要改的現有聲部）"""
        return {update.part_id: update.sort for update in plan.updates if update.sort is not None}

    def test_existing_parts_are_put_in_instrument_list_order(self):
        self.voices(("二胡", Headcount(1)), ("笛子", Headcount(1)))
        plan = self.plan([self.flute(1), self.erhu(2)])
        self.assertEqual(plan.order, ["二胡", "笛子"])
        self.assertTrue(plan.reorderable)
        self.assertTrue(plan.reorder)
        self.assertEqual(plan.updates, [
            PartUpdate("p-erhu", "二胡", None, None, None, 1), PartUpdate("p-flute", "笛子", None, None, None, 2),
        ])

    def test_a_part_only_on_huaxia_follows_the_part_before_it(self):
        self.voices(("二胡", Headcount(1)), ("笛子", Headcount(1)))
        misc = part("p-misc", "打擊雜項", section="打擊", sort=2)
        plan = self.plan([self.flute(1), misc, self.erhu(3)])
        self.assertEqual(plan.order, ["二胡", "笛子", "打擊雜項"])
        self.assertEqual(self.sorts(plan), {"p-erhu": 1, "p-flute": 2, "p-misc": 3})

    def test_parts_only_on_huaxia_stay_in_a_run_and_the_first_stays_first(self):
        self.voices(("二胡", Headcount(1)), ("笛子", Headcount(1)))
        plan = self.plan([
            part("p-yunluo", "雲鑼", sort=1), self.flute(2), part("p-chimes", "風鈴", sort=3),
            part("p-tambourine", "鈴鼓", sort=4), self.erhu(5),
        ])
        self.assertEqual(plan.order, ["雲鑼", "二胡", "笛子", "風鈴", "鈴鼓"])

    def test_new_parts_are_placed_by_the_instrument_list(self):
        self.voices(("笛子", Headcount(1)), ("琵琶", Headcount(2)), ("二胡", Headcount(1)))
        plan = self.plan([self.flute(1), self.erhu(2)])
        self.assertEqual(plan.order, ["笛子", "琵琶", "二胡"])
        self.assertEqual(plan.creates, [PartCreate("琵琶", "2", 2, "彈撥", 2)])
        self.assertEqual(self.sorts(plan), {"p-erhu": 3})

    def test_a_voice_not_published_this_time_keeps_its_place(self):
        self.voices(("二胡", Headcount(1)), ("笛子", Headcount(), False))
        plan = self.plan([self.flute(1), self.erhu(2)])
        self.assertEqual(plan.order, ["二胡", "笛子"])
        self.assertEqual(self.sorts(plan), {"p-erhu": 1, "p-flute": 2})

    def test_parts_absorbed_this_time_are_left_out_and_divisi_are_never_sorted(self):
        self.voices(("二胡", Headcount(1)), ("笛子", Headcount(1)), ("擊樂2", Headcount(2)))
        chimes, tambourine = part("p-chimes", "風鈴", sort=2), part("p-tambourine", "鈴鼓", sort=3)
        erhu_one = HuaXiaPart("p-erhu-1", "hx-1", "二胡1", "1", 1, "拉弦", 9, parent_id="p-erhu")
        plan = self.plan(
            [self.flute(1), chimes, tambourine, self.erhu(4), erhu_one], VoiceDecisions(absorb={2: {"p-chimes"}}),
        )
        # 「鈴鼓」的前一個「風鈴」這次被收成分部，改跟「笛子」
        self.assertEqual(plan.order, ["二胡", "笛子", "鈴鼓", "擊樂2"])
        self.assertEqual(self.sorts(plan), {"p-erhu": 1, "p-flute": 2})
        self.assertEqual(plan.creates, [PartCreate("擊樂2", "2", 2, "", 4, ("p-chimes",))])

    def test_unticking_reorder_keeps_existing_sorts_and_appends_new_parts(self):
        self.voices(("二胡", Headcount(1)), ("琵琶", Headcount(2)), ("笛子", Headcount(1)))
        plan = self.plan([self.flute(1), self.erhu(2)], VoiceDecisions(keep_order=True))
        self.assertTrue(plan.reorderable)
        self.assertFalse(plan.reorder)
        self.assertEqual(plan.order, ["笛子", "二胡", "琵琶"])
        self.assertEqual(plan.updates, [])
        self.assertEqual(plan.creates, [PartCreate("琵琶", "2", 2, "彈撥", 3)])

    def test_an_order_already_like_the_instrument_list_writes_no_sort(self):
        self.voices(("笛子", Headcount(1)), ("二胡", Headcount(1)), ("琵琶", Headcount(2)))
        plan = self.plan([self.flute(3), self.erhu(7)])
        self.assertFalse(plan.reorderable)
        self.assertFalse(plan.reorder)
        self.assertEqual(plan.updates, [])
        self.assertEqual(plan.creates, [PartCreate("琵琶", "2", 2, "彈撥", 8)])

    def test_parts_sharing_a_sort_are_reordered_even_when_read_in_instrument_list_order(self):
        # HuaXia 網頁只依 sort 排，同值的先後不固定；泠靈讀到的依名稱排，碰巧和樂器表一樣也照樣重排
        self.voices(("二胡", Headcount(1)), ("笛子", Headcount(1)))
        plan = self.plan([self.erhu(0), self.flute(0)])
        self.assertTrue(plan.reorderable)
        self.assertEqual(plan.order, ["二胡", "笛子"])
        self.assertEqual(self.sorts(plan), {"p-erhu": 1, "p-flute": 2})

    def test_the_order_rides_along_with_an_overwrite(self):
        self.voices(("二胡", Headcount(4)), ("笛子", Headcount(1)))
        plan = self.plan([self.flute(1), self.erhu(2)])
        self.assertEqual(plan.updates[0], PartUpdate("p-erhu", "二胡", "4", 4, None, 1))
        self.assertTrue(plan.updates[0].overwrites)
        self.assertFalse(plan.updates[1].overwrites)


if __name__ == "__main__":
    unittest.main()
