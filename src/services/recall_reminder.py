# -*- coding: utf-8 -*-
"""
收回提醒的摘要

開泠靈時不連 HuaXia、不讀安全儲存，所以收回提醒依這台電腦記在偏好設定的摘要判斷（spec #82）：每次讀寫發譜紀錄時，
把「已發譜、還沒收回也沒標不收回的曲目，與它們掛的公演結束時間」重算一次記下（core/publish_log.py 的
reminder_summary），開泠靈時只讀它（due）。別台電腦的發譜要等這台讀過一次發譜紀錄才會被提醒。

- 讀發譜紀錄的地方（收回的預覽、譜庫視窗的發譜紀錄）以 read 讀，讀到就記下。
- 寫發譜紀錄的地方（發譜、收回、不收回、撤回）寫完後以 refresh 重讀一次記下；讀不到時摘要不變，把失敗寫進紀錄檔，
  不丟出例外（寫入已經做完）。
偏好設定可以從背景執行緒寫；read、refresh 經網路，要在背景執行緒呼叫。

使用範例：
    from services.recall_reminder import RecallReminder
    reminder = RecallReminder(preferences, activity_log)
    titles = reminder.due()          # 開泠靈時：公演都已結束、還沒收回的曲名（只讀偏好設定）
    records = reminder.read(catalog)  # 讀發譜紀錄並記下
    reminder.refresh(catalog)         # 寫完發譜紀錄後
"""
import datetime
from typing import Callable, List, Optional, Sequence

from core.catalog_errors import CatalogError, CatalogFailure
from core.constants import RECALL_REMINDER_KEY
from core.locale import t
from core.publish_log import PublishRecord, due_reminders, reminder_summary, utc_now
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.preferences_service import PreferencesService


class RecallReminder:
    """把收回提醒的摘要記在偏好設定，開泠靈時依它判斷要提醒哪幾首"""

    def __init__(
        self, preferences: PreferencesService, log: Optional[ActivityLog] = None,
        now: Callable[[], datetime.datetime] = utc_now,
    ):
        """
        Args:
            preferences: 偏好設定，摘要存在這裡
            log: 紀錄檔，refresh 讀不到時寫進去；省略時不寫
            now: 現在的時間（帶時區），判斷公演結束與否；測試傳入固定的時間
        """
        self._preferences = preferences
        self._log = log
        self._now = now

    def due(self) -> List[str]:
        """要提醒收回的曲名：有掛公演、掛的公演都已結束、還沒收回也沒標不收回（只讀偏好設定）"""
        return due_reminders(self._preferences.get(RECALL_REMINDER_KEY), self._now())

    def remember(self, records: Sequence[PublishRecord]) -> None:
        """以發譜紀錄的全部列重算摘要並存檔；存不進去時這次執行期間仍記得

        Args:
            records: 發譜紀錄的全部列，依紀錄的順序
        """
        self._preferences.set(RECALL_REMINDER_KEY, reminder_summary(records))
        try:
            self._preferences.save()
        except OSError:
            pass

    def read(self, catalog: CatalogService) -> List[PublishRecord]:
        """讀發譜紀錄的全部列並記下摘要

        Args:
            catalog: 譜庫服務

        Returns:
            發譜紀錄的全部列（CatalogService.publish_log）

        Raises:
            CatalogError: 讀不到；摘要不變
        """
        records = catalog.publish_log()
        self.remember(records)
        return records

    def refresh(self, catalog: CatalogService) -> None:
        """寫完發譜紀錄後重讀並記下；讀不到時摘要不變，把失敗寫進紀錄檔，不丟出例外

        Args:
            catalog: 剛寫入的譜庫服務
        """
        try:
            self.read(catalog)
        except CatalogError as error:
            if self._log is not None:
                self._log.record(CatalogFailure.from_error(error, t("catalog.action.read_publish_log")).log_entry())
