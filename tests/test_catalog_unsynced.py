# -*- coding: utf-8 -*-
"""
未同步的測試：寫不進譜庫的改動標成未同步並存進專案檔，下次寫回成功後清掉

未同步只標在寫回失敗的群組上；未同步的值是那個群組待寫入的改動，原值就是譜庫最後一次讀到或寫進去的值。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.models import CatalogChange, CatalogChangeKind, Headcount
from services.project_service import ProjectService
from test_catalog_write_back import WriteBackTestCase


class TestUnsyncedMark(WriteBackTestCase):
    """寫回失敗的群組標成未同步"""

    def save_and_load(self):
        """存成專案檔再讀回來"""
        path = os.path.join(tempfile.mkdtemp(), "unsynced.llproj")
        ProjectService().save_project(self.project, path)
        return ProjectService().load_project(path)

    def test_unsynced_changes_are_kept_in_the_project_file(self):
        self.project.set_headcount(self.group, 2, Headcount(3))
        self.project.mark_catalog_unsynced([self.group.id])
        loaded = self.save_and_load()
        self.assertEqual(
            loaded.unsynced_changes(loaded.groups[0]),
            [CatalogChange(CatalogChangeKind.HEADCOUNT, "琵琶", Headcount(2), Headcount(3))],
        )
        self.assertEqual(loaded.unsynced_groups(), [loaded.groups[0]])

    def test_changes_not_yet_saved_are_not_unsynced(self):
        self.project.set_headcount(self.group, 2, Headcount(3))
        self.assertEqual(self.project.unsynced_changes(self.group), [])
        self.assertEqual(self.project.unsynced_groups(), [])

    def test_writing_back_clears_the_mark(self):
        self.project.set_headcount(self.group, 2, Headcount(3))
        self.project.mark_catalog_unsynced([self.group.id])
        self.sync()
        self.project.set_duration(self.group, 430)
        self.assertEqual(self.project.unsynced_changes(self.group), [])
        self.assertEqual(self.save_and_load().unsynced_groups(), [])


if __name__ == "__main__":
    unittest.main()
