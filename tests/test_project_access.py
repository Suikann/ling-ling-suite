# -*- coding: utf-8 -*-
"""
專案存取測試

開啟與存檔：專案檔本身讀寫成功就算成功，最近清單與工作區 meta 所屬專案的寫入失敗只帶在結果裡回報。
全部在暫存目錄進行，偏好設定與工作區的位置由建構時注入；寫入失敗以注入的檔案服務模擬。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core.constants import MAX_RECENT_PROJECTS, WORKSPACE_META_FILE, WorkspaceStatus
from core.models import FileInfo, Group, Project
from services.preferences_service import PreferencesService
from services.project_access import ProjectAccess
from services.project_service import ProjectService
from services.workspace_service import WorkspaceService
from failing_writes import FailingWrites


class ProjectAccessTestCase(unittest.TestCase):
    """專案存取測試的共用骨架：偏好設定、工作區、專案檔都在暫存目錄"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.preferences_path = os.path.join(self.temp_dir, "config", "preferences.json")
        self.file_service = FailingWrites()
        self.workspace = WorkspaceService(self.file_service, os.path.join(self.temp_dir, "workspace"))
        self.access = self._access()

    def _access(self) -> ProjectAccess:
        """以偏好設定檔目前的內容建立專案存取（如同重新啟動程式）"""
        preferences = PreferencesService(self.preferences_path, self.file_service)
        preferences.load()
        return ProjectAccess(self.file_service, preferences, self.workspace)

    def _create(self, name: str, directory: str = None) -> str:
        path = os.path.join(directory or self.temp_dir, name)
        with open(path, "w") as f:
            f.write("dummy")
        return path

    def _workspace_folder(self, source: str, owner: str = "") -> str:
        """建立來源合併譜與它在工作區的子資料夾（以來源檔名命名），回傳子資料夾；owner 為 meta 記錄的所屬專案"""
        folder = os.path.join(self.workspace.workspace_dir, os.path.splitext(source)[0])
        self.workspace.write_meta(folder, self._create(source), owner)
        return folder

    def _workspace_part(self, owner: str = "", source: str = "合併譜.pdf") -> tuple:
        """在工作區建一份分譜，回傳（子資料夾，分譜路徑）；owner 為 meta 記錄的所屬專案"""
        folder = self._workspace_folder(source, owner)
        return folder, self._create("高笙.pdf", folder)

    def _project_file(self, name: str, *paths: str) -> str:
        """把引用 paths 的專案存成 name，回傳專案檔路徑（不經專案存取，不動最近清單）"""
        project = Project(groups=[Group(name="g", files=[FileInfo(p, os.path.basename(p)) for p in paths])])
        path = os.path.join(self.temp_dir, name)
        ProjectService().save_project(project, path)
        return path

    def _owner(self, folder: str) -> str:
        return self.workspace.read_meta(folder)["project_path"]


