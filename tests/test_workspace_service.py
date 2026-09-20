# -*- coding: utf-8 -*-
"""
工作區服務單元測試
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.constants import (
    ATOMIC_WRITE_TEMP_SUFFIX, WORKSPACE_FOLDER_HASH_LENGTH, WORKSPACE_META_FILE, WorkspaceStatus,
)
from core.models import FileInfo, Group, Project
from services.file_service import FileService
from services.project_service import ProjectService
from services.workspace_service import WorkspaceService


class _NoTrashFileService(FileService):
    """測試用：刪除直接移除，不進資源回收桶"""

    def delete_file(self, path):
        os.remove(path)

    def delete_directory(self, path):
        shutil.rmtree(path)


class TestWorkspaceService(unittest.TestCase):
    """WorkspaceService 測試"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_dir = os.path.join(self.temp_dir, "workspace")
        self.file_service = _NoTrashFileService()
        self.service = WorkspaceService(self.file_service, self.workspace_dir)
        self.source = self._create_file("3307 分譜.pdf")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_file(self, name, directory=None, content="dummy"):
        path = os.path.join(directory or self.temp_dir, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def _project_file(self, name):
        """專案檔的假想位置：temp_dir 下的絕對路徑，任何 OS 都能原樣寫進 meta 再讀回"""
        return os.path.join(self.temp_dir, name + ".llproj")

    def _project_with(self, *paths, ungrouped=()):
        project = Project()
        project.groups.append(Group(
            name="g", files=[FileInfo(p, os.path.basename(p)) for p in paths],
        ))
        project.ungrouped_files = [FileInfo(p, os.path.basename(p)) for p in ungrouped]
        return project

    def _save_project(self, project, name):
        path = os.path.join(self.temp_dir, name)
        ProjectService().save_project(project, path)
        return path

    # --- 路徑 ---

    def test_folder_name_is_fixed_length_hash(self):
        folder = self.service.folder_for_source(self.source)
        self.assertEqual(os.path.dirname(folder), self.workspace_dir)
        self.assertEqual(len(os.path.basename(folder)), WORKSPACE_FOLDER_HASH_LENGTH)

    def test_folder_name_is_deterministic_and_source_specific(self):
        other = self._create_file("other.pdf")
        self.assertEqual(
            self.service.folder_for_source(self.source),
            self.service.folder_for_source(self.source),
        )
        self.assertNotEqual(
            self.service.folder_for_source(self.source),
            self.service.folder_for_source(other),
        )

    def test_same_file_name_in_different_folders_do_not_collide(self):
        sub = os.path.join(self.temp_dir, "another")
        os.makedirs(sub)
        twin = self._create_file("3307 分譜.pdf", sub)
        self.assertNotEqual(
            self.service.folder_for_source(self.source),
            self.service.folder_for_source(twin),
        )

    def test_is_in_workspace(self):
        folder = self.service.prepare_folder(self.source)
        self.assertTrue(self.service.is_in_workspace(os.path.join(folder, "a.pdf")))
        self.assertFalse(self.service.is_in_workspace(self.source))
        self.assertIsNone(self.service.folder_of(self.source))
        self.assertEqual(self.service.folder_of(os.path.join(folder, "a.pdf")), folder)

    # --- 生命週期 ---

    def test_prepare_folder_writes_meta(self):
        folder = self.service.prepare_folder(self.source, self._project_file("a"))
        meta = json.load(open(os.path.join(folder, WORKSPACE_META_FILE), encoding="utf-8"))
        self.assertEqual(meta["source_name"], "3307 分譜.pdf")
        self.assertEqual(meta["source_path"], os.path.abspath(self.source))
        self.assertEqual(meta["project_path"], self._project_file("a"))
        self.assertIn("created_at", meta)

    def test_prepare_folder_stores_project_path_absolute(self):
        # meta 會被別的 session 讀，相對路徑離開當下的工作目錄就沒有意義
        folder = self.service.prepare_folder(self.source, "a.llproj")
        self.assertEqual(
            self.service.read_meta(folder)["project_path"], os.path.join(os.getcwd(), "a.llproj"),
        )

    def test_prepare_folder_by_unsaved_project_clears_previous_owner(self):
        # 未存檔專案重新分割後，輸出已不是先前專案的，所屬專案回到未知
        folder = self.service.prepare_folder(self.source, self._project_file("a"))
        self.service.prepare_folder(self.source, "")
        self.assertEqual(self.service.read_meta(folder)["project_path"], "")

    def test_clear_outputs_keeps_meta(self):
        folder = self.service.prepare_folder(self.source)
        self._create_file("高笙.pdf", folder)
        self._create_file("揚琴.pdf", folder)
        self.service.clear_outputs(folder)
        self.assertEqual(self.service.list_outputs(folder), [])
        self.assertIsNotNone(self.service.read_meta(folder))

    def test_remove_folder_keeps_folder_with_outputs(self):
        folder = self.service.prepare_folder(self.source)
        self._create_file("高笙.pdf", folder)
        self.service.remove_folder(folder)
        self.assertTrue(os.path.isdir(folder))

    def test_emptied_folder_keeps_meta_until_purge(self):
        folder = self.service.prepare_folder(self.source)
        part = self._create_file("高笙.pdf", folder)
        os.remove(part)
        # 搬空後 meta 仍在，復原時能辨識來源
        self.assertIsNotNone(self.service.read_meta(folder))
        removed = self.service.purge_empty_folders()
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(folder))

    def test_remove_folder_clears_atomic_write_residue(self):
        # 寫 meta.json 途中當機留下的暫名不該讓搬空的子資料夾清不掉
        folder = self.service.prepare_folder(self.source)
        self._create_file(WORKSPACE_META_FILE + ATOMIC_WRITE_TEMP_SUFFIX, folder, "{partial")
        self.service.remove_folder(folder)
        self.assertFalse(os.path.exists(folder))

    def test_remove_folder_clears_residue_even_without_meta(self):
        folder = self.service.prepare_folder(self.source)
        os.remove(os.path.join(folder, WORKSPACE_META_FILE))
        self._create_file(WORKSPACE_META_FILE + ATOMIC_WRITE_TEMP_SUFFIX, folder, "{partial")
        self.service.remove_folder(folder)
        self.assertFalse(os.path.exists(folder))

    def test_purge_skips_in_use_and_non_empty(self):
        f_in_use = self.service.prepare_folder(self._create_file("a.pdf"))
        f_full = self.service.prepare_folder(self._create_file("b.pdf"))
        self._create_file("x.pdf", f_full)
        f_empty = self.service.prepare_folder(self._create_file("c.pdf"))
        removed = self.service.purge_empty_folders({os.path.normcase(os.path.abspath(f_in_use))})
        self.assertEqual(removed, 1)
        self.assertTrue(os.path.isdir(f_in_use))
        self.assertTrue(os.path.isdir(f_full))
        self.assertFalse(os.path.exists(f_empty))

    def test_scan_purges_empty_folders_first(self):
        f_empty = self.service.prepare_folder(self._create_file("a.pdf"))
        f_full = self.service.prepare_folder(self._create_file("b.pdf"))
        self._create_file("x.pdf", f_full)
        scan = self.service.scan(None, [], ProjectService().load_project)
        self.assertEqual([e.folder for e in scan.entries], [f_full])
        self.assertFalse(os.path.exists(f_empty))

    def test_update_project_path(self):
        folder = self.service.prepare_folder(self.source)
        part = self._create_file("高笙.pdf", folder)
        project = self._project_with(part)
        failed = self.service.update_project_path(project, self._project_file("x"))
        self.assertEqual(failed, [])
        self.assertEqual(
            self.service.read_meta(folder)["project_path"],
            self._project_file("x"),
        )

    def test_update_project_path_reports_folders_it_could_not_write(self):
        f_ok = self.service.prepare_folder(self.source)
        f_bad = self.service.prepare_folder(self._create_file("b.pdf"))
        project = self._project_with(self._create_file("x.pdf", f_ok), self._create_file("x.pdf", f_bad))
        real_write = self.file_service.write_json_atomic

        def failing_write(path, data):
            if os.path.normcase(path).startswith(os.path.normcase(f_bad)):
                raise OSError("locked")
            real_write(path, data)

        self.file_service.write_json_atomic = failing_write
        failed = self.service.update_project_path(project, self._project_file("x"))
        self.assertEqual([os.path.normcase(f) for f in failed], [os.path.normcase(f_bad)])
        self.assertEqual(self.service.read_meta(f_ok)["project_path"], self._project_file("x"))

    # --- 跨專案 ---

    def test_other_owner_is_none_for_same_project_or_unowned(self):
        mine = os.path.join(self.temp_dir, "mine.llproj")
        folder = self.service.prepare_folder(self.source, mine)
        self.assertIsNone(self.service.other_owner(folder, mine))
        unowned = self.service.prepare_folder(self._create_file("b.pdf"), "")
        self.assertIsNone(self.service.other_owner(unowned, mine))
        self.assertIsNone(self.service.other_owner(unowned, ""))

    def test_other_owner_names_existing_project(self):
        theirs = self._save_project(Project(), "theirs.llproj")
        folder = self.service.prepare_folder(self.source, theirs)
        owner = self.service.other_owner(folder, os.path.join(self.temp_dir, "mine.llproj"))
        self.assertEqual((owner.project_path, owner.exists), (theirs, True))
        # 尚未存檔的專案也算「不是目前專案」
        self.assertEqual(self.service.other_owner(folder, "").project_path, theirs)

    def test_other_owner_flags_missing_project(self):
        gone = os.path.join(self.temp_dir, "gone.llproj")
        folder = self.service.prepare_folder(self.source, gone)
        owner = self.service.other_owner(folder, os.path.join(self.temp_dir, "mine.llproj"))
        self.assertEqual((owner.project_path, owner.exists), (gone, False))

    def test_other_owner_forgets_previous_project_after_unsaved_resplit(self):
        theirs = self._save_project(Project(), "theirs.llproj")
        folder = self.service.prepare_folder(self.source, theirs)
        self.assertIsNotNone(self.service.other_owner(folder, ""))
        # 未存檔專案取代過一次後，再次重新分割取代的是自己上次的嘗試，不該再點名別人
        self.service.prepare_folder(self.source, "")
        self.assertIsNone(self.service.other_owner(folder, ""))

    def test_other_owner_is_none_without_meta(self):
        folder = os.path.join(self.workspace_dir, "deadbeef")
        os.makedirs(folder)
        self.assertIsNone(self.service.other_owner(folder, ""))

    # --- 掃描與清理 ---

    def test_scan_statuses(self):
        loader = ProjectService().load_project
        # 使用中：目前專案引用
        f_in_use = self.service.prepare_folder(self._create_file("a.pdf"))
        p_in_use = self._create_file("x.pdf", f_in_use)
        current = self._project_with(p_in_use)
        # 屬於其他專案：最近專案引用
        f_owned = self.service.prepare_folder(self._create_file("b.pdf"))
        p_owned = self._create_file("x.pdf", f_owned)
        other_path = self._save_project(self._project_with(p_owned), "other.llproj")
        # 所屬專案無法讀取
        broken_path = os.path.join(self.temp_dir, "broken.llproj")
        with open(broken_path, "w") as f:
            f.write("{not json")
        f_unreadable = self.service.prepare_folder(self._create_file("c.pdf"), broken_path)
        self._create_file("x.pdf", f_unreadable)
        # 孤兒
        f_orphan = self.service.prepare_folder(self._create_file("d.pdf"))
        self._create_file("x.pdf", f_orphan)
        # 來源不明：沒有 meta
        f_unknown = os.path.join(self.workspace_dir, "deadbeef")
        os.makedirs(f_unknown)
        self._create_file("x.pdf", f_unknown)
        missing_path = os.path.join(self.temp_dir, "gone.llproj")

        scan = self.service.scan(current, [other_path, broken_path, missing_path], loader)
        status = {e.folder: e.status for e in scan.entries}
        self.assertEqual(status[f_in_use], WorkspaceStatus.IN_USE.value)
        self.assertEqual(status[f_owned], WorkspaceStatus.OWNED_BY_OTHER.value)
        self.assertEqual(status[f_unreadable], WorkspaceStatus.OWNER_UNREADABLE.value)
        self.assertEqual(status[f_orphan], WorkspaceStatus.ORPHAN.value)
        self.assertEqual(status[f_unknown], WorkspaceStatus.UNKNOWN_SOURCE.value)
        self.assertEqual(scan.missing_projects, [missing_path])
        self.assertEqual(scan.unreadable_projects, [broken_path])
        owned_entry = next(e for e in scan.entries if e.folder == f_owned)
        self.assertEqual(owned_entry.project_path, other_path)

    def test_scan_treats_ungrouped_files_as_in_use(self):
        folder = self.service.prepare_folder(self.source)
        part = self._create_file("高笙.pdf", folder)
        current = self._project_with(ungrouped=[part])
        scan = self.service.scan(current, [], ProjectService().load_project)
        self.assertEqual(scan.entries[0].status, WorkspaceStatus.IN_USE)

    def test_scan_loads_owner_from_meta_even_if_not_recent(self):
        folder = self.service.prepare_folder(self.source)
        part = self._create_file("高笙.pdf", folder)
        owner_path = self._save_project(self._project_with(part), "old.llproj")
        self.service.update_project_path(self._project_with(part), owner_path)
        scan = self.service.scan(None, [], ProjectService().load_project)
        self.assertEqual(scan.entries[0].status, WorkspaceStatus.OWNED_BY_OTHER)
        self.assertEqual(scan.entries[0].project_path, owner_path)
        self.assertEqual(scan.missing_projects, [])

    def test_scan_keeps_emptied_folder_owned_by_other_project(self):
        folder = self.service.prepare_folder(self.source)
        part = self._create_file("高笙.pdf", folder)
        owner_path = self._save_project(self._project_with(part), "other.llproj")
        os.remove(part)
        self.service.scan(None, [owner_path], ProjectService().load_project)
        self.assertTrue(os.path.isdir(folder))
        self.assertIsNotNone(self.service.read_meta(folder))

    def test_scan_entry_summary(self):
        folder = self.service.prepare_folder(self.source)
        self._create_file("高笙.pdf", folder, "12345")
        self._create_file("揚琴.pdf", folder, "678")
        scan = self.service.scan(None, [], ProjectService().load_project)
        entry = scan.entries[0]
        self.assertEqual(entry.source_name, "3307 分譜.pdf")
        self.assertEqual(entry.file_count, 2)
        self.assertEqual(entry.total_bytes, 8)

    def test_scan_without_workspace_dir(self):
        scan = self.service.scan(None, [], ProjectService().load_project)
        self.assertEqual(scan.entries, [])

    def test_cleanup_removes_folders(self):
        folder = self.service.prepare_folder(self.source)
        self._create_file("高笙.pdf", folder)
        scan = self.service.scan(None, [], ProjectService().load_project)
        removed = self.service.cleanup(scan.entries)
        self.assertEqual(removed, 1)
        self.assertFalse(os.path.exists(folder))


