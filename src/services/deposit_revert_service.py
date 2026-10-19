# -*- coding: utf-8 -*-
"""
撤銷存入

從存入紀錄挑一次存入、再挑其中的群組，撤掉那次的結果（每份檔的下場怎麼決定在 core/deposit_revert.py）：
preview 讀譜庫現況與本機有哪些檔，交回每個群組每份檔的下場與不撤銷的群組（撤銷會毀掉唯一的一份）；
revert 照選的群組逐一去做，碰譜庫一律經譜庫服務（CatalogService）：

0. 預覽時就不撤銷的群組（RevertBlock）不做，交回原因
1. 群組裡要撤銷、本機已清除的檔，先以「從譜庫下載」的同一套做法（DownloadService.put_back）下載回原位並核對 MD5；
   有任何一份沒放回去（下載失敗、MD5 不符、原位有別的檔），這個群組就不撤銷，交回原因，其他群組照常。
   本機留著的檔再看一次還在不在，預覽之後不見的，這個群組也不撤銷（存入之後改過內容的照常撤銷）
2. 逐份動 Drive：動之前重讀那份檔，版本或垃圾桶狀態跟預覽時不同（預覽之後在 Drive 上改過）的不動、交回；
   那次新上傳的丟進垃圾桶（不永久刪除），那次更新內容的還原成存入前的版本，只登記的不動 Drive
3. 做完（或取消、中途失敗）後以一次試算表寫入移除登記，並每份撤掉的檔寫一列存入紀錄
4. 選了「一起撤掉編制」的群組，做完之後把撤得掉的編制以寫回譜庫的同一套比對寫進那處（編制寫入紀錄的來源記撤銷）

要求取消時做完目前這一份就停；中途失敗也停下。已做的照樣寫進存入紀錄，再撤銷一次時已撤掉的不再列為要做的。
撤掉的每份檔寫一筆紀錄檔。網路請求與讀寫檔會卡住呼叫的執行緒，UI 要在背景執行緒呼叫 preview 與 revert。

使用範例：
    from services.deposit_revert_service import DepositRevertService
    service = DepositRevertService(file_service, download_service, log)
    preview = service.preview(deposit_id, catalog, project.orchestra_type, LocalProject.of(project, catalog_only))
    outcome = service.revert(preview, [RevertChoice(group, True)], catalog, email, orchestra_type, should_cancel, report)
"""
from dataclasses import dataclass, field
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from core.catalog_download import DownloadItem
from core.catalog_errors import CatalogError
from core.catalog_revert import pending_revert, revert_count
from core.catalog_sync import WriteSource, revert_write
from core.constants import OrchestraType
from core.deposit_revert import (
    LocalProject, RevertAction, RevertBlock, RevertChoice, RevertFile, RevertGroup, RevertPreview, revert_preview,
)
from core.locale import t
from core.log_entry import LogEntry
from core.paths import path_key
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.download_service import DownloadOutcome, DownloadProgress, DownloadService
from services.file_service import FileService


class RevertProgress(NamedTuple):
    """撤銷存入的進度：做完幾份（下載回原位與動 Drive 都算）、一共幾份、正在做的檔名"""
    done: int
    total: int
    file_name: str


@dataclass(frozen=True)
class RevertOutcome:
    """一次撤銷存入的結果

    Attributes:
        done: 撤掉了的每份檔（已寫進存入紀錄）
        skipped: 本機已清除的檔沒全部下載回來而不撤銷的群組，與那次下載的結果（原因）
        refused: 撤銷會毀掉唯一的一份而不撤銷的群組與原因（預覽時就是，或預覽之後本機那份改過、不見了）
        changed: 預覽之後 Drive 上的那份又變了（版本或垃圾桶狀態不同）而沒撤的檔
        fingerprints: 下載回原位的每份檔的指紋：本機路徑 → 譜庫那份的 MD5
        instrumentation: 撤掉了編制的群組
        total: 要做的份數（下載回原位與動 Drive）
        error: 中途失敗的錯誤；沒有時為 None
        cancelled: 是否因為要求取消而停下
    """
    done: List[RevertFile] = field(default_factory=list)
    skipped: List[Tuple[RevertGroup, DownloadOutcome]] = field(default_factory=list)
    refused: List[Tuple[RevertGroup, RevertBlock]] = field(default_factory=list)
    changed: List[RevertFile] = field(default_factory=list)
    fingerprints: Dict[str, str] = field(default_factory=dict)
    instrumentation: List[RevertGroup] = field(default_factory=list)
    total: int = 0
    error: Optional[CatalogError] = None
    cancelled: bool = False

    @property
    def completed(self) -> int:
        """撤掉了幾份"""
        return len(self.done)

    @property
    def reverted_paths(self) -> List[str]:
        """撤掉了的檔的本機路徑（譜庫不再有這份內容，專案要拿掉它們的存入指紋）"""
        return [file.local_path for file in self.done]


