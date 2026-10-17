# -*- coding: utf-8 -*-
"""
啟動與平台設定單元測試

輸入環境變數、平台與是否為打包版，驗證建立 QApplication 前要補的環境變數，以及外部程式子行程的環境。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.launch_env import external_process_environment, qt_environment_defaults


class TestQtEnvironmentDefaults(unittest.TestCase):
    """qt_environment_defaults：Linux 上 QT_IM_MODULES 沒設時補上，設了不動"""

    def test_table(self):
        cases = [
            ("Linux 沒設時補上", {}, "linux", {"QT_IM_MODULES": "wayland;fcitx;ibus"}),
            (
                "只設了 QT_IM_MODULE=fcitx 也補上",
                {"QT_IM_MODULE": "fcitx"}, "linux", {"QT_IM_MODULES": "wayland;fcitx;ibus"},
            ),
            ("使用者自己設了不動", {"QT_IM_MODULES": "ibus"}, "linux", {}),
            ("Windows 不補", {}, "win32", {}),
        ]
        for name, environ, platform, expected in cases:
            with self.subTest(name):
                self.assertEqual(qt_environment_defaults(environ, platform), expected)


class TestExternalProcessEnvironment(unittest.TestCase):
    """external_process_environment：Linux 打包版替外部程式還原 LD_LIBRARY_PATH"""

    def test_table(self):
        bundled = {"LD_LIBRARY_PATH": "/tmp/_MEI/_internal", "LD_LIBRARY_PATH_ORIG": "/opt/lib", "HOME": "/home/u"}
        unset_before = {"LD_LIBRARY_PATH": "/tmp/_MEI/_internal", "HOME": "/home/u"}
        cases = [
            (
                "有 _ORIG 時還原", bundled, "linux", True,
                {"LD_LIBRARY_PATH": "/opt/lib", "LD_LIBRARY_PATH_ORIG": "/opt/lib", "HOME": "/home/u"},
            ),
            ("沒有 _ORIG 時移除", unset_before, "linux", True, {"HOME": "/home/u"}),
            ("從原始碼執行不動", unset_before, "linux", False, unset_before),
            ("Windows 不動", bundled, "win32", True, bundled),
        ]
        for name, environ, platform, frozen, expected in cases:
            with self.subTest(name):
                self.assertEqual(external_process_environment(environ, platform, frozen), expected)

    def test_leaves_given_environment_untouched(self):
        environ = {"LD_LIBRARY_PATH": "/tmp/_MEI/_internal"}
        external_process_environment(environ, "linux", True)
        self.assertEqual(environ, {"LD_LIBRARY_PATH": "/tmp/_MEI/_internal"})


if __name__ == "__main__":
    unittest.main()
