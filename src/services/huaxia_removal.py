# -*- coding: utf-8 -*-
"""
撤回與收回共用的步驟

撤回（services/retract_service.py）與收回（services/recall_service.py）照同一個骨架，以幹部身分經 REST 拿掉 HuaXia
上的東西：重查幹部身分（不是幹部就什麼都不做）→ 撤掉樂譜的登記、把分部改回獨立聲部、刪聲部（分成幾個請求，中途失敗時
已經做到的照樣交回）
→ 刪 Storage 的檔（orphan_cleanup.delete_unregistered）→ 把做到的寫進發譜紀錄（寫不進去不丟出，交回錯誤）並更新
收回提醒的摘要 → 寫紀錄檔。這裡是那些共用的步驟；要拿掉哪些、紀錄寫哪幾列由各自決定。
網路請求會卡住呼叫的執行緒，要在背景執行緒呼叫。

使用範例：
    from services.huaxia_removal import HuaXiaRemoval
    removal = HuaXiaRemoval(huaxia_auth, log, reminder)
    if removal.recheck() is None:
        removed = []
        removal.remove_scores(score_ids, removed)   # 失敗時丟出 HuaXiaError，removed 是失敗前撤掉的
        log_error = removal.write_log(catalog, lambda operator: catalog.begin_publish(records, operator))
"""
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.catalog_errors import CatalogError
from core.locale import t
from core.log_entry import LogEntry
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.huaxia_auth_service import HuaXiaAuthService, NotOfficer
from services.huaxia_client import HuaXiaError, HuaXiaPart
from services.orphan_cleanup import StorageDeletion, delete_unregistered
from services.recall_reminder import RecallReminder


def error_message(error: HuaXiaError) -> str:
    """HuaXia 錯誤寫進紀錄檔與發譜紀錄的說明：錯誤代碼與說明（沒有的略過）"""
    return " ".join(part for part in (error.code, error.detail or str(error)) if part)


class HuaXiaRemoval:
    """撤回與收回共用的步驟：重查、撤登記、改回獨立聲部、刪聲部、刪檔、寫發譜紀錄並更新收回提醒、寫紀錄檔"""

    def __init__(
        self, auth: HuaXiaAuthService, log: Optional[ActivityLog] = None, reminder: Optional[RecallReminder] = None,
    ):
        """
        Args:
            auth: HuaXia 登入服務（全程式一份）；請求經它的用戶端送出
            log: 紀錄檔；省略時不寫
            reminder: 收回提醒的摘要，寫完發譜紀錄後重讀一次記下；省略時不記
        """
        self._auth = auth
        self._client = auth.client
        self._log = log
        self._reminder = reminder

    def recheck(self) -> Optional[NotOfficer]:
        """重查幹部身分；不是幹部時已登出，交回分類

        Raises:
            HuaXiaError: 沒有登入、登入已失效或連不上 HuaXia
        """
        return self._auth.recheck_officer()

    def remove_scores(self, score_ids: List[str], removed: List[str]) -> None:
        """撤掉這些樂譜的登記，真的撤掉的編號接在 removed 後面（中途失敗時是失敗前撤掉的）

        Raises:
            HuaXiaError: 連不上或刪除失敗
        """
        self._remove(self._client.delete_scores, score_ids, removed)

    def detach_divisi(self, divisi: List[HuaXiaPart], detached: List[str]) -> None:
        """把這些分部改回獨立聲部（只動現在還屬於它的 parent_id 的），真的改回的編號接在 detached 後面

        Raises:
            HuaXiaError: 連不上或修改失敗
        """
        parents: Dict[str, List[str]] = {}
        for part in divisi:
            parents.setdefault(part.parent_id, []).append(part.id)
        for parent_id, part_ids in parents.items():
            self._remove(
                lambda token, ids, parent_id=parent_id: self._client.detach_divisi(token, parent_id, ids),
                part_ids, detached,
            )

    def remove_parts(self, part_ids: List[str], removed: List[str]) -> None:
        """刪掉這些聲部，真的刪掉的編號接在 removed 後面

        HuaXia 會連帶刪掉它們的分部、排人與樂譜，呼叫前先確認。

        Raises:
            HuaXiaError: 連不上或刪除失敗
        """
        self._remove(self._client.delete_parts, part_ids, removed)

    def remove_files(self, paths: Iterable[str]) -> StorageDeletion:
        """刪掉這些 Storage 檔裡沒有任何登記指著的（orphan_cleanup.delete_unregistered）

        Returns:
            刪掉的、還在用的與 Storage 沒刪掉的

        Raises:
            HuaXiaError: 連不上、讀不到登記或刪除失敗
        """
        return delete_unregistered(self._auth, paths)

    def write_log(self, catalog: CatalogService, write: Callable[[str], Any]) -> Optional[CatalogError]:
        """以登入的 Discord 名稱為操作者寫發譜紀錄（write），再更新收回提醒的摘要

        Args:
            catalog: 譜庫服務
            write: 以操作者呼叫的寫入（例如 catalog.begin_publish(records, operator)）

        Returns:
            寫不進譜庫時的錯誤（HuaXia 已經變動）；寫進去了為 None
        """
        error = None
        try:
            write(self._auth.status().discord_name)
        except CatalogError as e:
            error = e
        if self._reminder is not None:
            self._reminder.refresh(catalog)
        return error

    def record(self, done_key: str, stopped_key: str, error: Optional[HuaXiaError], **fields: str) -> None:
        """寫一筆紀錄檔：做完了（done_key），或中途停下（stopped_key）與原因；不記 HuaXia 傳回的團員資料

        Args:
            done_key: 做完了的介面字串鍵
            stopped_key: 中途停下的介面字串鍵
            error: 中途停下的原因；做完了為 None
            fields: LogEntry 的其他欄（piece_title、file_name）
        """
        if self._log is None:
            return
        if error is None:
            self._log.record(LogEntry(t(done_key), **fields))
        else:
            self._log.record(LogEntry(t(stopped_key), message=error_message(error), **fields))

    def _remove(self, write: Callable[[str, List[str]], List[str]], ids: List[str], done: List[str]) -> None:
        """經 write 分批刪掉或修改，真的動到的接在 done 後面；中途失敗時接上失敗前動到的再丟出"""
        try:
            done.extend(write(self._auth.access_token(), ids))
        except HuaXiaError as e:
            done.extend(e.done)
            raise
