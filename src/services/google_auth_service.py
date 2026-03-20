# -*- coding: utf-8 -*-
"""
Google API 認證服務

處理 OAuth2 認證流程，管理存取權杖。

使用範例：
    from services.google_auth_service import GoogleAuthService
    auth = GoogleAuthService()
    creds = auth.get_credentials()
"""
import os
from typing import Optional
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from core.catalog_constants import GOOGLE_SCOPES, TOKEN_FILE, CREDENTIALS_FILE
from core.constants import APPDATA_DIR


class GoogleAuthService:
    """Google OAuth2 認證管理"""

    def __init__(self, credentials_path: str = ""):
        self._credentials_path = credentials_path or os.path.join(
            APPDATA_DIR, CREDENTIALS_FILE,
        )
        self._token_path = os.path.join(APPDATA_DIR, TOKEN_FILE)
        self._credentials: Optional[Credentials] = None

    @property
    def is_authenticated(self) -> bool:
        """是否已通過認證"""
        return self._credentials is not None and self._credentials.valid

    @property
    def has_credentials_file(self) -> bool:
        """是否有 OAuth 憑證檔案"""
        return os.path.isfile(self._credentials_path)

    def get_credentials(self) -> Optional[Credentials]:
        """取得有效的認證憑據

        嘗試從快取載入，過期時自動重新整理。

        Returns:
            Google 認證憑據，未認證時回傳 None
        """
        if self._credentials and self._credentials.valid:
            return self._credentials
        self._credentials = self._load_token()
        if self._credentials and self._credentials.expired and self._credentials.refresh_token:
            try:
                self._credentials.refresh(Request())
                self._save_token(self._credentials)
            except Exception:
                self._credentials = None
        return self._credentials if self._credentials and self._credentials.valid else None

    def authenticate(self) -> Credentials:
        """執行完整的 OAuth2 認證流程

        開啟瀏覽器讓使用者授權，取得存取權杖。

        Returns:
            Google 認證憑據

        Raises:
            FileNotFoundError: 找不到 OAuth 憑證檔案
        """
        if not self.has_credentials_file:
            raise FileNotFoundError(
                f"找不到 Google OAuth 憑證檔案：{self._credentials_path}\n"
                "請從 Google Cloud Console 下載 OAuth 2.0 用戶端憑證，"
                f"並儲存至 {self._credentials_path}",
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            self._credentials_path, GOOGLE_SCOPES,
        )
        self._credentials = flow.run_local_server(port=0)
        self._save_token(self._credentials)
        return self._credentials

    def logout(self):
        """登出並清除快取的權杖"""
        self._credentials = None
        if os.path.isfile(self._token_path):
            os.remove(self._token_path)

    def _load_token(self) -> Optional[Credentials]:
        """從檔案載入快取的權杖"""
        if not os.path.isfile(self._token_path):
            return None
        try:
            return Credentials.from_authorized_user_file(
                self._token_path, GOOGLE_SCOPES,
            )
        except Exception:
            return None

    def _save_token(self, credentials: Credentials):
        """將權杖存入檔案"""
        os.makedirs(os.path.dirname(self._token_path), exist_ok=True)
        with open(self._token_path, "w", encoding="utf-8") as f:
            f.write(credentials.to_json())
