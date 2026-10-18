# -*- coding: utf-8 -*-
"""
本機回呼登入

OAuth 桌面登入共用的一段（Google 與 Discord）：在 http://127.0.0.1:<隨機埠>/ 開一個只收一次回呼的本機伺服器，
經 ExternalOpener 開瀏覽器到提供者的授權網址，等瀏覽器帶著 query 參數（code 或 error 一類）回到本機，
把參數原樣交回呼叫端；兌換權杖、檢查 state 由各登入服務處理。

等待有逾時（預設 5 分鐘），也可以從別的執行緒取消；結束時一律關掉本機伺服器。
receive 會卡住呼叫它的執行緒直到結束，介面要在背景執行緒呼叫，等待視窗與逾時訊息由呼叫端顯示。

使用範例：
    from services.local_callback import LocalCallback, CallbackTimedOut
    callback = LocalCallback(ExternalOpener())
    params = callback.receive(lambda redirect_uri: build_authorization_url(redirect_uri))
    callback.cancel()   # 從別的執行緒取消
"""
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable, Dict, Optional
from urllib.parse import parse_qs, urlsplit

from core.locale import t
from services.external_opener import ExternalOpener

# Discord 的授權碼從開始登入起算 300 秒就失效，Google 比照
DEFAULT_TIMEOUT_SECONDS = 300.0
_LOOPBACK_HOST = "127.0.0.1"
# 等待時每隔這麼久檢查一次有沒有被取消
_POLL_SECONDS = 0.1
# 回呼的 error：使用者在提供者的同意畫面拒絕授權（OAuth 2.0 的 access_denied，Google 與 Supabase 相同）
ACCESS_DENIED = "access_denied"


class CallbackError(Exception):
    """沒等到回呼就結束"""


class CallbackTimedOut(CallbackError):
    """等到上限都沒有回呼"""


class CallbackCancelled(CallbackError):
    """等待中被取消"""


class BrowserNotOpened(CallbackError):
    """瀏覽器開不了"""


class _CallbackServer(HTTPServer):
    """只在 127.0.0.1 收回呼的本機伺服器；收到的第一個回呼的 query 參數記在 params"""

    params: Optional[Dict[str, str]] = None


class _CallbackHandler(BaseHTTPRequestHandler):
    """處理瀏覽器回到本機的請求：根路徑帶 query 的是回呼，其餘（例如 favicon）回 404"""

    # 連上卻遲遲不送請求的連線最多等這麼久，等待才不會被它卡住
    timeout = 5

    def do_GET(self):
        """收下回呼，回一頁告訴使用者可以關閉分頁"""
        url = urlsplit(self.path)
        params = {key: values[0] for key, values in parse_qs(url.query).items()}
        if url.path != "/" or not params:
            self.send_error(404)
            return
        if self.server.params is None:
            self.server.params = params
        text = t("login.callback.not_done") if "error" in params else t("login.callback.done")
        page = f'<!doctype html><meta charset="utf-8"><title>{text}</title><p>{text}</p>'.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(page)))
        self.end_headers()
        self.wfile.write(page)

    def log_message(self, format, *args):
        """不寫存取紀錄到 stderr"""


class LocalCallback:
    """開瀏覽器到授權網址，在本機等提供者回呼；一個物件只用於一次登入"""

    def __init__(self, opener: ExternalOpener, timeout: float = DEFAULT_TIMEOUT_SECONDS):
        """
        Args:
            opener: 開瀏覽器用的外部開啟服務
            timeout: 等待回呼的上限（秒）
        """
        self._opener = opener
        self._timeout = timeout
        self._cancelled = threading.Event()

    def cancel(self) -> None:
        """取消等待（可從別的執行緒呼叫）；receive 隨即結束並關掉本機伺服器，還沒開始等就不開瀏覽器"""
        self._cancelled.set()

    def receive(self, authorization_url: Callable[[str], str]) -> Dict[str, str]:
        """開瀏覽器到授權網址，等回呼帶回 query 參數；結束時關掉本機伺服器

        Args:
            authorization_url: 以回呼網址（http://127.0.0.1:<埠>/）組出提供者授權網址的函式

        Returns:
            回呼的 query 參數，例如 code 與 state；提供者回傳拒絕時含 error，收到就立刻回傳

        Raises:
            CallbackTimedOut: 等到上限都沒有回呼
            CallbackCancelled: 等待中被取消
            BrowserNotOpened: 瀏覽器開不了
        """
        server = _CallbackServer((_LOOPBACK_HOST, 0), _CallbackHandler)
        try:
            redirect_uri = f"http://{_LOOPBACK_HOST}:{server.server_port}/"
            if self._cancelled.is_set():
                raise CallbackCancelled()
            if not self._opener.open(authorization_url(redirect_uri)):
                raise BrowserNotOpened()
            deadline = time.monotonic() + self._timeout
            while server.params is None:
                if self._cancelled.is_set():
                    raise CallbackCancelled()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CallbackTimedOut()
                server.timeout = min(_POLL_SECONDS, remaining)
                server.handle_request()
            return server.params
        finally:
            server.server_close()
