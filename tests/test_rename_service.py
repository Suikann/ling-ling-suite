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
from services.rename_service import RenameService


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
            instruments=["Flute", "Oboe"],
            master_template="{序號}. {樂器} - {曲名}.pdf",
            groups=[Group(
                files=[
                    FileInfo(p1, "raw_fl.pdf"),
                    FileInfo(p2, "raw_ob.pdf"),
                ],
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
            instruments=["Flute"],
            master_template="{序號}. {樂器}.pdf",
            use_subfolders=True,
            subfolder_template="{曲名}",
            groups=[Group(
                files=[FileInfo(p1, "fl.pdf")],
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

    def test_generate_plan_skips_empty_group(self):
        project = Project(
            instruments=["Flute"],
            groups=[Group(files=[], selected_instruments=[])],
        )
        plan = self.rename_service.generate_rename_plan(project)
        self.assertEqual(len(plan), 0)

    def test_generate_plan_with_small_template(self):
        p1 = self._create_file("fl.pdf")
        project = Project(
            instruments=["Flute"],
            master_template="{序號}. {樂器}.pdf",
            groups=[Group(
                files=[FileInfo(p1, "fl.pdf")],
                selected_instruments=[0],
                piece_name="Test",
                use_small_template=True,
                small_template="{樂器} - {曲名}.pdf",
            )],
        )
        plan = self.rename_service.generate_rename_plan(project)
        self.assertEqual(os.path.basename(plan[0].new_path), "Flute - Test.pdf")


if __name__ == '__main__':
    unittest.main()
