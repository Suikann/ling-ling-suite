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

from core.models import HuaXiaLink, Project
from services.huaxia_client import HuaXiaPart
from services.project_service import ProjectService
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


if __name__ == "__main__":
    unittest.main()
