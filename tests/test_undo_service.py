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
from services.workspace_service import WorkspaceService


class TestUndoService(unittest.TestCase):
    """UndoService 測試"""

    def setUp(self):
        self.file_service = FileService()
        self.temp_dir = tempfile.mkdtemp()
        self.workspace = WorkspaceService(self.file_service, os.path.join(self.temp_dir, "workspace"))
        self.undo_service = UndoService(self.file_service, self.workspace)
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

    # --- 工作區 meta 快照 ---

    def _rename_out_of_workspace(self, project_path="/proj/a.llproj"):
        """把一份分譜從工作區搬到輸出位置，回傳（子資料夾、復原紀錄）"""
        source = self._create("合併譜.pdf")
        folder = self.workspace.prepare_folder(source, project_path)
        part = os.path.join(folder, "高笙.pdf")
        with open(part, "w") as f:
            f.write("part")
        renamed = os.path.join(self.temp_dir, "01-高笙.pdf")
        os.rename(part, renamed)
        record = UndoRecord(
            timestamp="20260101_170000", description="重新命名",
            mappings=[UndoMapping(original=part, renamed=renamed)],
        )
        return folder, record

    def test_save_snapshots_meta_of_workspace_sources(self):
        folder, record = self._rename_out_of_workspace()
        expected = self.workspace.read_meta(folder)
        with self._record_dirs():
            self.undo_service.save_undo_record(record)
            loaded = self.undo_service.get_latest_undo_record()
        self.assertEqual(record.workspace_meta, {folder: expected})
        self.assertEqual(loaded.workspace_meta, {folder: expected})

    def test_save_does_not_snapshot_sources_outside_workspace(self):
        with self._record_dirs():
            self.undo_service.save_undo_record(self._make_record())
            loaded = self.undo_service.get_latest_undo_record()
        self.assertEqual(loaded.workspace_meta, {})

    def test_undo_restores_meta_after_workspace_cleanup(self):
        folder, record = self._rename_out_of_workspace()
        with self._record_dirs():
            self.undo_service.save_undo_record(record)
            # 清理掃描把搬空的子資料夾連 meta 一起刪掉
            self.workspace.purge_empty_folders()
            self.assertFalse(os.path.exists(folder))
            self.undo_service.execute_undo(record)
        self.assertTrue(os.path.isfile(record.mappings[0].original))
        self.assertEqual(self.workspace.read_meta(folder), record.workspace_meta[folder])

    def test_undo_keeps_meta_written_in_the_meantime(self):
        folder, record = self._rename_out_of_workspace()
        with self._record_dirs():
            self.undo_service.save_undo_record(record)
            self.workspace.prepare_folder(self._create("合併譜.pdf"), "/proj/other.llproj")
            self.undo_service.execute_undo(record)
        self.assertEqual(self.workspace.read_meta(folder)["project_path"], "/proj/other.llproj")

    def test_undo_still_moves_files_when_meta_cannot_be_written(self):
        folder, record = self._rename_out_of_workspace()
        with self._record_dirs():
            self.undo_service.save_undo_record(record)
            self.workspace.purge_empty_folders()
            real_write = self.file_service.write_json_atomic

            def failing_write(path, data):
                if os.path.basename(path) == "meta.json":
                    raise OSError("locked")
                real_write(path, data)

            self.file_service.write_json_atomic = failing_write
            self.undo_service.execute_undo(record)
            self.assertIsNotNone(self.undo_service.get_latest_redo_record())
        self.assertTrue(os.path.isfile(record.mappings[0].original))
        self.assertIsNone(self.workspace.read_meta(folder))

    def test_record_without_workspace_meta_field_still_loads(self):
        import json
        os.makedirs(self.undo_dir)
        with open(os.path.join(self.undo_dir, "undo_20260101_180000.json"), "w", encoding="utf-8") as f:
            json.dump({
                "timestamp": "20260101_180000", "description": "舊版紀錄", "operation_type": "rename",
                "mappings": [{"original": "/a.pdf", "renamed": "/b.pdf"}],
            }, f)
        with self._record_dirs():
            loaded = self.undo_service.get_latest_undo_record()
        self.assertEqual(loaded.workspace_meta, {})
        self.assertEqual(loaded.mappings[0].renamed, "/b.pdf")


if __name__ == '__main__':
    unittest.main()
