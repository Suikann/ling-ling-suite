# -*- coding: utf-8 -*-
"""
記憶體裡的 keyring backend

取代系統的安全儲存，注入給 SecureStorage：測試不會碰到真的 Windows 認證管理員或 Secret Service。
可設成這台電腦沒有安全儲存、讀寫時被鎖住、寫入或刪除時丟一般例外，並記下收到的每個呼叫。

使用範例：
    from tests.memory_keyring import MemoryKeyring
    keyring = MemoryKeyring()
    storage = SecureStorage("service", backend=keyring)
    keyring.locked = True  # 之後的讀寫都丟 KeyringLocked
"""
from typing import Dict, List, Optional, Tuple

from keyring.errors import KeyringLocked, PasswordDeleteError


class MemoryKeyring:
    """以字典保存密碼的 keyring backend 替身"""

    def __init__(self, viable: bool = True):
        self.viable = viable
        self.locked = False
        self.failing_writes = False
        self.failing_deletes = False
        self.passwords: Dict[Tuple[str, str], str] = {}
        self.calls: List[str] = []

    def get_password(self, service: str, username: str) -> Optional[str]:
        """讀出密碼；被鎖住時丟 KeyringLocked"""
        self.calls.append("get")
        if self.locked:
            raise KeyringLocked("Failed to unlock the collection!")
        return self.passwords.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        """寫入密碼；被鎖住時丟 KeyringLocked，設成寫入失敗時丟一般例外"""
        self.calls.append("set")
        if self.locked:
            raise KeyringLocked("Failed to unlock the collection!")
        if self.failing_writes:
            raise OSError("The stub received bad data")
        self.passwords[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        """刪除密碼；被鎖住或設成刪除失敗時丟例外"""
        self.calls.append("delete")
        if self.locked:
            raise KeyringLocked("Failed to unlock the collection!")
        if self.failing_deletes:
            raise OSError("delete failed")
        if (service, username) not in self.passwords:
            raise PasswordDeleteError("No such password!")
        del self.passwords[(service, username)]

    def stored(self) -> List[str]:
        """目前存著的所有密碼"""
        return list(self.passwords.values())
