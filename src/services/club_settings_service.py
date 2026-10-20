# -*- coding: utf-8 -*-
"""
社團設定服務

這台電腦記住譜庫的社團設定裡啟用的發譜平台，發譜功能依它顯示：連上譜庫時讀一次（read），結果記在偏好設定；
啟動時只看記住的結果（remembered），不連網、不讀安全儲存。從沒讀到過時為沒有，發譜功能隱藏。
讀不到時丟出帶類別的 CatalogError，記住的結果不變（沿用上次的）。連上譜庫時以 refresh 重讀：讀不到不顯示提示列，
只把失敗寫進紀錄檔。按下發譜時再讀一次：讀不到看錯誤類別，回傳 None 表示社團設定已拿掉發譜平台。
記住的結果寫入後（讀到了、忘掉時）通知訂閱者，主視窗與譜庫設定據以更新畫面；讀不到時不通知。
訂閱者丟出例外時寫進紀錄檔，其餘的訂閱者照常通知。

讀譜庫要經網路，read、refresh 要在背景執行緒呼叫；偏好設定與紀錄檔都可以從背景執行緒寫。
訂閱者在寫入的那個執行緒被呼叫（多半是背景執行緒），要碰畫面的訂閱者以 Qt 訊號轉回主執行緒。

使用範例：
    from services.club_settings_service import ClubSettingsService
    club = ClubSettingsService(preferences, activity_log)
    show_publish = club.remembered() is not None      # 啟動時：只看偏好設定
    club.refresh(lambda: catalog)                     # 連上譜庫時；讀不到時寫進紀錄檔，不丟出例外
    platform = club.read(catalog)                     # 連上譜庫或按下發譜時；讀不到時丟出 CatalogError
    club.forget()                                     # 譜庫設定存了另一份譜庫總表時
    club.subscribe(changed.emit)                      # 記住的結果寫入後通知（在寫入的執行緒呼叫）
"""
from typing import Callable, List, Optional

from core.catalog_constants import PublishingPlatform
from core.catalog_errors import CatalogFailure, as_catalog_error, exception_message
from core.constants import CLUB_PUBLISHING_PLATFORM_KEY
from core.locale import t
from core.log_entry import LogEntry
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.preferences_service import PreferencesService


class ClubSettingsService:
    """讀譜庫的社團設定，並把啟用的發譜平台記在偏好設定"""

    def __init__(self, preferences: PreferencesService, log: Optional[ActivityLog] = None):
        """
        Args:
            preferences: 偏好設定，記住的結果存在這裡
            log: 紀錄檔，refresh 讀不到時寫進去；省略時以使用者資料目錄下的紀錄檔資料夾建立
        """
        self._preferences = preferences
        self._log = log or ActivityLog()
        self._listeners: List[Callable[[], None]] = []

    def subscribe(self, listener: Callable[[], None]) -> None:
        """記住的結果寫入後呼叫 listener（在寫入的執行緒，多半是背景執行緒）；它丟出的例外寫進紀錄檔"""
        self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[], None]) -> None:
        """不再通知 listener（例如關閉的對話框）"""
        if listener in self._listeners:
            self._listeners.remove(listener)

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

    def refresh(self, connect: Callable[[], CatalogService]) -> None:
        """連上譜庫時重讀社團設定並記住；讀不到（含連不上譜庫）時記住的不變，把失敗寫進紀錄檔，不丟出例外

        程式的錯（不是 CatalogError 的例外）同樣歸為其他寫進紀錄檔，不會被吞掉。

        Args:
            connect: 取得譜庫服務的函式，拿不到時丟出 CatalogError
        """
        try:
            self.read(connect())
        except Exception as error:
            failure = CatalogFailure.from_error(as_catalog_error(error), t("catalog.action.read_club_settings"))
            self._log.record(failure.log_entry())

    def forget(self) -> None:
        """忘掉記住的結果（換了譜庫總表時）：發譜功能隱藏，直到讀到新譜庫的社團設定"""
        self._remember("")

    def _remember(self, value: str) -> None:
        """記在偏好設定並存檔（存不進去時這次執行期間仍記得），再通知訂閱者"""
        self._preferences.set(CLUB_PUBLISHING_PLATFORM_KEY, value)
        try:
            self._preferences.save()
        except OSError:
            pass
        # 複製一份再逐一呼叫：主執行緒同時增減訂閱者也不影響這一輪
        for listener in list(self._listeners):
            try:
                listener()
            except Exception as error:
                # 一個訂閱者出錯（例如已刪掉的對話框）不影響其他訂閱者，錯誤寫進紀錄檔
                self._log.record(LogEntry(t("catalog.action.read_club_settings"), message=exception_message(error)))
