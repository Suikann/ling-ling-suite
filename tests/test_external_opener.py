# -*- coding: utf-8 -*-
"""
外部開啟單元測試

注入平台、環境變數與啟動外部程式的替身，驗證開啟網址或檔案時交給作業系統的指令與子行程環境。
"""
import os
import shutil
import sys
import tempfile
import time
import unittest
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from services.external_opener import ExternalOpener


class RecordingLauncher:
    """記下每次啟動的指令與環境；fail 為 True 時像找不到程式一樣拋出 OSError"""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.spawned: List[Tuple[List[str], Dict[str, str]]] = []
        self.started: List[str] = []

    def spawn(self, argv: List[str], env: Dict[str, str]) -> None:
        if self.fail:
            raise FileNotFoundError(argv[0])
        self.spawned.append((argv, env))

    def startfile(self, target: str) -> None:
        if self.fail:
            raise OSError("no association")
        self.started.append(target)


def make_opener(
    platform: str, environ: Optional[Dict[str, str]] = None, frozen: bool = True, fail: bool = False,
) -> Tuple[ExternalOpener, RecordingLauncher]:
    launcher = RecordingLauncher(fail)
    opener = ExternalOpener(
        platform=platform, environ=environ or {}, frozen=frozen,
        spawn=launcher.spawn, startfile=launcher.startfile,
    )
    return opener, launcher


class TestExternalOpener(unittest.TestCase):
    """ExternalOpener.open：網址與本機路徑都交給作業系統的預設程式"""

    def test_linux_opens_with_xdg_open_and_restored_library_path(self):
        environ = {"LD_LIBRARY_PATH": "/tmp/_MEI/_internal", "LD_LIBRARY_PATH_ORIG": "/opt/lib", "HOME": "/home/u"}
        opener, launcher = make_opener("linux", environ)
        self.assertTrue(opener.open("https://example.com/?a=1&b=2"))
        argv, env = launcher.spawned[0]
        self.assertEqual(argv, ["xdg-open", "https://example.com/?a=1&b=2"])
        self.assertEqual(env["LD_LIBRARY_PATH"], "/opt/lib")
        self.assertEqual(env["HOME"], "/home/u")

    def test_linux_opens_local_path(self):
        opener, launcher = make_opener("linux")
        self.assertTrue(opener.open("/home/u/譜/Flute 1.pdf"))
        self.assertEqual(launcher.spawned[0][0], ["xdg-open", "/home/u/譜/Flute 1.pdf"])

    def test_windows_opens_with_startfile(self):
        opener, launcher = make_opener("win32")
        self.assertTrue(opener.open(r"C:\Users\u\workspace"))
        self.assertEqual(launcher.started, [r"C:\Users\u\workspace"])
        self.assertEqual(launcher.spawned, [])

    def test_reports_failure_when_program_cannot_start(self):
        for platform in ("linux", "win32"):
            with self.subTest(platform):
                opener, _ = make_opener(platform, fail=True)
                self.assertFalse(opener.open("https://example.com/"))


@unittest.skipUnless(sys.platform.startswith("linux"), "xdg-open 只在 Linux 上使用")
class TestExternalOpenerLaunchesProgram(unittest.TestCase):
    """預設的啟動方式：真的執行 PATH 上的 xdg-open，網址原樣交給它、帶著還原後的環境"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.record = os.path.join(self.temp_dir, "record.txt")
        script = os.path.join(self.temp_dir, "xdg-open")
        with open(script, "w", encoding="utf-8") as f:
            f.write(f'#!/bin/sh\nprintf "%s\\n%s\\n" "$1" "${{LD_LIBRARY_PATH-unset}}" > "{self.record}.part"\n'
                    f'mv "{self.record}.part" "{self.record}"\n')
        os.chmod(script, 0o755)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _wait_for_record(self) -> List[str]:
        deadline = time.monotonic() + 5
        while not os.path.exists(self.record):
            if time.monotonic() > deadline:
                self.fail("xdg-open 沒有被執行")
            time.sleep(0.02)
        with open(self.record, encoding="utf-8") as f:
            return f.read().splitlines()

    def test_runs_xdg_open_with_restored_environment(self):
        environ = {"PATH": f"{self.temp_dir}:/usr/bin:/bin", "LD_LIBRARY_PATH": "/tmp/_MEI/_internal"}
        opener = ExternalOpener(platform="linux", environ=environ, frozen=True)
        self.assertTrue(opener.open("https://example.com/?a=1&b=$HOME"))
        self.assertEqual(self._wait_for_record(), ["https://example.com/?a=1&b=$HOME", "unset"])

    def test_reports_failure_when_xdg_open_is_missing(self):
        opener = ExternalOpener(platform="linux", environ={"PATH": os.path.join(self.temp_dir, "empty")}, frozen=True)
        self.assertFalse(opener.open("https://example.com/"))


if __name__ == "__main__":
    unittest.main()
