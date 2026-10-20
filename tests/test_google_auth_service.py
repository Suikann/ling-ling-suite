# -*- coding: utf-8 -*-
"""
Google 登入服務測試

從登入服務的公開介面（登入、取消登入、取得憑證、登出、目前狀態與原因、帳號 email、啟動清理）驗證。
安全儲存換成記憶體裡的 keyring backend，Google 權杖端點換成替身；開瀏覽器的替身拿到授權網址後，
真的對 127.0.0.1 的本機回呼送 HTTP 請求。不連外網、不碰系統的安全儲存。
「重開泠靈」以同一個 keyring 替身與偏好設定檔建立新的登入服務來模擬。
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.catalog_constants import GOOGLE_SCOPES
from services.google_auth_service import GoogleAuthService, LoginFailed, LoginFailure, LoginReason, LoginStatus
from services.file_service import FileService
from services.preferences_service import PreferencesService
from services.secure_storage import SecureStorage
from tests.fake_browser import CallbackBrowser, server_is_closed
from tests.fake_google import CLIENT_ID, CLIENT_SECRET, FakeTokenEndpoint, write_client_secrets
from tests.memory_keyring import MemoryKeyring


class GoogleAuthTestCase(unittest.TestCase):
    """登入服務測試的共用骨架：臨時目錄裡的用戶端檔與舊權杖檔位置、keyring 與權杖端點替身"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.client_secrets = write_client_secrets(os.path.join(self.temp_dir, "client_secret.json"))
        self.legacy_token = os.path.join(self.temp_dir, "google_token.json")
        self.preferences_path = os.path.join(self.temp_dir, "preferences.json")
        self.keyring = MemoryKeyring()
        self.endpoint = FakeTokenEndpoint()
        self.browser = CallbackBrowser()
        self.addCleanup(self.browser.finish)

    def service(self, login_timeout: float = 30) -> GoogleAuthService:
        """建立一個登入服務；再呼叫一次就像重開泠靈（安全儲存與偏好設定檔是同一份）"""
        preferences = PreferencesService(self.preferences_path)
        preferences.load()
        return GoogleAuthService(
            storage=SecureStorage("test-google", backend=self.keyring),
            request=self.endpoint,
            opener=self.browser,
            preferences=preferences,
            client_secrets_path=self.client_secrets,
            legacy_token_path=self.legacy_token,
            login_timeout=login_timeout,
        )


class TestSigningIn(GoogleAuthTestCase):
    """登入經本機回呼取得授權碼，再向 Google 兌換權杖"""

    def test_login_exchanges_code_from_local_callback_for_offline_access(self):
        auth = self.service()
        creds = auth.authenticate()
        self.assertEqual(creds.refresh_token, "refresh-1")
        self.assertEqual(self.keyring.stored(), ["refresh-1"])
        self.assertTrue(auth.status().signed_in)
        query = self.browser.authorization_query()
        self.assertEqual(query["access_type"], "offline")
        self.assertEqual(query["client_id"], CLIENT_ID)
        self.assertRegex(query["redirect_uri"], r"^http://127\.0\.0\.1:\d+/$")
        exchanged = self.endpoint.exchanged()
        self.assertEqual([e["code"] for e in exchanged], ["auth-code"])
        self.assertEqual(exchanged[0]["redirect_uri"], query["redirect_uri"])
        self.browser.finish()
        self.assertIn("登入完成，可以關閉這個分頁", self.browser.pages[0])

    def test_login_missing_a_scope_is_not_kept(self):
        for granted in GOOGLE_SCOPES:
            with self.subTest(granted=granted):
                self.endpoint.sign_in_as("library@example.com", scopes=[granted])
                auth = self.service()
                with self.assertRaises(LoginFailed) as raised:
                    auth.authenticate()
                self.assertEqual(raised.exception.failure, LoginFailure.SCOPES_MISSING)
                self.assertEqual(self.keyring.stored(), [])
                self.assertFalse(auth.status().signed_in)
                self.assertIsNone(auth.get_credentials())


