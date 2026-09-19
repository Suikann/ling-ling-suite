# -*- coding: utf-8 -*-
"""
復原服務單元測試
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.models import UndoMapping, UndoRecord
from services.file_service import FileService
from services.undo_service import UndoService


class TestUndoService(unittest.TestCase):
    """UndoService 測試"""

    def setUp(self):
        self.file_service = FileService()
        self.undo_service = UndoService(self.file_service)
        self.temp_dir = tempfile.mkdtemp()
        self.undo_dir = os.path.join(self.temp_dir, "undo")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create(self, name, content="dummy"):
        path = os.path.join(self.temp_dir, name)
        with open(path, "w") as f:
            f.write(content)
        return path

    def _read(self, path):
        with open(path) as f:
            return f.read()

    def _record_dirs(self):
        """把復原／重做紀錄目錄都導到暫存目錄"""
        return patch.multiple(
            "services.undo_service",
            UNDO_DIR=self.undo_dir, REDO_DIR=os.path.join(self.temp_dir, "redo"),
        )

    def _make_record(self, timestamp="20260101_120000"):
        return UndoRecord(
            timestamp=timestamp,
            description="測試操作",
            mappings=[
                UndoMapping(
                    original=os.path.join(self.temp_dir, "old.pdf"),
                    renamed=os.path.join(self.temp_dir, "new.pdf"),
                ),
            ],
        )

    @patch('services.undo_service.UNDO_DIR')
    def test_save_and_load(self, mock_dir):
        mock_dir.__str__ = lambda s: self.undo_dir
        # 直接覆寫模組層級常數
        import services.undo_service as mod
        original_dir = mod.UNDO_DIR
        mod.UNDO_DIR = self.undo_dir
        try:
            record = self._make_record()
            path = self.undo_service.save_undo_record(record)
            self.assertTrue(os.path.isfile(path))
            loaded = self.undo_service.get_latest_undo_record()
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.description, "測試操作")
            self.assertEqual(len(loaded.mappings), 1)
        finally:
            mod.UNDO_DIR = original_dir

    @patch('services.undo_service.UNDO_DIR')
    def test_get_latest_returns_newest(self, mock_dir):
        import services.undo_service as mod
        original_dir = mod.UNDO_DIR
        mod.UNDO_DIR = self.undo_dir
        try:
            r1 = self._make_record("20260101_100000")
            r2 = self._make_record("20260101_120000")
            self.undo_service.save_undo_record(r1)
            self.undo_service.save_undo_record(r2)
            latest = self.undo_service.get_latest_undo_record()
            self.assertEqual(latest.timestamp, "20260101_120000")
        finally:
            mod.UNDO_DIR = original_dir

    @patch('services.undo_service.UNDO_DIR')
    def test_get_latest_empty_dir(self, mock_dir):
        import services.undo_service as mod
        original_dir = mod.UNDO_DIR
        mod.UNDO_DIR = self.undo_dir
        try:
            result = self.undo_service.get_latest_undo_record()
            self.assertIsNone(result)
        finally:
            mod.UNDO_DIR = original_dir

    def test_execute_undo(self):
        old_path = os.path.join(self.temp_dir, "old.pdf")
        new_path = os.path.join(self.temp_dir, "new.pdf")
        with open(new_path, 'w') as f:
            f.write('dummy')
        record = UndoRecord(
            timestamp="20260101_120000",
            description="測試",
            mappings=[UndoMapping(original=old_path, renamed=new_path)],
        )
        import services.undo_service as mod
        original_dir = mod.UNDO_DIR
        mod.UNDO_DIR = self.undo_dir
        try:
            self.undo_service.save_undo_record(record)
            self.undo_service.execute_undo(record)
            self.assertTrue(os.path.isfile(old_path))
            self.assertFalse(os.path.isfile(new_path))
        finally:
            mod.UNDO_DIR = original_dir

    def test_execute_undo_cleans_empty_dirs(self):
        sub_dir = os.path.join(self.temp_dir, "SubFolder")
        os.makedirs(sub_dir)
        old_path = os.path.join(self.temp_dir, "old.pdf")
        new_path = os.path.join(sub_dir, "new.pdf")
        with open(new_path, 'w') as f:
            f.write('dummy')
        record = UndoRecord(
            timestamp="20260101_130000",
            description="測試子資料夾",
            mappings=[UndoMapping(original=old_path, renamed=new_path)],
            created_directories=[sub_dir],
        )
        import services.undo_service as mod
        original_dir = mod.UNDO_DIR
        mod.UNDO_DIR = self.undo_dir
        try:
            self.undo_service.save_undo_record(record)
            self.undo_service.execute_undo(record)
            self.assertTrue(os.path.isfile(old_path))
            self.assertFalse(os.path.isdir(sub_dir))
        finally:
            mod.UNDO_DIR = original_dir

    def test_execute_undo_reverses_a_swap(self):
        a = self._create("a.pdf", "B")
        b = self._create("b.pdf", "A")
        record = UndoRecord(
            timestamp="20260101_140000", description="對調",
            mappings=[UndoMapping(original=a, renamed=b), UndoMapping(original=b, renamed=a)],
        )
        with self._record_dirs():
            self.undo_service.save_undo_record(record)
            self.undo_service.execute_undo(record)
            self.assertIsNone(self.undo_service.get_latest_undo_record())
            self.assertIsNotNone(self.undo_service.get_latest_redo_record())
        self.assertEqual([self._read(p) for p in (a, b)], ["A", "B"])
        files = [n for n in os.listdir(self.temp_dir) if os.path.isfile(os.path.join(self.temp_dir, n))]
        self.assertEqual(sorted(files), ["a.pdf", "b.pdf"])

    def test_execute_redo_replays_a_swap(self):
        a = self._create("a.pdf", "A")
        b = self._create("b.pdf", "B")
        record = UndoRecord(
            timestamp="20260101_150000", description="對調",
            mappings=[UndoMapping(original=a, renamed=b), UndoMapping(original=b, renamed=a)],
        )
        with self._record_dirs():
            self.undo_service.execute_redo(record)
            self.assertIsNotNone(self.undo_service.get_latest_undo_record())
        self.assertEqual([self._read(p) for p in (a, b)], ["B", "A"])

    def test_execute_undo_refuses_when_original_spot_is_taken(self):
        old_path = os.path.join(self.temp_dir, "old.pdf")
        new_path = self._create("new.pdf", "N")
        self._create("old.pdf", "X")
        record = UndoRecord(
            timestamp="20260101_160000", description="測試",
            mappings=[UndoMapping(original=old_path, renamed=new_path)],
        )
        with self._record_dirs():
            self.undo_service.save_undo_record(record)
            with self.assertRaises(FileExistsError):
                self.undo_service.execute_undo(record)
            self.assertIsNotNone(self.undo_service.get_latest_undo_record())
        self.assertEqual(self._read(old_path), "X")
        self.assertEqual(self._read(new_path), "N")


if __name__ == '__main__':
    unittest.main()
