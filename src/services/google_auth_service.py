# -*- coding: utf-8 -*-
"""
Google API 認證服務

使用 Service Account 金鑰檔進行認證，免去使用者登入流程。
金鑰檔隨程式打包發佈，使用者無需任何認證操作。

使用範例：
    from services.google_auth_service import GoogleAuthService
    auth = GoogleAuthService()
    creds = auth.get_credentials()
"""
import os
import sys
from typing import Optional
from google.oauth2.service_account import Credentials
from core.catalog_constants import GOOGLE_SCOPES, SERVICE_ACCOUNT_FILE


def _get_app_dir() -> str:
    """取得應用程式目錄（支援 PyInstaller 打包環境）"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GoogleAuthService:
    """Google Service Account 認證管理"""

    def __init__(self, key_path: str = ""):
        self._key_path = key_path or os.path.join(
            _get_app_dir(), SERVICE_ACCOUNT_FILE,
        )
        self._credentials: Optional[Credentials] = None

    @property
    def is_authenticated(self) -> bool:
        """是否已通過認證"""
        return self._credentials is not None and self._credentials.valid

    @property
    def has_key_file(self) -> bool:
        """是否有 Service Account 金鑰檔"""
        return os.path.isfile(self._key_path)

    @property
    def key_file_path(self) -> str:
        """金鑰檔路徑"""
        return self._key_path

    def get_credentials(self) -> Optional[Credentials]:
        """取得有效的認證憑據

        從 Service Account 金鑰檔載入，過期時自動重新整理。

        Returns:
            Google 認證憑據，金鑰檔不存在時回傳 None
        """
        if self._credentials and self._credentials.valid:
            return self._credentials
        if not self.has_key_file:
            return None
        try:
            self._credentials = Credentials.from_service_account_file(
                self._key_path, scopes=GOOGLE_SCOPES,
            )
            return self._credentials
        except Exception:
            self._credentials = None
            return None
