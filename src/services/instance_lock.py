# -*- coding: utf-8 -*-
"""
單一實例鎖

以作業系統的檔案鎖確保同一時間只有一個程式執行：Windows 用 msvcrt.locking，
其他平台用 fcntl.flock。鎖跟著開啟的檔案走，程式結束（含當機、強制結束）時由作業系統解除，
所以不會留下擋住下一次啟動的鎖；鎖檔本身留著無妨，不刪除。

使用範例：
    from services.instance_lock import InstanceLock
    lock = InstanceLock(INSTANCE_LOCK_FILE)
    if not lock.acquire():
        ...  # 已有另一個程式在執行
    lock.release()
"""
import os
from typing import Optional

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class InstanceLock:
    """單一實例鎖"""

    def __init__(self, lock_path: str):
        self.lock_path = lock_path
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        """嘗試取得鎖，不等待

        Returns:
            是否取得；已被另一個程式持有時為 False

        Raises:
            OSError: 無法建立或開啟鎖檔
        """
        if self._fd is not None:
            return True
        os.makedirs(os.path.dirname(self.lock_path) or ".", exist_ok=True)
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT)
        try:
            _lock(fd)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self):
        """釋放鎖；未持有時不做任何事"""
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            _unlock(fd)
        finally:
            os.close(fd)


def _lock(fd: int):
    """以不等待的方式鎖住檔案；已被鎖住時拋出 OSError"""
    if os.name == "nt":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int):
    """解除 _lock 加上的鎖"""
    if os.name == "nt":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
