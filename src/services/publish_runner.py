# -*- coding: utf-8 -*-
"""
三段式發譜

照 ADR 0003 把發譜預覽交出的內容（publish_service.GroupPublish）寫進 HuaXia：

- 開始前（start，按下「發譜」時）：重查幹部身分與發譜函式的版本（publish_service.recheck），都過了才做快照：
  把要傳的本機檔複製到暫存資料夾底下這次的子資料夾，以複本的內容算 SHA-256 與泠靈的 Storage 路徑；壓縮過的檔
  本來就在暫存資料夾，直接用。人數、聲部組與要掛的公演也在這一刻取值。之後專案怎麼改、原檔怎麼搬都不影響這次發譜。
  新建曲目的編號由發譜預覽先產生、存進專案（PublishPreview.assign_piece_ids），這裡只照它發。
- 執行（run，背景執行緒）：先經譜庫服務寫一列列「開始發譜」到發譜紀錄，寫不進去就什麼都不做。接著：
  0. 清孤兒檔（services/orphan_cleanup.py）：這次涉及的曲目底下泠靈路徑格式、但沒有登記的檔，扣掉這次要傳的；
     清不成只寫出原因，照樣發譜。
  1. 傳檔：每首曲目先列出 Storage 裡泠靈資料夾已有的檔，內容相同（路徑相同）的跳過；其餘同時傳 UPLOAD_WORKERS 份，
     先不登記，團員看不到。要求取消時還沒開始傳的不傳，傳完手上的就停，也不切換。
  2. 切換：一個群組呼叫一次 HuaXia 的發譜函式，在一個交易裡完成這個群組的寫入。有檔沒傳上去的群組不呼叫；
     某個群組失敗不影響其他群組。要求取消時做完目前這個群組就停。
  3. 清舊檔：切換成功的群組刪掉它被取代的舊檔本體（GroupPublish.deletes，不含這次自己用到的檔），經
     orphan_cleanup.delete_unregistered：那一刻還有任何樂譜登記指著的不刪（例如別首曲目也登記著）。要求取消時做完目前這個群組就停，沒刪的成為孤兒檔（由孤兒檔清理處理）。
  結束後補一列列「發譜結果」（連同清孤兒檔的列），快照與壓縮檔不論成功、失敗或取消都刪掉（沒刪到的下次啟動清掉）。

中斷等同斷線：已傳的檔下次列出時跳過，新建曲目的編號沿用，發譜函式重跑不重複建，所以重發會從中斷處接著做。
每個群組的結果寫進紀錄檔（不記 HuaXia 傳回的團員資料）。寫完發譜結果後更新收回提醒的摘要（有傳入時）。網路請求與讀檔會卡住呼叫的執行緒，要在背景執行緒呼叫。

使用範例：
    from services.publish_runner import PublishRunner
    runner = PublishRunner(huaxia_auth, file_service, compressor, log)
    readiness, snapshot = runner.start(preview.publish_plan())   # 按下「發譜」時（等待視窗）
    if snapshot is not None:
        outcome = runner.run(snapshot, open_catalog, should_cancel, report)   # 背景執行緒
"""
import concurrent.futures
import os
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Set, Tuple

from core.catalog_errors import CatalogError
from core.constants import TEMP_DIR
from core.locale import t
from core.log_entry import LogEntry
from core.publish_log import PublishAction, PublishRecord
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.compression_service import CompressionService
from services.file_service import FileService
from services.huaxia_auth_service import HuaXiaAuthService
from services.huaxia_client import (
    PUBLISH_INTERFACE_VERSION, HuaXiaError, HuaXiaEvent, HuaXiaScore, lingling_storage_folder,
)
from services.orphan_cleanup import OrphanCleanup, clear_orphans, delete_unregistered
from services.publish_service import GroupPublish, PartCreate, PublishReadiness, recheck
from services.recall_reminder import RecallReminder

# 第 1 段同時傳幾份
UPLOAD_WORKERS = 3
# 泠靈登記的總譜在 HuaXia 上的樂譜名稱（分譜是聲部名稱）
SCORE_TITLE = "總譜"
# 覆寫聲部時可能改的欄位（PartUpdate 的同名欄位，None 為不改）
_PART_FIELDS = ("needed", "needed_min", "section")