class DepositRevertService:
    """撤銷存入的預覽與執行"""

    def __init__(self, file_service: FileService, downloads: DownloadService, log: Optional[ActivityLog] = None):
        """
        Args:
            file_service: 查本機留著的檔還在不在
            downloads: 下載服務（本機已清除的檔先下載回原位）
            log: 紀錄檔，撤掉的每份檔寫一筆；省略時不寫
        """
        self._files = file_service
        self._downloads = downloads
        self._log = log

    def preview(
        self, deposit_id: str, catalog: CatalogService, orchestra_type: OrchestraType, local: LocalProject,
    ) -> RevertPreview:
        """讀譜庫現況與本機有哪些檔，算出撤銷那次存入時每個群組每份檔的下場、撤得掉的編制與不撤銷的群組

        Args:
            deposit_id: 要撤銷的存入編號
            catalog: 譜庫服務
            orchestra_type: 專案的樂團類型
            local: 開著的專案（呼叫端在主執行緒以專案與磁碟現況判定）；本機那份經它找（LocalProject.local_path）

        Returns:
            那次存入與它的每個群組

        Raises:
            CatalogError: 讀不到譜庫
        """
        reading = catalog.revert_reading(deposit_id, orchestra_type)
        paths = {
            local.local_path(entry, reading.files.get(entry.drive_file_id))
            for entry in reading.entries if entry.deposit_id == deposit_id
        }
        present = {path_key(path) for path in paths if self._files.file_exists(path)}
        return revert_preview(reading, deposit_id, local, present)

    def revert(
        self, preview: RevertPreview, choices: Sequence[RevertChoice], catalog: CatalogService, operator: str,
        orchestra_type: OrchestraType, should_cancel: Callable[[], bool], report: Callable[[RevertProgress], None],
    ) -> RevertOutcome:
        """撤銷選了的群組（步驟見模組說明）

        Args:
            preview: preview 算出的預覽
            choices: 要撤銷的群組與要不要一起撤掉編制，依預覽的順序
            catalog: 譜庫服務
            operator: 操作者（登入的 email），寫進存入紀錄與編制寫入紀錄
            orchestra_type: 專案的樂團類型
            should_cancel: 是否已要求取消
            report: 回報進度

        Returns:
            撤銷的結果；失敗不丟出，記在 error
        """
        refused = [(c.group, c.group.block) for c in choices if c.group.block is not None]
        choices = [c for c in choices if c.group.block is None]
        total = sum(len(c.group.downloads()) + len(c.group.reverting()) for c in choices)
        counter = _Counter(total, report)
        done: List[Tuple[RevertFile, str]] = []
        skipped: List[Tuple[RevertGroup, DownloadOutcome]] = []
        changed: List[RevertFile] = []
        fingerprints: Dict[str, str] = {}
        finished: List[RevertChoice] = []
        error: Optional[CatalogError] = None
        cancelled = False
        try:
            for choice in choices:
                group = choice.group
                if group.downloads():
                    restored = self._put_back(group, catalog, should_cancel, counter)
                    fingerprints.update(restored.fingerprints)
                    if restored.cancelled:
                        cancelled = True
                        break
                    if restored.error is not None or restored.not_done:
                        skipped.append((group, restored))
                        continue
                if not all(self._files.file_exists(f.local_path) for f in group.kept_locally()):
                    refused.append((group, RevertBlock.NO_LOCAL_COPY))
                    continue
                for file in group.reverting():
                    if should_cancel():
                        cancelled = True
                        break
                    counter.start(file.file.name if file.file else file.entry.voice)
                    if not self._unchanged(catalog, file):
                        changed.append(file)
                        continue
                    done.append((file, self._revert_file(catalog, file)))
                if cancelled:
                    break
                finished.append(choice)
        except CatalogError as e:
            error = e
        if done:
            try:
                catalog.record_revert(preview.deposit.deposit_id, done, operator)
            except CatalogError as e:
                error = error or e
                done, finished = [], []
        for file, _ in done:
            self._record_in_log(file)
        reverted_groups = [c.group for c in finished if c.instrumentation and revert_count(c.group.instrumentation)]
        if reverted_groups and error is None:
            try:
                catalog.sync_instrumentation([
                    revert_write(group.group_id, group.name, pending_revert(
                        group.edition_id, group.movement_id, group.instrumentation,
                    )) for group in reverted_groups
                ], orchestra_type, operator, WriteSource.UNDO, {})
            except CatalogError as e:
                error = e
                reverted_groups = []
        return RevertOutcome(
            [file for file, _ in done], skipped, refused, changed, fingerprints,
            reverted_groups if error is None else [], total, error, cancelled,
        )

    @staticmethod
    def _unchanged(catalog: CatalogService, file: RevertFile) -> bool:
        """動 Drive 之前重讀那份檔：版本與垃圾桶狀態跟預覽時相同才撤（預覽之後在 Drive 上改過的不動）"""
        now = catalog.drive_file(file.entry.drive_file_id)
        return now is not None and (now.head_revision, now.trashed) == (file.file.head_revision, file.file.trashed)

    def _put_back(
        self, group: RevertGroup, catalog: CatalogService, should_cancel: Callable[[], bool], counter: "_Counter",
    ) -> DownloadOutcome:
        """把群組裡要撤銷、本機已清除的檔下載回原位"""
        title = group.place.piece_title if group.place else ""
        items = [DownloadItem(file.file, file.local_path, title) for file in group.downloads()]
        start = counter.done

        def report(progress: DownloadProgress) -> None:
            """下載的進度接在前面做完的之後"""
            counter.at(start + progress.done, progress.file_name)
        outcome = self._downloads.put_back(items, catalog, should_cancel, report)
        counter.at(start + len(items), "")
        return outcome

    @staticmethod
    def _revert_file(catalog: CatalogService, file: RevertFile) -> str:
        """撤掉一份檔在 Drive 上的結果，回傳撤銷後那份的版本 ID"""
        if file.action is RevertAction.TRASH and not file.file.trashed:
            catalog.trash_file(file.file.id)
        elif file.action is RevertAction.RESTORE:
            return catalog.restore_revision(file.file, file.restore_to).head_revision
        return file.file.head_revision

    def _record_in_log(self, file: RevertFile) -> None:
        """撤掉的一份檔寫一筆紀錄檔"""
        if self._log is not None:
            self._log.record(LogEntry(
                t("revert.log.action", action=t(f"revert.action.{file.action.value}")),
                file_name=file.file.name if file.file else "", path=file.local_path,
            ))


class _Counter:
    """撤銷的進度：下載回原位與動 Drive 一起數"""

    def __init__(self, total: int, report: Callable[[RevertProgress], None]):
        """
        Args:
            total: 一共幾份
            report: 回報進度
        """
        self.done = 0
        self._total = total
        self._report = report

    def start(self, file_name: str) -> None:
        """開始做下一份：回報目前做完幾份，做完這一份就多一份"""
        self._report(RevertProgress(self.done, self._total, file_name))
        self.done += 1

    def at(self, done: int, file_name: str) -> None:
        """下載回原位做到第幾份"""
        self.done = done
        if file_name:
            self._report(RevertProgress(done, self._total, file_name))
