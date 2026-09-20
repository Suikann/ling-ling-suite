# -*- coding: utf-8 -*-
"""
批次搬移服務單元測試：進行中紀錄與中斷後還原
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from services.file_service import FileService
from services.move_journal import MoveJournalStore
from services.move_service import MoveService


class _Crash(BaseException):
    """模擬程式被強制結束：不是 Exception，所以 execute 不會攔下來回滾"""


class TestMoveJournal(unittest.TestCase):
    """MoveService 的進行中紀錄與還原"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        journal_file = os.path.join(self.temp_dir, "journal", "pending_move.json")
        self._patch = patch("services.move_journal.MOVE_JOURNAL_FILE", journal_file)
        self._patch.start()
        self.file_service = FileService()
        self.store = MoveJournalStore(self.file_service)
        self.mover = MoveService(self.file_service, self.store)

    def tearDown(self):
        import shutil
        self._patch.stop()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create(self, name, content="dummy"):
        path = os.path.join(self.temp_dir, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def _read(self, path):
        with open(path) as f:
            return f.read()

    def _spy_rename(self, before=None):
        """把 rename_file 換成每次搬移前先呼叫 before(old, new) 的版本"""
        original = self.file_service.rename_file

        def spy(old_path, new_path):
            if before:
                before(old_path, new_path)
            original(old_path, new_path)

        self.file_service.rename_file = spy

    def _raise_on_rename(self, **by_call):
        """第 n 次（從 1 起算）呼叫 rename_file 時、搬移前拋出指定例外，例如 _raise_on_rename(n2=_Crash())"""
        calls = []

        def before(old_path, new_path):
            calls.append(old_path)
            error = by_call.get(f"n{len(calls)}")
            if error:
                raise error

        self._spy_rename(before)

    def _crash_on_rename(self, nth):
        """第 nth 次（從 1 起算）呼叫 rename_file 時、搬移前當機"""
        self._raise_on_rename(**{f"n{nth}": _Crash()})

    def _swap(self):
        a = self._create("a.pdf", "A")
        b = self._create("b.pdf", "B")
        return a, b, [(a, b), (b, a)]

    def test_execute_journals_before_first_move_and_clears_after_success(self):
        a = self._create("a.pdf", "A")
        b = os.path.join(self.temp_dir, "b.pdf")
        seen = []
        self._spy_rename(lambda old, new: seen.append(self.store.load()))
        self.mover.execute([(a, b)])
        self.assertEqual(len(seen), 1)
        journal = seen[0]
        self.assertEqual([(s.index, s.source, s.target) for s in journal.steps], [(0, a, b)])
        self.assertEqual(journal.completed, 0)
        self.assertIsNone(self.store.load())

    def test_crash_in_first_phase_leaves_journal_of_actual_positions(self):
        a, b, moves = self._swap()
        self._crash_on_rename(2)
        with self.assertRaises(_Crash):
            self.mover.execute(moves)
        journal = self.mover.load_pending()
        self.assertEqual(journal.completed, 1)
        self.assertEqual(journal.moved_indices(), [0])
        self.assertEqual(
            [(s.source, s.target) for s in journal.steps],
            [(a, a + ".moving"), (b, b + ".moving"), (a + ".moving", b), (b + ".moving", a)],
        )
        self.assertEqual(sorted(os.listdir(self.temp_dir)), ["a.pdf.moving", "b.pdf", "journal"])

    def _interrupt(self, moves, nth):
        """執行 moves 並在第 nth 次搬移前當機，回傳讀回的進行中紀錄"""
        self._crash_on_rename(nth)
        with self.assertRaises(_Crash):
            self.mover.execute(moves)
        self.file_service.rename_file = FileService().rename_file
        return self.mover.load_pending()

    def _files(self):
        return sorted(n for n in os.listdir(self.temp_dir) if n != "journal")

    def test_recover_from_first_phase_interruption(self):
        a, b, moves = self._swap()
        result = self.mover.recover(self._interrupt(moves, 2))
        self.assertEqual(result.restored, [a])
        self.assertEqual(result.skipped, [])
        self.assertEqual(result.residual, [])
        self.assertEqual(self._files(), ["a.pdf", "b.pdf"])
        self.assertEqual([self._read(p) for p in (a, b)], ["A", "B"])
        self.assertIsNone(self.mover.load_pending())

    def test_recover_from_second_phase_interruption(self):
        a, b, moves = self._swap()
        journal = self._interrupt(moves, 4)
        self.assertEqual(self._files(), ["b.pdf", "b.pdf.moving"])
        self.assertEqual(journal.moved_indices(), [0, 1])
        result = self.mover.recover(journal)
        self.assertEqual(result.restored, [a, b])
        self.assertEqual(self._files(), ["a.pdf", "b.pdf"])
        self.assertEqual([self._read(p) for p in (a, b)], ["A", "B"])

    def test_load_pending_counts_a_move_done_but_not_yet_recorded(self):
        a, b, moves = self._swap()
        original_save = self.store.save
        saves = []

        def crash_on_second_save(journal):
            saves.append(journal.completed)
            if len(saves) == 2:
                raise _Crash()
            original_save(journal)

        self.store.save = crash_on_second_save
        with self.assertRaises(_Crash):
            self.mover.execute(moves)
        self.store.save = original_save
        self.assertEqual(self._files(), ["a.pdf.moving", "b.pdf"])
        journal = self.mover.load_pending()
        self.assertEqual(journal.completed, 1)
        self.assertEqual(journal.moved_indices(), [0])
        result = self.mover.recover(journal)
        self.assertEqual(result.restored, [a])
        self.assertEqual(self._files(), ["a.pdf", "b.pdf"])

    def test_recover_after_crash_during_rollback(self):
        a, b, moves = self._swap()
        # 第 4 步就位失敗觸發回滾；回滾逆轉了第 3 步之後、逆轉第 2 步之前當機
        self._raise_on_rename(n4=OSError("simulated"), n6=_Crash())
        with self.assertRaises(_Crash):
            self.mover.execute(moves)
        self.file_service.rename_file = FileService().rename_file
        self.assertEqual(self._files(), ["a.pdf.moving", "b.pdf.moving"])
        result = self.mover.recover(self.mover.load_pending())
        self.assertEqual(result.restored, [a, b])
        self.assertEqual(result.skipped, [])
        self.assertEqual(self._files(), ["a.pdf", "b.pdf"])
        self.assertEqual([self._read(p) for p in (a, b)], ["A", "B"])

    def test_recover_skips_and_lists_files_no_longer_at_recorded_location(self):
        a, b, moves = self._swap()
        journal = self._interrupt(moves, 3)
        os.remove(a + ".moving")
        result = self.mover.recover(journal)
        self.assertEqual(result.restored, [b])
        self.assertEqual([(m.original, m.renamed) for m in result.skipped], [(a, a + ".moving")])
        self.assertEqual(result.residual, [])
        self.assertEqual(self._files(), ["b.pdf"])
        self.assertIsNone(self.mover.load_pending())

    def test_recover_reports_files_it_cannot_move_back_as_residual(self):
        a, b, moves = self._swap()
        journal = self._interrupt(moves, 3)
        self._raise_on_rename(n2=OSError("simulated"))
        result = self.mover.recover(journal)
        self.assertEqual(result.restored, [b])
        self.assertEqual(result.skipped, [])
        self.assertEqual([(m.original, m.renamed) for m in result.residual], [(a, a + ".moving")])
        self.assertEqual(self._files(), ["a.pdf.moving", "b.pdf"])
        self.assertIsNone(self.mover.load_pending())

    def test_rollback_after_failure_clears_journal(self):
        a, b, moves = self._swap()
        self._raise_on_rename(n3=OSError("simulated"))
        with self.assertRaises(OSError):
            self.mover.execute(moves)
        self.assertIsNone(self.mover.load_pending())
        self.assertEqual(self._files(), ["a.pdf", "b.pdf"])

    def test_recover_removes_directories_created_by_the_interrupted_batch(self):
        a = self._create("a.pdf", "A")
        b = self._create("b.pdf", "B")
        sub = os.path.join(self.temp_dir, "Sub")
        moves = [(a, os.path.join(sub, "a.pdf")), (b, os.path.join(sub, "b.pdf"))]
        journal = self._interrupt(moves, 2)
        self.assertEqual(journal.created_directories, [sub])
        result = self.mover.recover(journal)
        self.assertEqual(result.restored, [a])
        self.assertFalse(os.path.exists(sub))
        self.assertEqual(self._files(), ["a.pdf", "b.pdf"])

    def test_load_pending_returns_none_when_nothing_was_interrupted(self):
        self.assertIsNone(self.mover.load_pending())


if __name__ == '__main__':
    unittest.main()