class GroupFailure(Enum):
    """群組沒發成在哪一段；值寫進發譜紀錄"""

    UPLOAD = "upload"        # 有檔沒傳上去，沒有切換
    SWITCH = "switch"        # 發譜函式失敗，這個群組的寫入全部不生效
    CANCELLED = "cancelled"  # 要求取消時還沒切換


class PublishEvent(Enum):
    """發譜逐行紀錄的一行是什麼事"""

    UPLOADED = "uploaded"            # 一份檔傳上去了
    PRESENT = "present"              # Storage 已經有內容相同的，不重傳
    UPLOAD_FAILED = "upload_failed"  # 一份檔沒傳上去
    SWITCHED = "switched"            # 一個群組切換了
    SWITCH_FAILED = "switch_failed"  # 一個群組的切換失敗
    SKIPPED = "skipped"              # 一個群組沒有切換（有檔沒傳上去，或取消了）
    DELETED = "deleted"              # 一個群組被取代的舊檔刪掉了
    IN_USE = "in_use"                # 一個群組被取代的舊檔還有樂譜登記指著，不刪
    DELETE_FAILED = "delete_failed"  # 一個群組被取代的舊檔沒刪成，留成孤兒檔
    ORPHAN_CLEARED = "orphan_cleared"  # 發譜開始前清掉一份孤兒檔（不算在總進度裡）
    ORPHANS_NOT_CLEARED = "orphans_not_cleared"  # 一首曲目的孤兒檔沒清成（不算在總進度裡）


class PublishProgress(NamedTuple):
    """發譜的進度：剛發生的事與總進度（傳檔、切換、清舊檔的步數合計）

    Attributes:
        event: 剛發生的事
        done: 做完幾步
        total: 一共幾步
        group: 群組名稱（清孤兒檔的事是曲名）
        name: 檔名（傳檔的事）或 Storage 路徑（清舊檔、清孤兒檔的事）；其他為空字串
        detail: 失敗的原因；其他為空字串
    """
    event: PublishEvent
    done: int
    total: int
    group: str
    name: str = ""
    detail: str = ""


@dataclass(frozen=True)
class SnapshotFile:
    """快照裡一份要傳、要登記的檔

    Attributes:
        name: 本機檔名（顯示用）
        title: 在 HuaXia 上的樂譜名稱：聲部名稱，總譜是 SCORE_TITLE
        part_id: 登記到的現有 HuaXia 聲部；總譜與要新建的聲部為 None
        new_part: 登記到這次新建的聲部（名稱）；其他為 None
        copy: 要傳的那份：快照裡的複本，壓縮過的是暫存資料夾裡的壓縮檔
        storage_path: 在 Storage 的路徑（以 copy 的內容算）
    """
    name: str
    title: str
    part_id: Optional[str]
    new_part: Optional[str]
    copy: str
    storage_path: str

    def score(self) -> Dict[str, Any]:
        """發譜函式 add_scores 的一項"""
        entry: Dict[str, Any] = {"title": self.title, "file_path": self.storage_path}
        if self.part_id is not None:
            entry["part_id"] = self.part_id
        elif self.new_part is not None:
            entry["new_part"] = self.new_part
        return entry


