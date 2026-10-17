# -*- coding: utf-8 -*-
"""
本機回呼單元測試

從本機回呼的公開介面（receive、cancel）驗證：開瀏覽器到授權網址、在 127.0.0.1 的隨機埠等回呼、
回傳 query 參數、逾時、取消。開瀏覽器換成真的對回呼送 HTTP 請求的替身，不連外網。
"""
import os
import socket
import sys
import time
import unittest
from urllib.parse import urlencode

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from services.local_callback import BrowserNotOpened, CallbackCancelled, CallbackTimedOut, LocalCallback
from tests.fake_browser import CallbackBrowser


def provider_url(redirect_uri: str) -> str:
    """某個提供者的授權網址"""
    return "https://provider.example/authorize?" + urlencode({"redirect_uri": redirect_uri, "state": "s1"})


class TestReceive(unittest.TestCase):
    """開瀏覽器到授權網址，回傳回呼帶回來的 query 參數"""

    def setUp(self):
        self.browser = CallbackBrowser()

    def test_returns_callback_query_and_tells_browser_login_is_done(self):
        params = LocalCallback(self.browser).receive(provider_url)
        self.browser.finish()
        self.assertEqual(params, {"code": "auth-code", "state": "s1"})
        self.assertRegex(self.browser.authorization_query()["redirect_uri"], r"^http://127\.0\.0\.1:\d+/$")
        self.assertEqual(len(self.browser.pages), 1)
        self.assertIn("登入完成，可以關閉這個分頁", self.browser.pages[0])

    def test_provider_error_ends_at_once(self):
        self.browser.deny()
        started = time.monotonic()
        params = LocalCallback(self.browser, timeout=30).receive(provider_url)
        self.browser.finish()
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(params, {"error": "access_denied", "state": "s1"})
        self.assertIn("登入沒有完成", self.browser.pages[0])


def server_is_closed(port: int) -> bool:
    """127.0.0.1 的這個埠是否已經連不上"""
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    except ConnectionRefusedError:
        return True
    return False


class TestEnding(unittest.TestCase):
    """逾時與取消都會結束等待並關掉本機伺服器"""

    def setUp(self):
        self.browser = CallbackBrowser()
        self.browser.stay_silent()

    def test_timeout_ends_waiting_and_closes_server(self):
        with self.assertRaises(CallbackTimedOut):
            LocalCallback(self.browser, timeout=0.2).receive(provider_url)
        self.assertTrue(server_is_closed(self.browser.callback_port()))

    def test_cancel_ends_waiting_and_closes_server(self):
        callback = LocalCallback(self.browser, timeout=30)
        self.browser.on_open = callback.cancel
        started = time.monotonic()
        with self.assertRaises(CallbackCancelled):
            callback.receive(provider_url)
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(server_is_closed(self.browser.callback_port()))

    def test_browser_that_cannot_be_opened_ends_at_once(self):
        self.browser.opens = False
        started = time.monotonic()
        with self.assertRaises(BrowserNotOpened):
            LocalCallback(self.browser, timeout=30).receive(provider_url)
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(server_is_closed(self.browser.callback_port()))


if __name__ == "__main__":
    unittest.main()
