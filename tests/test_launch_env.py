# -*- coding: utf-8 -*-
"""
啟動與平台設定單元測試

輸入環境變數、平台與是否為打包版，驗證建立 QApplication 前要補的環境變數、外部程式子行程的環境，
內附 Google 用戶端檔的位置，以及版號；這次執行是否為打包版在呼叫當下讀 sys。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.constants import REPO_ROOT
from core.launch_env import (
    Build, app_version, bundled_client_secrets_path, current_build, external_process_environment,
    qt_environment_defaults,
)


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


class TestBundledClientSecretsPath(unittest.TestCase):
    """bundled_client_secrets_path：打包版讀資源目錄，從原始碼執行讀 repo 根目錄"""

    def test_table(self):
        resource_dir = os.path.join("tmp", "_MEI12345")
        repo_root = os.path.join("home", "u", "ling-ling-suite")
        cases = [
            ("打包版讀資源目錄", True, os.path.join("tmp", "_MEI12345", "client_secret.json")),
            ("從原始碼執行讀 repo 根目錄", False, os.path.join("home", "u", "ling-ling-suite", "client_secret.json")),
        ]
        for name, frozen, expected in cases:
            with self.subTest(name):
                self.assertEqual(bundled_client_secrets_path(frozen, resource_dir, repo_root), expected)


class TestAppVersion(unittest.TestCase):
    """app_version：打包版讀資源目錄的版號檔，讀不到或從原始碼執行時是開發版（None）"""

    def test_table(self):
        resource_dir = os.path.join("tmp", "_MEI12345")
        version_file = os.path.join("tmp", "_MEI12345", "version.txt")
        cases = [
            ("打包版讀版號檔", True, {version_file: "v1.2.0\n"}, "v1.2.0"),
            ("打包版沒有版號檔是開發版", True, {}, None),
            ("打包版版號檔是空的是開發版", True, {version_file: " \n"}, None),
            ("從原始碼執行是開發版", False, {version_file: "v1.2.0\n"}, None),
        ]
        for name, frozen, files, expected in cases:
            with self.subTest(name):
                self.assertEqual(app_version(frozen, resource_dir, _reader(files)), expected)


class TestCurrentBuild(unittest.TestCase):
    """current_build：呼叫當下讀 sys.frozen 與 sys._MEIPASS；REPO_ROOT 是 src/ 的上層"""

    def test_source_run_is_not_frozen(self):
        self.assertEqual(current_build(), Build(False, ""))

    def test_packaged_build_reads_resource_dir(self):
        resource_dir = os.path.join("tmp", "_MEI12345")
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", resource_dir, create=True):
            self.assertEqual(current_build(), Build(True, resource_dir))

    def test_repo_root_is_the_folder_above_src(self):
        here = os.path.dirname(os.path.abspath(__file__))
        self.assertEqual(REPO_ROOT, os.path.dirname(here))


def _reader(files):
    """讀檔的替身：files 裡有的路徑回傳內容，沒有的像檔案不存在"""

    def read_text(path):
        if path not in files:
            raise FileNotFoundError(path)
        return files[path]
    return read_text


if __name__ == "__main__":
    unittest.main()
