# -*- coding: utf-8 -*-
"""
使用者偏好服務

將語言與外觀模式等偏好持久化至 %APPDATA%/LingLingSuite/preferences.json。

使用範例：
    from services.preferences_service import PreferencesService
    prefs = PreferencesService()
    prefs.load()
    prefs.set("language", "en")
    prefs.save()
"""
import json
import os
from typing import Any, Dict
from core.constants import APPDATA_DIR

PREFERENCES_FILE = os.path.join(APPDATA_DIR, "preferences.json")

_DEFAULTS: Dict[str, Any] = {
    "language": "zh_TW",
    "appearance_mode": "Dark",
    "recent_projects": [],
    "catalog_spreadsheet_id": "",
    "catalog_root_folder_id": "",
}

MAX_RECENT = 8


class PreferencesService:
    """使用者偏好管理服務"""

    def __init__(self):
        self._data: Dict[str, Any] = dict(_DEFAULTS)

    def load(self):
        """從檔案載入偏好設定，檔案不存在或格式錯誤時使用預設值"""
        if not os.path.isfile(PREFERENCES_FILE):
            return
        try:
            with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                for key in _DEFAULTS:
                    if key in loaded:
                        self._data[key] = loaded[key]
        except (json.JSONDecodeError, OSError):
            pass

    def save(self):
        """將偏好設定寫入檔案"""
        os.makedirs(os.path.dirname(PREFERENCES_FILE), exist_ok=True)
        with open(PREFERENCES_FILE, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)

    def get(self, key: str) -> Any:
        """取得偏好值

        Args:
            key: 偏好鍵名

        Returns:
            偏好值，不存在時回傳 None
        """
        return self._data.get(key)

    def set(self, key: str, value: Any):
        """設定偏好值

        Args:
            key: 偏好鍵名
            value: 偏好值
        """
        self._data[key] = value

    def add_recent_project(self, path: str):
        """將路徑加入最近專案清單頂部，自動去重與截斷

        Args:
            path: 專案檔路徑
        """
        recent = self._data.get("recent_projects", [])
        path = os.path.normpath(path)
        recent = [p for p in recent if os.path.normpath(p) != path]
        recent.insert(0, path)
        self._data["recent_projects"] = recent[:MAX_RECENT]
