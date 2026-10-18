# -*- coding: utf-8 -*-
"""
HuaXia 登入服務測試

從登入服務的公開介面（登入、取消登入、取得 access token、重查幹部身分、登出、目前狀態）驗證，
對 HTTP 層的假 HuaXia：HuaXia 用戶端的 HTTP session 換成 tests/fake_huaxia.py。安全儲存換成記憶體裡的 keyring
backend；開瀏覽器的替身拿到授權網址後向假 HuaXia 取得授權碼，再真的對 127.0.0.1 的本機回呼送 HTTP 請求。
不連外網、不碰系統的安全儲存。「重開泠靈」以同一個 keyring 替身建立新的登入服務來模擬。
"""
import datetime
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from services.huaxia_auth_service import (
    AccountStanding, HuaXiaAuthService, HuaXiaLoginFailed, HuaXiaLoginFailure, HuaXiaReason, NotOfficer,
)
from services.huaxia_client import HuaXiaClient, HuaXiaError, HuaXiaErrorKind
from services.secure_storage import SecureStorage
from tests.fake_browser import CallbackBrowser
from tests.fake_huaxia import FakeHuaXia
from tests.memory_keyring import MemoryKeyring
from tests.test_google_auth_service import server_is_closed


class HuaXiaAuthTestCase(unittest.TestCase):
    """HuaXia 登入測試的共用骨架：假 HuaXia 上有一位幹部 officer_amy，瀏覽器以她登入"""

    def setUp(self):
        self.huaxia = FakeHuaXia()
        self.huaxia.add_account("officer_amy", roles=("member", "officer"))
        self.huaxia.sign_in_as("officer_amy")
        self.keyring = MemoryKeyring()
        self.browser = CallbackBrowser(provider=self.huaxia.authorize)
        self.addCleanup(self.browser.finish)

    def service(self, login_timeout: float = 30) -> HuaXiaAuthService:
        """建立一個登入服務；再呼叫一次就像重開泠靈（安全儲存是同一份）"""
        return HuaXiaAuthService(
            HuaXiaClient(session=self.huaxia),
            storage=SecureStorage("test-huaxia", backend=self.keyring),
            opener=self.browser,
            login_timeout=login_timeout,
        )


class TestOfficerSignsIn(HuaXiaAuthTestCase):
    """幹部以 Discord 經 PKCE 登入，續期權杖存進安全儲存"""

    def test_officer_login_exchanges_code_with_pkce_and_is_remembered(self):
        auth = self.service()
        self.assertEqual(auth.authenticate(), "officer_amy")
        query = self.browser.authorization_query()
        self.assertEqual(query["provider"], "discord")
        self.assertRegex(query["redirect_to"], r"^http://127\.0\.0\.1:\d+/$")
        self.assertEqual(len(self.keyring.stored()), 1)
        status = auth.status()
        self.assertTrue(status.signed_in)
        self.assertEqual(status.discord_name, "officer_amy")
        self.browser.finish()
        self.assertIn("登入完成，可以關閉這個分頁", self.browser.pages[0])

    def test_name_falls_back_to_the_user_name_fields(self):
        for metadata, expected in (({"user_name": "lin_u"}, "lin_u"), ({"preferred_username": "pref_p"}, "pref_p")):
            with self.subTest(metadata):
                self.huaxia.add_account(expected, metadata=metadata)
                self.huaxia.sign_in_as(expected)
                self.assertEqual(self.service().authenticate(), expected)


