# -*- coding: utf-8 -*-
"""
寫不進去的檔案服務

模擬檔案被鎖住或位置唯讀：原子寫入到指定檔案時失敗，其餘照常。
注入給要測寫入失敗的服務，不必替換全域常數或私有成員。

使用範例：
    from failing_writes import FailingWrites
    file_service = FailingWrites(preferences_path)
    preferences = PreferencesService(preferences_path, file_service)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.paths import path_key
from services.file_service import FileService


class FailingWrites(FileService):
    """原子寫入到指定檔案時拋出 OSError 的檔案服務"""

    def __init__(self, *blocked: str):
        super().__init__()
        self._blocked = {path_key(p) for p in blocked}

    def block(self, path: str) -> None:
        """之後寫入 path 一律失敗"""
        self._blocked.add(path_key(path))

    def write_json_atomic(self, path, data) -> None:
        """寫入被擋下的檔案時拋出 OSError，其餘照常原子寫入"""
        if path_key(path) in self._blocked:
            raise OSError(f"read-only: {path}")
        super().write_json_atomic(path, data)