class TestOpen(ProjectAccessTestCase):
    """開啟：專案檔讀得到就算開啟成功"""

    def test_recent_list_write_failure_still_opens_and_updates_meta_owner(self):
        folder, part = self._workspace_part(owner=os.path.join(self.temp_dir, "old.llproj"))
        path = self._project_file("moved.llproj", part)
        self.file_service.block(self.preferences_path)
        result = self.access.open(path)
        self.assertIsNone(result.error)
        self.assertEqual([g.name for g in result.project.groups], ["g"])
        self.assertTrue(result.recent_failed)
        self.assertEqual(self._owner(folder), path)

    def test_meta_write_failure_still_opens_and_reports_the_folder(self):
        old = os.path.join(self.temp_dir, "old.llproj")
        folder, part = self._workspace_part(owner=old)
        path = self._project_file("moved.llproj", part)
        self.file_service.block(os.path.join(folder, WORKSPACE_META_FILE))
        result = self.access.open(path)
        self.assertIsNone(result.error)
        self.assertEqual(result.owner_failed, [folder])
        self.assertFalse(result.recent_failed)
        self.assertEqual(self._owner(folder), old)
        self.assertEqual(self._access().recent_projects(), [path])

    def test_missing_project_is_removed_from_recent_list(self):
        kept = self._project_file("kept.llproj")
        gone = self._project_file("gone.llproj")
        self.access.open(gone)
        self.access.open(kept)
        os.remove(gone)
        result = self.access.open(gone)
        self.assertIsInstance(result.error, FileNotFoundError)
        self.assertIsNone(result.project)
        self.assertEqual(self.access.recent_projects(), [kept])
        self.assertEqual(self._access().recent_projects(), [kept])

    def test_unreadable_project_stays_in_recent_list(self):
        path = self._project_file("broken.llproj")
        self.access.open(path)
        with open(path, "w") as f:
            f.write("{not json")
        result = self.access.open(path)
        self.assertIsNotNone(result.error)
        self.assertEqual(self.access.recent_projects(), [path])

    def test_lists_files_the_project_cannot_find(self):
        existing = self._create("ok.pdf")
        gone = [os.path.join(self.temp_dir, n) for n in ("gone_score.pdf", "gone_part.pdf", "gone_ungrouped.pdf")]
        project = Project(
            groups=[Group(
                name="g", score_file=FileInfo(gone[0], "gone_score.pdf"),
                files=[FileInfo(existing, "ok.pdf"), FileInfo(gone[1], "gone_part.pdf")],
            )],
            ungrouped_files=[FileInfo(gone[2], "gone_ungrouped.pdf")],
        )
        path = os.path.join(self.temp_dir, "p.llproj")
        ProjectService().save_project(project, path)
        self.assertEqual(self.access.open(path).missing_files, gone)


class TestSave(ProjectAccessTestCase):
    """存檔：專案檔寫成就算存檔成功，存檔後的快照就是寫出的內容"""

    def _edited_project(self, *paths: str) -> Project:
        """引用 paths、有未存檔修改的專案"""
        project = Project()
        project.add_group("g", "總譜", [FileInfo(p, os.path.basename(p)) for p in paths])
        return project

    def test_recent_list_write_failure_still_saves_and_updates_meta_owner(self):
        folder, part = self._workspace_part()
        project = self._edited_project(part)
        path = os.path.join(self.temp_dir, "saved.llproj")
        self.file_service.block(self.preferences_path)
        result = self.access.save(project, path)
        self.assertIsNone(result.error)
        self.assertTrue(result.recent_failed)
        self.assertEqual(self._owner(folder), path)
        self.assertFalse(project.is_modified())
        self.assertEqual([g.name for g in ProjectService().load_project(path).groups], ["g"])

    def test_meta_write_failure_still_saves_and_reports_the_folder(self):
        folder, part = self._workspace_part()
        project = self._edited_project(part)
        path = os.path.join(self.temp_dir, "saved.llproj")
        self.file_service.block(os.path.join(folder, WORKSPACE_META_FILE))
        result = self.access.save(project, path)
        self.assertIsNone(result.error)
        self.assertEqual(result.owner_failed, [folder])
        self.assertFalse(project.is_modified())
        self.assertEqual(self._access().recent_projects(), [path])

    def test_project_file_write_failure_fails_and_touches_nothing_else(self):
        folder, part = self._workspace_part()
        project = self._edited_project(part)
        path = os.path.join(self.temp_dir, "saved.llproj")
        self.file_service.block(path)
        result = self.access.save(project, path)
        self.assertIsInstance(result.error, OSError)
        self.assertTrue(project.is_modified())
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self._access().recent_projects(), [])
        self.assertEqual(self._owner(folder), "")

    def test_saved_snapshot_is_what_was_written(self):
        project = self._edited_project()
        path = os.path.join(self.temp_dir, "saved.llproj")
        self.access.save(project, path)
        self.assertTrue(self.access.matches_file(project, path))
        project.set_master_template("{曲名}.pdf")
        self.assertTrue(project.is_modified())
        self.assertFalse(self.access.matches_file(project, path))


