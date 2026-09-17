# -*- coding: utf-8 -*-
"""
重新命名服務單元測試
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.models import FileInfo, Group, Project, RenameEntry
from services.file_service import FileService
from services.rename_service import RenameRollbackError, RenameService


class TestRenameService(unittest.TestCase):
    """RenameService 測試"""

    def setUp(self):
        self.file_service = FileService()
        self.rename_service = RenameService(self.file_service)
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_file(self, name):
        path = os.path.join(self.temp_dir, name)
        with open(path, 'w') as f:
            f.write('dummy')
        return path

    def test_generate_rename_plan_basic(self):
        p1 = self._create_file("raw_fl.pdf")
        p2 = self._create_file("raw_ob.pdf")
        project = Project(
            master_template="{序號}. {樂器} - {曲名}.pdf",
            groups=[Group(
                files=[
                    FileInfo(p1, "raw_fl.pdf"),
                    FileInfo(p2, "raw_ob.pdf"),
                ],
                instruments=["Flute", "Oboe"],
                selected_instruments=[0, 1],
                piece_name="Sym5",
            )],
        )
        plan = self.rename_service.generate_rename_plan(project)
        self.assertEqual(len(plan), 2)
        self.assertIn("1. Flute - Sym5.pdf", os.path.basename(plan[0].new_path))
        self.assertIn("2. Oboe - Sym5.pdf", os.path.basename(plan[1].new_path))

    def test_generate_plan_with_subfolders(self):
        p1 = self._create_file("fl.pdf")
        project = Project(
            master_template="{序號}. {樂器}.pdf",
            use_subfolders=True,
            subfolder_template="{曲名}",
            groups=[Group(
                files=[FileInfo(p1, "fl.pdf")],
                instruments=["Flute"],
                selected_instruments=[0],
                piece_name="Test",
            )],
        )
        plan = self.rename_service.generate_rename_plan(project)
        self.assertEqual(len(plan), 1)
        self.assertIn("Test", plan[0].new_path)
        self.assertIn("1. Flute.pdf", os.path.basename(plan[0].new_path))

    def test_detect_conflicts(self):
        plan = [
            RenameEntry("a.pdf", os.path.join(self.temp_dir, "Same.pdf")),
            RenameEntry("b.pdf", os.path.join(self.temp_dir, "same.pdf")),
        ]
        conflicts = self.rename_service.detect_conflicts(plan)
        self.assertEqual(len(conflicts), 1)

    def test_detect_no_conflicts(self):
        plan = [
            RenameEntry("a.pdf", os.path.join(self.temp_dir, "A.pdf")),
            RenameEntry("b.pdf", os.path.join(self.temp_dir, "B.pdf")),
        ]
        conflicts = self.rename_service.detect_conflicts(plan)
        self.assertEqual(len(conflicts), 0)

    def test_apply_auto_suffix(self):
        plan = [
            RenameEntry("a.pdf", os.path.join(self.temp_dir, "Same.pdf")),
            RenameEntry("b.pdf", os.path.join(self.temp_dir, "Same.pdf")),
            RenameEntry("c.pdf", os.path.join(self.temp_dir, "Same.pdf")),
        ]
        result = self.rename_service.apply_auto_suffix(plan)
        names = [os.path.basename(e.new_path) for e in result]
        self.assertEqual(names[0], "Same.pdf")
        self.assertEqual(names[1], "Same (1).pdf")
        self.assertEqual(names[2], "Same (2).pdf")

    def test_execute_rename(self):
        p1 = self._create_file("old1.pdf")
        p2 = self._create_file("old2.pdf")
        plan = [
            RenameEntry(p1, os.path.join(self.temp_dir, "new1.pdf")),
            RenameEntry(p2, os.path.join(self.temp_dir, "new2.pdf")),
        ]
        project = Project()
        record = self.rename_service.execute_rename(plan, project)
        self.assertEqual(len(record.mappings), 2)
        self.assertTrue(os.path.isfile(os.path.join(self.temp_dir, "new1.pdf")))
        self.assertTrue(os.path.isfile(os.path.join(self.temp_dir, "new2.pdf")))
        self.assertFalse(os.path.isfile(p1))
        self.assertFalse(os.path.isfile(p2))

    def test_execute_rename_creates_subdirectory(self):
        p1 = self._create_file("fl.pdf")
        sub_dir = os.path.join(self.temp_dir, "SubFolder")
        plan = [
            RenameEntry(p1, os.path.join(sub_dir, "new.pdf")),
        ]
        project = Project()
        record = self.rename_service.execute_rename(plan, project)
        self.assertTrue(os.path.isdir(sub_dir))
        self.assertTrue(os.path.isfile(os.path.join(sub_dir, "new.pdf")))
        self.assertIn(sub_dir, record.created_directories)

    def test_detect_duplicate_sources(self):
        plan = [
            RenameEntry("shared.pdf", os.path.join(self.temp_dir, "A.pdf")),
            RenameEntry("shared.pdf", os.path.join(self.temp_dir, "B.pdf")),
            RenameEntry("other.pdf", os.path.join(self.temp_dir, "C.pdf")),
        ]
        duplicates = self.rename_service.detect_duplicate_sources(plan)
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(len(next(iter(duplicates.values()))), 2)

    def test_execute_rename_rejects_duplicate_sources(self):
        p1 = self._create_file("shared.pdf")
        plan = [
            RenameEntry(p1, os.path.join(self.temp_dir, "A.pdf")),
            RenameEntry(p1, os.path.join(self.temp_dir, "B.pdf")),
        ]
        with self.assertRaises(FileExistsError):
            self.rename_service.execute_rename(plan, Project())
        self.assertTrue(os.path.isfile(p1))
        self.assertFalse(os.path.isfile(os.path.join(self.temp_dir, "A.pdf")))

    def test_execute_rename_rejects_missing_source(self):
        plan = [
            RenameEntry(os.path.join(self.temp_dir, "ghost.pdf"), os.path.join(self.temp_dir, "A.pdf")),
        ]
        with self.assertRaises(FileNotFoundError):
            self.rename_service.execute_rename(plan, Project())

    def test_execute_rename_rejects_occupied_target(self):
        p1 = self._create_file("old.pdf")
        occupied = self._create_file("taken.pdf")
        plan = [RenameEntry(p1, occupied)]
        with self.assertRaises(FileExistsError):
            self.rename_service.execute_rename(plan, Project())
        self.assertTrue(os.path.isfile(p1))
        with open(occupied) as f:
            self.assertEqual(f.read(), "dummy")

    def test_execute_rename_rejects_duplicate_targets(self):
        p1 = self._create_file("a.pdf")
        p2 = self._create_file("b.pdf")
        target = os.path.join(self.temp_dir, "Same.pdf")
        with self.assertRaises(FileExistsError):
            self.rename_service.execute_rename([RenameEntry(p1, target), RenameEntry(p2, target)], Project())
        self.assertTrue(os.path.isfile(p1))
        self.assertTrue(os.path.isfile(p2))

    def test_find_missing_sources(self):
        p1 = self._create_file("a.pdf")
        ghost = os.path.join(self.temp_dir, "ghost.pdf")
        plan = [RenameEntry(p1, os.path.join(self.temp_dir, "A.pdf")), RenameEntry(ghost, os.path.join(self.temp_dir, "G.pdf"))]
        self.assertEqual(self.rename_service.find_missing_sources(plan), [ghost])

    def test_rollback_failure_reports_residual(self):
        p1 = self._create_file("old1.pdf")
        p2 = self._create_file("old2.pdf")
        p3 = self._create_file("old3.pdf")
        new1 = os.path.join(self.temp_dir, "new1.pdf")
        new2 = os.path.join(self.temp_dir, "new2.pdf")
        plan = [RenameEntry(p1, new1), RenameEntry(p2, new2), RenameEntry(p3, os.path.join(self.temp_dir, "new3.pdf"))]
        original_rename = self.file_service.rename_file

        def flaky_rename(old_path, new_path):
            # 第三筆搬移失敗觸發回滾；回滾時第一筆搬不回去
            if old_path == p3 or new_path == p1:
                raise OSError("simulated")
            original_rename(old_path, new_path)

        self.file_service.rename_file = flaky_rename
        with self.assertRaises(RenameRollbackError) as ctx:
            self.rename_service.execute_rename(plan, Project())
        self.assertEqual([m.renamed for m in ctx.exception.residual], [new1])
        self.assertTrue(os.path.isfile(new1))
        self.assertTrue(os.path.isfile(p2))
        self.assertTrue(os.path.isfile(p3))
        self.assertIn("new1.pdf", str(ctx.exception))

    def test_execute_rename_allows_noop_entry(self):
        p1 = self._create_file("same.pdf")
        record = self.rename_service.execute_rename([RenameEntry(p1, p1)], Project())
        self.assertEqual(len(record.mappings), 1)
        self.assertTrue(os.path.isfile(p1))

    def test_execute_rename_rolls_back_on_failure(self):
        p1 = self._create_file("old1.pdf")
        p2 = self._create_file("old2.pdf")
        sub_dir = os.path.join(self.temp_dir, "NewDir")
        plan = [
            RenameEntry(p1, os.path.join(sub_dir, "new1.pdf")),
            RenameEntry(p2, os.path.join(sub_dir, "new2.pdf")),
        ]
        original_rename = self.file_service.rename_file
        calls = []

        def failing_rename(old_path, new_path):
            calls.append(old_path)
            if len(calls) == 2:
                raise OSError("simulated failure")
            original_rename(old_path, new_path)

        self.file_service.rename_file = failing_rename
        with self.assertRaises(OSError):
            self.rename_service.execute_rename(plan, Project())
        self.assertTrue(os.path.isfile(p1))
        self.assertTrue(os.path.isfile(p2))
        self.assertFalse(os.path.exists(sub_dir))

    def test_generate_plan_skips_empty_group(self):
        project = Project(
            groups=[Group(files=[], instruments=[])],
        )
        plan = self.rename_service.generate_rename_plan(project)
        self.assertEqual(len(plan), 0)

    def test_generate_plan_with_small_template(self):
        p1 = self._create_file("fl.pdf")
        project = Project(
            master_template="{序號}. {樂器}.pdf",
            groups=[Group(
                files=[FileInfo(p1, "fl.pdf")],
                instruments=["Flute"],
                selected_instruments=[0],
                piece_name="Test",
                use_small_template=True,
                small_template="{樂器} - {曲名}.pdf",
            )],
        )
        plan = self.rename_service.generate_rename_plan(project)
        self.assertEqual(os.path.basename(plan[0].new_path), "Flute - Test.pdf")

    def test_generate_plan_with_score_file(self):
        p_score = self._create_file("score.pdf")
        p1 = self._create_file("fl.pdf")
        project = Project(
            master_template="{序號}-{樂器}.pdf",
            groups=[Group(
                files=[FileInfo(p1, "fl.pdf")],
                instruments=["Flute"],
                selected_instruments=[0],
                score_file=FileInfo(p_score, "score.pdf"),
                score_label="Full Score",
            )],
        )
        plan = self.rename_service.generate_rename_plan(project)
        self.assertEqual(len(plan), 2)
        self.assertIn("00-Full Score.pdf", os.path.basename(plan[0].new_path))
        self.assertIn("1-Flute.pdf", os.path.basename(plan[1].new_path))


if __name__ == '__main__':
    unittest.main()
