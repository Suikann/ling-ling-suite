# -*- coding: utf-8 -*-
"""
從譜庫下載

下載回原位與一般下載共用同一套下載與 MD5 核對（要下載哪些檔、放哪裡的規則在 core/catalog_download.py）：
每份先下載到暫存資料夾，以 Drive 的 md5Checksum 核對暫存檔，一致才搬到要放的位置（資料夾不在就建），
不一致、下載中斷或取消時暫存檔丟掉，要放的位置不留半份檔案。暫存資料夾在啟動時清空，當機留下的也不會留著。

放的位置已經有檔時絕不覆蓋：
- 下載回原位：內容跟譜庫那份相同就算已經放回去（略過），不同就不動、列在 occupied
- 一般下載：內容相同就略過，不同就照重新命名的規則加「 (1)」「 (2)」…後綴（core/filename.py 的 numbered），
  加了後綴的那份已經有、內容也相同時一樣略過，所以再按一次不會多出一份

讀不到譜庫或下載失敗（例如斷網）時停下，已下載的保留，錯誤記在 error；要求取消時做完目前這一份就停。
兩者都是再按一次就補齊。下載回原位交回放回去的每份檔的指紋（譜庫那份的 MD5），清除後重新存入過的就是新的，
呼叫端交給 Project.record_fingerprints。下載的每份檔寫一筆紀錄檔；不放上復原堆疊。

網路請求與讀寫檔會卡住呼叫的執行緒，UI 要在背景執行緒呼叫 restore 與 download；restore_sources 只看本機，
在主執行緒呼叫。

使用範例：
    from services.download_service import DownloadService
    service = DownloadService(file_service, log)
    sources = service.restore_sources(project, [group.id])          # 主執行緒
    outcome = service.restore(sources, catalog, should_cancel, report)  # 背景執行緒
    project.record_fingerprints(outcome.fingerprints)
    outcome = service.download(folder_items(pdfs, folder, True), catalog, should_cancel, report)
    outcome = service.put_back([DownloadItem(drive_file, path)], catalog, should_cancel, report)  # 撤銷存入前
"""
import os
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Collection, Dict, List, NamedTuple, Optional, Sequence, Tuple

from core.catalog_download import DownloadItem, restore_items
from core.catalog_errors import CatalogError
from core.clear_local import ClearSources, clear_sources
from core.constants import TEMP_DIR
from core.filename import numbered
from core.locale import t
from core.log_entry import LogEntry
from core.models import Project
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.file_service import FileService


class DownloadProgress(NamedTuple):
    """下載的進度：做完幾份、一共幾份、正在下載的檔名"""
    done: int
    total: int
    file_name: str


@dataclass(frozen=True)
class DownloadOutcome:
    """一次下載的結果

    Attributes:
        saved: 下載並放好了的檔（target 是實際放的位置，加了後綴時是加了的那個）
        skipped: 放的位置已經有內容相同的一份，沒下載
        occupied: 下載回原位時原位置已經有別的檔，沒覆蓋
        mismatched: 下載到的內容跟 Drive 的 MD5 不符，沒留檔
        unavailable: 下載回原位時譜庫裡已經沒有的檔（本機路徑）
        failed: 本機寫不進去的檔與原因
        total: 要下載的份數
        error: 讀不到譜庫或下載失敗而停下時的錯誤
        cancelled: 是否因為要求取消而停下
    """
    saved: List[DownloadItem] = field(default_factory=list)
    skipped: List[DownloadItem] = field(default_factory=list)
    occupied: List[DownloadItem] = field(default_factory=list)
    mismatched: List[DownloadItem] = field(default_factory=list)
    unavailable: List[str] = field(default_factory=list)
    failed: List[Tuple[DownloadItem, str]] = field(default_factory=list)
    total: int = 0
    error: Optional[CatalogError] = None
    cancelled: bool = False

    @property
    def completed(self) -> int:
        """完成了幾份（下載了的加上已經有的）"""
        return len(self.saved) + len(self.skipped)

    @property
    def not_done(self) -> List[str]:
        """沒放好的檔（原位置有別的檔、MD5 不符、寫不進去、譜庫裡沒有）的本機路徑"""
        return (
            [item.target for item in self.occupied + self.mismatched] + [item.target for item, _ in self.failed]
            + list(self.unavailable)
        )

    @property
    def fingerprints(self) -> Dict[str, str]:
        """放好了的每份檔的指紋：本機路徑 → 譜庫裡那份的 MD5"""
        return {item.target: item.file.md5 for item in self.saved + self.skipped}