@dataclass(frozen=True)
class GroupSnapshot:
    """快照裡一個群組這次要寫進 HuaXia 的全部內容（按下「發譜」那一刻的值，之後不再碰專案）

    Attributes:
        group_id: 群組 ID
        group: 群組名稱
        piece_id: HuaXia 曲目編號（新建的是發譜預覽先產生、存進專案的）
        piece_title: 曲名
        new_piece: 要新建的曲目欄位（發譜函式的 new_piece）；挑選現有曲目時為 None
        creates: 要新建的聲部
        updates: 要覆寫的聲部：id、name 與改的欄位各自的［原值，新值］
        files: 要傳、要登記的檔
        removes: 要撤掉登記的樂譜（被取代的、泠靈發的舊檔）
        deletes: 清舊檔要刪的 Storage 路徑
        events: 要掛的公演
    """
    group_id: str
    group: str
    piece_id: str
    piece_title: str
    new_piece: Optional[Dict[str, Any]]
    creates: List[PartCreate]
    updates: List[Dict[str, Any]]
    files: List[SnapshotFile]
    removes: List[HuaXiaScore]
    deletes: List[str]
    events: List[HuaXiaEvent]

    @property
    def paths(self) -> Set[str]:
        """這個群組要在 Storage 上的檔"""
        return {file.storage_path for file in self.files}

    def payload(self) -> Dict[str, Any]:
        """呼叫發譜函式的輸入（docs/huaxia/LINGLING.md）"""
        return {
            "version": PUBLISH_INTERFACE_VERSION,
            "piece_id": self.piece_id,
            "new_piece": self.new_piece,
            "create_parts": [create._asdict() for create in self.creates],
            "update_parts": [
                {"id": update["id"], **{name: update[name][1] for name in _PART_FIELDS if name in update}}
                for update in self.updates
            ],
            "add_scores": [file.score() for file in self.files],
            "remove_scores": [score.id for score in self.removes],
            "attach_events": [event.id for event in self.events],
        }

    def start_record(self) -> PublishRecord:
        """發譜紀錄裡這個群組的「開始發譜」"""
        return PublishRecord(
            PublishAction.START, group_id=self.group_id, group=self.group, piece_id=self.piece_id,
            piece_title=self.piece_title, new_piece=self.new_piece,
            parts_created=[create._asdict() for create in self.creates], parts_updated=list(self.updates),
            files_uploaded=sorted(self.paths), scores_added=[file.score() for file in self.files],
            scores_removed=[_score_entry(score) for score in self.removes], files_deleted=list(self.deletes),
            events=[_event_entry(event) for event in self.events],
        )


@dataclass(frozen=True)
class PublishSnapshot:
    """按下「發譜」那一刻的內容（PublishRunner.start）

    Attributes:
        folder: 暫存資料夾裡這次的子資料夾（本機檔的複本）
        groups: 各群組，依專案中的順序
        compressed: 要傳的壓縮檔（在暫存資料夾裡，發譜結束時丟掉）
        operator: 操作者的 Discord 名稱
    """
    folder: str
    groups: List[GroupSnapshot]
    compressed: List[str]
    operator: str


@dataclass(frozen=True)
class GroupOutcome:
    """一個群組發譜的結果

    Attributes:
        group: 群組這次的內容
        failure: 沒發成在哪一段；發成了為 None
        code: 沒發成時 HuaXia 回的錯誤代碼；沒有時為空字串
        detail: 沒發成的原因；發成了為空字串
        result: 發譜函式的輸出（新建的聲部、登記與撤掉的樂譜、新掛的場次）；沒切換時為空
        uploaded: 這次傳上去、這個群組要用的 Storage 路徑
        deleted: 清舊檔刪掉的 Storage 路徑
    """
    group: GroupSnapshot
    failure: Optional[GroupFailure] = None
    code: str = ""
    detail: str = ""
    result: Dict[str, Any] = field(default_factory=dict)
    uploaded: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)

    @property
    def published(self) -> bool:
        """切換成功了"""
        return self.failure is None

    def record(self) -> PublishRecord:
        """發譜紀錄裡這個群組的「發譜結果」"""
        group = self.group
        attached = set(self.result.get("attached_events") or [])
        return PublishRecord(
            PublishAction.RESULT, group_id=group.group_id, group=group.group, piece_id=group.piece_id,
            piece_title=group.piece_title,
            new_piece=group.new_piece if self.result.get("piece_created") else None,
            parts_created=list(self.result.get("created_parts") or []),
            parts_updated=list(group.updates) if self.published else [],
            files_uploaded=list(self.uploaded),
            scores_added=list(self.result.get("added_scores") or []),
            scores_removed=list(self.result.get("removed_scores") or []),
            files_deleted=list(self.deleted),
            events=[_event_entry(event) for event in group.events if event.id in attached],
            failure=None if self.published else {"stage": self.failure.value, "code": self.code, "detail": self.detail},
        )


