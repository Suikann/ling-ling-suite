# -*- coding: utf-8 -*-
"""
Google API 認證服務

使用 OAuth2 桌面應用程式流程進行認證。
用戶端憑證（client_secrets.json）隨程式打包，
使用者首次啟動時透過瀏覽器登入 Google 帳號授權，
權杖快取於本地，後續啟動自動登入。

使用範例：
    from services.google_auth_service import GoogleAuthService
    auth = GoogleAuthService()
    creds = auth.get_credentials()  # 已登入時直接取得
    creds = auth.authenticate()     # 首次登入
"""
import os
import sys
from typing import Optional
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from core.catalog_constants import GOOGLE_SCOPES, TOKEN_FILE, CLIENT_SECRETS_FILE
from core.constants import APPDATA_DIR


def _get_app_dir() -> str:
    """取得應用程式根目錄（支援 PyInstaller 打包環境）

    開發環境：回傳 src/ 的上層（專案根目錄）
    打包環境：回傳 exe 所在目錄
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    src_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.dirname(src_dir)


class GoogleAuthService:
    """Google OAuth2 認證管理"""

    def __init__(self):
        self._client_secrets_path = os.path.join(
            _get_app_dir(), CLIENT_SECRETS_FILE,
        )
        self._token_path = os.path.join(APPDATA_DIR, TOKEN_FILE)
        self._credentials: Optional[Credentials] = None

    @property
    def is_authenticated(self) -> bool:
        """是否已通過認證"""
        try:
            creds = self.get_credentials()
            return creds is not None
        except Exception:
            return False

    @property
    def has_client_secrets(self) -> bool:
        """是否有 OAuth 用戶端憑證檔"""
        return os.path.isfile(self._client_secrets_path)

    @property
    def client_secrets_path(self) -> str:
        """用戶端憑證檔路徑"""
        return self._client_secrets_path

    def get_credentials(self) -> Optional[Credentials]:
        """取得有效的認證憑據（不觸發登入流程）

        從快取載入權杖，過期時自動重新整理。

        Returns:
            Google 認證憑據，尚未登入時回傳 None
        """
        if self._credentials and self._credentials.valid:
            return self._credentials
        self._credentials = self._load_token()
        if not self._credentials:
            return None
        if self._credentials.expired and self._credentials.refresh_token:
            try:
                self._credentials.refresh(Request())
                self._save_token(self._credentials)
            except Exception:
                self._credentials = None
                return None
        if self._credentials and self._credentials.valid:
            return self._credentials
        return None

    def authenticate(self) -> Credentials:
        """執行 OAuth2 登入流程

        開啟瀏覽器讓使用者登入 Google 帳號並授權。
        授權後權杖自動儲存於本地，後續啟動免再登入。

        Returns:
            Google 認證憑據

        Raises:
            FileNotFoundError: 找不到用戶端憑證檔
        """
        if not self.has_client_secrets:
            raise FileNotFoundError(self._client_secrets_path)
        flow = InstalledAppFlow.from_client_secrets_file(
            self._client_secrets_path, GOOGLE_SCOPES,
        )
        try:
            self._credentials = flow.run_local_server(port=0)
        except Exception as e:
            self._credentials = None
            raise RuntimeError(f"OAuth 登入流程失敗：{e}") from e
        if not self._credentials:
            raise RuntimeError("使用者取消了登入")
        self._save_token(self._credentials)
        return self._credentials

    def logout(self):
        """登出並清除本地權杖"""
        self._credentials = None
        if os.path.isfile(self._token_path):
            os.remove(self._token_path)

    def _load_token(self) -> Optional[Credentials]:
        """從本地快取載入權杖"""
        if not os.path.isfile(self._token_path):
            return None
        try:
            return Credentials.from_authorized_user_file(
                self._token_path, GOOGLE_SCOPES,
            )
        except Exception:
            return None

    def _save_token(self, credentials: Credentials):
        """將權杖儲存至本地"""
        os.makedirs(os.path.dirname(self._token_path), exist_ok=True)
        with open(self._token_path, "w", encoding="utf-8") as f:
            f.write(credentials.to_json())
