# -*- coding: utf-8 -*-
"""
清除本機檔案的測試：專案在臨時目錄的檔案先存進假 Google，再經清除服務核對 MD5、永久刪掉本機那份，
檢查每份檔的歸類、刪掉的檔與資料夾、斷網時不能清除，以及清除後推算的「只在譜庫」
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.catalog_errors import CatalogError, CatalogErrorKind
from core.catalog_constants import SHEET_PARTS
from core.clear_local import ClearStatus
from core.models import FileInfo, UndoMapping
from services.clear_local_service import ClearLocalService
from services.deposit_service import DepositService
from services.file_service import FileService
from services.preferences_service import PreferencesService
from services.project_access import ProjectAccess
from services.project_service import ProjectService
from services.workspace_service import WorkspaceService
from test_catalog_link import records
from test_deposit import DepositFixture


class ClearTestCase(DepositFixture, unittest.TestCase):
    """清除服務對假 Google 與臨時目錄；專案的檔都已存進譜庫（情境見 DepositFixture）"""

    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp)
        self.build(self.VOICES)
        self.home = os.path.join(self.temp, "home")
        self.clearer = ClearLocalService(FileService(), home=self.home)
        self.archive()

    def archive(self) -> None:
        """把專案的檔存進譜庫，指紋交回專案"""
        service = DepositService(FileService())
        plan = service.preview(self.project, self.catalog)
        outcome = service.deposit(plan, self.catalog, "office@example.com", lambda: False, lambda progress: None)
        self.project.record_deposit(outcome.records)
        self.project.mark_saved()

    def clear(self, group_ids=None, should_cancel=lambda: False):
        """預覽後清除（預設清除全部群組），回傳（預覽，結果）"""
        plan = self.clearer.preview(self.project, self.catalog)
        chosen = [group.group_id for group in plan.groups] if group_ids is None else group_ids
        return plan, self.clearer.clear(plan, chosen, should_cancel, lambda progress: None)

    def registered_id(self, voice: str) -> str:
        """分譜工作表裡第一樂章這個聲部登記的 Drive 檔案 ID；總譜以「總譜」為鍵"""
        return next(
            r["drive_file_id"] for r in records(self.google, SHEET_PARTS)
            if (r["is_score"] and voice == "總譜") or (not r["is_score"] and r["instrument_name"] == voice)
        )

    def statuses(self, plan) -> dict:
        """第一個群組每份檔的歸類：檔名 → ClearStatus"""
        return {os.path.basename(item.path): item.status for item in plan.groups[0].items}


class TestVerification(ClearTestCase):
    """核對：只刪本機 MD5 跟 Drive 上登記的那份一致的，其餘不刪並各自歸類"""

    def test_only_files_matching_the_catalog_are_deleted_and_the_rest_are_classified(self):
        with open(self.parts[1].original_path, "wb") as f:
            f.write(b"new pipa")
        self.google.drive.trash(self.registered_id("總譜"))
        plan, outcome = self.clear()
        self.assertEqual(self.statuses(plan), {
            "總譜.pdf": ClearStatus.NOT_IN_CATALOG,
            "二胡 I.pdf": ClearStatus.CLEARABLE,
            "琵琶.pdf": ClearStatus.DIFFERENT,
        })
        self.assertEqual([item.path for item in outcome.cleared], [self.parts[0].original_path])
        self.assertFalse(os.path.exists(self.parts[0].original_path))
        self.assertTrue(os.path.exists(self.parts[1].original_path))
        self.assertTrue(os.path.exists(self.score.original_path))

    def test_files_never_registered_in_the_catalog_are_not_deleted(self):
        self.build(self.VOICES)
        plan, outcome = self.clear()
        self.assertEqual(set(self.statuses(plan).values()), {ClearStatus.NOT_IN_CATALOG})
        self.assertEqual(outcome.cleared, [])
        self.assertTrue(os.path.exists(self.parts[0].original_path))

    def test_files_of_a_group_not_linked_to_the_catalog_count_as_not_archived(self):
        group = self.project.add_group("另一首", "總譜")
        self.project.add_files([self.put("另一首/笛子.pdf", b"dizi")], group)
        plan, outcome = self.clear()
        self.assertEqual([item.status for item in plan.groups[1].items], [ClearStatus.NOT_ARCHIVED])
        self.assertFalse(plan.groups[1].choosable)
        self.assertTrue(os.path.exists(os.path.join(self.out, "另一首", "笛子.pdf")))

    def test_only_chosen_groups_are_cleared(self):
        plan, outcome = self.clear(group_ids=[])
        self.assertEqual(outcome.cleared, [])
        self.assertTrue(os.path.exists(self.parts[0].original_path))

    def test_a_file_changed_after_the_preview_is_not_deleted(self):
        plan = self.clearer.preview(self.project, self.catalog)
        with open(self.parts[0].original_path, "wb") as f:
            f.write(b"changed meanwhile")
        outcome = self.clearer.clear(plan, [plan.groups[0].group_id], lambda: False, lambda progress: None)
        self.assertNotIn(self.parts[0].original_path, [item.path for item in outcome.cleared])
        self.assertTrue(os.path.exists(self.parts[0].original_path))

    def test_cleared_files_report_the_catalog_fingerprint(self):
        _, outcome = self.clear()
        self.assertEqual(
            outcome.fingerprints,
            {item.path: self.google.drive.file(self.registered_id(name)).md5
             for item, name in zip(outcome.cleared, ("總譜", "二胡 I", "琵琶"))},
        )


class TestEmptyFolders(ClearTestCase):
    """清空後變空的資料夾往上刪到輸出位置為止（含輸出位置）；家目錄與標準使用者資料夾不刪"""

    def move_output(self, target: str, output_directory: str) -> None:
        """把整個輸出位置搬到 target，專案的檔跟著改路徑，輸出位置設成 output_directory"""
        os.makedirs(os.path.dirname(target), exist_ok=True)
        old = self.out
        os.rename(old, target)
        self.project.replace_paths([
            UndoMapping(ref.file.original_path, os.path.join(target, os.path.relpath(ref.file.original_path, old)))
            for ref in self.project.file_refs()
        ])
        self.project.set_output_settings(output_directory=output_directory)
        self.out = target

    def test_emptied_folders_are_deleted_up_to_and_including_the_output_location(self):
        self.clear()
        self.assertFalse(os.path.exists(self.out))
        self.assertTrue(os.path.isdir(os.path.dirname(self.out)))

    def test_a_folder_that_still_has_other_files_is_kept(self):
        self.put("春江花月夜/分譜/筆記.txt", b"note")
        self.clear()
        self.assertEqual(os.listdir(os.path.join(self.out, "春江花月夜", "分譜")), ["筆記.txt"])

    def test_a_standard_user_folder_used_as_the_output_location_is_kept(self):
        desktop = os.path.join(self.home, "Desktop")
        self.move_output(desktop, desktop)
        self.clear()
        self.assertEqual(os.listdir(desktop), [])

    def test_the_home_folder_used_as_the_output_location_is_kept(self):
        self.move_output(self.home, self.home)
        self.clear()
        self.assertEqual(os.listdir(self.home), [])

    def test_a_folder_given_as_standard_by_the_system_is_kept(self):
        documents = os.path.join(self.temp, "文件")
        self.clearer = ClearLocalService(FileService(), home=self.home, standard_folders=[documents])
        self.move_output(documents, documents)
        self.clear()
        self.assertEqual(os.listdir(documents), [])

    def test_renamed_in_place_the_output_location_is_the_folder_the_sources_were_in(self):
        source = os.path.join(self.temp, "來源")
        self.move_output(source, "")
        self.clear()
        self.assertFalse(os.path.exists(source))
        self.assertTrue(os.path.isdir(self.temp))


class TestCatalogOnly(ClearTestCase):
    """清除後：本機沒有、群組記著存入指紋的檔推算為只在譜庫，不算找不到；放回原位就恢復正常"""

    def setUp(self):
        super().setUp()
        _, outcome = self.clear()
        self.project.record_fingerprints(outcome.fingerprints)
        self.path = os.path.join(self.temp, "專案.llproj")
        ProjectService().save_project(self.project, self.path)
        preferences = PreferencesService(os.path.join(self.temp, "preferences.json"), FileService())
        workspace = WorkspaceService(FileService(), os.path.join(self.temp, "workspace"))
        self.access = ProjectAccess(FileService(), preferences, workspace)

    def paths(self) -> list:
        """群組的總譜與分譜的路徑"""
        return [self.score.original_path] + [part.original_path for part in self.parts]

    def test_cleared_files_are_only_in_the_catalog_and_not_reported_missing_on_open(self):
        result = self.access.open(self.path)
        self.assertEqual(result.missing_files, [])
        self.assertEqual(result.catalog_only_files, self.paths())

    def test_a_cleared_group_shows_as_only_in_the_catalog_and_cannot_be_chosen(self):
        plan = self.clearer.preview(self.project, self.catalog)
        self.assertEqual(set(self.statuses(plan).values()), {ClearStatus.CATALOG_ONLY})
        self.assertTrue(plan.groups[0].cleared)
        self.assertFalse(plan.groups[0].choosable)

    def test_a_file_put_back_in_place_is_normal_again(self):
        self.put("春江花月夜/分譜/琵琶.pdf", "琵琶".encode())
        result = self.access.open(self.path)
        self.assertEqual(result.catalog_only_files, self.paths()[:2])
        self.assertEqual(result.missing_files, [])
        self.assertEqual(self.statuses(self.clearer.preview(self.project, self.catalog))["琵琶.pdf"], ClearStatus.CLEARABLE)

    def test_a_missing_file_with_no_copy_in_the_catalog_is_still_missing(self):
        group = self.project.add_group("另一首", "總譜")
        lost = self.put("另一首/笛子.pdf", b"dizi")
        self.project.add_files([lost], group)
        ProjectService().save_project(self.project, self.path)
        os.remove(lost.original_path)
        result = self.access.open(self.path)
        self.assertEqual(result.missing_files, [lost.original_path])
        plan = self.clearer.preview(self.project, self.catalog)
        self.assertEqual([item.status for item in plan.groups[1].items], [ClearStatus.MISSING])


class TestOffline(ClearTestCase):
    """斷網時整個動作不能用：預覽讀不到譜庫就丟出，什麼都不刪"""

    def test_preview_fails_without_network_and_nothing_is_deleted(self):
        self.google.fail_next(CatalogErrorKind.OFFLINE, times=10)
        with self.assertRaises(CatalogError) as raised:
            self.clearer.preview(self.project, self.catalog)
        self.assertIs(raised.exception.kind, CatalogErrorKind.OFFLINE)
        self.assertTrue(os.path.exists(self.parts[0].original_path))


if __name__ == "__main__":
    unittest.main()
