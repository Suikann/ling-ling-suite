# -*- coding: utf-8 -*-
"""
使用者偏好服務

將語言、外觀模式、最近專案清單等偏好持久化成一個 JSON 檔；檔案位置在建構時注入，
預設值見 core.constants.DEFAULT_PREFERENCES。最近專案清單的規則由專案存取維護，這裡只負責存放。
可以從背景執行緒呼叫（登入在背景記下帳號 email）：讀、改、存都經同一把鎖，存檔寫完前別的執行緒改不了，
後存的一定帶著先改的。

使用範例：
    from core.constants import PREFERENCES_FILE
    from services.preferences_service import PreferencesService
    prefs = PreferencesService(PREFERENCES_FILE)
    prefs.load()
    prefs.set("language", "en")
    prefs.save()
"""
import copy
import threading
from typing import Any, Dict, Optional
from core.constants import DEFAULT_PREFERENCES
from services.file_service import FileService


class PreferencesService:
    """使用者偏好管理服務"""

    def __init__(self, path: str, file_service: Optional[FileService] = None):
        """
        Args:
            path: 偏好設定檔的位置
            file_service: 檔案服務
        """
        self.path = path
        self.file_service = file_service or FileService()
        self._data: Dict[str, Any] = copy.deepcopy(DEFAULT_PREFERENCES)
        self._lock = threading.RLock()

    def load(self):
        """從檔案載入偏好設定；檔案不存在或格式錯誤時保留預設值，不認得的鍵略過"""
        try:
            loaded = self.file_service.read_json(self.path)
        except (OSError, ValueError):
            return
        if isinstance(loaded, dict):
            with self._lock:
                for key in DEFAULT_PREFERENCES:
                    if key in loaded:
                        self._data[key] = loaded[key]

    def save(self):
        """將偏好設定原子寫入檔案；寫不進去時拋出 OSError"""
        with self._lock:
            self.file_service.write_json_atomic(self.path, self._data)

    def get(self, key: str) -> Any:
        """取得偏好值

        Args:
            key: 偏好鍵名

        Returns:
            偏好值，不存在時回傳 None
        """
        with self._lock:
            return self._data.get(key)

    def set(self, key: str, value: Any):
        """設定偏好值（只改記憶體，save 才寫入檔案）

        Args:
            key: 偏好鍵名
            value: 偏好值
        """
        with self._lock:
            self._data[key] = value
