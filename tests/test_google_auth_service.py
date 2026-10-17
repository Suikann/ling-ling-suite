# -*- coding: utf-8 -*-
"""
Google 登入服務測試

從登入服務的公開介面（登入、取得憑證、登出、目前狀態與原因、啟動清理）驗證。
安全儲存換成記憶體裡的 keyring backend，Google 權杖端點與瀏覽器登入換成替身，不連網、不碰系統的安全儲存。
「重開泠靈」以同一個 keyring 替身建立新的登入服務來模擬。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from services.google_auth_service import GoogleAuthService, LoginReason, LoginStatus
from services.file_service import FileService
from services.secure_storage import SecureStorage
from tests.fake_google import (
    CLIENT_ID, CLIENT_SECRET, FakeBrowserLogin, FakeTokenEndpoint, write_client_secrets,
)
from tests.memory_keyring import MemoryKeyring


class GoogleAuthTestCase(unittest.TestCase):
    """登入服務測試的共用骨架：臨時目錄裡的用戶端檔與舊權杖檔位置、keyring 與權杖端點替身"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.client_secrets = write_client_secrets(os.path.join(self.temp_dir, "client_secret.json"))
        self.legacy_token = os.path.join(self.temp_dir, "google_token.json")
        self.keyring = MemoryKeyring()
        self.endpoint = FakeTokenEndpoint()
        self.browser = FakeBrowserLogin("refresh-1")

    def service(self) -> GoogleAuthService:
        """建立一個登入服務；再呼叫一次就像重開泠靈（安全儲存是同一份）"""
        return GoogleAuthService(
            storage=SecureStorage("test-google", backend=self.keyring),
            request=self.endpoint,
            browser_login=self.browser,
            client_secrets_path=self.client_secrets,
            legacy_token_path=self.legacy_token,
        )


class TestRestoringLogin(GoogleAuthTestCase):
    """重開泠靈後，以安全儲存裡的續期權杖重建登入"""

    def test_saved_login_is_refreshed_after_restart(self):
        self.service().authenticate()
        self.endpoint.grant("access-2")
        later = self.service()
        creds = later.get_credentials()
        self.assertIsNotNone(creds)
        self.assertEqual(creds.token, "access-2")
        self.assertEqual(self.endpoint.refreshed_with(), ["refresh-1"])
        self.assertTrue(later.status().signed_in)

    def test_new_refresh_token_from_google_replaces_the_saved_one(self):
        self.service().authenticate()
        self.endpoint.grant("access-2", refresh_token="refresh-2")
        self.service().get_credentials()
        self.assertEqual(self.keyring.stored(), ["refresh-2"])
        self.endpoint.grant("access-3")
        self.service().get_credentials()
        self.assertEqual(self.endpoint.refreshed_with(), ["refresh-1", "refresh-2"])

    def test_rejected_refresh_reports_google_no_longer_accepts_the_token(self):
        self.service().authenticate()
        self.endpoint.reject()
        later = self.service()
        self.assertIsNone(later.get_credentials())
        self.assertEqual(later.status(), LoginStatus(False, LoginReason.TOKEN_REJECTED))

    def test_unreachable_google_is_told_apart_from_a_rejected_token(self):
        self.service().authenticate()
        self.endpoint.go_offline()
        later = self.service()
        self.assertIsNone(later.get_credentials())
        self.assertEqual(later.status(), LoginStatus(False, LoginReason.UNREACHABLE))
        self.endpoint.grant("access-2")
        self.assertEqual(later.status(), LoginStatus(True))

    def test_new_refresh_token_that_cannot_be_saved_is_announced(self):
        self.service().authenticate()
        self.endpoint.grant("access-2", refresh_token="refresh-2")
        self.keyring.failing_writes = True
        later = self.service()
        announced = []
        later.subscribe_not_saved(lambda: announced.append(True))
        self.assertIsNotNone(later.get_credentials())
        self.assertEqual(announced, [True])
        self.assertEqual(later.status(), LoginStatus(True, LoginReason.NOT_SAVED))


