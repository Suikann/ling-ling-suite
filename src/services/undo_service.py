# -*- coding: utf-8 -*-
"""
復原服務

提供操作復原紀錄的儲存、讀取與執行。
"""
import json
import os
from typing import Optional
from core.constants import UNDO_DIR
from core.models import UndoMapping, UndoRecord
from services.file_service import FileService


class UndoService:
    """復原操作管理服務"""

    def __init__(self, file_service: FileService):
        self.file_service = file_service

    def save_undo_record(self, record: UndoRecord) -> str:
        """儲存復原紀錄至檔案

        Args:
            record: 復原紀錄

        Returns:
            儲存的檔案路徑
        """
        os.makedirs(UNDO_DIR, exist_ok=True)
        filename = f"undo_{record.timestamp}.json"
        filepath = os.path.join(UNDO_DIR, filename)
        data = {
            "timestamp": record.timestamp,
            "description": record.description,
            "mappings": [
                {"original": m.original, "renamed": m.renamed}
                for m in record.mappings
            ],
            "created_directories": record.created_directories,
        }
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return filepath

    def get_latest_undo_record(self) -> Optional[UndoRecord]:
        """取得最近一次的復原紀錄

        Returns:
            復原紀錄，若無則回傳 None
        """
        if not os.path.isdir(UNDO_DIR):
            return None
        files = [
            f for f in os.listdir(UNDO_DIR)
            if f.startswith("undo_") and f.endswith(".json")
        ]
        if not files:
            return None
        files.sort(reverse=True)
        filepath = os.path.join(UNDO_DIR, files[0])
        return self._load_record(filepath)

    def _load_record(self, filepath: str) -> UndoRecord:
        """從檔案載入復原紀錄

        Args:
            filepath: JSON 檔案路徑

        Returns:
            復原紀錄
        """
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        record = UndoRecord(
            timestamp=data["timestamp"],
            description=data["description"],
            created_directories=data.get("created_directories", []),
        )
        for m in data["mappings"]:
            record.mappings.append(UndoMapping(
                original=m["original"],
                renamed=m["renamed"],
            ))
        return record

    def execute_undo(self, record: UndoRecord) -> None:
        """執行復原操作

        逆序重新命名檔案，然後清理空的子資料夾。

        Args:
            record: 復原紀錄
        """
        for mapping in reversed(record.mappings):
            if os.path.isfile(mapping.renamed):
                target_dir = os.path.dirname(mapping.original)
                if target_dir and not os.path.isdir(target_dir):
                    self.file_service.create_directory(target_dir)
                self.file_service.rename_file(mapping.renamed, mapping.original)
        for dir_path in reversed(sorted(record.created_directories)):
            self.file_service.remove_empty_directory(dir_path)
        self._remove_record_file(record)

    def _remove_record_file(self, record: UndoRecord) -> None:
        """移除已使用的復原紀錄檔案

        Args:
            record: 復原紀錄
        """
        filename = f"undo_{record.timestamp}.json"
        filepath = os.path.join(UNDO_DIR, filename)
        if os.path.isfile(filepath):
            os.remove(filepath)
