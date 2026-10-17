# -*- coding: utf-8 -*-
"""
Google 登入的替身：權杖端點與 Drive 的帳號資訊

權杖端點替身照 google-auth 的 transport 介面（以網址、方法、內容呼叫，回傳 status、headers、data），
注入給登入服務當作兌換授權碼、續期與查帳號 email 時送出請求的對象：
兌換授權碼時回傳指定的續期權杖、範圍與 email；續期時回傳指定的權杖、拒絕續期，或模擬連不上。
開瀏覽器的替身見 tests/fake_browser.py。

使用範例：
    from tests.fake_google import FakeTokenEndpoint
    endpoint = FakeTokenEndpoint()
    endpoint.sign_in_as("library@example.com", refresh_token="refresh-1")
    endpoint.grant("access-2", refresh_token="refresh-2")
    auth = GoogleAuthService(request=endpoint, opener=CallbackBrowser(), ...)
"""
import json
import os
import sys
from typing import List, Optional
from urllib.parse import parse_qs

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from google.auth import exceptions

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
    """Google 權杖端點替身

    兌換授權碼預設成功：續期權杖 refresh-1、兩個範圍都給、帳號 library@example.com。
    續期預設被拒，先以 grant 設定要回傳的權杖。
    """

    def __init__(self):
        self._outcome = "reject"
        self._access_token = ""
        self._refresh_token: Optional[str] = None
        self._sign_in_refresh_token = "refresh-1"
        self._granted_scopes = list(GOOGLE_SCOPES)
        self._email = "library@example.com"
        self.requests: List[dict] = []

    def sign_in_as(self, email: str, refresh_token: str = "refresh-1", scopes: Optional[List[str]] = None) -> None:
        """之後兌換授權碼時給 refresh_token 與 scopes（省略時兩個範圍都給），帳號資訊回傳 email"""
        self._email = email
        self._sign_in_refresh_token = refresh_token
        self._granted_scopes = list(GOOGLE_SCOPES) if scopes is None else scopes

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
        return [r["refresh_token"][0] for r in self.requests if r.get("grant_type") == ["refresh_token"]]

    def exchanged(self) -> List[dict]:
        """各次兌換授權碼的請求內容（表單欄位名稱對應值）"""
        return [
            {key: values[0] for key, values in r.items() if key != "url"}
            for r in self.requests if r.get("grant_type") == ["authorization_code"]
        ]

    def __call__(self, url, method="GET", body=None, headers=None, timeout=None, **kwargs):
        """收下一個請求，依設定回應"""
        text = body.decode("utf-8") if isinstance(body, bytes) else (body or "")
        self.requests.append({"url": url, **parse_qs(text)})
        if self._outcome == "offline":
            raise exceptions.TransportError("Failed to establish a new connection")
        if "/drive/v3/about" in url:
            return _Response(200, {"user": {"emailAddress": self._email}})
        if parse_qs(text).get("grant_type") == ["authorization_code"]:
            return _Response(200, {
                "access_token": "access-1", "expires_in": 3599, "token_type": "Bearer",
                "refresh_token": self._sign_in_refresh_token, "scope": " ".join(self._granted_scopes),
            })
        if self._outcome == "reject":
            return _Response(400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."})
        payload = {"access_token": self._access_token, "expires_in": 3599, "token_type": "Bearer"}
        if self._refresh_token:
            payload["refresh_token"] = self._refresh_token
        return _Response(200, payload)

