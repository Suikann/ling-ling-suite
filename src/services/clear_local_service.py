# -*- coding: utf-8 -*-
"""
清除本機檔案

確認譜庫裡有內容相同的一份之後，永久刪掉本機的總譜與分譜，連同清空後變空的資料夾：
preview 讀本機檔案的 MD5 與譜庫各處登記的 Drive 檔，交回每份檔的歸類（規則在 core/clear_local.py）；
clear 只刪勾了的群組裡可清除的檔，刪之前再算一次 MD5，預覽之後內容變了的不刪。
這是泠靈唯一的永久刪除（FileService.delete_permanently），其他刪除照舊走資源回收桶；不放上復原堆疊。

清除後本機沒有、群組記著存入指紋的檔就是「只在譜庫」（Group.has_catalog_copy），不另外存；
clear 交回每份清除了的檔的指紋，呼叫端交給 Project.record_fingerprints，沒經過泠靈存入的檔也認得出來。

讀不到譜庫（例如斷網）時 preview 丟出 CatalogError，什麼都不刪。網路請求與讀檔會卡住呼叫的執行緒，
UI 要在背景執行緒呼叫。

使用範例：
    from services.clear_local_service import ClearLocalService
    service = ClearLocalService(file_service, log)
    plan = service.preview(project, catalog)
    outcome = service.clear(plan, group_ids, should_cancel, report)
    project.record_fingerprints(outcome.fingerprints)
"""
import os
from dataclasses import dataclass, field
from typing import Callable, Collection, Dict, List, NamedTuple, Optional, Sequence, Tuple

from core.clear_local import (
    ClearItem, ClearPlan, ClearStatus, GroupClear, classify, clear_sources, emptied_folders, standard_user_folders,
)
from core.deposit import PlaceFiles
from core.locale import t
from core.log_entry import LogEntry
from core.models import Project
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.file_service import FileService


class ClearProgress(NamedTuple):
    """清除的進度：做完幾份、一共幾份、正在刪的檔名"""
    done: int
    total: int
    file_name: str


@dataclass(frozen=True)
class ClearOutcome:
    """一次清除的結果

    Attributes:
        cleared: 永久刪掉了的檔
        total: 要刪的份數
        changed: 預覽之後本機內容變了、沒刪的檔
        failed: 刪不掉的檔與原因
        cancelled: 是否因為要求取消而停下
    """
    cleared: List[ClearItem] = field(default_factory=list)
    total: int = 0
    changed: List[ClearItem] = field(default_factory=list)
    failed: List[Tuple[ClearItem, str]] = field(default_factory=list)
    cancelled: bool = False

    @property
    def fingerprints(self) -> Dict[str, str]:
        """清除了的每份檔的指紋：本機路徑 → 譜庫裡那份內容的 MD5"""
        return {item.path: item.md5 for item in self.cleared}

    @property
    def freed(self) -> int:
        """釋放了多少空間（位元組）"""
        return sum(item.size for item in self.cleared)


class ClearLocalService:
    """清除本機檔案的預覽與執行"""

    def __init__(
        self, file_service: FileService, log: Optional[ActivityLog] = None, home: Optional[str] = None,
        standard_folders: Sequence[str] = (),
    ):
        """
        Args:
            file_service: 讀本機檔案（MD5、大小）、永久刪檔與刪空資料夾
            log: 紀錄檔，清除的每份檔寫一筆；省略時不寫
            home: 家目錄；省略時為目前使用者的家目錄
            standard_folders: 系統的標準使用者資料夾（例如在地化名稱的桌面、文件），連同家目錄底下英文名稱的那些都不刪
        """
        self._files = file_service
        self._log = log
        home = home if home is not None else os.path.expanduser("~")
        self._protected = standard_user_folders(home) + list(standard_folders)

    def preview(self, project: Project, catalog: CatalogService) -> ClearPlan:
        """算出清除計畫：本機檔案的 MD5 與大小，加上譜庫各處登記的 Drive 檔

        Args:
            project: 專案
            catalog: 譜庫服務

        Returns:
            每個群組各一個，依專案的順序

        Raises:
            CatalogError: 讀不到譜庫（例如斷網）
        """
        groups = clear_sources(project)
        local: Dict[str, Tuple[str, int]] = {}
        for sources in groups:
            for source in sources.sources:
                try:
                    local[source.path] = (self._files.md5(source.path), self._files.file_size(source.path))
                except OSError:
                    continue
        keys = sorted({
            (g.link.edition_id, g.link.movement_id) for g in groups
            if g.link is not None and any(source.path in local for source in g.sources)
        })
        places: Dict[Tuple[str, str], PlaceFiles] = catalog.deposit_places(keys) if keys else {}
        plan = []
        for sources in groups:
            place = places.get((sources.link.edition_id, sources.link.movement_id)) if sources.link else None
            items = []
            for source in sources.sources:
                if source.path not in local:
                    status = ClearStatus.CATALOG_ONLY if source.catalog_copy else ClearStatus.MISSING
                    items.append(ClearItem(source, status))
                    continue
                md5, size = local[source.path]
                items.append(ClearItem(source, classify(source, md5, place), md5, size))
            plan.append(GroupClear(sources, items))
        return ClearPlan(plan)

    def clear(
        self, plan: ClearPlan, group_ids: Collection[str], should_cancel: Callable[[], bool],
        report: Callable[[ClearProgress], None],
    ) -> ClearOutcome:
        """永久刪掉勾了的群組裡可清除的檔，再刪掉因此變空的資料夾（往上到輸出位置為止）

        每份刪之前再算一次 MD5，跟預覽時不同就不刪；要求取消時做完目前這一份就停。

        Args:
            plan: preview 算出的計畫
            group_ids: 勾了的群組
            should_cancel: 是否已要求取消
            report: 回報進度

        Returns:
            清除的結果；刪不掉的檔不丟出，記在 failed
        """
        work = plan.chosen(group_ids)
        cleared: List[ClearItem] = []
        changed: List[ClearItem] = []
        failed: List[Tuple[ClearItem, str]] = []
        cancelled = False
        for done, item in enumerate(work):
            if should_cancel():
                cancelled = True
                break
            report(ClearProgress(done, len(work), os.path.basename(item.path)))
            try:
                if self._files.md5(item.path) != item.md5:
                    changed.append(item)
                    continue
                self._files.delete_permanently(item.path)
            except OSError as e:
                failed.append((item, str(e)))
                continue
            cleared.append(item)
            self._record_in_log(plan, item)
        self._remove_emptied_folders(cleared)
        return ClearOutcome(cleared, len(work), changed, failed, cancelled)

    def _remove_emptied_folders(self, cleared: List[ClearItem]) -> None:
        """刪掉清除後變空的資料夾，由深到淺；還有東西的不刪"""
        folders = {
            folder for item in cleared
            for folder in emptied_folders(item.path, item.source.location, self._protected)
        }
        for folder in sorted(folders, key=lambda f: (-f.count(os.sep), f)):
            self._files.remove_empty_directory(folder)

    def _record_in_log(self, plan: ClearPlan, item: ClearItem) -> None:
        """清除的一份檔寫一筆紀錄檔"""
        if self._log is None:
            return
        group = next(g for g in plan.groups if item in g.items)
        link = group.sources.link
        self._log.record(LogEntry(
            t("clear.log.action"), piece_title=link.piece_title if link else "",
            file_name=os.path.basename(item.path), path=item.path,
        ))
