# -*- coding: utf-8 -*-
"""
開瀏覽器的替身：拿到授權網址後真的對本機回呼送 HTTP 請求

取代 ExternalOpener，注入給本機回呼與登入服務。不開瀏覽器，而是讀出授權網址裡的 redirect_uri，
在背景對那個 127.0.0.1 位址送一個 GET，帶上 code 或 error（以及授權網址的 state），
也可以設成永遠不送，模擬提供者只在瀏覽器顯示錯誤、沒有回呼。回呼網址讀 redirect_uri（Google），
沒有時讀 redirect_to（Supabase）。建立時傳入 provider（例如假 HuaXia 的 authorize）時，同意授權的回呼參數由它
依授權網址發給，替身才拿得到提供者記下 code_challenge 後發的授權碼。

使用範例：
    from tests.fake_browser import CallbackBrowser
    browser = CallbackBrowser()
    browser.deny()                 # 之後的登入都回傳 error=access_denied
    callback = LocalCallback(browser)
    params = callback.receive(build_url)
    browser.finish()
    browser.pages                  # 回呼頁面的內容
"""
import threading
import urllib.error
import urllib.request
from typing import Callable, Dict, List, Optional
from urllib.parse import parse_qs, urlencode, urlsplit

# 不經系統設定的 HTTP proxy：回呼位址是 127.0.0.1，交給 proxy 會送不到
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _redirect_of(query: Dict[str, List[str]]) -> str:
    """授權網址的回呼網址：Google 用 redirect_uri，Supabase 用 redirect_to"""
    return (query.get("redirect_uri") or query["redirect_to"])[0]


class CallbackBrowser:
    """開瀏覽器的替身；預設以授權碼 auth-code 回呼"""

    def __init__(self, provider: Optional[Callable[[str], Dict[str, str]]] = None):
        """建立替身：拿到授權網址時送回 code，不偽造 state

        Args:
            provider: 同意授權時以授權網址求出回呼參數的函式；省略時回呼固定的授權碼
        """
        self.opens = True
        self._provider = provider
        self.on_open: Optional[Callable[[], None]] = None
        # 設了就以它取代授權網址的 state 回呼，模擬不是這次登入發出的回呼
        self.forged_state: Optional[str] = None
        self.opened: List[str] = []
        self.pages: List[str] = []
        self._reply: Optional[Dict[str, str]] = {"code": "auth-code"}
        self._threads: List[threading.Thread] = []

    def approve(self, code: str = "auth-code") -> None:
        """之後的登入都以授權碼 code 回呼"""
        self._reply = {"code": code}

    def deny(self, error: str = "access_denied") -> None:
        """之後的登入都以 error 回呼（使用者在同意畫面按了取消）"""
        self._reply = {"error": error}

    def stay_silent(self) -> None:
        """之後的登入都不回呼"""
        self._reply = None

    def open(self, target: str) -> bool:
        """記下授權網址，依設定在背景對其中的 redirect_uri 送出回呼；opens 為 False 時像瀏覽器開不了"""
        self.opened.append(target)
        if not self.opens:
            return False
        if self.on_open:
            self.on_open()
        if self._reply is not None:
            query = parse_qs(urlsplit(target).query)
            params = dict(self._reply)
            if self._provider is not None and "code" in params:
                params = self._provider(target)
            if self.forged_state is not None:
                params["state"] = self.forged_state
            elif "state" in query:
                params["state"] = query["state"][0]
            url = _redirect_of(query) + "?" + urlencode(params)
            thread = threading.Thread(target=self._send, args=(url,), daemon=True)
            thread.start()
            self._threads.append(thread)
        return True

    def finish(self) -> None:
        """等送出的回呼都收到回應"""
        for thread in self._threads:
            thread.join(5)

    def authorization_query(self) -> Dict[str, str]:
        """最近一次授權網址的 query 參數"""
        return {key: values[0] for key, values in parse_qs(urlsplit(self.opened[-1]).query).items()}

    def callback_port(self) -> int:
        """最近一次授權網址的回呼埠"""
        query = {key: [value] for key, value in self.authorization_query().items()}
        return urlsplit(_redirect_of(query)).port

    def _send(self, url: str) -> None:
        """對回呼送 GET，記下回應頁面的內容"""
        try:
            with _DIRECT.open(url, timeout=5) as response:
                self.pages.append(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            self.pages.append(e.read().decode("utf-8"))
        except OSError:
            pass
