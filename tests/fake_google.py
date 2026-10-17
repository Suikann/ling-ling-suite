# -*- coding: utf-8 -*-
"""
Google 登入的替身：權杖端點與瀏覽器登入

權杖端點替身照 google-auth 的 transport 介面（以網址、方法、內容呼叫，回傳 status、headers、data），
注入給登入服務當作續期時送出請求的對象：可回傳指定的權杖、拒絕續期，或模擬連不上。
瀏覽器登入替身不開瀏覽器，直接回傳帶指定續期權杖的憑證。

使用範例：
    from tests.fake_google import FakeTokenEndpoint, FakeBrowserLogin
    endpoint = FakeTokenEndpoint()
    endpoint.grant("access-2", refresh_token="refresh-2")
    auth = GoogleAuthService(request=endpoint, browser_login=FakeBrowserLogin("refresh-1"), ...)
"""
import datetime
import json
import os
import sys
from typing import List, Optional
from urllib.parse import parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from google.auth import exceptions
from google.oauth2.credentials import Credentials

from core.catalog_constants import GOOGLE_SCOPES

TOKEN_URI = "https://oauth2.googleapis.com/token"
CLIENT_ID = "test-client.apps.googleusercontent.com"
CLIENT_SECRET = "test-secret"


def write_client_secrets(path: str) -> str:
    """在 path 寫出桌面應用程式的用戶端檔（client_secret.json 的格式），回傳 path"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"installed": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": TOKEN_URI,
        }}, f)
    return path


class _Response:
    """google-auth transport 的回應"""

    def __init__(self, status: int, payload: dict):
        self.status = status
        self.headers = {"content-type": "application/json"}
        self.data = json.dumps(payload).encode("utf-8")


class FakeTokenEndpoint:
    """Google 權杖端點替身；預設拒絕續期，先以 grant 設定要回傳的權杖"""

    def __init__(self):
        self._outcome = "reject"
        self._access_token = ""
        self._refresh_token: Optional[str] = None
        self.requests: List[dict] = []

    def grant(self, access_token: str, refresh_token: Optional[str] = None) -> None:
        """之後的續期都成功，回傳 access_token；帶 refresh_token 時一併換發新的續期權杖"""
        self._outcome = "grant"
        self._access_token = access_token
        self._refresh_token = refresh_token

    def reject(self) -> None:
        """之後的續期都被拒絕（invalid_grant：換了用戶端、撤銷授權或太久沒用）"""
        self._outcome = "reject"

    def go_offline(self) -> None:
        """之後的請求都連不上"""
        self._outcome = "offline"

    def refreshed_with(self) -> List[str]:
        """各次續期送出的續期權杖"""
        return [r.get("refresh_token", [""])[0] for r in self.requests]

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        """收下一個請求，依設定回應"""
        text = body.decode("utf-8") if isinstance(body, bytes) else (body or "")
        self.requests.append({"url": url, **parse_qs(text)})
        if self._outcome == "offline":
            raise exceptions.TransportError("Failed to establish a new connection")
        if self._outcome == "reject":
            return _Response(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
        payload = {"access_token": self._access_token, "expires_in": 3599, "token_type": "Bearer"}
        if self._refresh_token:
            payload["refresh_token"] = self._refresh_token
        return _Response(200, payload)


class FakeBrowserLogin:
    """瀏覽器登入替身：不開瀏覽器，回傳帶指定續期權杖、一小時內有效的憑證"""

    def __init__(self, refresh_token: str = "refresh-1"):
        self.refresh_token = refresh_token
        self.calls = 0

    def __call__(self, client_secrets_path: str, scopes: List[str]) -> Credentials:
        """模擬使用者在瀏覽器完成登入"""
        self.calls += 1
        return Credentials(
            token="access-1", refresh_token=self.refresh_token, token_uri=TOKEN_URI,
            client_id=CLIENT_ID, client_secret=CLIENT_SECRET, scopes=list(GOOGLE_SCOPES),
            expiry=datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) + datetime.timedelta(hours=1),
        )
