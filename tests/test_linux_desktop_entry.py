# -*- coding: utf-8 -*-
"""
AppImage 所附 .desktop 檔的單元測試

驗證 packaging/linux/lingling-suite.desktop 與程式啟動時設的識別名一致：檔名、Icon、StartupWMClass
都是 APP_ID，視窗管理器才對得起捷徑與執行中的視窗；顯示名稱中英各一。
"""
import configparser
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.constants import APP_DISPLAY_NAME, APP_ID, REPO_ROOT

DESKTOP_FILE = os.path.join(REPO_ROOT, "packaging", "linux", f"{APP_ID}.desktop")


def _read_entry() -> configparser.SectionProxy:
    """讀出 .desktop 的 [Desktop Entry]，鍵保留大小寫"""
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    with open(DESKTOP_FILE, encoding="utf-8") as desktop:
        parser.read_file(desktop)
    return parser["Desktop Entry"]


class TestLinuxDesktopEntry(unittest.TestCase):
    """packaging/linux 的 .desktop：識別名與 APP_ID 一致"""

    def test_identity_matches_app_id(self):
        entry = _read_entry()
        self.assertEqual(entry["StartupWMClass"], APP_ID)
        self.assertEqual(entry["Icon"], APP_ID)

    def test_names(self):
        entry = _read_entry()
        self.assertEqual(entry["Name"], "Ling Ling Suite")
        self.assertEqual(entry["Name[zh_TW]"], APP_DISPLAY_NAME)

    def test_launches_bundled_program(self):
        entry = _read_entry()
        self.assertEqual(entry["Type"], "Application")
        self.assertEqual(entry["Exec"], "LingLingSuite")


if __name__ == '__main__':
    unittest.main()
