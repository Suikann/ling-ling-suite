# -*- coding: utf-8 -*-
"""
匯入服務單元測試
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from services.file_service import FileService
from services.import_service import ImportService


class TestImportService(unittest.TestCase):
    """ImportService 測試"""

    def setUp(self):
        self.file_service = FileService()
        self.import_service = ImportService(self.file_service)
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_file(self, *parts):
        path = os.path.join(self.temp_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            f.write('dummy')
        return path

    def test_import_files(self):
        p1 = self._create_file("a.pdf")
        p2 = self._create_file("b.pdf")
        result = self.import_service.import_files([p1, p2])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0].display_name, "a.pdf")
        self.assertEqual(result[1].display_name, "b.pdf")

    def test_import_files_filters_non_pdf(self):
        p1 = self._create_file("a.pdf")
        p2 = self._create_file("b.txt")
        result = self.import_service.import_files([p1, p2])
        self.assertEqual(len(result), 1)

    def test_import_folder_flat(self):
        self._create_file("Song - Flute.pdf")
        self._create_file("Song - Oboe.pdf")
        groups, ungrouped = self.import_service.import_folder(self.temp_dir)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(ungrouped), 0)
        self.assertEqual(len(groups[0].files), 2)
        self.assertEqual(groups[0].piece_name, "Song")

    def test_import_folder_nested(self):
        self._create_file("Movement1", "Sym5 - Flute.pdf")
        self._create_file("Movement1", "Sym5 - Oboe.pdf")
        self._create_file("Movement2", "Sym5 - Flute.pdf")
        groups, ungrouped = self.import_service.import_folder(self.temp_dir)
        self.assertEqual(len(groups), 2)
        self.assertEqual(len(ungrouped), 0)

    def test_import_folder_nested_with_root_pdfs(self):
        self._create_file("root.pdf")
        self._create_file("sub", "a.pdf")
        groups, ungrouped = self.import_service.import_folder(self.temp_dir)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(ungrouped), 1)
        self.assertEqual(ungrouped[0].display_name, "root.pdf")

    def test_import_folder_empty_subfolder_skipped(self):
        os.makedirs(os.path.join(self.temp_dir, "empty_sub"))
        self._create_file("has_pdf", "a.pdf")
        groups, ungrouped = self.import_service.import_folder(self.temp_dir)
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].name, "has_pdf")

    def test_import_folder_hidden_subdirs_ignored(self):
        """含隱藏子資料夾（無 PDF）時，根目錄 PDF 應歸為一個群組"""
        os.makedirs(os.path.join(self.temp_dir, ".hidden"))
        os.makedirs(os.path.join(self.temp_dir, "$RECYCLE.BIN"))
        self._create_file("Song - Flute.pdf")
        self._create_file("Song - Oboe.pdf")
        groups, ungrouped = self.import_service.import_folder(self.temp_dir)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(ungrouped), 0)
        self.assertEqual(len(groups[0].files), 2)

    def test_import_folder_only_pdf_files(self):
        self._create_file("a.pdf")
        self._create_file("b.txt")
        self._create_file("c.doc")
        groups, ungrouped = self.import_service.import_folder(self.temp_dir)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0].files), 1)


class TestFileService(unittest.TestCase):
    """FileService 測試"""

    def setUp(self):
        self.file_service = FileService()
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_list_pdf_files(self):
        for name in ["a.pdf", "b.PDF", "c.txt"]:
            with open(os.path.join(self.temp_dir, name), 'w') as f:
                f.write('dummy')
        result = self.file_service.list_pdf_files(self.temp_dir)
        names = [os.path.basename(p) for p in result]
        self.assertIn("a.pdf", names)
        self.assertIn("b.PDF", names)
        self.assertNotIn("c.txt", names)

    def test_has_subdirectories(self):
        self.assertFalse(self.file_service.has_subdirectories(self.temp_dir))
        os.makedirs(os.path.join(self.temp_dir, "sub"))
        self.assertTrue(self.file_service.has_subdirectories(self.temp_dir))

    def test_create_directory(self):
        path = os.path.join(self.temp_dir, "a", "b", "c")
        self.file_service.create_directory(path)
        self.assertTrue(os.path.isdir(path))

    def test_rename_file(self):
        old = os.path.join(self.temp_dir, "old.pdf")
        new = os.path.join(self.temp_dir, "new.pdf")
        with open(old, 'w') as f:
            f.write('test')
        self.file_service.rename_file(old, new)
        self.assertTrue(os.path.isfile(new))
        self.assertFalse(os.path.isfile(old))


if __name__ == '__main__':
    unittest.main()
