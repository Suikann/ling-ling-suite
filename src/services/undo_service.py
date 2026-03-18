# -*- coding: utf-8 -*-
"""
復原/重做服務

提供操作復原紀錄的儲存、讀取與執行，支援重新命名、分割、旋轉三種操作類型。
"""
import json
import os
import shutil
from typing import Optional
from core.constants import UNDO_DIR, REDO_DIR, BACKUP_DIR
from core.models import UndoMapping, UndoRecord
from services.file_service import FileService


class UndoService:
    """復原/重做操作管理服務"""

    def __init__(self, file_service: FileService):
        self.file_service = file_service

    # --- 儲存與讀取 ---

    def save_undo_record(self, record: UndoRecord) -> str:
        """儲存復原紀錄並清除重做堆疊"""
        os.makedirs(UNDO_DIR, exist_ok=True)
        filepath = os.path.join(UNDO_DIR, f"undo_{record.timestamp}.json")
        self._write_record(filepath, record)
        self.clear_redo_stack()
        return filepath

    def get_latest_undo_record(self) -> Optional[UndoRecord]:
        """取得最近一次的復原紀錄"""
        return self._get_latest_record(UNDO_DIR, "undo_")

    def get_latest_redo_record(self) -> Optional[UndoRecord]:
        """取得最近一次的重做紀錄"""
        return self._get_latest_record(REDO_DIR, "redo_")

    def clear_redo_stack(self):
        """清除所有重做紀錄"""
        if not os.path.isdir(REDO_DIR):
            return
        for f in os.listdir(REDO_DIR):
            if f.startswith("redo_") and f.endswith(".json"):
                os.remove(os.path.join(REDO_DIR, f))

    # --- 執行復原 ---

    def execute_undo(self, record: UndoRecord) -> None:
        """執行復原操作"""
        op = record.operation_type
        if op == "rename":
            for mapping in reversed(record.mappings):
                if os.path.isfile(mapping.renamed):
                    target_dir = os.path.dirname(mapping.original)
                    if target_dir and not os.path.isdir(target_dir):
                        self.file_service.create_directory(target_dir)
                    self.file_service.rename_file(mapping.renamed, mapping.original)
            for dir_path in reversed(sorted(record.created_directories)):
                self.file_service.remove_empty_directory(dir_path)
        elif op == "split":
            for path in record.created_files:
                if os.path.isfile(path):
                    self.file_service.delete_file(path)
            for dir_path in reversed(sorted(record.created_directories)):
                self.file_service.remove_empty_directory(dir_path)
        elif op == "rotate":
            if record.backup_path and os.path.isfile(record.backup_path):
                shutil.copy2(record.backup_path, record.original_path)
                os.remove(record.backup_path)
        self._move_to_redo(record)

    # --- 執行重做 ---

    def execute_redo(self, record: UndoRecord) -> None:
        """執行重做操作（僅支援重新命名）"""
        if record.operation_type == "rename":
            created_dirs = set()
            for mapping in record.mappings:
                target_dir = os.path.dirname(mapping.renamed)
                if target_dir and not os.path.isdir(target_dir):
                    self.file_service.create_directory(target_dir)
                    created_dirs.add(target_dir)
                self.file_service.rename_file(mapping.original, mapping.renamed)
            record.created_directories = sorted(created_dirs)
        self._move_to_undo(record)

    # --- 備份 ---

    @staticmethod
    def create_backup(file_path: str) -> str:
        """建立檔案備份供旋轉復原使用

        Args:
            file_path: 要備份的檔案路徑

        Returns:
            備份檔案路徑
        """
        os.makedirs(BACKUP_DIR, exist_ok=True)
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        name = os.path.basename(file_path)
        backup = os.path.join(BACKUP_DIR, f"{ts}_{name}")
        shutil.copy2(file_path, backup)
        return backup

    # --- 內部方法 ---

    def _get_latest_record(self, directory, prefix) -> Optional[UndoRecord]:
        if not os.path.isdir(directory):
            return None
        files = [
            f for f in os.listdir(directory)
            if f.startswith(prefix) and f.endswith(".json")
        ]
        if not files:
            return None
        files.sort(reverse=True)
        return self._load_record(os.path.join(directory, files[0]))

    def _load_record(self, filepath: str) -> UndoRecord:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        record = UndoRecord(
            timestamp=data["timestamp"],
            description=data["description"],
            operation_type=data.get("operation_type", "rename"),
            created_directories=data.get("created_directories", []),
            created_files=data.get("created_files", []),
            backup_path=data.get("backup_path", ""),
            original_path=data.get("original_path", ""),
        )
        for m in data.get("mappings", []):
            record.mappings.append(UndoMapping(
                original=m["original"],
                renamed=m["renamed"],
            ))
        return record

    def _write_record(self, filepath: str, record: UndoRecord):
        data = {
            "timestamp": record.timestamp,
            "description": record.description,
            "operation_type": record.operation_type,
            "mappings": [
                {"original": m.original, "renamed": m.renamed}
                for m in record.mappings
            ],
            "created_directories": record.created_directories,
            "created_files": record.created_files,
            "backup_path": record.backup_path,
            "original_path": record.original_path,
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _move_to_redo(self, record: UndoRecord):
        os.makedirs(REDO_DIR, exist_ok=True)
        redo_path = os.path.join(REDO_DIR, f"redo_{record.timestamp}.json")
        self._write_record(redo_path, record)
        undo_path = os.path.join(UNDO_DIR, f"undo_{record.timestamp}.json")
        if os.path.isfile(undo_path):
            os.remove(undo_path)

    def _move_to_undo(self, record: UndoRecord):
        os.makedirs(UNDO_DIR, exist_ok=True)
        undo_path = os.path.join(UNDO_DIR, f"undo_{record.timestamp}.json")
        self._write_record(undo_path, record)
        redo_path = os.path.join(REDO_DIR, f"redo_{record.timestamp}.json")
        if os.path.isfile(redo_path):
            os.remove(redo_path)