class TestAccountEmail(GoogleAuthTestCase):
    """登入後記下帳號 email，下次登入帶 login_hint"""

    def test_login_remembers_account_email(self):
        self.endpoint.sign_in_as("library@example.com")
        auth = self.service()
        self.assertIsNone(auth.account_email)
        auth.authenticate()
        self.assertEqual(auth.account_email, "library@example.com")
        self.assertEqual(self.service().account_email, "library@example.com")

    def test_next_login_hints_remembered_email(self):
        self.service().authenticate()
        self.assertNotIn("login_hint", self.browser.authorization_query())
        self.service().authenticate()
        self.assertEqual(self.browser.authorization_query()["login_hint"], "library@example.com")


class TestLoginNotCompleted(GoogleAuthTestCase):
    """登入沒有完成時回報原因、不保存、關掉本機伺服器"""

    def assert_login_fails(self, auth: GoogleAuthService, failure: LoginFailure):
        """登入以 failure 結束，沒有保存、不算登入"""
        with self.assertRaises(LoginFailed) as raised:
            auth.authenticate()
        self.assertEqual(raised.exception.failure, failure)
        self.assertEqual(self.keyring.stored(), [])
        self.assertFalse(auth.status().signed_in)

    def test_timeout_ends_login_and_closes_local_server(self):
        self.browser.stay_silent()
        self.assert_login_fails(self.service(login_timeout=0.2), LoginFailure.TIMED_OUT)
        self.assertTrue(server_is_closed(self.browser.callback_port()))

    def test_cancel_ends_login_and_closes_local_server(self):
        self.browser.stay_silent()
        auth = self.service()
        self.browser.on_open = auth.cancel_login
        started = time.monotonic()
        self.assert_login_fails(auth, LoginFailure.CANCELLED)
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(server_is_closed(self.browser.callback_port()))

    def test_denied_consent_ends_login_at_once(self):
        self.browser.deny()
        started = time.monotonic()
        self.assert_login_fails(self.service(), LoginFailure.DENIED)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(self.endpoint.exchanged(), [])
        self.assertTrue(server_is_closed(self.browser.callback_port()))

    def test_browser_that_cannot_be_opened_is_reported(self):
        self.browser.opens = False
        self.assert_login_fails(self.service(), LoginFailure.BROWSER_NOT_OPENED)

    def test_missing_bundled_client_is_reported(self):
        os.remove(self.client_secrets)
        self.assert_login_fails(self.service(), LoginFailure.CLIENT_MISSING)
        self.assertEqual(self.browser.opened, [])

    def test_callback_with_another_state_is_not_exchanged(self):
        self.browser.forged_state = "someone-else"
        self.assert_login_fails(self.service(), LoginFailure.FAILED)
        self.assertEqual(self.endpoint.exchanged(), [])


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
        self.assertIsNotNone(later.get_credentials())
        self.assertEqual(later.status(), LoginStatus(True))

    def test_status_after_a_cancelled_unlock_does_not_ask_to_unlock_again(self):
        self.service().authenticate()
        self.keyring.locked = True
        later = self.service()
        self.assertIsNone(later.get_credentials())
        reads = self.keyring.calls.count("get")
        self.assertEqual(later.status(), LoginStatus(False, LoginReason.STORAGE_LOCKED))
        self.assertEqual(later.status(), LoginStatus(False, LoginReason.STORAGE_LOCKED))
        self.assertEqual(self.keyring.calls.count("get"), reads)

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
        self.assertFalse(any("revoke" in r["url"] for r in self.endpoint.requests))
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
            request=self.endpoint, opener=self.browser,
            client_secrets_path=self.client_secrets, legacy_token_path=self.legacy_token,
        )
        auth.remove_legacy_token()
        self.assertTrue(os.path.exists(self.legacy_token))
        self.assertIsNone(auth.get_credentials())
        self.assertEqual(self.endpoint.requests, [])


if __name__ == "__main__":
    unittest.main()