class TestRememberingLogin(GoogleAuthTestCase):
    """安全儲存不可用、被鎖或寫不進去時，各自回報對的原因"""

    def test_without_secure_storage_login_works_but_is_not_remembered(self):
        self.keyring.viable = False
        auth = self.service()
        self.assertEqual(auth.status(), LoginStatus(False, LoginReason.NO_SECURE_STORAGE))
        auth.authenticate()
        self.assertEqual(auth.status(), LoginStatus(True, LoginReason.NO_SECURE_STORAGE))
        self.assertFalse(self.service().status().signed_in)

    def test_cancelled_unlock_reports_locked_and_asks_again_next_time(self):
        self.service().authenticate()
        self.endpoint.grant("access-2")
        self.keyring.locked = True
        later = self.service()
        self.assertEqual(later.status(), LoginStatus(False, LoginReason.STORAGE_LOCKED))
        self.keyring.locked = False
        self.assertEqual(later.status(), LoginStatus(True))

    def test_login_that_cannot_be_saved_still_succeeds_and_is_announced(self):
        self.keyring.failing_writes = True
        auth = self.service()
        announced = []
        auth.subscribe_not_saved(lambda: announced.append(True))
        self.assertIsNotNone(auth.authenticate())
        self.assertEqual(auth.status(), LoginStatus(True, LoginReason.NOT_SAVED))
        self.assertEqual(announced, [True])

    def test_login_while_storage_is_locked_is_announced_as_not_saved(self):
        self.keyring.locked = True
        auth = self.service()
        announced = []
        auth.subscribe_not_saved(lambda: announced.append(True))
        auth.authenticate()
        self.assertEqual(auth.status(), LoginStatus(True, LoginReason.STORAGE_LOCKED))
        self.assertEqual(announced, [True])

    def test_login_without_secure_storage_is_not_announced(self):
        self.keyring.viable = False
        auth = self.service()
        announced = []
        auth.subscribe_not_saved(lambda: announced.append(True))
        auth.authenticate()
        self.assertEqual(announced, [])


class TestLogout(GoogleAuthTestCase):
    """登出只刪這台電腦安全儲存裡的那份"""

    def test_logout_deletes_only_the_local_copy(self):
        auth = self.service()
        auth.authenticate()
        auth.logout()
        self.assertEqual(self.keyring.stored(), [])
        self.assertEqual(self.endpoint.requests, [])
        self.assertFalse(auth.status().signed_in)
        self.assertFalse(self.service().status().signed_in)

    def test_logout_after_unsaved_login_no_longer_says_sign_in_again_next_time(self):
        self.keyring.failing_writes = True
        auth = self.service()
        auth.authenticate()
        auth.logout()
        self.assertEqual(auth.status(), LoginStatus(False))

    def test_logout_that_cannot_delete_does_not_raise(self):
        auth = self.service()
        auth.authenticate()
        self.endpoint.grant("access-2")
        self.keyring.failing_deletes = True
        auth.logout()
        self.assertFalse(auth.status().signed_in)
        self.keyring.locked = True
        auth.logout()
        self.assertFalse(auth.status().signed_in)


class FailingRemovals(FileService):
    """刪不掉檔案的檔案服務（檔案被占用或沒有權限）"""

    def remove_file(self, path: str) -> None:
        """一律拋出 PermissionError"""
        raise PermissionError(f"in use: {path}")


class TestStartup(GoogleAuthTestCase):
    """啟動時刪掉舊的明文權杖檔，且不碰安全儲存"""

    def write_legacy_token(self):
        """寫出舊版的明文權杖檔，內容是一份看起來有效的憑證"""
        with open(self.legacy_token, "w", encoding="utf-8") as f:
            json.dump({
                "token": "legacy-access", "refresh_token": "legacy-refresh",
                "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                "token_uri": "https://oauth2.googleapis.com/token", "expiry": "2999-01-01T00:00:00Z",
            }, f)

    def test_startup_cleanup_deletes_legacy_token_without_touching_secure_storage(self):
        self.write_legacy_token()
        auth = self.service()
        auth.remove_legacy_token()
        self.assertFalse(os.path.exists(self.legacy_token))
        self.assertEqual(self.keyring.calls, [])

    def test_startup_cleanup_without_legacy_token_does_not_raise(self):
        self.service().remove_legacy_token()

    def test_legacy_token_that_cannot_be_deleted_is_never_used_to_sign_in(self):
        self.write_legacy_token()
        self.endpoint.grant("access-2")
        auth = GoogleAuthService(
            FailingRemovals(), storage=SecureStorage("test-google", backend=self.keyring),
            request=self.endpoint, browser_login=self.browser,
            client_secrets_path=self.client_secrets, legacy_token_path=self.legacy_token,
        )
        auth.remove_legacy_token()
        self.assertTrue(os.path.exists(self.legacy_token))
        self.assertIsNone(auth.get_credentials())
        self.assertEqual(self.endpoint.requests, [])


if __name__ == "__main__":
    unittest.main()