@dataclass(frozen=True)
class PublishOutcome:
    """一次發譜的結果

    Attributes:
        publish_id: 發譜編號；「開始發譜」寫不進譜庫時為空字串
        groups: 各群組的結果，依專案中的順序；沒開始時為空
        orphans: 被取代、這次沒刪成的舊檔（取消或刪除失敗；下次發同一首時由孤兒檔清理處理）
        cleanups: 開始前清孤兒檔的結果，一首曲目一個
        error: 「開始發譜」寫不進譜庫：HuaXia 上沒有任何變動
        log_error: 「發譜結果」寫不進譜庫（HuaXia 已照 groups 變動）
        cancelled: 是否因為要求取消而停下
    """
    publish_id: str = ""
    groups: List[GroupOutcome] = field(default_factory=list)
    orphans: List[str] = field(default_factory=list)
    cleanups: List[OrphanCleanup] = field(default_factory=list)
    error: Optional[CatalogError] = None
    log_error: Optional[CatalogError] = None
    cancelled: bool = False

    @property
    def failed(self) -> List[GroupOutcome]:
        """沒發成的群組"""
        return [group for group in self.groups if not group.published]


class _Steps:
    """總進度：做完一步就回報一次"""

    def __init__(self, total: int, report: Callable[[PublishProgress], None]):
        """以總步數與回報進度的函式建立"""
        self.done = 0
        self.total = total
        self._report = report

    def step(self, event: PublishEvent, group: str, name: str = "", detail: str = "") -> None:
        """做完一步並回報"""
        self.done += 1
        self.note(event, group, name, detail)

    def note(self, event: PublishEvent, group: str, name: str = "", detail: str = "") -> None:
        """回報一件不算在總進度裡的事"""
        self._report(PublishProgress(event, self.done, self.total, group, name, detail))