class TestNotAnOfficer(HuaXiaAuthTestCase):
    """不是幹部：依帳號狀態分成四種、寫出 Discord 帳號名稱，以 scope=local 登出、不存續期權杖"""

    def assert_not_officer(self, standing: AccountStanding, discord_name: str):
        """登入以不是幹部結束，分類與名稱如預期；沒有保存、不算登入，HuaXia 上的 session 已結束"""
        auth = self.service()
        with self.assertRaises(HuaXiaLoginFailed) as raised:
            auth.authenticate()
        self.assertEqual(raised.exception.failure, HuaXiaLoginFailure.NOT_OFFICER)
        self.assertEqual(raised.exception.not_officer, NotOfficer(standing, discord_name))
        self.assertEqual(self.keyring.stored(), [])
        self.assertFalse(auth.status().signed_in)
        self.assertEqual(self.huaxia.logouts(), ["local"])
        self.assertEqual(self.huaxia.live_sessions(discord_name), 0)

    def test_discord_without_a_huaxia_account_just_created_one(self):
        self.huaxia.sign_in_as("new_member")
        self.assert_not_officer(AccountStanding.JUST_CREATED, "new_member")

    def test_account_created_long_ago_is_awaiting_approval(self):
        self.huaxia.add_account("waiting_wu", status="pending", roles=())
        self.huaxia.sign_in_as("waiting_wu")
        self.assert_not_officer(AccountStanding.AWAITING_APPROVAL, "waiting_wu")

    def test_account_created_within_the_clock_skew_counts_as_just_created(self):
        just_before = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=30)
        self.huaxia.add_account("skewed_su", status="pending", roles=(), created_at=just_before)
        self.huaxia.sign_in_as("skewed_su")
        self.assert_not_officer(AccountStanding.JUST_CREATED, "skewed_su")

    def test_disabled_account(self):
        self.huaxia.add_account("former_fan", status="inactive", roles=("officer",))
        self.huaxia.sign_in_as("former_fan")
        self.assert_not_officer(AccountStanding.DISABLED, "former_fan")

    def test_active_member_without_officer_role(self):
        self.huaxia.add_account("member_mei", roles=("member",))
        self.huaxia.sign_in_as("member_mei")
        self.assert_not_officer(AccountStanding.NOT_OFFICER, "member_mei")

    def test_admin_is_an_officer(self):
        self.huaxia.add_account("admin_an", roles=("admin",))
        self.huaxia.sign_in_as("admin_an")
        self.assertEqual(self.service().authenticate(), "admin_an")
        self.assertEqual(len(self.keyring.stored()), 1)


class TestLoginNotCompleted(HuaXiaAuthTestCase):
    """登入沒有完成時回報原因、不保存、關掉本機伺服器"""

    def assert_login_fails(self, auth: HuaXiaAuthService, failure: HuaXiaLoginFailure):
        """登入以 failure 結束，沒有保存、不算登入"""
        with self.assertRaises(HuaXiaLoginFailed) as raised:
            auth.authenticate()
        self.assertEqual(raised.exception.failure, failure)
        self.assertEqual(self.keyring.stored(), [])
        self.assertFalse(auth.status().signed_in)

    def test_denied_in_discord_ends_login_at_once(self):
        self.browser.deny()
        started = time.monotonic()
        self.assert_login_fails(self.service(), HuaXiaLoginFailure.DENIED)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(self.huaxia.requests, [])
        self.assertTrue(server_is_closed(self.browser.callback_port()))

    def test_timeout_ends_login_and_closes_local_server(self):
        self.browser.stay_silent()
        self.assert_login_fails(self.service(login_timeout=0.2), HuaXiaLoginFailure.TIMED_OUT)
        self.assertTrue(server_is_closed(self.browser.callback_port()))

    def test_cancel_ends_login_and_closes_local_server(self):
        self.browser.stay_silent()
        auth = self.service()
        self.browser.on_open = auth.cancel_login
        started = time.monotonic()
        self.assert_login_fails(auth, HuaXiaLoginFailure.CANCELLED)
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(server_is_closed(self.browser.callback_port()))

    def test_browser_that_cannot_be_opened_is_reported(self):
        self.browser.opens = False
        self.assert_login_fails(self.service(), HuaXiaLoginFailure.BROWSER_NOT_OPENED)

    def test_code_that_huaxia_does_not_know_is_not_kept(self):
        self.browser = CallbackBrowser()
        self.assert_login_fails(self.service(), HuaXiaLoginFailure.FAILED)


