# -*- coding: utf-8 -*-
"""
單一實例鎖測試

以子行程持有鎖來模擬第一個程式，驗證第二個程式能否取得鎖。
"""
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from services.instance_lock import InstanceLock
from tests.other_instance import OtherInstance

# 第一個程式被強制結束後，等待作業系統解除它的鎖的上限（秒）
RELEASE_GRACE = 5


class InstanceLockTest(unittest.TestCase):
    """第二個程式只在第一個程式執行中時取不到鎖"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.lock_path = os.path.join(self.temp_dir, "instance.lock")

    def _second(self) -> InstanceLock:
        lock = InstanceLock(self.lock_path)
        self.addCleanup(lock.release)
        return lock

    def _acquires_soon(self, lock: InstanceLock) -> bool:
        """在 RELEASE_GRACE 秒內反覆嘗試取得鎖

        被強制結束的行程來不及自己解鎖，由作業系統解除，而 Windows 文件不保證立即解除，所以給一段寬限；
        鎖若會一直留著（例如只靠檔案存在與否判斷），寬限過後仍取不到。
        """
        deadline = time.monotonic() + RELEASE_GRACE
        while not lock.acquire():
            if time.monotonic() > deadline:
                return False
            time.sleep(0.05)
        return True

    def test_cannot_acquire_while_first_instance_runs(self):
        first = OtherInstance(self.lock_path)
        self.addCleanup(first.kill)
        self.assertFalse(self._second().acquire())

    def test_acquires_after_first_instance_exits_normally(self):
        OtherInstance(self.lock_path).exit()
        self.assertTrue(self._second().acquire())

    def test_acquires_after_first_instance_is_killed(self):
        OtherInstance(self.lock_path).kill()
        self.assertTrue(self._acquires_soon(self._second()))


if __name__ == "__main__":
    unittest.main()
