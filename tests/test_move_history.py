# -*- coding: utf-8 -*-
"""
搬移歷程測試

從 MoveHistory 的介面驗證重新命名、復原、重做與中斷還原：在暫存目錄裡用真檔案，
復原、重做、進行中紀錄與備份目錄都由建構時注入，不 patch 全域常數。
當機與寫入失敗以替換 FileService 的方法注入。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.locale import set_locale
from core.models import RenameEntry
from services.file_service import FileService
from services.move_history import MoveHistory, RenameVerdict
from services.project_service import ProjectService
from services.workspace_service import WorkspaceService


class _Crash(BaseException):
    """模擬程式被強制結束：不是 Exception，所以搬移歷程不會攔下來回滾"""


class _TrashRecordingFileService(FileService):
    """測試用：移到資源回收桶改為記下路徑後直接移除"""

    def __init__(self):
        super().__init__()
        self.trashed = []

    def delete_file(self, path):
        self.trashed.append(path)
        os.remove(path)


class MoveHistoryTestCase(unittest.TestCase):
    """搬移歷程測試的共用骨架：檔案放在 scores/，使用者資料放在 data/"""

    def setUp(self):
        set_locale("zh_TW")
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.scores = os.path.join(self.temp_dir, "scores")
        os.makedirs(self.scores)
        self.data_dir = os.path.join(self.temp_dir, "data")
        self.file_service = _TrashRecordingFileService()
        self.workspace = WorkspaceService(self.file_service, os.path.join(self.data_dir, "workspace"))
        self.history = self.new_history()

    def new_history(self, **dirs) -> MoveHistory:
        """以暫存目錄下的使用者資料目錄建立搬移歷程；dirs 可覆寫個別目錄"""
        paths = {
            "undo_dir": os.path.join(self.data_dir, "undo"),
            "redo_dir": os.path.join(self.data_dir, "redo"),
            "journal_path": os.path.join(self.data_dir, "pending_move.json"),
            "backup_dir": os.path.join(self.data_dir, "backups"),
        }
        paths.update(dirs)
        return MoveHistory(self.file_service, self.workspace, **paths)

    def path(self, name: str) -> str:
        """scores/ 底下的路徑"""
        return os.path.join(self.scores, name)

    def create(self, name: str, content: str = "dummy") -> str:
        """在 scores/ 建立檔案，回傳路徑"""
        path = self.path(name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return path

    def read(self, path: str) -> str:
        with open(path) as f:
            return f.read()

    def files(self):
        """scores/ 底下的檔案（含子資料夾，以相對路徑列出）"""
        found = []
        for root, _, names in os.walk(self.scores):
            found.extend(os.path.relpath(os.path.join(root, n), self.scores) for n in names)
        return sorted(found)

    def fail_moves_when(self, predicate):
        """之後搬移檔案時，predicate(來源, 目標) 成立的那幾次拋出 OSError（不搬）"""
        real_rename = FileService.rename_file

        def flaky_rename(old_path, new_path):
            if predicate(old_path, new_path):
                raise OSError("simulated")
            real_rename(self.file_service, old_path, new_path)

        self.file_service.rename_file = flaky_rename

    def stop_failing(self):
        """搬移恢復正常"""
        del self.file_service.rename_file

    def raise_on_move(self, **by_call):
        """第 n 次（從 1 起算）搬移檔案時、搬移前拋出指定例外，例如 raise_on_move(n2=_Crash())"""
        calls = []
        real_rename = FileService.rename_file

        def counting_rename(old_path, new_path):
            calls.append(old_path)
            error = by_call.get(f"n{len(calls)}")
            if error:
                raise error
            real_rename(self.file_service, old_path, new_path)

        self.file_service.rename_file = counting_rename

    def crash_on_journal_write(self, nth: int):
        """第 nth 次寫入進行中紀錄時當機（不寫）"""
        journal = os.path.join(self.data_dir, "pending_move.json")
        writes = []
        real_write = FileService.write_json_atomic

        def write(path, data):
            if path == journal:
                writes.append(path)
                if len(writes) == nth:
                    raise _Crash()
            real_write(self.file_service, path, data)

        self.file_service.write_json_atomic = write

    def interrupt(self, run, **by_call):
        """執行 run 並依 by_call（同 raise_on_move）在搬移時當機，之後搬移恢復正常"""
        self.raise_on_move(**by_call)
        with self.assertRaises(_Crash):
            run()
        self.stop_failing()

    def crash_writing_into(self, run, directory: str, after: bool = False):
        """執行 run，第一次往 directory 寫入 JSON 時當機（after 為 True 時寫完才當機），之後寫入恢復正常"""
        real_write = FileService.write_json_atomic

        def write(path, data):
            into = os.path.dirname(path) == directory
            if into and not after:
                raise _Crash()
            real_write(self.file_service, path, data)
            if into:
                raise _Crash()

        self.file_service.write_json_atomic = write
        with self.assertRaises(_Crash):
            run()
        del self.file_service.write_json_atomic


class TestRename(MoveHistoryTestCase):
    """重新命名：兩階段搬移讓對調與連鎖可以執行，不能安全執行的整批取消"""

    def test_swap_two_files(self):
        a, b = self.create("a.pdf", "A"), self.create("b.pdf", "B")
        result = self.history.rename(RenameVerdict([RenameEntry(a, b), RenameEntry(b, a)]))
        self.assertIsNone(result.error)
        self.assertEqual([(m.original, m.renamed) for m in result.changes], [(a, b), (b, a)])
        self.assertEqual([self.read(p) for p in (a, b)], ["B", "A"])
        self.assertEqual(self.files(), ["a.pdf", "b.pdf"])

    def test_follow_a_chain(self):
        a, b = self.create("a.pdf", "A"), self.create("b.pdf", "B")
        c = self.path("c.pdf")
        self.history.rename(RenameVerdict([RenameEntry(a, b), RenameEntry(b, c)]))
        self.assertEqual([self.read(p) for p in (b, c)], ["A", "B"])
        self.assertEqual(self.files(), ["b.pdf", "c.pdf"])

    def test_entry_that_stays_in_place_is_allowed(self):
        a = self.create("same.pdf")
        result = self.history.rename(RenameVerdict([RenameEntry(a, a)]))
        self.assertIsNone(result.error)
        self.assertEqual(self.files(), ["same.pdf"])

    def test_new_folders_are_created_and_removed_again_on_undo(self):
        a = self.create("a.pdf")
        sub = os.path.join(self.scores, "Sub")
        self.history.rename(RenameVerdict([RenameEntry(a, os.path.join(sub, "a.pdf"))]))
        self.assertEqual(self.files(), [os.path.join("Sub", "a.pdf")])
        self.history.undo()
        self.assertEqual(self.files(), ["a.pdf"])
        self.assertFalse(os.path.exists(sub))

    def _assert_refused(self, plan, *expected_in_message):
        """整批取消：結果帶錯誤、沒有路徑變動、檔案原封不動、沒有寫入復原紀錄"""
        before = self.files()
        result = self.history.rename(RenameVerdict(plan))
        self.assertIsNotNone(result.error)
        for text in expected_in_message:
            self.assertIn(text, str(result.error))
        self.assertEqual((result.changes, result.residual), ([], []))
        self.assertEqual(self.files(), before)
        self.assertIsNone(self.history.latest_undo())

    def test_refuses_an_empty_name(self):
        a = self.create("a.pdf")
        self._assert_refused([RenameEntry(a, self.path(".pdf"))], a)

    def test_refuses_a_missing_source(self):
        self._assert_refused([RenameEntry(self.path("ghost.pdf"), self.path("A.pdf"))], "ghost.pdf")

    def test_refuses_an_occupied_target(self):
        a, taken = self.create("a.pdf"), self.create("taken.pdf", "keep")
        self._assert_refused([RenameEntry(a, taken)], "taken.pdf")
        self.assertEqual(self.read(taken), "keep")

    def test_refuses_a_taken_staging_name(self):
        a, b = self.create("a.pdf"), self.create("b.pdf")
        self.create("a.pdf.moving")
        self._assert_refused([RenameEntry(a, b), RenameEntry(b, a)], "a.pdf.moving")

    def test_refuses_one_source_written_in_two_spellings(self):
        a = self.create("a.pdf")
        spelled = os.path.join(self.scores, ".", "a.pdf")
        self._assert_refused([RenameEntry(a, self.path("A.pdf")), RenameEntry(spelled, self.path("B.pdf"))])

    def test_refuses_one_target_written_in_two_spellings(self):
        a, b = self.create("a.pdf"), self.create("b.pdf")
        target = self.path("Same.pdf")
        spelled = os.path.join(self.scores, "same.PDF")
        self._assert_refused([RenameEntry(a, target), RenameEntry(b, spelled)])

    def test_failure_in_first_phase_rolls_back_everything(self):
        a, b, c, d = (self.create(n, n[0]) for n in ("a.pdf", "b.pdf", "c.pdf", "d.pdf"))
        self.fail_moves_when(lambda old, new: old == c)
        result = self.history.rename(RenameVerdict([RenameEntry(a, b), RenameEntry(b, a), RenameEntry(c, d), RenameEntry(d, c)]))
        self.assertIsNotNone(result.error)
        self.assertEqual((result.changes, result.residual), ([], []))
        self.assertEqual([self.read(p) for p in (a, b, c, d)], ["a", "b", "c", "d"])
        self.assertIsNone(self.history.latest_undo())

    def test_failure_in_second_phase_rolls_back_everything_and_removes_new_folders(self):
        a, b, e = self.create("a.pdf", "A"), self.create("b.pdf", "B"), self.create("e.pdf", "E")
        f = os.path.join(self.scores, "Sub", "f.pdf")
        self.fail_moves_when(lambda old, new: new == f)
        result = self.history.rename(RenameVerdict([RenameEntry(a, b), RenameEntry(b, a), RenameEntry(e, f)]))
        self.assertIsNotNone(result.error)
        self.assertEqual(self.files(), ["a.pdf", "b.pdf", "e.pdf"])
        self.assertEqual([self.read(p) for p in (a, b, e)], ["A", "B", "E"])

    def test_rollback_failure_leaves_a_file_at_its_staging_name_as_residual(self):
        a, b, e = self.create("a.pdf", "A"), self.create("b.pdf", "B"), self.create("e.pdf", "E")
        f, staging = self.path("f.pdf"), a + ".moving"
        # 第三筆就位失敗觸發回滾；回滾最後一步（暫名搬回 a）失敗
        self.fail_moves_when(lambda old, new: new == f or (old, new) == (staging, a))
        result = self.history.rename(RenameVerdict([RenameEntry(a, b), RenameEntry(b, a), RenameEntry(e, f)]))
        self.assertEqual([(m.original, m.renamed) for m in result.residual], [(a, staging)])
        self.assertIn("a.pdf.moving", str(result.error))
        self.assertEqual(self.files(), ["a.pdf.moving", "b.pdf", "e.pdf"])

    def test_rollback_never_overwrites_a_file_stuck_in_the_way(self):
        a, b, e = self.create("a.pdf", "A"), self.create("b.pdf", "B"), self.create("e.pdf", "E")
        c, f, staging = self.path("c.pdf"), self.path("f.pdf"), b + ".moving"
        # 第三筆就位失敗觸發回滾；a 的檔案搬不回 a、卡在 b，b 的檔案就不能從暫名搬回 b
        self.fail_moves_when(lambda old, new: new == f or (old, new) == (b, a))
        result = self.history.rename(RenameVerdict([RenameEntry(a, b), RenameEntry(b, c), RenameEntry(e, f)]))
        self.assertEqual([(m.original, m.renamed) for m in result.residual], [(a, b), (b, staging)])
        self.assertEqual([self.read(p) for p in (b, staging)], ["A", "B"])


class TestUndoRedo(MoveHistoryTestCase):
    """復原把檔案搬回原位、重做再搬回新位置；對調也能復原與重做"""

    def test_undo_and_redo_a_swap(self):
        a, b = self.create("a.pdf", "A"), self.create("b.pdf", "B")
        self.history.rename(RenameVerdict([RenameEntry(a, b), RenameEntry(b, a)]))
        result = self.history.undo()
        self.assertEqual([(m.original, m.renamed) for m in result.changes], [(b, a), (a, b)])
        self.assertEqual([self.read(p) for p in (a, b)], ["A", "B"])
        self.assertIsNone(self.history.latest_undo())
        self.history.redo()
        self.assertEqual([self.read(p) for p in (a, b)], ["B", "A"])
        self.assertIsNone(self.history.latest_redo())
        self.assertEqual(self.files(), ["a.pdf", "b.pdf"])

    def test_undo_is_refused_when_the_original_spot_is_taken(self):
        a = self.create("a.pdf", "A")
        new_a = self.path("A1.pdf")
        self.history.rename(RenameVerdict([RenameEntry(a, new_a)]))
        self.create("a.pdf", "X")
        record = self.history.latest_undo()
        result = self.history.undo()
        self.assertIsNotNone(result.error)
        self.assertEqual([self.read(p) for p in (a, new_a)], ["X", "A"])
        self.assertEqual(self.history.latest_undo().id, record.id)

    def test_nothing_to_undo_or_redo(self):
        self.assertIsNone(self.history.undo())
        self.assertIsNone(self.history.redo())


class WorkspaceMetaTestCase(MoveHistoryTestCase):
    """工作區 meta 測試的共用骨架：合併譜放在 scores/，分譜放在它的工作區子資料夾"""

    def _project_file(self, name: str) -> str:
        return os.path.join(self.temp_dir, name + ".llproj")

    def _part_in_workspace(self, name: str = "高笙.pdf"):
        """在合併譜的工作區子資料夾放一份分譜（所屬專案 a），回傳（子資料夾、分譜路徑）"""
        source = self.path("合併譜.pdf")
        if not os.path.exists(source):
            self.create("合併譜.pdf")
        folder = self.workspace.prepare_folder(source, self._project_file("a"))
        part = os.path.join(folder, name)
        with open(part, "w") as f:
            f.write("part")
        return folder, part

    def _to_scores(self, *moves) -> RenameVerdict:
        """把工作區的分譜重新命名到輸出位置 scores/ 的判定；moves 為（來源，目標）"""
        return RenameVerdict([RenameEntry(source, target, output_directory=self.scores) for source, target in moves])


class TestWorkspaceMeta(WorkspaceMetaTestCase):
    """來源在工作區的分譜重新命名後，子資料夾的 meta 被清理掉也能在復原時寫回"""

    def test_undo_writes_meta_back_after_cleanup_removed_the_folder(self):
        folder, part = self._part_in_workspace()
        self.history.rename(self._to_scores((part, self.path("01-高笙.pdf"))))
        self.workspace.purge_empty_folders()
        self.assertFalse(os.path.exists(folder))
        self.history.undo()
        self.assertTrue(os.path.isfile(part))
        meta = self.workspace.read_meta(folder)
        self.assertEqual(meta["source_name"], "合併譜.pdf")
        self.assertEqual(meta["project_path"], self._project_file("a"))

    def test_undo_keeps_meta_written_in_the_meantime(self):
        folder, part = self._part_in_workspace()
        self.history.rename(self._to_scores((part, self.path("01-高笙.pdf"))))
        self.workspace.prepare_folder(self.path("合併譜.pdf"), self._project_file("other"))
        self.history.undo()
        self.assertEqual(self.workspace.read_meta(folder)["project_path"], self._project_file("other"))

    def test_undo_still_moves_files_when_meta_cannot_be_written(self):
        folder, part = self._part_in_workspace()
        self.history.rename(self._to_scores((part, self.path("01-高笙.pdf"))))
        self.workspace.purge_empty_folders()
        real_write = FileService.write_json_atomic

        def failing_write(path, data):
            if os.path.basename(path) == "meta.json":
                raise OSError("locked")
            real_write(self.file_service, path, data)

        self.file_service.write_json_atomic = failing_write
        result = self.history.undo()
        self.assertIsNone(result.error)
        self.assertTrue(os.path.isfile(part))
        self.assertIsNone(self.workspace.read_meta(folder))
        self.assertIsNotNone(self.history.latest_redo())

    def test_undo_writes_meta_back_even_when_rollback_strands_a_file_in_workspace(self):
        folder, first = self._part_in_workspace("高笙.pdf")
        _, second = self._part_in_workspace("揚琴.pdf")
        outs = [self.path("01-高笙.pdf"), self.path("02-揚琴.pdf")]
        self.history.rename(self._to_scores((first, outs[0]), (second, outs[1])))
        self.workspace.purge_empty_folders()
        # 第二個檔案搬不回工作區觸發回滾；第一個檔案又搬不回輸出位置，卡在工作區
        self.fail_moves_when(lambda old, new: new == second or (old, new) == (first, outs[0]))
        result = self.history.undo()
        self.assertEqual([m.renamed for m in result.residual], [first])
        self.assertEqual(self.workspace.read_meta(folder)["project_path"], self._project_file("a"))

    def test_redo_refreshes_the_snapshot_so_a_later_undo_writes_back_the_current_owner(self):
        folder, part = self._part_in_workspace()
        self.history.rename(self._to_scores((part, self.path("01-高笙.pdf"))))
        self.history.undo()
        # 復原與重做之間專案另存到新位置
        self.workspace.prepare_folder(self.path("合併譜.pdf"), self._project_file("moved"))
        self.history.redo()
        self.workspace.purge_empty_folders()
        self.history.undo()
        self.assertEqual(self.workspace.read_meta(folder)["project_path"], self._project_file("moved"))

    def test_rename_then_cleanup_scan_then_undo_keeps_the_source_identifiable(self):
        folder, part = self._part_in_workspace()
        self.history.rename(self._to_scores((part, self.path("01-高笙.pdf"))))
        self.workspace.scan(None, [], ProjectService().load_project)
        self.assertFalse(os.path.exists(folder))
        self.history.undo()
        scan = self.workspace.scan(None, [], ProjectService().load_project)
        self.assertEqual([(e.folder, e.source_name) for e in scan.entries], [(folder, "合併譜.pdf")])


class TestUndoRedoOrder(MoveHistoryTestCase):
    """復原與重做堆疊後進先出"""

    def test_redo_after_two_undos_redoes_the_most_recently_undone_first(self):
        x = self.create("x.pdf", "X")
        self.history.rename(RenameVerdict([RenameEntry(x, self.path("y.pdf"))]))
        self.history.rename(RenameVerdict([RenameEntry(self.path("y.pdf"), self.path("z.pdf"))]))
        self.history.undo()
        self.history.undo()
        self.assertEqual(self.files(), ["x.pdf"])
        self.history.redo()
        self.assertEqual(self.files(), ["y.pdf"])
        self.history.redo()
        self.assertEqual(self.files(), ["z.pdf"])

    def test_two_renames_in_the_same_second_both_stay_undoable_in_reverse_order(self):
        a, c = self.create("a.pdf"), self.create("c.pdf")
        self.history.rename(RenameVerdict([RenameEntry(a, self.path("b.pdf"))]))
        self.history.rename(RenameVerdict([RenameEntry(c, self.path("d.pdf"))]))
        self.history.undo()
        self.assertEqual(self.files(), ["b.pdf", "c.pdf"])
        self.history.undo()
        self.assertEqual(self.files(), ["a.pdf", "c.pdf"])
        self.assertIsNone(self.history.latest_undo())


class TestMissingFiles(MoveHistoryTestCase):
    """復原與重做對已不在的檔一致：略過並列出，只有實際搬動的對照轉入另一個堆疊"""

    def _rename_two(self):
        """把 a、b 改名成 A、B，回傳四個路徑"""
        a, b = self.create("a.pdf", "a"), self.create("b.pdf", "b")
        new_a, new_b = self.path("A1.pdf"), self.path("B1.pdf")
        self.history.rename(RenameVerdict([RenameEntry(a, new_a), RenameEntry(b, new_b)]))
        return a, b, new_a, new_b

    def test_undo_skips_and_lists_a_file_that_was_moved_away(self):
        a, b, new_a, new_b = self._rename_two()
        os.rename(new_b, self.path("elsewhere.pdf"))
        result = self.history.undo()
        self.assertIsNone(result.error)
        self.assertEqual(result.skipped, [new_b])
        self.assertEqual([(m.original, m.renamed) for m in result.changes], [(new_a, a)])
        self.assertEqual(self.files(), ["a.pdf", "elsewhere.pdf"])

    def test_redo_after_a_partial_undo_moves_only_the_undone_files(self):
        a, b, new_a, new_b = self._rename_two()
        os.rename(new_b, self.path("elsewhere.pdf"))
        self.history.undo()
        result = self.history.redo()
        self.assertIsNone(result.error)
        self.assertEqual(result.skipped, [])
        self.assertEqual([(m.original, m.renamed) for m in result.changes], [(a, new_a)])
        self.assertEqual(self.files(), ["A1.pdf", "elsewhere.pdf"])

    def test_redo_skips_and_lists_a_file_that_was_moved_away(self):
        a, b, new_a, new_b = self._rename_two()
        self.history.undo()
        os.remove(a)
        result = self.history.redo()
        self.assertIsNone(result.error)
        self.assertEqual(result.skipped, [a])
        self.assertEqual(self.files(), ["B1.pdf"])
        self.history.undo()
        self.assertEqual(self.files(), ["b.pdf"])


class TestResidual(MoveHistoryTestCase):
    """中途失敗、有檔案搬不回原位時：殘留紀錄放上復原堆疊、標示原操作種類，不動重做堆疊"""

    def _leave_something_to_redo(self):
        """重新命名後復原，讓重做堆疊有一筆紀錄；回傳它的 id"""
        r = self.create("r.pdf")
        self.history.rename(RenameVerdict([RenameEntry(r, self.path("R1.pdf"))]))
        self.history.undo()
        return self.history.latest_redo().id

    def test_rename_residual_is_recorded_as_rename_and_keeps_the_redo_stack(self):
        redo_id = self._leave_something_to_redo()
        a, b = self.create("a.pdf"), self.create("b.pdf")
        new_a = self.path("A1.pdf")
        # 第二筆搬移失敗觸發回滾；回滾時第一筆搬不回原位
        self.fail_moves_when(lambda old, new: old == b or new == a)
        result = self.history.rename(RenameVerdict([RenameEntry(a, new_a), RenameEntry(b, self.path("B1.pdf"))]))
        self.assertIsNotNone(result.error)
        self.assertEqual([(m.original, m.renamed) for m in result.residual], [(a, new_a)])
        self.assertEqual(result.changes, [])
        residual = self.history.latest_undo()
        self.assertEqual(residual.operation_type, "rename")
        self.assertEqual(residual.description, "重新命名失敗後留下的 1 個檔案")
        self.assertEqual([(m.original, m.renamed) for m in residual.mappings], [(a, new_a)])
        self.assertEqual(self.history.latest_redo().id, redo_id)

    def test_undo_residual_is_recorded_as_undo_above_the_record_that_failed(self):
        a, b = self.create("a.pdf"), self.create("b.pdf")
        new_a, new_b = self.path("A1.pdf"), self.path("B1.pdf")
        self.history.rename(RenameVerdict([RenameEntry(a, new_a), RenameEntry(b, new_b)]))
        renamed = self.history.latest_undo()
        # 第二個檔搬回原位失敗觸發回滾；第一個檔又搬不回新位置
        self.fail_moves_when(lambda old, new: old == new_b or new == new_a)
        result = self.history.undo()
        self.assertIsNotNone(result.error)
        self.assertEqual([(m.original, m.renamed) for m in result.residual], [(new_a, a)])
        residual = self.history.latest_undo()
        self.assertEqual(residual.operation_type, "undo")
        self.assertEqual(residual.description, "復原失敗後留下的 1 個檔案")
        self.stop_failing()
        self.history.undo()
        self.assertEqual(self.history.latest_undo().id, renamed.id)
        self.assertEqual(self.files(), ["A1.pdf", "B1.pdf"])


class TestRecordNotWritten(MoveHistoryTestCase):
    """檔案已搬好、只有紀錄寫不進去（例如磁碟已滿）：結果仍回報路徑變動"""

    def _unwritable_dir(self) -> str:
        """一個寫不進去的目錄位置（該處已是一個檔案）"""
        os.makedirs(self.data_dir, exist_ok=True)
        path = os.path.join(self.data_dir, "not-a-directory")
        with open(path, "w") as f:
            f.write("")
        return path

    def test_rename_still_reports_path_changes(self):
        history = self.new_history(undo_dir=self._unwritable_dir())
        a = self.create("a.pdf")
        new_a = self.path("A1.pdf")
        result = history.rename(RenameVerdict([RenameEntry(a, new_a)]))
        self.assertIsNone(result.error)
        self.assertIsNotNone(result.record_error)
        self.assertEqual([(m.original, m.renamed) for m in result.changes], [(a, new_a)])
        self.assertEqual(self.files(), ["A1.pdf"])

    def test_undo_still_reports_path_changes(self):
        a = self.create("a.pdf")
        new_a = self.path("A1.pdf")
        self.history.rename(RenameVerdict([RenameEntry(a, new_a)]))
        history = self.new_history(redo_dir=self._unwritable_dir())
        result = history.undo()
        self.assertIsNone(result.error)
        self.assertIsNotNone(result.record_error)
        self.assertEqual([(m.original, m.renamed) for m in result.changes], [(new_a, a)])
        self.assertEqual(self.files(), ["a.pdf"])


class TestLegacyRecords(MoveHistoryTestCase):
    """舊版以時間戳命名、沒有 id 的復原與重做紀錄照常載入並能執行"""

    def _write_legacy(self, stack: str, timestamp: str, original: str, renamed: str, description: str):
        """照舊版格式寫一筆重新命名紀錄（沒有 id、沒有 workspace_meta）"""
        directory = os.path.join(self.data_dir, stack)
        os.makedirs(directory, exist_ok=True)
        with open(os.path.join(directory, f"{stack}_{timestamp}.json"), "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": timestamp, "description": description, "operation_type": "rename",
                "mappings": [{"original": original, "renamed": renamed}],
                "created_directories": [], "created_files": [], "backup_path": "", "original_path": "",
            }, f, ensure_ascii=False)

    def test_legacy_undo_records_undo_newest_first_then_redo(self):
        a, b = self.path("a.pdf"), self.path("b.pdf")
        new_a, new_b = self.create("A1.pdf"), self.create("B1.pdf")
        self._write_legacy("undo", "20260101_100000", a, new_a, "舊版紀錄一")
        self._write_legacy("undo", "20260101_120000", b, new_b, "舊版紀錄二")
        self.assertEqual(self.history.latest_undo().description, "舊版紀錄二")
        self.history.undo()
        self.assertEqual(self.files(), ["A1.pdf", "b.pdf"])
        self.history.undo()
        self.assertEqual(self.files(), ["a.pdf", "b.pdf"])
        self.assertIsNone(self.history.latest_undo())
        self.history.redo()
        self.assertEqual(self.files(), ["A1.pdf", "b.pdf"])

    def test_legacy_redo_record_redoes(self):
        a, new_a = self.create("a.pdf"), self.path("A1.pdf")
        self._write_legacy("redo", "20260101_120000", a, new_a, "舊版紀錄")
        self.assertEqual(self.history.latest_redo().description, "舊版紀錄")
        result = self.history.redo()
        self.assertIsNone(result.error)
        self.assertEqual(self.files(), ["A1.pdf"])
        self.assertIsNone(self.history.latest_redo())
        self.history.undo()
        self.assertEqual(self.files(), ["a.pdf"])

    def test_new_records_sit_above_legacy_ones(self):
        a, new_a = self.path("a.pdf"), self.create("A1.pdf")
        self._write_legacy("undo", "29991231_235959", a, new_a, "舊版紀錄")
        c = self.create("c.pdf")
        self.history.rename(RenameVerdict([RenameEntry(c, self.path("C1.pdf"))]))
        self.history.undo()
        self.assertEqual(self.files(), ["A1.pdf", "c.pdf"])


class TestSplitAndRotateRecords(MoveHistoryTestCase):
    """分割與旋轉的復原紀錄由搬移歷程組裝；復原撤銷其檔案效果，不進重做堆疊"""

    def test_undo_rotate_save_as_moves_the_saved_file_to_the_recycle_bin(self):
        source = self.create("a.pdf", "original")
        output = self.create("a rotated.pdf", "rotated")
        self.history.record_rotate(source, output)
        result = self.history.undo()
        self.assertIsNone(result.error)
        self.assertEqual(self.file_service.trashed, [output])
        self.assertEqual(self.files(), ["a.pdf"])
        self.assertEqual(self.read(source), "original")
        self.assertIsNone(self.history.latest_undo())

    def test_undo_rotate_overwrite_puts_the_backup_back(self):
        source = self.create("a.pdf", "original")
        backup = self.history.create_backup(source)
        with open(source, "w") as f:
            f.write("rotated")
        self.history.record_rotate(source, source, backup)
        self.history.undo()
        self.assertEqual(self.read(source), "original")
        self.assertFalse(os.path.exists(backup))
        self.assertEqual(self.file_service.trashed, [])

    def test_undo_split_moves_the_parts_to_the_recycle_bin_and_removes_new_folders(self):
        folder = os.path.join(self.scores, "out")
        parts = [self.create(os.path.join("out", n)) for n in ("Flute.pdf", "Oboe.pdf")]
        self.history.record_split(parts, [folder])
        self.assertEqual(self.history.latest_undo().description, "PDF 分割：建立 2 個檔案")
        self.history.undo()
        self.assertEqual(self.file_service.trashed, parts)
        self.assertFalse(os.path.exists(folder))
        self.assertIsNone(self.history.latest_undo())
        self.assertIsNone(self.history.latest_redo())

    def test_a_new_split_clears_the_redo_stack(self):
        a = self.create("a.pdf")
        self.history.rename(RenameVerdict([RenameEntry(a, self.path("A1.pdf"))]))
        self.history.undo()
        self.history.record_split([self.create("Flute.pdf")], [])
        self.assertIsNone(self.history.latest_redo())

    def test_legacy_rotate_save_as_record_is_undone_by_moving_the_saved_file_to_the_recycle_bin(self):
        output = self.create("a rotated.pdf")
        directory = os.path.join(self.data_dir, "undo")
        os.makedirs(directory)
        with open(os.path.join(directory, "undo_20260101_120000.json"), "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": "20260101_120000", "description": "PDF 旋轉", "operation_type": "rotate",
                "mappings": [], "created_directories": [], "created_files": [],
                "backup_path": "", "original_path": output, "workspace_meta": {},
            }, f, ensure_ascii=False)
        self.history.undo()
        self.assertEqual(self.file_service.trashed, [output])


class TestInterruptedBatch(MoveHistoryTestCase):
    """程式在搬移途中被關掉：下次讀得到未完成的批次，還原後檔案回到原位"""

    def _swap(self):
        a, b = self.create("a.pdf", "A"), self.create("b.pdf", "B")
        return a, b, lambda: self.history.rename(RenameVerdict([RenameEntry(a, b), RenameEntry(b, a)]))

    def test_nothing_pending_when_nothing_was_interrupted(self):
        self.assertIsNone(self.history.pending())

    def test_crash_in_first_phase_is_reported_and_restored(self):
        a, b, swap = self._swap()
        self.interrupt(swap, n2=_Crash())
        pending = self.history.pending()
        self.assertEqual((pending.operation, pending.moved, pending.complete), ("rename", 1, False))
        self.assertEqual(self.files(), ["a.pdf.moving", "b.pdf"])
        result = self.history.recover()
        self.assertIsNone(result.error)
        self.assertEqual([(m.original, m.renamed) for m in result.changes], [(a + ".moving", a)])
        self.assertEqual((result.skipped, result.residual), ([], []))
        self.assertEqual([self.read(p) for p in (a, b)], ["A", "B"])
        self.assertIsNone(self.history.pending())

    def test_crash_in_second_phase_is_restored(self):
        a, b, swap = self._swap()
        self.interrupt(swap, n4=_Crash())
        self.assertEqual(self.files(), ["b.pdf", "b.pdf.moving"])
        self.assertEqual(self.history.pending().moved, 2)
        result = self.history.recover()
        self.assertEqual([m.renamed for m in result.changes], [a, b])
        self.assertEqual([self.read(p) for p in (a, b)], ["A", "B"])

    def test_a_move_done_but_not_yet_recorded_is_counted(self):
        a, b, swap = self._swap()
        self.crash_on_journal_write(2)
        with self.assertRaises(_Crash):
            swap()
        del self.file_service.write_json_atomic
        self.assertEqual(self.files(), ["a.pdf.moving", "b.pdf"])
        self.assertEqual(self.history.pending().moved, 1)
        self.history.recover()
        self.assertEqual(self.files(), ["a.pdf", "b.pdf"])

    def test_a_case_only_rename_done_but_not_recorded_is_counted_on_a_case_insensitive_filesystem(self):
        a = self.create("a.pdf")
        self.crash_on_journal_write(2)
        with self.assertRaises(_Crash):
            self.history.rename(RenameVerdict([RenameEntry(a, self.path("A.pdf"))]))
        del self.file_service.write_json_atomic
        self.assertEqual(self.files(), ["A.pdf"])
        # 模擬不分大小寫的檔案系統：a.pdf 與 A.pdf 都算存在
        self.file_service.file_exists = lambda p: os.path.isfile(p) or os.path.isfile(p.lower())
        self.assertEqual(self.history.pending().moved, 1)

    def test_crash_during_rollback_is_restored(self):
        a, b, swap = self._swap()
        # 第 4 步就位失敗觸發回滾；回滾逆轉了第 3 步之後、逆轉第 2 步之前當機
        self.interrupt(swap, n4=OSError("simulated"), n6=_Crash())
        self.assertEqual(self.files(), ["a.pdf.moving", "b.pdf.moving"])
        result = self.history.recover()
        self.assertEqual([m.renamed for m in result.changes], [a, b])
        self.assertEqual([self.read(p) for p in (a, b)], ["A", "B"])

    def test_crash_after_rollback_reversed_two_steps_is_restored(self):
        a, b, swap = self._swap()
        # 第 4 步就位失敗觸發回滾；回滾逆轉了第 3、2 步之後、逆轉第 1 步之前當機
        self.interrupt(swap, n4=OSError("simulated"), n7=_Crash())
        self.assertEqual(self.files(), ["a.pdf.moving", "b.pdf"])
        self.assertEqual(self.history.pending().moved, 1)
        result = self.history.recover()
        self.assertEqual([m.renamed for m in result.changes], [a])
        self.assertEqual([self.read(p) for p in (a, b)], ["A", "B"])

    def test_recovery_skips_and_lists_files_no_longer_where_recorded(self):
        a, b, swap = self._swap()
        self.interrupt(swap, n3=_Crash())
        os.remove(a + ".moving")
        result = self.history.recover()
        self.assertEqual(result.skipped, [a + ".moving"])
        self.assertEqual([m.renamed for m in result.changes], [b])
        self.assertEqual(self.files(), ["b.pdf"])
        self.assertIsNone(self.history.pending())

    def test_recovery_records_files_it_cannot_move_back_as_residual(self):
        a, b, swap = self._swap()
        self.interrupt(swap, n3=_Crash())
        self.raise_on_move(n2=OSError("simulated"))
        result = self.history.recover()
        self.assertEqual([(m.original, m.renamed) for m in result.residual], [(a, a + ".moving")])
        self.assertEqual([m.renamed for m in result.changes], [b])
        self.assertEqual(self.files(), ["a.pdf.moving", "b.pdf"])
        residual = self.history.latest_undo()
        self.assertEqual((residual.operation_type, residual.residual), ("rename", True))
        self.assertEqual([(m.original, m.renamed) for m in residual.mappings], [(a, a + ".moving")])
        self.assertIsNone(self.history.pending())

    def test_recovery_keeps_the_journal_when_the_residual_record_cannot_be_written(self):
        a, b, swap = self._swap()
        self.interrupt(swap, n3=_Crash())
        blocked = os.path.join(self.data_dir, "not-a-directory")
        with open(blocked, "w") as f:
            f.write("")
        history = self.new_history(undo_dir=blocked)
        self.raise_on_move(n2=OSError("simulated"))
        result = history.recover()
        self.stop_failing()
        self.assertIsNotNone(result.record_error)
        self.assertEqual(history.pending().moved, 1)
        history.recover()
        self.assertEqual(self.files(), ["a.pdf", "b.pdf"])
        self.assertIsNone(history.pending())

    def test_recovery_removes_directories_created_by_the_interrupted_batch(self):
        a, b = self.create("a.pdf"), self.create("b.pdf")
        sub = os.path.join(self.scores, "Sub")
        plan = [RenameEntry(a, os.path.join(sub, "a.pdf")), RenameEntry(b, os.path.join(sub, "b.pdf"))]
        self.interrupt(lambda: self.history.rename(RenameVerdict(plan)), n2=_Crash())
        self.history.recover()
        self.assertFalse(os.path.exists(sub))
        self.assertEqual(self.files(), ["a.pdf", "b.pdf"])

    def test_recovery_interrupted_midway_resumes(self):
        a, b, swap = self._swap()
        self.interrupt(swap, n3=_Crash())
        self.interrupt(self.history.recover, n2=_Crash())
        self.assertEqual(self.files(), ["a.pdf.moving", "b.pdf"])
        self.assertEqual(self.history.pending().moved, 1)
        self.history.recover()
        self.assertEqual(self.files(), ["a.pdf", "b.pdf"])

    def test_new_batch_is_refused_while_an_interrupted_one_is_pending(self):
        a, b, swap = self._swap()
        self.interrupt(swap, n2=_Crash())
        c = self.create("c.pdf")
        result = self.history.rename(RenameVerdict([RenameEntry(c, self.path("d.pdf"))]))
        self.assertIn("中斷提示", str(result.error))
        self.assertNotIn("重新啟動", str(result.error))
        self.assertEqual(result.changes, [])
        self.assertEqual(self.files(), ["a.pdf.moving", "b.pdf", "c.pdf"])
        self.assertEqual(self.history.pending().moved, 1)

    def test_finished_batch_whose_record_was_not_written_is_pending_complete_and_restorable(self):
        os.makedirs(self.data_dir)
        blocked = os.path.join(self.data_dir, "not-a-directory")
        with open(blocked, "w") as f:
            f.write("")
        history = self.new_history(undo_dir=blocked)
        a, b = self.create("a.pdf", "A"), self.create("b.pdf", "B")
        history.rename(RenameVerdict([RenameEntry(a, b), RenameEntry(b, a)]))
        self.assertEqual([self.read(p) for p in (a, b)], ["B", "A"])
        pending = history.pending()
        self.assertEqual((pending.moved, pending.complete), (2, True))
        history.recover()
        self.assertEqual([self.read(p) for p in (a, b)], ["A", "B"])
        self.assertIsNone(history.pending())

    def test_unreadable_journal_raises_and_can_be_discarded(self):
        os.makedirs(self.data_dir)
        with open(os.path.join(self.data_dir, "pending_move.json"), "w") as f:
            f.write("{broken")
        with self.assertRaises(ValueError):
            self.history.pending()
        self.history.discard_pending()
        self.assertIsNone(self.history.pending())

    def _write_legacy_journal(self, steps):
        """照舊版格式（沒有操作種類與紀錄 id）寫一份進行中紀錄"""
        os.makedirs(self.data_dir, exist_ok=True)
        with open(os.path.join(self.data_dir, "pending_move.json"), "w", encoding="utf-8") as f:
            json.dump({"steps": steps, "pending": None, "complete": False, "created_directories": []}, f)

    def test_legacy_journal_without_operation_is_a_rename(self):
        a = self.path("a.pdf")
        moved = self.create("A1.pdf")
        self._write_legacy_journal([{"index": 0, "source": a, "target": moved}])
        self.assertEqual(self.history.pending().operation, "rename")
        result = self.history.recover()
        self.assertEqual(result.operation, "rename")
        self.assertEqual(self.files(), ["a.pdf"])

    def test_journal_left_after_a_finished_rollback_has_nothing_to_restore(self):
        self._write_legacy_journal([])
        self.assertEqual(self.history.pending().moved, 0)
        result = self.history.recover()
        self.assertEqual((result.changes, result.skipped, result.residual), ([], [], []))
        self.assertIsNone(self.history.pending())


class TestRecoveryByKind(MoveHistoryTestCase):
    """中斷還原依被中斷的操作種類處理：「還原」與「保留結果」後，復原與重做堆疊都和檔案的實際位置一致"""

    def _rename_two(self):
        """把 a、b 改名成 A1、B1，回傳（原路徑、新路徑、復原紀錄 id）"""
        a, b = self.create("a.pdf"), self.create("b.pdf")
        new_a, new_b = self.path("A1.pdf"), self.path("B1.pdf")
        self.history.rename(RenameVerdict([RenameEntry(a, new_a), RenameEntry(b, new_b)]))
        return (a, b), (new_a, new_b), self.history.latest_undo().id

    def test_restoring_an_interrupted_undo_keeps_its_record_on_the_undo_stack(self):
        _, _, record_id = self._rename_two()
        self.interrupt(self.history.undo, n2=_Crash())
        self.assertEqual(self.history.pending().operation, "undo")
        result = self.history.recover()
        self.assertEqual(result.operation, "undo")
        self.assertEqual(self.files(), ["A1.pdf", "B1.pdf"])
        self.assertEqual(self.history.latest_undo().id, record_id)
        self.assertIsNone(self.history.latest_redo())
        self.assertIsNone(self.history.undo().error)
        self.assertEqual(self.files(), ["a.pdf", "b.pdf"])

    def test_restoring_an_undo_interrupted_after_its_record_reached_the_redo_stack(self):
        _, _, record_id = self._rename_two()
        self.crash_writing_into(self.history.undo, os.path.join(self.data_dir, "redo"), after=True)
        self.assertTrue(self.history.pending().complete)
        self.history.recover()
        self.assertEqual(self.files(), ["A1.pdf", "B1.pdf"])
        self.assertEqual(self.history.latest_undo().id, record_id)
        self.assertIsNone(self.history.latest_redo())
        self.history.undo()
        self.assertEqual(self.files(), ["a.pdf", "b.pdf"])

    def test_restoring_an_interrupted_redo_keeps_its_record_on_the_redo_stack(self):
        _, _, record_id = self._rename_two()
        self.history.undo()
        self.interrupt(self.history.redo, n2=_Crash())
        self.assertEqual(self.history.pending().operation, "redo")
        self.history.recover()
        self.assertEqual(self.files(), ["a.pdf", "b.pdf"])
        self.assertEqual(self.history.latest_redo().id, record_id)
        self.assertIsNone(self.history.latest_undo())
        self.history.redo()
        self.assertEqual(self.files(), ["A1.pdf", "B1.pdf"])

    def test_restoring_a_redo_interrupted_after_its_record_reached_the_undo_stack(self):
        _, _, record_id = self._rename_two()
        self.history.undo()
        self.crash_writing_into(self.history.redo, os.path.join(self.data_dir, "undo"), after=True)
        self.history.recover()
        self.assertEqual(self.files(), ["a.pdf", "b.pdf"])
        self.assertEqual(self.history.latest_redo().id, record_id)
        self.assertIsNone(self.history.latest_undo())

    def test_restoring_a_rename_interrupted_after_its_record_was_written_leaves_no_record(self):
        a, b = self.create("a.pdf"), self.create("b.pdf")
        plan = [RenameEntry(a, self.path("A1.pdf")), RenameEntry(b, self.path("B1.pdf"))]
        self.crash_writing_into(lambda: self.history.rename(RenameVerdict(plan)), os.path.join(self.data_dir, "undo"), after=True)
        self.history.recover()
        self.assertEqual(self.files(), ["a.pdf", "b.pdf"])
        self.assertIsNone(self.history.latest_undo())

    def test_restoring_a_finished_undo_interrupted_midway_is_no_longer_reported_as_finished(self):
        _, _, record_id = self._rename_two()
        self.crash_writing_into(self.history.undo, os.path.join(self.data_dir, "redo"))
        self.interrupt(self.history.recover, n2=_Crash())
        pending = self.history.pending()
        self.assertEqual((pending.moved, pending.complete), (1, False))
        self.history.recover()
        self.assertEqual(self.files(), ["A1.pdf", "B1.pdf"])
        self.assertEqual(self.history.latest_undo().id, record_id)

    def test_keeping_a_finished_undo_moves_its_record_to_the_redo_stack(self):
        (a, b), (new_a, new_b), record_id = self._rename_two()
        self.crash_writing_into(self.history.undo, os.path.join(self.data_dir, "redo"))
        pending = self.history.pending()
        self.assertEqual((pending.operation, pending.moved, pending.complete), ("undo", 2, True))
        result = self.history.keep_result()
        self.assertEqual(result.operation, "undo")
        self.assertEqual([(m.original, m.renamed) for m in result.changes], [(new_a, a), (new_b, b)])
        self.assertIsNone(self.history.pending())
        self.assertEqual(self.history.latest_redo().id, record_id)
        self.assertIsNone(self.history.latest_undo())
        self.history.redo()
        self.assertEqual(self.files(), ["A1.pdf", "B1.pdf"])

    def test_keeping_an_undo_interrupted_after_its_record_reached_the_redo_stack(self):
        _, _, record_id = self._rename_two()
        self.crash_writing_into(self.history.undo, os.path.join(self.data_dir, "redo"), after=True)
        self.history.keep_result()
        self.assertIsNone(self.history.latest_undo())
        self.assertEqual(self.history.latest_redo().id, record_id)
        self.history.redo()
        self.assertIsNone(self.history.latest_redo())
        self.assertEqual(self.files(), ["A1.pdf", "B1.pdf"])

    def test_keeping_a_finished_redo_moves_its_record_back_to_the_undo_stack(self):
        (a, b), (new_a, new_b), record_id = self._rename_two()
        self.history.undo()
        self.crash_writing_into(self.history.redo, os.path.join(self.data_dir, "undo"))
        result = self.history.keep_result()
        self.assertEqual([(m.original, m.renamed) for m in result.changes], [(a, new_a), (b, new_b)])
        self.assertEqual(self.history.latest_undo().id, record_id)
        self.assertIsNone(self.history.latest_redo())
        self.history.undo()
        self.assertEqual(self.files(), ["a.pdf", "b.pdf"])

    def test_keeping_a_finished_rename_reports_path_changes_and_starts_a_new_history(self):
        r = self.create("r.pdf")
        self.history.rename(RenameVerdict([RenameEntry(r, self.path("R1.pdf"))]))
        self.history.undo()
        a = self.create("a.pdf")
        new_a = self.path("A1.pdf")
        plan = [RenameEntry(a, new_a)]
        self.crash_writing_into(lambda: self.history.rename(RenameVerdict(plan)), os.path.join(self.data_dir, "undo"))
        result = self.history.keep_result()
        self.assertEqual(result.operation, "rename")
        self.assertEqual([(m.original, m.renamed) for m in result.changes], [(a, new_a)])
        self.assertIsNone(self.history.pending())
        self.assertIsNone(self.history.latest_undo())
        self.assertIsNone(self.history.latest_redo())


class TestRecoveryWorkspaceMeta(WorkspaceMetaTestCase):
    """中斷還原同樣寫回工作區 meta；meta 寫回都在刪除進行中紀錄之前完成"""

    def _scanned_sources(self):
        """清理工作區掃描到的（子資料夾、來源檔名）"""
        scan = self.workspace.scan(None, [], ProjectService().load_project)
        return [(e.folder, e.source_name) for e in scan.entries]

    def test_keeping_a_finished_undo_writes_meta_back(self):
        folder, part = self._part_in_workspace()
        self.history.rename(self._to_scores((part, self.path("01-高笙.pdf"))))
        self.workspace.purge_empty_folders()
        self.crash_writing_into(self.history.undo, os.path.join(self.data_dir, "redo"))
        self.history.keep_result()
        self.assertTrue(os.path.isfile(part))
        self.assertEqual(self._scanned_sources(), [(folder, "合併譜.pdf")])

    def test_restoring_an_interrupted_rename_writes_meta_back(self):
        folder, first = self._part_in_workspace("高笙.pdf")
        _, second = self._part_in_workspace("揚琴.pdf")
        verdict = self._to_scores((first, self.path("01-高笙.pdf")), (second, self.path("02-揚琴.pdf")))
        self.interrupt(lambda: self.history.rename(verdict), n2=_Crash())
        # 中斷後子資料夾的 meta 不見了
        os.remove(os.path.join(folder, "meta.json"))
        self.history.recover()
        self.assertTrue(os.path.isfile(first))
        self.assertEqual(self._scanned_sources(), [(folder, "合併譜.pdf")])

    def test_crash_while_undo_writes_meta_back_leaves_the_journal(self):
        folder, part = self._part_in_workspace()
        self.history.rename(self._to_scores((part, self.path("01-高笙.pdf"))))
        self.workspace.purge_empty_folders()
        self.crash_writing_into(self.history.undo, folder)
        self.assertTrue(self.history.pending().complete)
        self.history.keep_result()
        self.assertEqual(self._scanned_sources(), [(folder, "合併譜.pdf")])

    def test_crash_while_recovery_writes_meta_back_leaves_the_journal(self):
        folder, first = self._part_in_workspace("高笙.pdf")
        _, second = self._part_in_workspace("揚琴.pdf")
        verdict = self._to_scores((first, self.path("01-高笙.pdf")), (second, self.path("02-揚琴.pdf")))
        self.interrupt(lambda: self.history.rename(verdict), n2=_Crash())
        os.remove(os.path.join(folder, "meta.json"))
        self.crash_writing_into(self.history.recover, folder)
        self.assertIsNotNone(self.history.pending())
        self.history.recover()
        self.assertIsNone(self.history.pending())
        self.assertEqual(self._scanned_sources(), [(folder, "合併譜.pdf")])

    def test_crash_while_a_failed_undo_writes_meta_back_leaves_the_journal(self):
        folder, first = self._part_in_workspace("高笙.pdf")
        _, second = self._part_in_workspace("揚琴.pdf")
        outs = [self.path("01-高笙.pdf"), self.path("02-揚琴.pdf")]
        self.history.rename(self._to_scores((first, outs[0]), (second, outs[1])))
        self.workspace.purge_empty_folders()
        # 第二個檔案搬不回工作區觸發回滾；第一個檔案又搬不回輸出位置，卡在工作區
        self.fail_moves_when(lambda old, new: new == second or (old, new) == (first, outs[0]))
        self.crash_writing_into(self.history.undo, folder)
        self.stop_failing()
        self.assertIsNotNone(self.history.pending())


if __name__ == '__main__':
    unittest.main()