class TestRefreshTokenRotation(HuaXiaAuthTestCase):
    """續期權杖只能用一次：換到新的就寫回安全儲存，同一時間只發出一次續期"""

    def signed_in_with_expired_access(self) -> HuaXiaAuthService:
        """以幹部登入，拿到的 access token 已經到期；之後發的 access token 照常有效"""
        self.huaxia.expires_in = 0
        auth = self.service()
        auth.authenticate()
        self.huaxia.expires_in = 3600
        return auth

    def test_saved_login_is_refreshed_after_restart(self):
        self.service().authenticate()
        saved = self.keyring.stored()
        restarted = self.service()
        self.assertTrue(restarted.status().signed_in)
        restarted.access_token()
        self.assertEqual(self.huaxia.refreshes(), saved)
        self.assertEqual(restarted.status().discord_name, "officer_amy")

    def test_rotated_refresh_token_replaces_the_saved_one(self):
        auth = self.signed_in_with_expired_access()
        first = self.keyring.stored()
        auth.access_token()
        self.assertNotEqual(self.keyring.stored(), first)
        # 重開泠靈後拿安全儲存那份續期：是最新的，不會因為重用舊的而撤銷 session
        self.service().access_token()
        self.assertEqual(self.huaxia.live_sessions("officer_amy"), 1)

    def test_valid_access_token_is_reused_without_refreshing(self):
        auth = self.service()
        auth.authenticate()
        auth.access_token()
        auth.access_token()
        self.assertEqual(self.huaxia.refreshes(), [])

    def test_concurrent_refreshes_send_only_one_request(self):
        auth = self.signed_in_with_expired_access()
        gate = self.huaxia.hold_refreshes()
        tokens, errors = [], []

        def fetch():
            """在背景取 access token"""
            try:
                tokens.append(auth.access_token())
            except HuaXiaError as e:
                errors.append(e)
        threads = [threading.Thread(target=fetch) for _ in range(2)]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 5
        while not self.huaxia.refreshes() and time.monotonic() < deadline:
            time.sleep(0.01)
        # 給第二個執行緒時間：沒有排隊的話，它會在這段時間送出第二個續期
        time.sleep(0.2)
        gate.set()
        for thread in threads:
            thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(len(self.huaxia.refreshes()), 1)
        self.assertEqual(len(set(tokens)), 1)
        self.assertEqual(self.huaxia.live_sessions("officer_amy"), 1)

    def test_rotated_token_that_cannot_be_saved_is_announced(self):
        auth = self.signed_in_with_expired_access()
        announced = []
        auth.subscribe_not_saved(lambda: announced.append(True))
        self.keyring.failing_writes = True
        auth.access_token()
        self.assertEqual(announced, [True])
        self.assertEqual(auth.status().reason, HuaXiaReason.NOT_SAVED)

    def test_login_that_cannot_be_saved_still_succeeds_and_is_announced(self):
        self.keyring.failing_writes = True
        auth = self.service()
        announced = []
        auth.subscribe_not_saved(lambda: announced.append(True))
        self.assertEqual(auth.authenticate(), "officer_amy")
        self.assertEqual(announced, [True])
        self.assertTrue(auth.status().signed_in)