class TestFileServiceCrossDevice(unittest.TestCase):
    """FileService 跨磁碟搬移測試"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.file_service = FileService()
        self.src = os.path.join(self.temp_dir, "src.pdf")
        with open(self.src, "w") as f:
            f.write("content")
        self.dst = os.path.join(self.temp_dir, "dst.pdf")
        self._orig_rename = os.rename

    def tearDown(self):
        os.rename = self._orig_rename
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _force_exdev(self):
        import errno

        def fake_rename(a, b):
            raise OSError(errno.EXDEV, "Invalid cross-device link")

        os.rename = fake_rename

    def test_falls_back_to_copy_on_exdev(self):
        self._force_exdev()
        self.file_service.rename_file(self.src, self.dst)
        self.assertFalse(os.path.exists(self.src))
        self.assertTrue(os.path.isfile(self.dst))
        self.assertFalse(os.path.exists(self.dst + ".part"))
        with open(self.dst) as f:
            self.assertEqual(f.read(), "content")

    def test_copy_failure_leaves_no_partial_file(self):
        self._force_exdev()
        orig_copy2 = shutil.copy2

        def failing_copy2(a, b, **kw):
            with open(b, "w") as f:
                f.write("half")
            raise OSError("disk full")

        shutil.copy2 = failing_copy2
        try:
            with self.assertRaises(OSError):
                self.file_service.rename_file(self.src, self.dst)
        finally:
            shutil.copy2 = orig_copy2
        self.assertTrue(os.path.isfile(self.src))
        self.assertFalse(os.path.exists(self.dst))
        self.assertFalse(os.path.exists(self.dst + ".part"))

    def test_non_exdev_error_propagates(self):
        def fake_rename(a, b):
            raise PermissionError("denied")

        os.rename = fake_rename
        with self.assertRaises(PermissionError):
            self.file_service.rename_file(self.src, self.dst)
        self.assertTrue(os.path.isfile(self.src))


if __name__ == '__main__':
    unittest.main()
