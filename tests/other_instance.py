# -*- coding: utf-8 -*-
"""
以子行程模擬執行中的第一個程式

子行程取得單一實例鎖後持有，直到測試讓它正常結束或強制結束（kill）。

使用範例：
    first = OtherInstance(lock_path)
    first.exit()   # 或 first.kill()
"""
import os
import subprocess
import sys

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src'))
PROCESS_TIMEOUT = 30

# 子行程：取得鎖後回報，等標準輸入關閉（正常結束）才釋放並結束
_SCRIPT = """
import sys
sys.path.insert(0, sys.argv[1])
from services.instance_lock import InstanceLock
lock = InstanceLock(sys.argv[2])
print("locked" if lock.acquire() else "busy", flush=True)
sys.stdin.read()
lock.release()
"""


class OtherInstance:
    """持有單一實例鎖的子行程"""

    def __init__(self, lock_path: str):
        self._process = subprocess.Popen(
            [sys.executable, "-c", _SCRIPT, SRC_DIR, lock_path],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        )
        reply = self._process.stdout.readline().strip()
        if reply != "locked":
            self.kill()
            raise AssertionError(f"子行程沒有取得鎖：{reply!r}")

    def exit(self):
        """讓子行程正常結束，等到它結束才返回"""
        self._process.stdin.close()
        self._process.wait(PROCESS_TIMEOUT)
        self._process.stdout.close()

    def kill(self):
        """強制結束子行程（不釋放鎖），等到它結束才返回"""
        self._process.kill()
        self._process.wait(PROCESS_TIMEOUT)
        for stream in (self._process.stdin, self._process.stdout):
            if not stream.closed:
                stream.close()
