# -*- coding: utf-8 -*-
"""
社團設定服務

這台電腦記住譜庫的社團設定裡啟用的發譜平台，發譜功能依它顯示：連上譜庫時讀一次（read），結果記在偏好設定；
啟動時只看記住的結果（remembered），不連網、不讀安全儲存。從沒讀到過時為沒有，發譜功能隱藏。
讀不到時丟出帶類別的 CatalogError，記住的結果不變（沿用上次的）。按下發譜時再讀一次：讀不到看錯誤類別，
回傳 None 表示社團設定已拿掉發譜平台。

讀譜庫要經網路，read 要在背景執行緒呼叫；偏好設定可以從背景執行緒改與存。

使用範例：
    from services.club_settings_service import ClubSettingsService
    club = ClubSettingsService(preferences)
    show_publish = club.remembered() is not None      # 啟動時：只看偏好設定
    platform = club.read(catalog)                     # 連上譜庫或按下發譜時；讀不到時丟出 CatalogError
    club.forget()                                     # 譜庫設定換了譜庫試算表時
"""
from typing import Optional

from core.catalog_constants import PublishingPlatform
from core.constants import CLUB_PUBLISHING_PLATFORM_KEY
from services.catalog_service import CatalogService
from services.preferences_service import PreferencesService


class ClubSettingsService:
    """讀譜庫的社團設定，並把啟用的發譜平台記在偏好設定"""

    def __init__(self, preferences: PreferencesService):
        """
        Args:
            preferences: 偏好設定，記住的結果存在這裡
        """
        self._preferences = preferences

    def remembered(self) -> Optional[PublishingPlatform]:
        """這台電腦記住的發譜平台；沒有或從沒讀到過時為 None（隱藏發譜功能）"""
        value = self._preferences.get(CLUB_PUBLISHING_PLATFORM_KEY)
        try:
            return PublishingPlatform(value)
        except ValueError:
            return None

    def read(self, catalog: CatalogService) -> Optional[PublishingPlatform]:
        """重新讀譜庫的社團設定並記住結果

        偏好設定寫不進去時，這次執行期間仍記得，下次啟動回到上次存進去的結果。

        Args:
            catalog: 連上譜庫的譜庫服務

        Returns:
            啟用的發譜平台；社團設定沒有啟用（沒有這張工作表、沒有這一列或已拿掉）時為 None

        Raises:
            CatalogError: 讀不到；記住的結果不變
        """
        platform = catalog.publishing_platform()
        self._remember(platform.value if platform else "")
        return platform

    def forget(self) -> None:
        """忘掉記住的結果（換了譜庫試算表時）：發譜功能隱藏，直到讀到新譜庫的社團設定"""
        self._remember("")

    def _remember(self, value: str) -> None:
        """記在偏好設定並存檔；存不進去時這次執行期間仍記得"""
        self._preferences.set(CLUB_PUBLISHING_PLATFORM_KEY, value)
        try:
            self._preferences.save()
        except OSError:
            pass
