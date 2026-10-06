# -*- coding: utf-8 -*-
"""
使用者偏好服務

將語言、外觀模式、最近專案清單等偏好持久化成一個 JSON 檔；檔案位置在建構時注入，
預設值見 core.constants.DEFAULT_PREFERENCES。最近專案清單的規則由專案存取維護，這裡只負責存放。

使用範例：
    from core.constants import PREFERENCES_FILE
    from services.preferences_service import PreferencesService
    prefs = PreferencesService(PREFERENCES_FILE)
    prefs.load()
    prefs.set("language", "en")
    prefs.save()
"""
import copy
import json
from typing import Any, Dict, Optional
from core.constants import DEFAULT_PREFERENCES
from services.file_service import FileService


class PreferencesService:
    """使用者偏好管理服務"""

    def __init__(self, path: str, file_service: Optional[FileService] = None):
        self.path = path
        self.file_service = file_service or FileService()
        self._data: Dict[str, Any] = copy.deepcopy(DEFAULT_PREFERENCES)

    def load(self):
        """從檔案載入偏好設定；檔案不存在或格式錯誤時保留預設值，不認得的鍵略過"""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
        except (OSError, ValueError):
            return
        if isinstance(loaded, dict):
            for key in DEFAULT_PREFERENCES:
                if key in loaded:
                    self._data[key] = loaded[key]

    def save(self):
        """將偏好設定原子寫入檔案；寫不進去時拋出 OSError"""
        self.file_service.write_json_atomic(self.path, self._data)

    def get(self, key: str) -> Any:
        """取得偏好值

        Args:
            key: 偏好鍵名

        Returns:
            偏好值，不存在時回傳 None
        """
        return self._data.get(key)

    def set(self, key: str, value: Any):
        """設定偏好值（只改記憶體，save 才寫入檔案）

        Args:
            key: 偏好鍵名
            value: 偏好值
        """
        self._data[key] = value
