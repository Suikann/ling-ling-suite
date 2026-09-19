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


if __name__ == '__main__':
    unittest.main()
