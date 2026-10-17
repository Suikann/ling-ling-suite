# -*- coding: utf-8 -*-
"""
使用者偏好服務測試（臨時目錄）

偏好設定可以從背景執行緒存（登入在背景記下帳號 email）：與主執行緒同時存時，兩邊的改動都留在檔案裡。
"""
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.constants import GOOGLE_ACCOUNT_EMAIL_KEY
from services.file_service import FileService
from services.preferences_service import PreferencesService


class PausingFirstWrite(FileService):
    """第一次寫入文字檔時，在內容已序列化、還沒寫進磁碟前停下，等測試放行"""

    def __init__(self):
        super().__init__()
        self.writing = threading.Event()
        self.release = threading.Event()
        self._paused = False

    def write_text_atomic(self, path: str, text: str) -> None:
        """第一次呼叫時通知 writing、等 release 再寫入；之後照常寫入"""
        if not self._paused:
            self._paused = True
            self.writing.set()
            self.release.wait(5)
        super().write_text_atomic(path, text)


class TestPreferencesAcrossThreads(unittest.TestCase):
    """背景執行緒與主執行緒同時改偏好設定並存檔"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.path = os.path.join(self.temp_dir, "preferences.json")

    def test_change_saved_while_a_background_save_is_writing_is_not_lost(self):
        files = PausingFirstWrite()
        preferences = PreferencesService(self.path, files)

        def remember_email():
            preferences.set(GOOGLE_ACCOUNT_EMAIL_KEY, "library@example.com")
            preferences.save()

        def switch_language():
            preferences.set("language", "en")
            preferences.save()
        background = threading.Thread(target=remember_email)
        background.start()
        self.assertTrue(files.writing.wait(5))
        foreground = threading.Thread(target=switch_language)
        foreground.start()
        foreground.join(0.2)
        files.release.set()
        background.join(5)
        foreground.join(5)
        saved = PreferencesService(self.path)
        saved.load()
        self.assertEqual(saved.get(GOOGLE_ACCOUNT_EMAIL_KEY), "library@example.com")
        self.assertEqual(saved.get("language"), "en")


if __name__ == "__main__":
    unittest.main()
