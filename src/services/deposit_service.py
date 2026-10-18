# -*- coding: utf-8 -*-
"""
存入譜庫

把專案裡已重新命名、已連到譜庫的群組的總譜與分譜存進譜庫，本機那份不動：
preview 讀本機檔案的 MD5 與譜庫那處現在的登記、版本資料夾裡的檔，交回每份檔的動作（規則在 core/deposit.py）；
deposit 先以一次試算表寫入替每份要做的檔寫一列「要做」的存入紀錄，再照計畫一份一份上傳、更新或搬移，
做完（或取消、中途失敗）後以一次試算表寫入把做好的那些登記到分譜列，並各寫一列做了的存入紀錄，
所以請求數不隨聲部數增加。登記寫不進去時，蓋掉的內容照樣有「要做」列可撤銷。碰譜庫一律經譜庫服務（CatalogService）。
undeposited 拿本機檔案的 MD5 比對存入指紋，找出存入後本機又改過的檔（未存入），不連網；
catalog_only 照磁碟現況挑出只在譜庫的檔（本機找不到、群組記著譜庫裡有一份），各處要判定只在譜庫都經它。

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
    cleared = service.catalog_only(project, paths)
"""
from dataclasses import dataclass, field, replace
from typing import Callable, Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

from core.catalog_errors import CatalogError, CatalogErrorKind, exception_message
from core.catalog_models import DriveFile
from core.deposit import (
    DepositAction, DepositBlock, DepositItem, DepositPlan, DepositRecord, GroupDeposit, GroupSources, PlaceFiles,
    deposit_sources, folder_key, plan_group,
)
from core.deposit_revert import interrupted
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

    def catalog_only(self, project: Project, paths: Iterable[str]) -> List[str]:
        """這些路徑裡只在譜庫的：本機找不到、所在群組記著譜庫裡有一份（Project.catalog_only_paths）；只看本機，不連網

        Args:
            project: 專案
            paths: 要看的本機路徑

        Returns:
            其中只在譜庫的路徑，順序同 paths
        """
        return project.catalog_only_paths(path for path in paths if not self._files.file_exists(path))

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
        """算出存入計畫：本機檔案的 MD5 與大小，加上譜庫各處現在的登記與版本資料夾裡的檔，與上次存入有沒有中斷

        上次中斷從存入紀錄推算（只有「要做」列的檔），所以程式重開、換一台電腦也看得到。

        Args:
            project: 專案
            catalog: 譜庫服務

        Returns:
            每個群組各一個，依專案的順序；只在譜庫的檔跳過，本機有其他找不到的檔的群組整個不存入

        Raises:
            CatalogError: 讀不到譜庫
        """
        groups = []
        local: Dict[str, Tuple[str, int]] = {}
        for sources in deposit_sources(project):
            if sources.block is None:
                sources = self._read_local(sources, local)
            groups.append(sources)
        keys = sorted({(g.link.edition_id, g.link.movement_id) for g in groups if g.block is None})
        places = catalog.deposit_places(keys) if keys else {}
        history = catalog.deposit_log()
        return DepositPlan([
            plan_group(g, local, places.get((g.link.edition_id, g.link.movement_id)) if g.link else None)
            for g in groups
        ], interrupted(history, {g.id for g in project.groups}, {g.name for g in project.groups}))

    def _read_local(self, sources: GroupSources, local: Dict[str, Tuple[str, int]]) -> GroupSources:
        """讀群組每份檔的 MD5 與大小寫進 local；只在譜庫的檔跳過，其他找不到的檔讓整個群組不存入

        Returns:
            去掉只在譜庫的檔之後的群組；有其他找不到的檔時為 MISSING_FILES，檔都只在譜庫時為 CATALOG_ONLY
        """
        kept = []
        for source in sources.sources:
            try:
                path = source.local_path
                local[path] = (self._files.md5(path), self._files.file_size(path))
            except OSError:
                if not source.catalog_copy:
                    return sources.blocked(DepositBlock.MISSING_FILES)
                continue
            kept.append(source)
        if not kept:
            return sources.blocked(DepositBlock.CATALOG_ONLY)
        return replace(sources, sources=kept)

    def deposit(
        self, plan: DepositPlan, catalog: CatalogService, operator: str, should_cancel: Callable[[], bool],
        report: Callable[[DepositProgress], None],
    ) -> DepositOutcome:
        """照計畫存入：先寫「要做」的存入紀錄，再一份一份上傳、更新或搬移，最後一次登記做好的那些並寫做了的存入紀錄

        新上傳的檔與還沒有的子資料夾先一起要好 ID，「要做」列就記得到新上傳的檔；「要做」列寫不進去時不動 Drive。
        要求取消時做完目前這一份就停；中途失敗也停下。兩種情況都照樣登記已經做好的，再按一次（重新預覽）就補齊。
        登記不進去時這次做好的都不算完成（Drive 上的檔留著，下次預覽時以同名檔認出來直接登記；蓋掉的內容以「要做」列撤銷）。

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
        deposit_id = ""
        error: Optional[CatalogError] = None
        cancelled = False
        try:
            file_ids, folder_ids = self._reserve_ids(catalog, work)
            planned = [_planned(group, item, file_ids.get(index, "")) for index, (group, item) in enumerate(work)]
            deposit_id = catalog.begin_deposit(planned, operator)
            made: Dict[_FolderSpot, str] = {}
            for index, (group, item) in enumerate(work):
                if should_cancel():
                    cancelled = True
                    break
                report(DepositProgress(len(done), len(work), item.source.file_name))
                done.append((group, self._put(catalog, planned[index], item, group.place, folder_ids, made)))
        except CatalogError as e:
            error = e
        except OSError as e:
            error = CatalogError(CatalogErrorKind.OTHER, exception_message(e))
        if done:
            try:
                catalog.record_deposits(deposit_id, [record for _, record in done], operator)
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

    @staticmethod
    def _reserve_ids(
        catalog: CatalogService, work: List[Tuple[GroupDeposit, DepositItem]],
    ) -> Tuple[Dict[int, str], Dict[_FolderSpot, str]]:
        """預先向 Drive 要新上傳的檔與還沒有的子資料夾的 ID（存入紀錄的「要做」列記得到新上傳的檔）

        Returns:
            （work 裡新上傳那幾份的位置 → ID，還沒有的子資料夾 → ID）
        """
        uploads = [index for index, (_, item) in enumerate(work) if item.action is DepositAction.NEW]
        folders = sorted(_missing_folders(work))
        ids = catalog.drive_ids(len(uploads) + len(folders)) if uploads or folders else []
        return dict(zip(uploads, ids)), dict(zip(folders, ids[len(uploads):]))

    def _put(
        self, catalog: CatalogService, planned: DepositRecord, item: DepositItem, place: PlaceFiles,
        folder_ids: Dict[_FolderSpot, str], made: Dict[_FolderSpot, str],
    ) -> DepositRecord:
        """存入一份檔：放好它的子資料夾，再依動作上傳、更新、搬移或只是登記，交回做了的那一列"""
        source = item.source
        folder_id = self._folder(catalog, place, source.folders, folder_ids, made)
        target = item.target
        if item.action is DepositAction.NEW:
            file = catalog.upload_file(
                planned.drive_file.id, source.file_name, folder_id, self._files.read_bytes(source.local_path),
            )
        elif item.action is DepositAction.UPDATE:
            file = catalog.update_file(target, self._files.read_bytes(source.local_path), source.file_name, folder_id)
        else:
            file = catalog.place_file(target, source.file_name, folder_id)
        return replace(planned, drive_file=file)

    @staticmethod
    def _folder(
        catalog: CatalogService, place: PlaceFiles, folders: Tuple[str, ...], folder_ids: Dict[_FolderSpot, str],
        made: Dict[_FolderSpot, str],
    ) -> str:
        """版本資料夾下這條子資料夾路徑的 ID，還沒有的那幾層以預先要到的 ID 建立（建好就不再建；重送時取已建好的）"""
        parent = place.folder_id
        for depth in range(1, len(folders) + 1):
            key = folder_key(folders[:depth])
            spot = (place.folder_id, key)
            existing = place.folders.get(key) or made.get(spot)
            if existing is None:
                existing = made[spot] = catalog.create_folder(folders[depth - 1], parent, folder_ids[spot]).id
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


def _planned(group: GroupDeposit, item: DepositItem, new_id: str) -> DepositRecord:
    """一份要做的檔在存入紀錄的「要做」列：要動的 Drive 檔（新上傳是預先要到的 ID）與被蓋掉的版本"""
    source = group.sources
    target = item.target if item.target is not None else DriveFile(id=new_id, name=item.source.file_name)
    return DepositRecord(
        source.link.edition_id, source.link.movement_id, source.group_id, source.group_name, item.source.voice,
        source.score_label, item.source.local_path, target, item.action,
        item.target.head_revision if item.target is not None else "",
    )


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
