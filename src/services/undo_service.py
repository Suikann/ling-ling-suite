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
from services.move_service import MoveService
from services.workspace_service import WorkspaceService


class UndoService:
    """復原/重做操作管理服務"""

    def __init__(self, file_service: FileService, workspace_service: WorkspaceService):
        self.file_service = file_service
        self.workspace_service = workspace_service
        self._mover = MoveService(file_service)

    # --- 儲存與讀取 ---

    def save_undo_record(self, record: UndoRecord) -> str:
        """儲存復原紀錄並清除重做堆疊

        重新命名紀錄中來源位於工作區的項目，順便快照其子資料夾的 meta.json：
        搬空的子資料夾會被清理掃描刪掉，復原時要靠快照把來源資訊寫回。
        """
        if record.operation_type == "rename":
            self._snapshot_workspace_meta(record)
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
        """執行復原操作

        重新命名紀錄交給搬移引擎整批搬回（對調與連鎖的紀錄也能復原；
        中途失敗整批回滾），已不在新位置的檔案略過；紀錄在引擎刪除進行中紀錄前轉入重做堆疊。

        Args:
            record: 要復原的紀錄

        Raises:
            RenameRollbackError: 搬回途中失敗且回滾時有檔案搬不回去
        """
        op = record.operation_type
        if op == "rename":
            self._mover.execute(
                [
                    (m.renamed, m.original) for m in record.mappings
                    if self.file_service.file_exists(m.renamed)
                ],
                on_complete=lambda _: self._move_to_redo(record),
            )
            for dir_path in reversed(sorted(record.created_directories)):
                self.file_service.remove_empty_directory(dir_path)
            self._restore_workspace_meta(record)
            return
        if op == "split":
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
        """執行重做操作（僅支援重新命名）

        交給搬移引擎整批重放，中途失敗整批回滾；紀錄在引擎刪除進行中紀錄前轉回復原堆疊。

        Args:
            record: 要重做的紀錄

        Raises:
            RenameRollbackError: 重放途中失敗且回滾時有檔案搬不回去
        """
        if record.operation_type != "rename":
            self._move_to_undo(record)
            return

        def complete(created_dirs) -> None:
            record.created_directories = created_dirs
            self._move_to_undo(record)

        self._mover.execute([(m.original, m.renamed) for m in record.mappings], on_complete=complete)

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

    def _snapshot_workspace_meta(self, record: UndoRecord) -> None:
        """把來源位於工作區的項目其子資料夾的 meta.json 快照進紀錄"""
        for mapping in record.mappings:
            folder = self.workspace_service.folder_of(mapping.original)
            if folder is None or folder in record.workspace_meta:
                continue
            meta = self.workspace_service.read_meta(folder)
            if meta is not None:
                record.workspace_meta[folder] = meta

    def _restore_workspace_meta(self, record: UndoRecord) -> None:
        """檔案搬回後，用快照補回被清掉的 meta.json；寫回失敗不影響已完成的檔案復原"""
        for folder, meta in record.workspace_meta.items():
            try:
                self.workspace_service.restore_meta(folder, meta)
            except OSError:
                continue

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
            workspace_meta=data.get("workspace_meta", {}),
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
            "workspace_meta": record.workspace_meta,
        }
        self.file_service.write_json_atomic(filepath, data)

    def _move_to_redo(self, record: UndoRecord):
        redo_path = os.path.join(REDO_DIR, f"redo_{record.timestamp}.json")
        self._write_record(redo_path, record)
        undo_path = os.path.join(UNDO_DIR, f"undo_{record.timestamp}.json")
        if os.path.isfile(undo_path):
            os.remove(undo_path)

    def _move_to_undo(self, record: UndoRecord):
        undo_path = os.path.join(UNDO_DIR, f"undo_{record.timestamp}.json")
        self._write_record(undo_path, record)
        redo_path = os.path.join(REDO_DIR, f"redo_{record.timestamp}.json")
        if os.path.isfile(redo_path):
            os.remove(redo_path)
