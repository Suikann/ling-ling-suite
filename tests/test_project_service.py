# -*- coding: utf-8 -*-
"""
專案服務單元測試
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.models import FileInfo, Group, Project
from services.project_service import ProjectService


class TestProjectService(unittest.TestCase):
    """ProjectService 測試"""

    def setUp(self):
        self.service = ProjectService()
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_save_and_load_roundtrip(self):
        project = Project(
            instruments=["Flute", "Oboe", "Clarinet"],
            master_template="{序號}. {樂器} - {曲名}.pdf",
            use_subfolders=True,
            subfolder_template="{曲名}",
        )
        project.ungrouped_files = [
            FileInfo("C:/test/ungrouped.pdf", "ungrouped.pdf"),
        ]
        project.groups = [
            Group(
                id="test-group-1",
                name="第一樂章",
                files=[
                    FileInfo("C:/test/fl.pdf", "fl.pdf"),
                    FileInfo("C:/test/ob.pdf", "ob.pdf"),
                ],
                selected_instruments=[0, 1],
                piece_name="貝多芬第五號",
                movement_number="1",
                movement_name="Allegro con brio",
                use_small_template=True,
                small_template="{樂器} - {曲名} {樂章名稱}.pdf",
            ),
        ]
        path = os.path.join(self.temp_dir, "test.llproj")
        self.service.save_project(project, path)
        self.assertTrue(os.path.isfile(path))
        loaded = self.service.load_project(path)
        self.assertEqual(loaded.instruments, ["Flute", "Oboe", "Clarinet"])
        self.assertEqual(loaded.master_template, "{序號}. {樂器} - {曲名}.pdf")
        self.assertTrue(loaded.use_subfolders)
        self.assertEqual(loaded.subfolder_template, "{曲名}")
        self.assertEqual(len(loaded.ungrouped_files), 1)
        self.assertEqual(loaded.ungrouped_files[0].display_name, "ungrouped.pdf")
        self.assertEqual(len(loaded.groups), 1)
        g = loaded.groups[0]
        self.assertEqual(g.id, "test-group-1")
        self.assertEqual(g.name, "第一樂章")
        self.assertEqual(len(g.files), 2)
        self.assertEqual(g.selected_instruments, [0, 1])
        self.assertEqual(g.piece_name, "貝多芬第五號")
        self.assertEqual(g.movement_number, "1")
        self.assertEqual(g.movement_name, "Allegro con brio")
        self.assertTrue(g.use_small_template)
        self.assertEqual(g.small_template, "{樂器} - {曲名} {樂章名稱}.pdf")

    def test_empty_project_roundtrip(self):
        project = Project()
        path = os.path.join(self.temp_dir, "empty.llproj")
        self.service.save_project(project, path)
        loaded = self.service.load_project(path)
        self.assertEqual(loaded.instruments, [])
        self.assertEqual(len(loaded.groups), 0)
        self.assertEqual(len(loaded.ungrouped_files), 0)

    def test_unicode_content(self):
        project = Project(instruments=["長笛", "雙簧管"])
        project.groups = [
            Group(name="莫札特", piece_name="第21號鋼琴協奏曲"),
        ]
        path = os.path.join(self.temp_dir, "unicode.llproj")
        self.service.save_project(project, path)
        loaded = self.service.load_project(path)
        self.assertEqual(loaded.instruments, ["長笛", "雙簧管"])
        self.assertEqual(loaded.groups[0].piece_name, "第21號鋼琴協奏曲")


if __name__ == '__main__':
    unittest.main()
