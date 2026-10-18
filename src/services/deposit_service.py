# -*- coding: utf-8 -*-
"""
存入譜庫

把專案裡已重新命名、已連到譜庫的群組的總譜與分譜存進譜庫，本機那份不動：
preview 讀本機檔案的 MD5 與譜庫那處現在的登記、版本資料夾裡的檔，交回每份檔的動作（規則在 core/deposit.py）；
deposit 照計畫一份一份上傳、更新或搬移，做完（或取消、中途失敗）後以一次試算表寫入把做好的那些登記到分譜列，
並各寫一列存入紀錄，所以請求數不隨聲部數增加。碰譜庫一律經譜庫服務（CatalogService）。
undeposited 拿本機檔案的 MD5 比對存入指紋，找出存入後本機又改過的檔（未存入），不連網。

不會產生重複的檔與列：新上傳以預先向 Drive 要的 ID 建檔，重送時 Drive 回已經存在；中斷後重按時重新預覽，
已登記且內容相同的跳過，上傳了但還沒登記的是目標資料夾裡的同名檔，內容相同就直接登記。

網路請求與讀檔會卡住呼叫的執行緒，UI 要在背景執行緒呼叫。

使用範例：
    from services.deposit_service import DepositService
    service = DepositService(file_service, log)
    plan = service.preview(project, catalog)
    outcome = service.deposit(plan, catalog, email, should_cancel, report)
    project.record_deposit(outcome.records)
    stale = service.undeposited(project)
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, NamedTuple, Optional, Set, Tuple

from core.catalog_errors import CatalogError, CatalogErrorKind, exception_message
from core.deposit import (
    DepositAction, DepositBlock, DepositItem, DepositPlan, DepositRecord, GroupDeposit, PlaceFiles, deposit_sources,
    folder_key, plan_group,
)
from core.locale import t
from core.log_entry import LogEntry
from core.models import FileRef, Project
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.file_service import FileService


class DepositProgress(NamedTuple):
    """存入的進度：做完幾份、一共幾份、正在做的檔名"""
    done: int
    total: int
    file_name: str


@dataclass(frozen=True)
class DepositOutcome:
    """一次存入的結果

    Attributes:
        records: 存進譜庫並登記了的每份檔
        total: 計畫裡要做的份數
        fingerprints: 存進譜庫的每份檔的指紋：本機路徑 → 譜庫裡那份內容的 MD5
        error: 中途失敗的錯誤；沒有時為 None
        cancelled: 是否因為要求取消而停下
    """
    records: List[DepositRecord] = field(default_factory=list)
    total: int = 0
    fingerprints: Dict[str, str] = field(default_factory=dict)
    error: Optional[CatalogError] = None
    cancelled: bool = False

    @property
    def completed(self) -> int:
        """已完成幾份"""
        return len(self.records)


# 一份檔的去處：（版本資料夾的 ID，子資料夾的比對鍵）
_FolderSpot = Tuple[str, Tuple[str, ...]]


class DepositService:
    """存入譜庫的預覽與執行"""

    def __init__(self, file_service: FileService, log: Optional[ActivityLog] = None):
        """
        Args:
            file_service: 讀本機檔案（MD5、大小、內容）
            log: 紀錄檔，存入的每份檔寫一筆；省略時不寫
        """
        self._files = file_service
        self._log = log
        # 算過的 MD5：本機路徑 → （算的時候的大小與修改時間，MD5）
        self._md5s: Dict[str, Tuple[Tuple[int, int], str]] = {}

    def undeposited(self, project: Project) -> List[FileRef]:
        """未存入的檔：存入過（有存入指紋），本機檔案的 MD5 卻跟指紋不一致；只讀本機檔案，不碰譜庫

        大小與修改時間沒變的檔沿用上次算的 MD5，重畫群組面板時不必每份重讀。

        Args:
            project: 專案

        Returns:
            依 Project.file_refs 的順序；本機找不到的檔不算
        """
        stale = []
        for ref in project.file_refs():
            if not ref.file.deposit_md5:
                continue
            try:
                current = self._md5(ref.file.original_path)
            except OSError:
                continue
            if current != ref.file.deposit_md5:
                stale.append(ref)
        return stale

    def _md5(self, path: str) -> str:
        """本機檔案的 MD5；大小與修改時間跟上次算的時候一樣就沿用

        Raises:
            OSError: 檔案讀不到
        """
        stamp = self._files.stamp(path)
        cached = self._md5s.get(path)
        if cached is None or cached[0] != stamp:
            cached = (stamp, self._files.md5(path))
            self._md5s[path] = cached
        return cached[1]

    def preview(self, project: Project, catalog: CatalogService) -> DepositPlan:
        """算出存入計畫：本機檔案的 MD5 與大小，加上譜庫各處現在的登記與版本資料夾裡的檔

        Args:
            project: 專案
            catalog: 譜庫服務

        Returns:
            每個群組各一個，依專案的順序；本機有找不到的檔的群組整個不存入

        Raises:
            CatalogError: 讀不到譜庫
        """
        groups = []
        local: Dict[str, Tuple[str, int]] = {}
        for sources in deposit_sources(project):
            if sources.block is None:
                try:
                    local.update({
                        source.local_path: (self._files.md5(source.local_path), self._files.file_size(source.local_path))
                        for source in sources.sources
                    })
                except OSError:
                    sources = sources.blocked(DepositBlock.MISSING_FILES)
            groups.append(sources)
        keys = sorted({(g.link.edition_id, g.link.movement_id) for g in groups if g.block is None})
        places = catalog.deposit_places(keys) if keys else {}
        return DepositPlan([
            plan_group(g, local, places.get((g.link.edition_id, g.link.movement_id)) if g.link else None)
            for g in groups
        ])

    def deposit(
        self, plan: DepositPlan, catalog: CatalogService, operator: str, should_cancel: Callable[[], bool],
        report: Callable[[DepositProgress], None],
    ) -> DepositOutcome:
        """照計畫存入：一份一份上傳、更新或搬移，最後一次登記做好的那些並寫存入紀錄

        要求取消時做完目前這一份就停；中途失敗也停下。兩種情況都照樣登記已經做好的，再按一次（重新預覽）就補齊。
        登記不進去時這次做好的都不算完成（Drive 上的檔留著，下次預覽時以同名檔認出來直接登記）。

        Args:
            plan: preview 算出的計畫
            catalog: 譜庫服務
            operator: 操作者（登入的 email），寫進存入紀錄
            should_cancel: 是否已要求取消
            report: 回報進度

        Returns:
            存入的結果；失敗不丟出，記在 error
        """
        work = plan.work()
        done: List[Tuple[GroupDeposit, DepositRecord]] = []
        error: Optional[CatalogError] = None
        cancelled = False
        try:
            fresh = sum(1 for _, item in work if item.action is DepositAction.NEW) + len(_missing_folders(work))
            ids = catalog.drive_ids(fresh) if fresh else []
            made: Dict[_FolderSpot, str] = {}
            for group, item in work:
                if should_cancel():
                    cancelled = True
                    break
                report(DepositProgress(len(done), len(work), item.source.file_name))
                done.append((group, self._put(catalog, group, item, ids, made)))
        except CatalogError as e:
            error = e
        except OSError as e:
            error = CatalogError(CatalogErrorKind.OTHER, exception_message(e))
        if done:
            try:
                catalog.record_deposits([record for _, record in done], operator)
            except CatalogError as e:
                error = error or e
                done = []
        for group, record in done:
            self._record_in_log(group, record)
        records = [record for _, record in done]
        return DepositOutcome(
            records=records, total=len(work), error=error, cancelled=cancelled,
            fingerprints={record.local_path: record.drive_file.md5 for record in records},
        )

    def _put(
        self, catalog: CatalogService, group: GroupDeposit, item: DepositItem, ids: List[str],
        made: Dict[_FolderSpot, str],
    ) -> DepositRecord:
        """存入一份檔：放好它的子資料夾，再依動作上傳、更新、搬移或只是登記"""
        source = item.source
        folder_id = self._folder(catalog, group.place, source.folders, ids, made)
        target = item.target
        if item.action is DepositAction.NEW:
            file = catalog.upload_file(ids.pop(0), source.file_name, folder_id, self._files.read_bytes(source.local_path))
        elif item.action is DepositAction.UPDATE:
            file = catalog.update_file(target, self._files.read_bytes(source.local_path), source.file_name, folder_id)
        else:
            file = catalog.place_file(target, source.file_name, folder_id)
        link = group.sources.link
        return DepositRecord(
            link.edition_id, link.movement_id, group.sources.group_id, group.sources.group_name, source.voice,
            group.sources.score_label,
            source.local_path, file, item.action, target.head_revision if target is not None else "",
        )

    @staticmethod
    def _folder(
        catalog: CatalogService, place: PlaceFiles, folders: Tuple[str, ...], ids: List[str],
        made: Dict[_FolderSpot, str],
    ) -> str:
        """版本資料夾下這條子資料夾路徑的 ID，還沒有的那幾層以預先要到的 ID 建立"""
        parent = place.folder_id
        for depth in range(1, len(folders) + 1):
            key = folder_key(folders[:depth])
            existing = place.folders.get(key) or made.get((place.folder_id, key))
            if existing is None:
                existing = catalog.create_folder(folders[depth - 1], parent, ids.pop(0)).id
                made[(place.folder_id, key)] = existing
            parent = existing
        return parent

    def _record_in_log(self, group: GroupDeposit, record: DepositRecord) -> None:
        """存入的一份檔寫一筆紀錄檔"""
        if self._log is None:
            return
        self._log.record(LogEntry(
            t("deposit.log.action", action=t(f"deposit.action.{record.action.value}")),
            piece_title=group.sources.link.piece_title, file_name=record.drive_file.name, path=record.local_path,
        ))


def _missing_folders(work: List[Tuple[GroupDeposit, DepositItem]]) -> Set[_FolderSpot]:
    """要存入的檔用到、版本資料夾裡還沒有的子資料夾（每一層各算一個）"""
    missing = set()
    for group, item in work:
        place = group.place
        for depth in range(1, len(item.source.folders) + 1):
            key = folder_key(item.source.folders[:depth])
            if key not in place.folders:
                missing.add((place.folder_id, key))
    return missing
