# -*- coding: utf-8 -*-
"""
重新命名服務單元測試
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core.models import RenameEntry
from services.file_service import FileService
from services.rename_service import RenameService
from path_spellings import spellings


class TestRenameService(unittest.TestCase):
    """RenameService 測試"""

    def setUp(self):
        self.file_service = FileService()
        self.rename_service = RenameService(self.file_service)
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_file(self, name, content='dummy'):
        path = os.path.join(self.temp_dir, name)
        with open(path, 'w') as f:
            f.write(content)
        return path

    def test_find_occupied_targets_ignores_sources_within_plan(self):
        a = self._create_file("a.pdf")
        b = self._create_file("b.pdf")
        taken = self._create_file("taken.pdf")
        free = os.path.join(self.temp_dir, "free.pdf")
        plan = [
            RenameEntry(a, b),
            RenameEntry(b, a),
            RenameEntry(self._create_file("c.pdf"), taken),
            RenameEntry(self._create_file("d.pdf"), free),
        ]
        self.assertEqual(self.rename_service.find_occupied_targets(plan), [taken])

    def test_find_empty_names(self):
        a = self._create_file("a.pdf")
        b = self._create_file("b.pdf")
        c = self._create_file("c.pdf")
        plan = [
            RenameEntry(a, os.path.join(self.temp_dir, ".pdf")),
            RenameEntry(b, os.path.join(self.temp_dir, "ok.pdf")),
            RenameEntry(c, os.path.join(self.temp_dir, "")),
        ]
        self.assertEqual(self.rename_service.find_empty_names(plan), [a, c])

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

    def test_auto_suffix_separates_one_target_written_in_two_spellings(self):
        target = os.path.join(os.getcwd(), "out", "Same.pdf")  # 只比對路徑，不碰磁碟
        for label, spelled in spellings(target).items():
            with self.subTest(label):
                plan = [RenameEntry("a.pdf", target), RenameEntry("b.pdf", spelled)]
                suffixed = self.rename_service.apply_auto_suffix(plan)
                self.assertEqual(self.rename_service.detect_conflicts(suffixed), {})

    def test_detect_duplicate_sources(self):
        plan = [
            RenameEntry("shared.pdf", os.path.join(self.temp_dir, "A.pdf")),
            RenameEntry("shared.pdf", os.path.join(self.temp_dir, "B.pdf")),
            RenameEntry("other.pdf", os.path.join(self.temp_dir, "C.pdf")),
        ]
        duplicates = self.rename_service.detect_duplicate_sources(plan)
        self.assertEqual(len(duplicates), 1)
        self.assertEqual(len(next(iter(duplicates.values()))), 2)

    def test_find_missing_sources(self):
        p1 = self._create_file("a.pdf")
        ghost = os.path.join(self.temp_dir, "ghost.pdf")
        plan = [RenameEntry(p1, os.path.join(self.temp_dir, "A.pdf")), RenameEntry(ghost, os.path.join(self.temp_dir, "G.pdf"))]
        self.assertEqual(self.rename_service.find_missing_sources(plan), [ghost])

if __name__ == '__main__':
    unittest.main()