class TestSignedOutElsewhere(HuaXiaAuthTestCase):
    """在 HuaXia 網頁登出全部之後，續期回報登入已失效（可能在別處登出了），不當成一般錯誤"""

    def test_refresh_after_global_logout_reports_session_gone(self):
        self.huaxia.expires_in = 0
        auth = self.service()
        auth.authenticate()
        self.huaxia.sign_out_everywhere("officer_amy")
        with self.assertRaises(HuaXiaError) as raised:
            auth.access_token()
        self.assertIs(raised.exception.kind, HuaXiaErrorKind.SESSION_GONE)
        status = auth.status()
        self.assertFalse(status.signed_in)
        self.assertEqual(status.reason, HuaXiaReason.SESSION_GONE)
        self.assertEqual(self.keyring.stored(), [])

    def test_saved_login_after_global_logout_reports_session_gone_on_restart(self):
        self.service().authenticate()
        self.huaxia.sign_out_everywhere("officer_amy")
        with self.assertRaises(HuaXiaError) as raised:
            self.service().recheck_officer()
        self.assertIs(raised.exception.kind, HuaXiaErrorKind.SESSION_GONE)

    def test_unreachable_huaxia_is_told_apart_and_keeps_the_login(self):
        self.huaxia.expires_in = 0
        auth = self.service()
        auth.authenticate()
        self.huaxia.go_offline()
        with self.assertRaises(HuaXiaError) as raised:
            auth.access_token()
        self.assertIs(raised.exception.kind, HuaXiaErrorKind.UNREACHABLE)
        self.assertTrue(auth.status().signed_in)
        self.assertEqual(len(self.keyring.stored()), 1)


class TestRecheckOfficer(HuaXiaAuthTestCase):
    """重查幹部身分（發譜按鈕用）：還是幹部就保持登入，不是了就登出並回報分類"""

    def test_still_an_officer_stays_signed_in(self):
        auth = self.service()
        auth.authenticate()
        self.assertIsNone(auth.recheck_officer())
        self.assertTrue(auth.status().signed_in)

    def test_officer_role_removed_signs_out_and_reports_why(self):
        auth = self.service()
        auth.authenticate()
        self.huaxia.remove_roles("officer_amy")
        self.assertEqual(auth.recheck_officer(), NotOfficer(AccountStanding.NOT_OFFICER, "officer_amy"))
        self.assertFalse(auth.status().signed_in)
        self.assertEqual(self.keyring.stored(), [])
        self.assertEqual(self.huaxia.logouts(), ["local"])

    def test_disabled_after_login_is_reported_as_disabled(self):
        auth = self.service()
        auth.authenticate()
        self.huaxia.set_status("officer_amy", "inactive")
        self.assertEqual(auth.recheck_officer().standing, AccountStanding.DISABLED)

    def test_recheck_without_login_reports_signed_out(self):
        with self.assertRaises(HuaXiaError) as raised:
            self.service().recheck_officer()
        self.assertIs(raised.exception.kind, HuaXiaErrorKind.SIGNED_OUT)


class TestLogout(HuaXiaAuthTestCase):
    """登出只結束這台電腦的 session（scope=local），再刪安全儲存那份；請求失敗時仍刪本機"""

    def test_logout_ends_only_this_session(self):
        browser_session = self.service()
        browser_session.authenticate()
        auth = self.service()
        self.keyring.passwords.clear()
        auth.authenticate()
        auth.logout()
        self.assertEqual(self.huaxia.logouts(), ["local"])
        self.assertEqual(self.huaxia.live_sessions("officer_amy"), 1)
        self.assertEqual(self.keyring.stored(), [])
        self.assertFalse(auth.status().signed_in)

    def test_logout_after_restart_ends_the_saved_session(self):
        self.service().authenticate()
        self.service().logout()
        self.assertEqual(self.huaxia.logouts(), ["local"])
        self.assertEqual(self.huaxia.live_sessions("officer_amy"), 0)
        self.assertEqual(self.keyring.stored(), [])

    def test_logout_while_unreachable_still_deletes_the_local_copy(self):
        auth = self.service()
        auth.authenticate()
        self.huaxia.go_offline()
        auth.logout()
        self.assertEqual(self.huaxia.logouts(), ["local"])
        self.assertEqual(self.keyring.stored(), [])
        self.assertFalse(auth.status().signed_in)

    def test_logout_that_cannot_delete_does_not_sign_in_again(self):
        auth = self.service()
        auth.authenticate()
        self.keyring.failing_deletes = True
        auth.logout()
        self.assertFalse(auth.status().signed_in)