class TestRecentList(ProjectAccessTestCase):
    """最近清單：開啟或存檔的專案放到頂端，同一個檔案只留一筆，超過上限時捨棄最舊的"""

    def test_reopening_moves_project_to_top_without_duplicating_another_spelling(self):
        first = self._project_file("first.llproj")
        second = self._project_file("second.llproj")
        self.access.open(first)
        self.access.open(second)
        self.access.open(os.path.join(self.temp_dir, ".", "first.llproj"))
        self.assertEqual([os.path.basename(p) for p in self._access().recent_projects()],
                         ["first.llproj", "second.llproj"])

    def test_forget_if_missing_removes_only_missing_projects(self):
        kept = self._project_file("kept.llproj")
        gone = self._project_file("gone.llproj")
        self.access.open(gone)
        self.access.open(kept)
        os.remove(gone)
        self.assertFalse(self.access.forget_if_missing(kept))
        self.assertTrue(self.access.forget_if_missing(gone))
        self.assertEqual(self._access().recent_projects(), [kept])

    def test_oldest_project_is_dropped_beyond_the_limit(self):
        paths = [self._project_file(f"p{i}.llproj") for i in range(MAX_RECENT_PROJECTS + 1)]
        for path in paths:
            self.access.open(path)
        recent = self._access().recent_projects()
        self.assertEqual(len(recent), MAX_RECENT_PROJECTS)
        self.assertEqual(recent[0], paths[-1])
        self.assertNotIn(paths[0], recent)


class TestWorkspaceCleanupScan(ProjectAccessTestCase):
    """清理工作區：候選專案是已知專案；只剩 meta 的空資料夾沒有已知專案引用就直接刪除（ADR-0001）"""

    def test_known_projects_are_recent_list_plus_meta_owners(self):
        first = self._project_file("first.llproj")
        second = self._project_file("second.llproj")
        self.access.open(first)
        self.access.open(second)
        elsewhere = os.path.join(self.temp_dir, "elsewhere.llproj")
        self._workspace_part(owner=elsewhere, source="a.pdf")
        self._workspace_part(owner=os.path.join(self.temp_dir, ".", "first.llproj"), source="b.pdf")
        self._workspace_part(owner="", source="c.pdf")
        self.assertEqual(self.access.known_projects(), [second, first, elsewhere])

    def test_meta_only_folders_are_removed_unless_a_known_project_references_them(self):
        by_recent, part = self._workspace_part(source="a.pdf")
        self.access.open(self._project_file("recent.llproj", part))
        os.remove(part)
        owner = os.path.join(self.temp_dir, "owner.llproj")
        by_owner, part = self._workspace_part(owner=owner, source="b.pdf")
        self._project_file("owner.llproj", part)
        os.remove(part)
        unreferenced = self._workspace_folder("c.pdf")
        self.access.scan_workspace(None)
        self.assertTrue(os.path.isdir(by_recent))
        self.assertTrue(os.path.isdir(by_owner))
        self.assertFalse(os.path.exists(unreferenced))

    def test_folder_referenced_by_meta_owner_outside_recent_list_belongs_to_that_project(self):
        owner = os.path.join(self.temp_dir, "owner.llproj")
        folder, part = self._workspace_part(owner=owner)
        self._project_file("owner.llproj", part)
        entry = self.access.scan_workspace(None).entries[0]
        self.assertEqual((entry.folder, entry.status, entry.project_path),
                         (folder, WorkspaceStatus.OWNED_BY_OTHER, owner))

    def test_missing_projects_are_removed_from_recent_list(self):
        kept = self._project_file("kept.llproj")
        gone = self._project_file("gone.llproj")
        self.access.open(gone)
        self.access.open(kept)
        os.remove(gone)
        self.access.scan_workspace(None)
        self.assertEqual(self._access().recent_projects(), [kept])


if __name__ == '__main__':
    unittest.main()
