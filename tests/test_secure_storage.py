# -*- coding: utf-8 -*-
"""
安全儲存測試

backend 依平台明確指定、不走 keyring 的自動選擇：環境設了 PYTHON_KEYRING_BACKEND 也照樣用指定的那個。
只檢查選到哪個 backend 類別、以記憶體裡的 backend 替身讀寫，不建立、不呼叫系統的安全儲存；
環境變數指向另一個替身，並確認 keyring 自己的選擇確實會照它走。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import keyring.core
from keyring.backends import SecretService, Windows

from services.secure_storage import SecureStorage, StorageStatus, platform_backend
from tests.memory_keyring import MemoryKeyring

# keyring 自動選擇時會照這個環境變數載入的 backend：記憶體裡的替身，看得出有沒有被用到
_ENV_BACKEND = "tests.test_secure_storage.EnvironmentKeyring"


class EnvironmentKeyring(MemoryKeyring):
    """PYTHON_KEYRING_BACKEND 指向的 backend；所有實例共用一份記錄"""

    priority = 1
    shared_calls = []

    def __init__(self):
        super().__init__()
        self.calls = EnvironmentKeyring.shared_calls


class TestExplicitBackend(unittest.TestCase):
    """環境裡的 keyring 設定不影響安全儲存用哪個 backend"""

    def setUp(self):
        EnvironmentKeyring.shared_calls.clear()
        patcher = mock.patch.dict(os.environ, {"PYTHON_KEYRING_BACKEND": _ENV_BACKEND})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_platform_backend_ignores_keyring_environment(self):
        self.assertIsInstance(keyring.core.load_env(), EnvironmentKeyring)
        self.assertIs(platform_backend("win32"), Windows.WinVaultKeyring)
        self.assertIs(platform_backend("linux"), SecretService.Keyring)

    def test_reads_and_writes_go_to_the_given_backend_only(self):
        given = MemoryKeyring()
        storage = SecureStorage("test-service", backend=given)
        self.assertIs(storage.write("secret"), StorageStatus.AVAILABLE)
        self.assertEqual(storage.read(), ("secret", StorageStatus.AVAILABLE))
        storage.delete()
        self.assertEqual(given.calls, ["set", "get", "delete"])
        self.assertEqual(EnvironmentKeyring.shared_calls, [])


if __name__ == "__main__":
    unittest.main()