class PublishRunner:
    """三段式發譜：開始前的重查與快照（start），背景的傳檔、切換、清舊檔與發譜紀錄（run）"""

    def __init__(
        self, auth: HuaXiaAuthService, file_service: FileService, compressor: CompressionService,
        log: Optional[ActivityLog] = None, temp_dir: str = TEMP_DIR, workers: int = UPLOAD_WORKERS,
        reminder: Optional[RecallReminder] = None,
    ):
        """
        Args:
            auth: HuaXia 登入服務（全程式一份）；請求經它的用戶端送出
            file_service: 複製、讀取與刪掉快照
            compressor: 產生預覽裡那些壓縮檔的壓縮服務；發譜結束時經它丟掉
            log: 紀錄檔；省略時不寫
            temp_dir: 暫存資料夾，快照寫在它底下這次的子資料夾（下次啟動時清掉殘檔）
            workers: 第 1 段同時傳幾份
            reminder: 收回提醒的摘要，寫完發譜紀錄後重讀一次記下；省略時不記
        """
        self._auth = auth
        self._client = auth.client
        self._files = file_service
        self._compressor = compressor
        self._log = log
        self._temp_dir = temp_dir
        self._workers = workers
        self._reminder = reminder

    def start(self, plans: List[GroupPublish]) -> Tuple[PublishReadiness, Optional[PublishSnapshot]]:
        """按下「發譜」時：重查幹部身分與發譜函式的版本（預覽可能開了很久），都過了才做快照

        不能發、或做快照失敗時，預覽交出的壓縮檔一併丟掉。

        Args:
            plans: 發譜預覽交出的各群組（新建曲目的編號已產生）

        Returns:
            （重查的結果，快照）；不能發時快照為 None

        Raises:
            HuaXiaError: 沒有登入、登入已失效或連不上 HuaXia
            OSError: 要傳的檔讀不到或複製不了
        """
        try:
            readiness = recheck(self._auth)
            if not readiness.ready:
                self.give_up(plans)
                return readiness, None
            return readiness, self._snapshot(plans, readiness.discord_name)
        except BaseException:
            self.give_up(plans)
            raise

    def give_up(self, plans: List[GroupPublish]) -> None:
        """不發了：丟掉發譜預覽交出、要傳的壓縮檔"""
        for path in _compressed(plans):
            self._compressor.discard_output(path)

    def discard(self, snapshot: PublishSnapshot) -> None:
        """丟掉快照：這次的複本與要傳的壓縮檔（run 結束時已經做過）"""
        self._files.discard_directory(snapshot.folder)
        for path in snapshot.compressed:
            self._compressor.discard_output(path)

    def run(
        self, snapshot: PublishSnapshot, open_catalog: Callable[[], CatalogService],
        should_cancel: Callable[[], bool], report: Callable[[PublishProgress], None],
    ) -> PublishOutcome:
        """照快照發譜：寫「開始發譜」→ 傳檔 → 切換 → 清舊檔 → 寫「發譜結果」；結束時丟掉快照

        Args:
            snapshot: start 交回的快照
            open_catalog: 取得譜庫服務（寫發譜紀錄）
            should_cancel: 是否已要求取消
            report: 回報進度與逐行紀錄

        Returns:
            發譜的結果；失敗不丟出，記在結果裡
        """
        try:
            return self._run(snapshot, open_catalog, should_cancel, report)
        finally:
            self.discard(snapshot)

    def _run(
        self, snapshot: PublishSnapshot, open_catalog: Callable[[], CatalogService],
        should_cancel: Callable[[], bool], report: Callable[[PublishProgress], None],
    ) -> PublishOutcome:
        """run 的本體（不負責丟掉快照）"""
        groups = snapshot.groups
        try:
            catalog = open_catalog()
            publish_id = catalog.begin_publish([group.start_record() for group in groups], snapshot.operator)
        except CatalogError as e:
            return PublishOutcome(error=e)
        uploads = _unique_files(groups)
        steps = _Steps(len(uploads) + len(groups) + sum(len(group.deletes) for group in groups), report)
        cleanups = self._clear_orphans(groups, steps)
        uploaded, failed = self._upload(uploads, should_cancel, steps)
        cancelled = should_cancel()
        outcomes = []
        for group in groups:
            mine = [path for path in uploaded if path in group.paths]
            broken = next((failed[path] for path in sorted(group.paths) if path in failed), None)
            if cancelled or broken is not None:
                failure = GroupFailure.CANCELLED if broken is None else GroupFailure.UPLOAD
                outcomes.append(GroupOutcome(group, failure, detail=broken or "", uploaded=mine))
                steps.step(PublishEvent.SKIPPED, group.group, detail=broken or "")
                continue
            outcomes.append(self._switch(group, mine, steps))
            cancelled = should_cancel()
        kept: Set[str] = set()
        if not cancelled:
            outcomes, kept = self._clean(outcomes, should_cancel, steps)
            cancelled = should_cancel()
        orphans = [
            path for outcome in outcomes if outcome.published for path in outcome.group.deletes
            if path not in outcome.deleted and path not in kept
        ]
        outcome = PublishOutcome(publish_id, outcomes, list(dict.fromkeys(orphans)), cleanups, cancelled=cancelled)
        records = [cleanup.record() for cleanup in cleanups if cleanup.deleted or cleanup.detail]
        try:
            catalog.record_publish(publish_id, records + [group.record() for group in outcomes], snapshot.operator)
        except CatalogError as e:
            outcome = replace(outcome, log_error=e)
        if self._reminder is not None:
            self._reminder.refresh(catalog)
        self._record_in_log(outcome)
        return outcome

    def _snapshot(self, plans: List[GroupPublish], operator: str) -> PublishSnapshot:
        """把要傳的本機檔複製到暫存資料夾這次的子資料夾，以複本的內容算 Storage 路徑，連同編制取值成快照"""
        folder = os.path.join(self._temp_dir, uuid.uuid4().hex)
        self._files.create_directory(folder)
        groups = []
        try:
            for plan in plans:
                piece_id = plan.link.piece_id
                files = []
                for upload in plan.uploads:
                    copy = upload.path
                    if upload.path == upload.file.path:
                        copy = os.path.join(folder, f"{sum(len(g.files) for g in groups) + len(files)}.pdf")
                        self._files.copy_file(upload.path, copy)
                    file = upload.file
                    new_part = file.voice if file.voice and file.part is None else None
                    files.append(SnapshotFile(
                        os.path.basename(file.path), file.voice or SCORE_TITLE, file.part.id if file.part else None,
                        new_part, copy, file.path_for(self._files.sha256(copy)),
                    ))
                groups.append(GroupSnapshot(
                    plan.group.id, plan.group.name, piece_id, plan.link.piece_title, _new_piece(plan),
                    list(plan.voices.creates), _updates(plan), files, plan.replaces, list(plan.deletes),
                    list(plan.events),
                ))
        except BaseException:
            self._files.discard_directory(folder)
            raise
        # 不刪這次自己要用的檔（例如原檔在預覽之後改回 HuaXia 上那份舊的內容）
        sending = {path for group in groups for path in group.paths}
        groups = [replace(group, deletes=[p for p in group.deletes if p not in sending]) for group in groups]
        return PublishSnapshot(folder, groups, _compressed(plans), operator)

    def _clear_orphans(self, groups: List[GroupSnapshot], steps: _Steps) -> List[OrphanCleanup]:
        """開始前清掉這次涉及的曲目底下的孤兒檔，不碰這次要傳的檔（還沒登記），清掉的與沒清成的各回報一行"""
        pieces = {group.piece_id: group.piece_title for group in groups}
        sending = {path for group in groups for path in group.paths}
        cleanups = clear_orphans(self._auth, pieces, keep=sending, log=self._log)
        for cleanup in cleanups:
            for path in cleanup.deleted:
                steps.note(PublishEvent.ORPHAN_CLEARED, cleanup.piece_title, path)
            if cleanup.detail:
                steps.note(PublishEvent.ORPHANS_NOT_CLEARED, cleanup.piece_title, detail=cleanup.detail)
        return cleanups

    def _upload(
        self, uploads: List[Tuple[GroupSnapshot, SnapshotFile]], should_cancel: Callable[[], bool], steps: _Steps,
    ) -> Tuple[List[str], Dict[str, str]]:
        """第 1 段：Storage 已有的跳過，其餘同時傳 workers 份；要求取消後還沒開始傳的不傳

        Returns:
            （這次傳上去的路徑，沒傳上去的路徑對應原因）
        """
        present: Set[str] = set()
        for piece_id in dict.fromkeys(group.piece_id for group, _ in uploads):
            try:
                present |= set(self._client.stored_files(self._auth.access_token(), lingling_storage_folder(piece_id)))
            except HuaXiaError:
                pass    # 列不出來就照傳：Storage 不覆寫，已有的檔回報已經存在
        todo = []
        for group, file in uploads:
            if file.storage_path in present:
                steps.step(PublishEvent.PRESENT, group.group, file.name)
            else:
                todo.append((group, file))
        uploaded: List[str] = []
        failed: Dict[str, str] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=self._workers) as pool:
            futures = {pool.submit(self._upload_one, file, should_cancel): (group, file) for group, file in todo}
            for future in concurrent.futures.as_completed(futures):
                group, file = futures[future]
                sent, error = future.result()
                if error:
                    failed[file.storage_path] = error
                    steps.step(PublishEvent.UPLOAD_FAILED, group.group, file.name, error)
                elif sent is None:
                    continue
                else:
                    if sent:
                        uploaded.append(file.storage_path)
                    steps.step(PublishEvent.UPLOADED if sent else PublishEvent.PRESENT, group.group, file.name)
        sent = set(uploaded)
        return [file.storage_path for _, file in todo if file.storage_path in sent], failed

    def _upload_one(self, file: SnapshotFile, should_cancel: Callable[[], bool]) -> Tuple[Optional[bool], str]:
        """傳一份檔（在傳檔的執行緒呼叫）

        Returns:
            （傳上去了為 True、已經有了為 False、取消了沒傳為 None，失敗的原因；沒失敗時為空字串）
        """
        if should_cancel():
            return None, ""
        try:
            return self._client.upload(self._auth.access_token(), file.storage_path, self._files.read_bytes(file.copy)), ""
        except (HuaXiaError, OSError) as e:
            return None, str(e) or type(e).__name__

    def _switch(self, group: GroupSnapshot, uploaded: List[str], steps: _Steps) -> GroupOutcome:
        """第 2 段的一個群組：呼叫一次發譜函式"""
        try:
            result = self._client.publish(self._auth.access_token(), group.payload())
        except HuaXiaError as e:
            detail = e.detail or str(e)
            steps.step(PublishEvent.SWITCH_FAILED, group.group, detail=detail)
            return GroupOutcome(group, GroupFailure.SWITCH, e.code, detail, uploaded=uploaded)
        steps.step(PublishEvent.SWITCHED, group.group)
        return GroupOutcome(group, result=result, uploaded=uploaded)

    def _clean(
        self, outcomes: List[GroupOutcome], should_cancel: Callable[[], bool], steps: _Steps,
    ) -> Tuple[List[GroupOutcome], Set[str]]:
        """第 3 段：每個切換成功的群組刪掉它被取代的舊檔（還有樂譜登記指著的不刪，例如別首曲目也登記著）；
        要求取消時做完目前這個群組就停

        Returns:
            （各群組的結果，填好刪掉的路徑；不必刪的路徑：還有登記指著的與本來就已經不在 Storage 的）
        """
        kept: Set[str] = set()
        settled = []
        for outcome in outcomes:
            paths = list(outcome.group.deletes) if outcome.published else []
            if not paths or should_cancel():
                settled.append(outcome)
                continue
            try:
                deletion = delete_unregistered(self._auth, paths)
            except HuaXiaError as e:
                for path in paths:
                    steps.step(PublishEvent.DELETE_FAILED, outcome.group.group, path, e.detail or str(e))
                settled.append(outcome)
                continue
            for path in paths:
                if path in deletion.in_use:
                    steps.step(PublishEvent.IN_USE, outcome.group.group, path)
                elif path in deletion.not_deleted:
                    steps.step(PublishEvent.DELETE_FAILED, outcome.group.group, path)
                else:
                    steps.step(PublishEvent.DELETED, outcome.group.group, path)
            # 還有登記指著的不是孤兒檔；本來就已經不在 Storage 的也不是
            kept.update(path for path in paths if path not in deletion.deleted and path not in deletion.not_deleted)
            settled.append(replace(outcome, deleted=list(deletion.deleted)))
        return settled, kept

    def _record_in_log(self, outcome: PublishOutcome) -> None:
        """每個群組的結果與沒刪成的舊檔各寫一筆紀錄檔（不記 HuaXia 傳回的團員資料）"""
        if self._log is None:
            return
        for group in outcome.groups:
            if group.published:
                entry = LogEntry(t("publish.log.published"), piece_title=group.group.piece_title,
                                 file_name=group.group.group)
            else:
                entry = LogEntry(
                    t("publish.log.failed"), piece_title=group.group.piece_title, file_name=group.group.group,
                    category=t(f"publish.run.failure.{group.failure.value}"),
                    message=" ".join(part for part in (group.code, group.detail) if part),
                )
            self._log.record(entry)
        for path in outcome.orphans:
            self._log.record(LogEntry(t("publish.log.orphan"), path=path))


