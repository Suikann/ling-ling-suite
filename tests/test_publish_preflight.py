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

from core.constants import HuaXiaEnsemble, OrchestraType
from core.models import CatalogChangeKind, CatalogLink, CatalogSnapshot, FileInfo, Headcount, HuaXiaLink, NewHuaXiaPiece, Project
from services.huaxia_auth_service import AccountStanding, HuaXiaAuthService, NotOfficer
from services.huaxia_client import PUBLISH_INTERFACE_VERSION, HuaXiaClient, HuaXiaError, HuaXiaErrorKind, HuaXiaPiece
from services.project_service import ProjectService
from services.publish_service import FunctionProblem, ProjectBlock, PublishService, check_groups, project_blocks
from services.secure_storage import SecureStorage
from tests.fake_browser import CallbackBrowser
from tests.fake_huaxia import FakeHuaXia
from tests.memory_keyring import MemoryKeyring

OUT_DIR = os.path.abspath("out")
IN_DIR = os.path.abspath("in")


def renamed_project() -> Project:
    """已存檔、國樂團的專案：一個已重新命名到輸出位置的群組（總譜、二胡 I、琵琶，人數都已填）"""
    project = Project(master_template="{序號}-{樂器}.pdf", output_directory=OUT_DIR)
    group = project.add_group("烽火操", "總譜")
    project.set_instruments(group, ["二胡 I", "琵琶"])
    names = ("00-總譜.pdf", "01-二胡 I.pdf", "02-琵琶.pdf")
    project.add_files([FileInfo(os.path.join(OUT_DIR, n), n) for n in names], group)
    project.set_headcount(group, 0, Headcount(6, 8))
    project.set_headcount(group, 1, Headcount(2))
    project.mark_saved()
    return project


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


class TestProjectBlocks(unittest.TestCase):
    """擋下整個發譜的專案層級檢查：未存檔、不是國樂團"""

    def test_a_saved_chinese_orchestra_project_passes(self):
        self.assertEqual(project_blocks(renamed_project()), [])

    def test_unsaved_changes_block(self):
        project = renamed_project()
        project.update_group(project.groups[0], composer="劉文金")
        self.assertEqual(project_blocks(project), [ProjectBlock.UNSAVED])

    def test_a_western_orchestra_project_is_blocked(self):
        project = renamed_project()
        project.set_orchestra_type(OrchestraType.WESTERN)
        project.mark_saved()
        self.assertEqual(project_blocks(project), [ProjectBlock.NOT_CHINESE_ORCHESTRA])


class TestGroupChecks(unittest.TestCase):
    """擋下個別群組的檢查：還沒重新命名、人數沒填最少、樂器表重名；未同步的值只標出"""

    def setUp(self):
        self.project = renamed_project()
        self.group = self.project.groups[0]

    def check(self):
        """唯一一個群組的檢查結果"""
        checks = check_groups(self.project)
        self.assertEqual([c.group for c in checks], [self.group])
        return checks[0]

    def test_a_renamed_group_with_headcounts_is_not_blocked(self):
        check = self.check()
        self.assertFalse(check.blocked)
        self.assertFalse(check.not_renamed)
        self.assertEqual(check.unfilled, [])
        self.assertEqual(check.duplicate_rows, [])

    def test_files_not_yet_at_the_output_location_are_not_renamed(self):
        part = self.group.files[1]
        self.project.remove_paths([part.original_path])
        self.project.add_files([FileInfo(os.path.join(IN_DIR, "pipa.pdf"), "pipa.pdf")], self.group)
        check = self.check()
        self.assertTrue(check.not_renamed)
        self.assertTrue(check.blocked)

    def test_a_template_change_after_renaming_means_not_renamed(self):
        self.project.set_master_template("{樂器}.pdf")
        self.assertTrue(self.check().not_renamed)

    def test_a_group_without_files_is_not_renamed(self):
        group = self.project.add_group("空的", "總譜")
        checks = check_groups(self.project)
        self.assertTrue(next(c for c in checks if c.group is group).not_renamed)

    def test_voices_without_a_minimum_are_listed(self):
        self.project.set_headcount(self.group, 1, Headcount())
        check = self.check()
        self.assertEqual(check.unfilled, ["琵琶"])
        self.assertTrue(check.blocked)

    def test_absent_voices_need_no_headcount(self):
        self.project.add_instrument(self.group, "古箏")
        self.project.set_present(self.group, 2, False)
        self.assertEqual(self.check().unfilled, [])

    def test_duplicate_rows_are_listed(self):
        self.group.instruments.append("二胡1")
        self.group.present.append(True)
        self.group.headcounts.append(Headcount(1))
        check = self.check()
        self.assertEqual(check.duplicate_rows, [0, 2])
        self.assertTrue(check.blocked)

    def test_unsynced_values_are_marked_but_do_not_block(self):
        self.group.catalog_link = CatalogLink(
            "e1", snapshot=CatalogSnapshot(
                [("二胡 I", Headcount(6, 8)), ("琵琶", Headcount(3))], sections={"二胡 I": "拉弦", "琵琶": "彈撥"},
            ),
            unsynced=True,
        )
        check = self.check()
        self.assertEqual([(c.kind, c.voice) for c in check.unsynced], [(CatalogChangeKind.HEADCOUNT, "琵琶")])
        self.assertFalse(check.blocked)


