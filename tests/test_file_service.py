# -*- coding: utf-8 -*-
"""
檔案服務原子寫入測試
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.constants import ATOMIC_WRITE_RETRIES
from services.file_service import FileService


class TestFileServiceAtomicWrite(unittest.TestCase):
    """FileService.write_json_atomic 測試"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.file_service = FileService()
        self.path = os.path.join(self.temp_dir, "data.json")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _leftovers(self):
        return [n for n in os.listdir(self.temp_dir) if n != "data.json"]

    def test_writes_json_readable_by_json_load(self):
        self.file_service.write_json_atomic(self.path, {"曲名": "貝五", "n": [1, 2]})
        with open(self.path, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"曲名": "貝五", "n": [1, 2]})
        self.assertEqual(self._leftovers(), [])

    def test_text_lines_end_with_the_platform_newline(self):
        """以 \\n 分行的文字寫成這個平台的換行：Windows 是 \\r\\n，Linux 是 \\n"""
        path = os.path.join(self.temp_dir, "list.txt")
        self.file_service.write_text_atomic(path, "長笛\nOboe")
        with open(path, "rb") as f:
            expected = {"nt": b"\r\n", "posix": b"\n"}[os.name]
            self.assertEqual(f.read(), "長笛".encode("utf-8") + expected + b"Oboe")

    def test_creates_missing_parent_directory(self):
        path = os.path.join(self.temp_dir, "undo", "undo_x.json")
        self.file_service.write_json_atomic(path, {"ok": True})
        with open(path, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"ok": True})

    def test_failure_mid_write_keeps_original_and_leaves_no_temp(self):
        self.file_service.write_json_atomic(self.path, {"version": "old"})
        with patch("services.file_service.os.fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.file_service.write_json_atomic(self.path, {"version": "new"})
        with open(self.path, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"version": "old"})
        self.assertEqual(self._leftovers(), [])

    def test_replace_retries_on_permission_error_then_succeeds(self):
        real_replace = os.replace
        outcomes = [PermissionError("locked"), PermissionError("locked"), real_replace]

        def flaky_replace(src, dst):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome(src, dst)

        with patch("services.file_service.time.sleep") as sleep, \
                patch("services.file_service.os.replace", side_effect=flaky_replace):
            self.file_service.write_json_atomic(self.path, {"ok": True})
        self.assertEqual(sleep.call_count, 2)
        with open(self.path, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"ok": True})
        self.assertEqual(self._leftovers(), [])

    def test_replace_raises_after_retries_exhausted(self):
        self.file_service.write_json_atomic(self.path, {"version": "old"})
        with patch("services.file_service.time.sleep") as sleep, \
                patch("services.file_service.os.replace", side_effect=PermissionError("locked")) as replace:
            with self.assertRaises(PermissionError):
                self.file_service.write_json_atomic(self.path, {"version": "new"})
        self.assertEqual(replace.call_count, ATOMIC_WRITE_RETRIES + 1)
        self.assertEqual(sleep.call_count, ATOMIC_WRITE_RETRIES)
        with open(self.path, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"version": "old"})
        self.assertEqual(self._leftovers(), [])


class TestFileServiceRename(unittest.TestCase):
    """FileService.rename_file 測試"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.file_service = FileService()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create(self, name, content):
        path = os.path.join(self.temp_dir, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def _read(self, path):
        with open(path) as f:
            return f.read()

    def test_refuses_to_overwrite_another_file(self):
        src = self._create("src.pdf", "S")
        dst = self._create("dst.pdf", "D")
        with self.assertRaises(FileExistsError):
            self.file_service.rename_file(src, dst)
        self.assertEqual(self._read(src), "S")
        self.assertEqual(self._read(dst), "D")

    def test_renaming_onto_itself_is_allowed(self):
        src = self._create("same.pdf", "S")
        self.file_service.rename_file(src, src)
        self.assertEqual(self._read(src), "S")


class TestFileServiceWriteBytes(unittest.TestCase):
    """FileService.write_bytes：把位元組寫進新的檔案，父目錄不存在時先建立"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, ignore_errors=True)

    def test_writes_the_bytes_and_creates_missing_folders(self):
        path = os.path.join(self.temp_dir, "a", "b", "part.pdf")
        FileService().write_bytes(path, b"%PDF \x00\xff")
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"%PDF \x00\xff")


class TestFileServiceExists(unittest.TestCase):
    """FileService.file_exists_exact 測試"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.file_service = FileService()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_file_exists_exact_matches_name_case_sensitively(self):
        path = os.path.join(self.temp_dir, "a.pdf")
        with open(path, "w") as f:
            f.write("A")
        self.assertTrue(self.file_service.file_exists_exact(path))
        self.assertFalse(self.file_service.file_exists_exact(os.path.join(self.temp_dir, "A.pdf")))

    def test_file_exists_exact_is_false_for_directories_and_missing_parents(self):
        os.makedirs(os.path.join(self.temp_dir, "sub"))
        self.assertFalse(self.file_service.file_exists_exact(os.path.join(self.temp_dir, "sub")))
        self.assertFalse(self.file_service.file_exists_exact(os.path.join(self.temp_dir, "nope", "x.pdf")))


class TestFileServiceListEntries(unittest.TestCase):
    """FileService.list_entries 測試"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)

    def test_lists_files_and_subdirectories_without_descending(self):
        os.makedirs(os.path.join(self.temp_dir, "b.splitting", "inner"))
        for name in ("c.txt", "A.pdf"):
            with open(os.path.join(self.temp_dir, name), "w") as f:
                f.write("x")
        names = [os.path.basename(p) for p in FileService().list_entries(self.temp_dir)]
        self.assertEqual(names, ["A.pdf", "b.splitting", "c.txt"])


if __name__ == '__main__':
    unittest.main()
