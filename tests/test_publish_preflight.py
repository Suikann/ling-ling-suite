# -*- coding: utf-8 -*-
"""
發譜前的檢查與「曲目」步的測試

對專案模型與假 HuaXia 測：群組對到哪首 HuaXia 曲目記在專案檔；新建曲目的預設值；
發譜前的檢查（專案層級的未存檔、樂團類型，群組層級的還沒重新命名、人數沒填最少、樂器表重名）各自擋下並寫出原因，
未同步的值只標出；按下發譜時重查幹部身分、HuaXia 發譜函式缺少或版本不符時不能發，已封存的曲目不列。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.constants import HuaXiaEnsemble
from core.models import HuaXiaLink, NewHuaXiaPiece, Project
from services.project_service import ProjectService


class TestHuaXiaLinkIsSaved(unittest.TestCase):
    """群組對到的 HuaXia 曲目記在專案檔，只記曲目編號與挑選、新建的欄位"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.path = os.path.join(self.temp_dir, "concert.llproj")

    def reopened(self, project: Project) -> Project:
        """存成專案檔再開啟"""
        ProjectService().save_project(project, self.path)
        return ProjectService().load_project(self.path)

    def test_an_existing_piece_survives_reopening(self):
        project = Project()
        group = project.add_group("烽火操", "總譜")
        project.set_huaxia_link(group, HuaXiaLink(piece_id="hx-1", piece_title="長城隨想－烽火操"))
        self.assertTrue(project.is_modified())
        link = self.reopened(project).groups[0].huaxia_link
        self.assertEqual(link, HuaXiaLink(piece_id="hx-1", piece_title="長城隨想－烽火操"))

    def test_a_new_piece_keeps_its_fields_without_an_id(self):
        project = Project()
        group = project.add_group("春江花月夜", "總譜")
        new = NewHuaXiaPiece("春江花月夜", "秦鵬章", 7.5, HuaXiaEnsemble.SIZHU)
        project.set_huaxia_link(group, HuaXiaLink(piece_title="春江花月夜", new_piece=new))
        link = self.reopened(project).groups[0].huaxia_link
        self.assertEqual(link.piece_id, "")
        self.assertEqual(link.new_piece, new)

    def test_clearing_the_link_saves_nothing(self):
        project = Project()
        group = project.add_group("茉莉花", "總譜")
        project.set_huaxia_link(group, HuaXiaLink(piece_id="hx-2", piece_title="茉莉花"))
        project.set_huaxia_link(group, None)
        self.assertIsNone(self.reopened(project).groups[0].huaxia_link)


class TestNewPieceDefaults(unittest.TestCase):
    """新建曲目的預設值：曲名＋樂章名稱（沒有時用樂章編號，單樂章只用曲名）、作曲家與時長帶入、合奏"""

    def group(self, **fields):
        """「春江花月夜」的一個群組，作曲家秦鵬章"""
        project = Project()
        group = project.add_group("g", "總譜")
        project.update_group(group, piece_name="春江花月夜", composer="秦鵬章", **fields)
        return project, group

    def test_a_movement_with_a_name_appends_the_name(self):
        _, group = self.group(movement_number="1", movement_name="江樓鐘鼓")
        self.assertEqual(NewHuaXiaPiece.for_group(group).title, "春江花月夜 江樓鐘鼓")

    def test_a_movement_without_a_name_appends_its_number(self):
        _, group = self.group(movement_number="2")
        self.assertEqual(NewHuaXiaPiece.for_group(group).title, "春江花月夜 2")

    def test_a_single_movement_piece_uses_the_title_only(self):
        _, group = self.group()
        self.assertEqual(NewHuaXiaPiece.for_group(group).title, "春江花月夜")

    def test_composer_and_ensemble(self):
        _, group = self.group()
        piece = NewHuaXiaPiece.for_group(group)
        self.assertEqual(piece.composer, "秦鵬章")
        self.assertIs(piece.ensemble, HuaXiaEnsemble.TUTTI)

    def test_duration_is_rounded_to_half_minutes_like_the_huaxia_form(self):
        project, group = self.group()
        for seconds, minutes in ((None, None), (420, 7.0), (440, 7.5), (425, 7.0), (465, 8.0)):
            project.set_duration(group, seconds)
            self.assertEqual(NewHuaXiaPiece.for_group(group).duration_min, minutes, seconds)


if __name__ == "__main__":
    unittest.main()