class _Placed(Enum):
    """一份檔的下場"""
    SAVED = "saved"
    SKIPPED = "skipped"
    OCCUPIED = "occupied"
    MISMATCHED = "mismatched"


class DownloadService:
    """從譜庫下載：下載回原位與一般下載"""

    def __init__(self, file_service: FileService, log: Optional[ActivityLog] = None, temp_dir: str = TEMP_DIR):
        """
        Args:
            file_service: 讀寫本機檔案（存在與否、MD5、寫暫存檔、搬到要放的位置）
            log: 紀錄檔，下載的每份檔寫一筆；省略時不寫
            temp_dir: 暫存資料夾，下載中的檔寫在它底下這次下載的子資料夾
        """
        self._files = file_service
        self._log = log
        self._temp_dir = temp_dir

    def restore_sources(self, project: Project, group_ids: Collection[str]) -> List[ClearSources]:
        """指定的群組裡只在譜庫的檔（群組記著譜庫裡有一份、本機找不到）；只看本機，不連網

        Args:
            project: 專案
            group_ids: 要下載回原位的群組

        Returns:
            有只在譜庫的檔的群組（依專案的順序），每個只留那些檔
        """
        result = []
        for group in clear_sources(project):
            if group.group_id not in group_ids:
                continue
            sources = [s for s in group.sources if s.catalog_copy and not self._files.file_exists(s.path)]
            if sources:
                result.append(ClearSources(group.group_id, group.group_name, group.score_label, group.link, sources))
        return result

    def restore(
        self, groups: Sequence[ClearSources], catalog: CatalogService, should_cancel: Callable[[], bool],
        report: Callable[[DownloadProgress], None],
    ) -> DownloadOutcome:
        """把只在譜庫的檔下載回原位：讀各群組連到的那處現在的登記，再逐份下載、核對 MD5、放回清除前的路徑

        Args:
            groups: restore_sources 找出的檔
            catalog: 譜庫服務
            should_cancel: 是否已要求取消
            report: 回報進度

        Returns:
            下載的結果；讀不到譜庫時什麼都不下載，錯誤記在 error
        """
        keys = sorted({(g.link.edition_id, g.link.movement_id) for g in groups if g.link is not None})
        try:
            places = catalog.deposit_places(keys) if keys else {}
        except CatalogError as error:
            return DownloadOutcome(total=sum(len(g.sources) for g in groups), error=error)
        items, unavailable = restore_items(groups, places)
        outcome = self._run(items, catalog, False, should_cancel, report)
        return DownloadOutcome(
            outcome.saved, outcome.skipped, outcome.occupied, outcome.mismatched, unavailable, outcome.failed,
            outcome.total, outcome.error, outcome.cancelled,
        )

    def put_back(
        self, items: Sequence[DownloadItem], catalog: CatalogService, should_cancel: Callable[[], bool],
        report: Callable[[DownloadProgress], None],
    ) -> DownloadOutcome:
        """把指定的 Drive 檔下載回本機的原位：逐份下載、核對 MD5，原位已有別的檔時不覆蓋（撤銷存入先放回本機已清除的檔）

        Args:
            items: 要下載的 Drive 檔與要放回的本機路徑
            catalog: 譜庫服務
            should_cancel: 是否已要求取消
            report: 回報進度

        Returns:
            下載的結果
        """
        return self._run(items, catalog, False, should_cancel, report)

    def download(
        self, items: Sequence[DownloadItem], catalog: CatalogService, should_cancel: Callable[[], bool],
        report: Callable[[DownloadProgress], None],
    ) -> DownloadOutcome:
        """一般下載：逐份下載、核對 MD5，放到指定的位置；同名檔內容不同時加後綴

        Args:
            items: 要下載的檔（core/catalog_download.py 的 folder_items）
            catalog: 譜庫服務
            should_cancel: 是否已要求取消
            report: 回報進度

        Returns:
            下載的結果
        """
        return self._run(items, catalog, True, should_cancel, report)

    def _run(
        self, items: Sequence[DownloadItem], catalog: CatalogService, suffix: bool,
        should_cancel: Callable[[], bool], report: Callable[[DownloadProgress], None],
    ) -> DownloadOutcome:
        """逐份下載；suffix 為 True 時同名檔內容不同就加後綴，否則不放"""
        results: Dict[_Placed, List[DownloadItem]] = {placed: [] for placed in _Placed}
        failed: List[Tuple[DownloadItem, str]] = []
        error: Optional[CatalogError] = None
        cancelled = False
        folder = os.path.join(self._temp_dir, uuid.uuid4().hex)
        try:
            for done, item in enumerate(items):
                if should_cancel():
                    cancelled = True
                    break
                report(DownloadProgress(done, len(items), os.path.basename(item.target)))
                try:
                    placed, item = self._fetch(item, catalog, folder, suffix)
                except CatalogError as e:
                    error = e
                    break
                except OSError as e:
                    failed.append((item, str(e)))
                    continue
                results[placed].append(item)
                if placed is _Placed.SAVED:
                    self._record_in_log(item)
        finally:
            self._files.discard_directory(folder)
        return DownloadOutcome(
            results[_Placed.SAVED], results[_Placed.SKIPPED], results[_Placed.OCCUPIED], results[_Placed.MISMATCHED],
            [], failed, len(items), error, cancelled,
        )

    def _fetch(self, item: DownloadItem, catalog: CatalogService, folder: str, suffix: bool) -> Tuple[_Placed, DownloadItem]:
        """下載一份：先決定放哪裡，下載到暫存檔核對 MD5，一致才搬過去

        Returns:
            （下場，實際放的位置的那份）

        Raises:
            CatalogError: 下載失敗
            OSError: 本機讀寫失敗
        """
        placed, target = self._target(item, suffix)
        if placed is not None:
            return placed, DownloadItem(item.file, target, item.piece_title)
        content = catalog.download_file(item.file)
        temp = os.path.join(folder, uuid.uuid4().hex)
        self._files.write_bytes(temp, content)
        if self._files.md5(temp) != item.file.md5:
            self._files.remove_file(temp)
            return _Placed.MISMATCHED, item
        self._files.create_directory(os.path.dirname(target))
        self._files.rename_file(temp, target)
        return _Placed.SAVED, DownloadItem(item.file, target, item.piece_title)

    def _target(self, item: DownloadItem, suffix: bool) -> Tuple[Optional[_Placed], str]:
        """要放的位置：沒有檔就放 item.target；有內容相同的就略過；不同時加後綴（suffix）或不放

        Returns:
            （略過或不放時的下場，否則 None；位置）
        """
        target = item.target
        count = 0
        while self._files.file_exists(target):
            if self._files.md5(target) == item.file.md5:
                return _Placed.SKIPPED, target
            if not suffix:
                return _Placed.OCCUPIED, target
            count += 1
            target = numbered(item.target, count)
        return None, target

    def _record_in_log(self, item: DownloadItem) -> None:
        """下載的一份檔寫一筆紀錄檔"""
        if self._log is not None:
            self._log.record(LogEntry(
                t("download.log.action"), piece_title=item.piece_title, file_name=os.path.basename(item.target),
                path=item.target,
            ))