def _unique_files(groups: List[GroupSnapshot]) -> List[Tuple[GroupSnapshot, SnapshotFile]]:
    """要傳的檔，同一個 Storage 路徑只傳一次（每筆登記各有自己的路徑，照理不重複），依群組與檔案的順序"""
    seen: Set[str] = set()
    unique = []
    for group in groups:
        for file in group.files:
            if file.storage_path not in seen:
                seen.add(file.storage_path)
                unique.append((group, file))
    return unique


def _compressed(plans: List[GroupPublish]) -> List[str]:
    """發譜預覽交出、要傳的壓縮檔（要傳的不是本機原檔的那些）"""
    return [upload.path for plan in plans for upload in plan.uploads if upload.path != upload.file.path]


def _new_piece(plan: GroupPublish) -> Optional[Dict[str, Any]]:
    """發譜函式的 new_piece：要新建的曲目欄位；挑選現有曲目時為 None"""
    new = plan.link.new_piece
    if new is None:
        return None
    return {
        "title": new.title, "composer": new.composer, "duration_min": new.duration_min, "ensemble": new.ensemble.value,
    }


def _updates(plan: GroupPublish) -> List[Dict[str, Any]]:
    """要覆寫的聲部：id、HuaXia 的名稱與改的欄位各自的［原值，新值］（原值取自預覽讀到的 HuaXia 聲部）"""
    parts = {row.part.id: row.part for row in plan.voices.rows if row.part is not None}
    updates = []
    for update in plan.voices.updates:
        entry: Dict[str, Any] = {"id": update.part_id, "name": update.name}
        before = parts.get(update.part_id)
        for name in _PART_FIELDS:
            value = getattr(update, name)
            if value is not None:
                entry[name] = [getattr(before, name) if before is not None else None, value]
        updates.append(entry)
    return updates


def _score_entry(score: HuaXiaScore) -> Dict[str, Any]:
    """發譜紀錄裡的一份樂譜：編號、聲部與 Storage 路徑"""
    return {"id": score.id, "part_id": score.part_id, "file_path": score.file_path}


def _event_entry(event: HuaXiaEvent) -> Dict[str, Any]:
    """發譜紀錄裡的一場公演：編號、名稱與結束時間（收回提醒用）"""
    return {"id": event.id, "title": event.title, "ends_at": event.ends_at.isoformat()}
