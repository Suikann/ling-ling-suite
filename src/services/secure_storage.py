# -*- coding: utf-8 -*-
"""
系統的安全儲存

把一筆祕密（例如 Google 的續期權杖）存在系統的安全儲存：Windows 為認證管理員，Linux 為 Secret Service。
backend 依平台明確指定，不用 keyring 的自動選擇：環境裡的 keyrings.alt、PYTHON_KEYRING_BACKEND、
keyringrc.cfg 都會讓自動選擇改存明文或改用要在終端機輸入主密碼的 EncryptedKeyring。
所有 keyring 呼叫的例外都接住、當成沒存成，對外只回報 StorageStatus。

建立時不碰安全儲存，也不載入 keyring；第一次讀寫時才連線（Linux 上可能在這時跳出解鎖視窗）。

使用範例：
    from services.secure_storage import SecureStorage, StorageStatus
    storage = SecureStorage(GOOGLE_KEYRING_SERVICE)
    value, status = storage.read()
    if storage.write("secret") is not StorageStatus.AVAILABLE:
        ...
"""
import sys
from enum import Enum
from typing import Optional, Tuple

# keyring 在第一次讀寫時才匯入，建立 SecureStorage 不必等它載入
# 每個服務名稱底下只存一筆，帳號名稱固定
_ACCOUNT = "refresh_token"


class StorageStatus(Enum):
    """安全儲存的狀態"""

    AVAILABLE = "available"        # 讀寫成功
    NO_STORAGE = "no_storage"      # 這台電腦沒有安全儲存
    LOCKED = "locked"              # 沒有解鎖（解鎖視窗被取消或被鎖住）
    WRITE_FAILED = "write_failed"  # 有安全儲存，但寫不進去


def platform_backend(platform: str = sys.platform):
    """依平台明確指定的 keyring backend 類別：Windows 為 WinVaultKeyring，其餘為 SecretService.Keyring

    兩個 backend 模組都明確 import，打包後 keyring 的 entry point metadata 遺失也找得到。

    Args:
        platform: sys.platform 的值

    Returns:
        keyring backend 類別
    """
    from keyring.backends import SecretService, Windows
    if platform == "win32":
        return Windows.WinVaultKeyring
    return SecretService.Keyring


def _is_locked(error: Exception) -> bool:
    """keyring 的例外是不是「沒有解鎖」（解鎖視窗被取消或被鎖住）"""
    from keyring.errors import KeyringLocked
    return isinstance(error, KeyringLocked)


class SecureStorage:
    """系統安全儲存裡以服務名稱區分的一筆祕密"""

    def __init__(self, service: str, backend=None):
        """
        Args:
            service: 服務名稱（Google 與 HuaXia 各用一個，見 core/constants.py）
            backend: keyring backend 物件；省略時用 platform_backend() 指定的那個，第一次讀寫時才建立
        """
        self._service = service
        self._backend = backend

    def read(self) -> Tuple[Optional[str], StorageStatus]:
        """讀出存著的祕密

        Returns:
            （祕密，狀態）；沒存過時祕密為 None、狀態為 AVAILABLE，讀不出來時祕密為 None
        """
        backend = self._viable_backend()
        if backend is None:
            return None, StorageStatus.NO_STORAGE
        try:
            return backend.get_password(self._service, _ACCOUNT), StorageStatus.AVAILABLE
        except Exception as e:
            return None, StorageStatus.LOCKED if _is_locked(e) else StorageStatus.NO_STORAGE

    def write(self, value: str) -> StorageStatus:
        """寫入祕密，取代原本那筆

        Returns:
            寫入成功為 AVAILABLE，否則為沒存成的原因
        """
        backend = self._viable_backend()
        if backend is None:
            return StorageStatus.NO_STORAGE
        try:
            backend.set_password(self._service, _ACCOUNT, value)
        except Exception as e:
            return StorageStatus.LOCKED if _is_locked(e) else StorageStatus.WRITE_FAILED
        return StorageStatus.AVAILABLE

    def delete(self) -> None:
        """刪除存著的祕密；沒存過或刪不掉都不拋出"""
        backend = self._viable_backend()
        if backend is None:
            return
        try:
            backend.delete_password(self._service, _ACCOUNT)
        except Exception:
            pass

    def _viable_backend(self):
        """可用的 backend；這台電腦沒有安全儲存時為 None"""
        try:
            if self._backend is None:
                self._backend = platform_backend()()
            return self._backend if self._backend.viable else None
        except Exception:
            return None