class PrepareTestCase(unittest.TestCase):
    """假 HuaXia 上有一位已登入的幹部 officer_amy、相符版本的發譜函式與三首曲目（其中一首已封存）"""

    def setUp(self):
        self.huaxia = FakeHuaXia()
        self.huaxia.add_account("officer_amy", roles=("member", "officer"))
        self.huaxia.sign_in_as("officer_amy")
        self.huaxia.install_publish_function(PUBLISH_INTERFACE_VERSION)
        self.moli = self.huaxia.add_piece("茉莉花", composer="江蘇民歌", duration_min=4.5)
        self.fenghuo = self.huaxia.add_piece("長城隨想－烽火操", composer="劉文金", ensemble="tutti")
        self.huaxia.add_piece("春節序曲", archived=True)
        browser = CallbackBrowser(provider=self.huaxia.authorize)
        self.addCleanup(browser.finish)
        self.auth = HuaXiaAuthService(
            HuaXiaClient(session=self.huaxia), storage=SecureStorage("test-huaxia", backend=MemoryKeyring()),
            opener=browser,
        )
        self.auth.authenticate()
        self.service = PublishService(self.auth)


class TestPrepare(PrepareTestCase):
    """按下發譜時：重查幹部身分、查發譜函式的版本，都過了才讀可以挑的曲目"""

    def test_an_officer_gets_the_unarchived_pieces_ordered_by_title_like_huaxia(self):
        readiness = self.service.prepare()
        self.assertTrue(readiness.ready)
        self.assertEqual(readiness.pieces, [
            HuaXiaPiece(self.moli, "茉莉花", "江蘇民歌", 4.5, HuaXiaEnsemble.TUTTI),
            HuaXiaPiece(self.fenghuo, "長城隨想－烽火操", "劉文金", None, HuaXiaEnsemble.TUTTI),
        ])
        self.assertEqual(readiness.discord_name, "officer_amy")

    def test_someone_no_longer_an_officer_is_signed_out_and_told_why(self):
        self.huaxia.remove_roles("officer_amy")
        readiness = self.service.prepare()
        self.assertFalse(readiness.ready)
        self.assertEqual(readiness.not_officer, NotOfficer(AccountStanding.NOT_OFFICER, "officer_amy"))
        self.assertFalse(self.auth.status().signed_in)
        self.assertEqual(readiness.pieces, [])

    def test_without_the_publish_function_publishing_is_unavailable(self):
        self.huaxia.install_publish_function(None)
        readiness = self.service.prepare()
        self.assertFalse(readiness.ready)
        self.assertIs(readiness.function_problem, FunctionProblem.MISSING)
        self.assertEqual(readiness.pieces, [])
        self.assertFalse(any(r["path"] == "/rest/v1/pieces" for r in self.huaxia.requests))

    def test_a_different_version_is_unavailable(self):
        self.huaxia.install_publish_function(PUBLISH_INTERFACE_VERSION + 1)
        readiness = self.service.prepare()
        self.assertIs(readiness.function_problem, FunctionProblem.VERSION_MISMATCH)
        self.assertEqual(readiness.function_version, PUBLISH_INTERFACE_VERSION + 1)
        self.assertFalse(readiness.ready)

    def test_not_signed_in_raises(self):
        self.auth.logout()
        with self.assertRaises(HuaXiaError) as raised:
            self.service.prepare()
        self.assertIs(raised.exception.kind, HuaXiaErrorKind.SIGNED_OUT)


if __name__ == "__main__":
    unittest.main()
