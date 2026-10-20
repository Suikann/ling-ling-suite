# -*- coding: utf-8 -*-
"""
撤回

從譜庫的發譜紀錄撤回某次發譜選定的群組（spec #82）：撤掉那次登記的分譜與總譜，連同那次新建、現在還沒排人的聲部。
被那次取代的舊檔不會回來，人數與聲部組不改回，場次不動。只動那次發譜結果記下的 HuaXia 編號
（core/publish_log.py 的 PublishRecord：scores_added、parts_created）與那次登記過的 Storage 檔。那次沒有發譜結果
（中途停下或結果寫不進譜庫）時，依開始發譜記下要登記的 Storage 路徑撤掉那首曲目現在登記著的泠靈樂譜；不知道那次
新建了哪些聲部，聲部一律留著。

- 預覽（preview）：向 HuaXia 查那次放的檔還在不在、那次新建的聲部有沒有排人或有人在網頁上設定過（小老師、備註、
  練習回報、改名）、這首曲目所有的檔，把每個聲部歸類
  （RetractEffect）：撤回後沒有譜、刪掉聲部、聲部留著但沒有譜、只撤這次的、不動。「撤回後沒有譜」是推算的：
  撤掉這次的檔之後，那個聲部在 HuaXia 上已經沒有任何樂譜。那次新建的聲部有人放了別的檔時不刪（刪聲部會連帶刪掉
  它的樂譜），只撤這次的；排了人或有人設定過的也不刪，聲部留著但沒有譜。沒發成的群組沒有可撤的登記。
- 撤回（retract）：重查幹部身分，重讀 HuaXia 再算一次，以使用者身分經 REST 依序撤掉樂譜的登記、刪掉聲部、
  刪 Storage 的檔（經 orphan_cleanup.delete_unregistered，只刪那一刻沒有任何登記還指著的），最後寫一列列「撤回」到發譜紀錄（沿用那次的發譜編號）、
  各群組寫一筆紀錄檔。中途中斷時剩下的檔成為孤兒，重做一次撤回會收斂（已撤掉的略過，沒刪的檔再刪一次）。
連錯的那次新建了曲目時，撤回後留下一首空曲目，由幹部在網頁刪除（RetractGroup.new_piece）。
寫完「撤回」後更新收回提醒的摘要（有傳入時）。網路請求會卡住呼叫的執行緒，要在背景執行緒呼叫。

使用範例：
    from services.retract_service import RetractService
    retracter = RetractService(huaxia_auth, log)
    preview = retracter.preview(entry)                       # entry 是 publish_entries 的一項（一次發譜）
    result = retracter.retract(entry, [group_id], catalog)   # 勾選的群組
"""
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Set

from core.catalog_errors import CatalogError
from core.constants import HUAXIA_PART_FIELDS
from core.publish_log import PublishAction, PublishLogEntry, PublishRecord, failure_entry, score_entry
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.huaxia_auth_service import HuaXiaAuthService, NotOfficer
from services.huaxia_client import (
    HuaXiaError, HuaXiaPart, HuaXiaScore, is_lingling_path, lingling_storage_folder,
)
from services.huaxia_removal import HuaXiaRemoval
from services.recall_reminder import RecallReminder



class RetractEffect(Enum):
    """撤回後一個聲部（或總譜）的結果；依這個順序列出與計數"""

    NO_SCORE = "no_score"        # 撤回後沒有譜
    DELETE_PART = "delete_part"  # 刪掉聲部（這次新建、還沒排人）
    KEEP_PART = "keep_part"      # 聲部留著但沒有譜（這次新建、已經排人或有人在網頁上設定過）
    PARTIAL = "partial"          # 只撤這次的，別人放的留著
    UNTOUCHED = "untouched"      # 不動


class RetractBlock(Enum):
    """群組不能撤回（不能勾）的原因"""

    FAILED = "failed"     # 那次沒發成，沒有可撤的登記
    NOTHING = "nothing"   # 沒有東西可撤（例如已經撤回過）


class RetractReason(Enum):
    """歸到這一類的原因"""

    ONLY_THIS = "only_this"              # 這個聲部只有這次放的檔
    REPLACED = "replaced"                # 這次取代的舊檔不會回來
    NEW_UNASSIGNED = "new_unassigned"    # 這次新建、還沒排人
    ASSIGNED = "assigned"                # 這次新建，但已經排人
    CONFIGURED = "configured"            # 這次新建，但有人在網頁上設定過（小老師、備註、練習回報、改名）
    OTHERS_KEPT = "others_kept"          # 別人放的檔不動
    NEW_WITH_OTHERS = "new_with_others"  # 這次新建，但別人放了檔，聲部留著
    GONE = "gone"                        # 這次放的檔已不在
    NOT_TOUCHED = "not_touched"          # 這次沒動到


@dataclass(frozen=True)
class RetractRow:
    """撤回預覽的一列：曲目上的一個聲部（或總譜）現在與撤回後的樣子

    Attributes:
        name: HuaXia 上的聲部名稱；總譜為空字串
        effect: 撤回後的結果
        reason: 原因
        ours: 這次登記、現在還在的樂譜（會撤掉）
        others: 撤回後還留著的樂譜
        kept: 這次覆寫、撤回不改回的欄位：欄位名稱對應［原值，新值］
    """
    name: str
    effect: RetractEffect
    reason: RetractReason
    ours: List[HuaXiaScore] = field(default_factory=list)
    others: List[HuaXiaScore] = field(default_factory=list)
    kept: Dict[str, List[Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class RetractGroup:
    """一次發譜裡一個群組的撤回：逐聲部的結果與要撤掉的東西

    Attributes:
        record: 那次的發譜結果（沒發成的 failure 不為空）；那次沒有結果時是開始發譜（unfinished）
        rows: 逐聲部（依 HuaXia 的排序）加總譜；沒發成時為空
        scores: 要撤掉登記的樂譜（這次登記、現在還在的）
        parts: 要刪的聲部（這次新建、還沒排人、沒有別人的樂譜、沒人在網頁上設定過）
        files: 要刪的 Storage 檔（這次登記過、泠靈路徑格式、還在 Storage；刪之前再確認沒有任何登記指著）
    """
    record: PublishRecord
    rows: List[RetractRow] = field(default_factory=list)
    scores: List[HuaXiaScore] = field(default_factory=list)
    parts: List[HuaXiaPart] = field(default_factory=list)
    files: List[str] = field(default_factory=list)

    @property
    def group_id(self) -> str:
        """群組 ID"""
        return self.record.group_id

    @property
    def name(self) -> str:
        """群組名稱"""
        return self.record.group

    @property
    def piece_title(self) -> str:
        """HuaXia 曲名"""
        return self.record.piece_title

    @property
    def failed(self) -> bool:
        """那次沒發成（沒有可撤的登記）"""
        return self.record.failure is not None

    @property
    def unfinished(self) -> bool:
        """那次沒有發譜結果（中途停下或結果寫不進譜庫）：不知道 HuaXia 的編號，依那次要登記的 Storage 路徑撤掉現在
        還登記著的樂譜；不知道那次新建了哪些聲部，聲部一律留著"""
        return self.record.action is not PublishAction.RESULT

    @property
    def block(self) -> Optional[RetractBlock]:
        """不能撤回的原因；可以撤回時為 None"""
        if self.failed:
            return RetractBlock.FAILED
        if not self.actionable:
            return RetractBlock.NOTHING
        return None

    @property
    def new_piece(self) -> bool:
        """那次新建了曲目：撤回後留下一首空曲目，由幹部在網頁刪除"""
        return self.record.new_piece is not None

    @property
    def events(self) -> List[Dict[str, Any]]:
        """那次掛上的場次（撤回不動）"""
        return list(self.record.events)

    @property
    def actionable(self) -> bool:
        """有東西可撤"""
        return bool(self.scores or self.parts or self.files)

    @property
    def missing(self) -> int:
        """撤回後沒有譜的聲部數（含總譜；沒有譜、聲部留著但沒有譜）"""
        return sum(1 for row in self.rows if row.effect in (RetractEffect.NO_SCORE, RetractEffect.KEEP_PART))

    def counts(self) -> Dict[RetractEffect, int]:
        """各類的聲部數（含總譜），依 RetractEffect 的順序，沒有的為 0"""
        return {effect: sum(1 for row in self.rows if row.effect is effect) for effect in RetractEffect}


@dataclass(frozen=True)
class RetractPreview:
    """撤回的預覽：一次發譜的各群組

    Attributes:
        entry: 發譜紀錄列表裡的那次發譜
        groups: 各群組，依發譜紀錄的順序
    """
    entry: PublishLogEntry
    groups: List[RetractGroup]


@dataclass(frozen=True)
class RetractResult:
    """撤回的結果

    Attributes:
        groups: 撤回的群組（撤回那一刻讀到的現況與要撤的）
        records: 寫進發譜紀錄的列（做到的）；什麼都沒做時為空
        error: 中途停下的原因；做完了為 None
        log_error: 「撤回」寫不進譜庫（HuaXia 已照 records 變動）
        not_officer: 不是幹部（什麼都沒做，已登出）
    """
    groups: List[RetractGroup] = field(default_factory=list)
    records: List[PublishRecord] = field(default_factory=list)
    error: Optional[HuaXiaError] = None
    log_error: Optional[CatalogError] = None
    not_officer: Optional[NotOfficer] = None

    @property
    def missing(self) -> int:
        """撤回後沒有譜的聲部數"""
        return sum(group.missing for group in self.groups)

    @property
    def left_pieces(self) -> List[str]:
        """撤回後留下的空曲目（那次新建的）"""
        return [group.piece_title for group in self.groups if group.new_piece]


class _HuaXiaState(NamedTuple):
    """撤回要的 HuaXia 現況：曲目的聲部與樂譜，那次新建的聲部裡已排人的與有人設定過的，Storage 裡泠靈的檔"""
    parts: List[HuaXiaPart]
    scores: List[HuaXiaScore]
    assigned: Set[str]
    configured: Set[str]
    stored: Set[str]


class RetractService:
    """撤回某次發譜選定的群組：預覽（preview）與撤回（retract）"""

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
        self._removal = HuaXiaRemoval(auth, log, reminder)

    def preview(self, entry: PublishLogEntry) -> RetractPreview:
        """向 HuaXia 讀現況，算出每個群組撤回後的樣子

        Args:
            entry: 發譜紀錄列表裡的一次發譜

        Returns:
            預覽；每個群組各自算（要刪的檔只看這個群組自己撤掉的登記）

        Raises:
            HuaXiaError: 沒有登入、登入已失效或連不上 HuaXia
        """
        state = self._read(entry.groups)
        groups = [_plan(record, state) for record in entry.groups]
        return RetractPreview(entry, [_with_files(group, state) for group in groups])

    def retract(self, entry: PublishLogEntry, group_ids: Sequence[str], catalog: CatalogService) -> RetractResult:
        """撤回這次發譜裡這幾個群組：重查幹部身分、重讀現況，撤掉登記、刪聲部、刪檔，寫一列列「撤回」

        Args:
            entry: 發譜紀錄列表裡的一次發譜
            group_ids: 勾選的群組
            catalog: 譜庫服務（寫發譜紀錄）

        Returns:
            撤回的結果；HuaXia 寫入中途失敗與發譜紀錄寫不進去不丟出，記在結果裡

        Raises:
            HuaXiaError: 沒有登入、登入已失效，或寫入前就連不上 HuaXia（什麼都沒做）
        """
        not_officer = self._removal.recheck()
        if not_officer is not None:
            return RetractResult(not_officer=not_officer)
        chosen = [record for record in entry.groups if record.group_id in set(group_ids) and _retractable(record)]
        state = self._read(chosen)
        groups = [_with_files(_plan(record, state), state) for record in chosen]
        removed: List[str] = []
        deleted: List[str] = []
        files: List[str] = []
        error: Optional[HuaXiaError] = None
        try:
            self._removal.remove_scores([score.id for group in groups for score in group.scores], removed)
            self._removal.remove_parts([part.id for group in groups for part in group.parts], deleted)
            files = self._removal.remove_files(path for group in groups for path in group.files).deleted
        except HuaXiaError as e:
            error = e
        records = [_retract_record(group, set(removed), set(deleted), set(files), error) for group in groups]
        if error is not None and not (removed or deleted or files):
            records = []
        log_error = None
        if records:
            log_error = self._removal.write_log(
                catalog, lambda operator: catalog.record_publish(entry.publish_id, records, operator),
            )
        for group in groups:
            self._removal.record(
                "retract.log.retracted", "retract.log.stopped", error, piece_title=group.piece_title,
                file_name=group.name,
            )
        return RetractResult(groups, records, error, log_error)

    def _read(self, records: Sequence[PublishRecord]) -> _HuaXiaState:
        """讀這幾個群組的曲目現在的聲部、樂譜、Storage 裡泠靈的檔，以及那次新建的聲部有沒有排人"""
        token = self._auth.access_token()
        pieces = list(dict.fromkeys(record.piece_id for record in records if _retractable(record)))
        if not pieces:
            return _HuaXiaState([], [], set(), set(), set())
        parts = self._client.piece_parts(token, pieces)
        scores = self._client.scores(token, pieces)
        existing = {part.id for part in parts}
        created = [
            p["id"] for record in records if _retractable(record) and record.action is PublishAction.RESULT
            for p in record.parts_created if p.get("id") in existing
        ]
        assigned = self._client.assigned_parts(token, created) if created else set()
        configured = self._client.configured_parts(token, created) if created else set()
        stored: Set[str] = set()
        for piece_id in pieces:
            stored |= set(self._client.stored_files(token, lingling_storage_folder(piece_id)))
        return _HuaXiaState(parts, scores, assigned, configured, stored)



def retractable(entry: PublishLogEntry) -> bool:
    """發譜紀錄列表的這一項可以按「撤回…」：是一次發譜，而且有群組發成了，或那次沒有發譜結果（中途停下）"""
    return entry.action is PublishAction.START and any(_retractable(record) for record in entry.groups)


def _retractable(record: PublishRecord) -> bool:
    """這列記著可撤的東西：發成了的發譜結果（HuaXia 的編號），或沒有結果那次的開始發譜（要登記的 Storage 路徑）"""
    return record.action is PublishAction.START or (record.action is PublishAction.RESULT and record.failure is None)


def _plan(record: PublishRecord, state: _HuaXiaState) -> RetractGroup:
    """一個群組撤回後的樣子：逐聲部歸類，算出要撤的登記與要刪的聲部（要刪的檔由 _with_files 補上）

    Args:
        record: 這個群組的發譜結果
        state: HuaXia 現況
    """
    if not _retractable(record):
        return RetractGroup(record)
    scores = [score for score in state.scores if score.piece_id == record.piece_id]
    if record.action is PublishAction.START:
        # 沒有結果：不知道 HuaXia 的編號，依那次要登記的路徑認（只認泠靈的路徑）；那次新建了哪些聲部也不知道，不刪
        planned = {score.get("file_path") for score in record.scores_added}
        ours = {score.id for score in scores if score.lingling and score.file_path in planned}
        created: Dict[str, Optional[str]] = {}
    else:
        ours = {score["id"] for score in record.scores_added}
        created = {part["id"]: part.get("name") for part in record.parts_created}
    replaced = {score.get("part_id") for score in record.scores_removed}
    updated = {update["id"]: update for update in record.parts_updated}
    touched = {score.get("part_id") for score in record.scores_added} | {
        score.part_id for score in scores if score.id in ours
    }
    rows: List[RetractRow] = []
    removing: List[HuaXiaScore] = []
    deleting: List[HuaXiaPart] = []
    slots = [(part.name, part) for part in state.parts if part.piece_id == record.piece_id]
    for name, part in slots + [("", None)]:
        part_id = part.id if part is not None else None
        here = [s for s in scores if s.part_id == part_id and (part is not None or not s.section)]
        mine = [s for s in here if s.id in ours]
        others = [s for s in here if s.id not in ours]
        if part is None and not here and part_id not in touched:
            continue
        kept = {
            column: values for column, values in updated.get(part_id, {}).items()
            if column in HUAXIA_PART_FIELDS and isinstance(values, list)
        }
        new = part is not None and part.id in created
        # 有人在網頁上設定過的不刪：刪聲部會丟掉小老師、備註，練習回報也對不回聲部
        configured = new and (part.id in state.configured or part.name != created[part.id])
        if new and part.id not in state.assigned and not others and not configured:
            effect, reason = RetractEffect.DELETE_PART, RetractReason.NEW_UNASSIGNED
            deleting.append(part)
        elif not mine:
            effect = RetractEffect.UNTOUCHED
            reason = RetractReason.GONE if part_id in touched else RetractReason.NOT_TOUCHED
        elif others:
            effect = RetractEffect.PARTIAL
            reason = RetractReason.NEW_WITH_OTHERS if new and part.id not in state.assigned else RetractReason.OTHERS_KEPT
        elif new:
            reason = RetractReason.ASSIGNED if part.id in state.assigned else RetractReason.CONFIGURED
            effect = RetractEffect.KEEP_PART
        else:
            effect = RetractEffect.NO_SCORE
            reason = RetractReason.REPLACED if part_id in replaced else RetractReason.ONLY_THIS
        removing.extend(mine)
        rows.append(RetractRow(name, effect, reason, mine, others, kept))
    rows.sort(key=lambda row: list(RetractEffect).index(row.effect))
    return RetractGroup(record, rows, removing, deleting)


def _with_files(group: RetractGroup, state: _HuaXiaState) -> RetractGroup:
    """補上要刪的 Storage 檔：這次登記過、泠靈路徑格式、還在 Storage 的（已經撤掉登記、檔還在的也算，重做撤回
    因此會收斂）；真的刪之前由 delete_unregistered 確認沒有任何登記還指著"""
    if not _retractable(group.record):
        return group
    paths = dict.fromkeys(score["file_path"] for score in group.record.scores_added)
    return replace(group, files=[path for path in paths if is_lingling_path(path) and path in state.stored])


def _retract_record(
    group: RetractGroup, removed: Set[str], deleted: Set[str], files: Set[str], error: Optional[HuaXiaError],
) -> PublishRecord:
    """發譜紀錄裡這個群組的「撤回」：做到的（撤掉的登記、刪掉的聲部與檔）與中途停下的原因"""
    record = group.record
    return PublishRecord(
        PublishAction.RETRACT, group_id=record.group_id, group=record.group, piece_id=record.piece_id,
        piece_title=record.piece_title,
        scores_removed=[score_entry(score) for score in group.scores if score.id in removed],
        parts_deleted=[{"id": p.id, "name": p.name} for p in group.parts if p.id in deleted],
        files_deleted=[path for path in group.files if path in files],
        failure=None if error is None else failure_entry(error.code, error.detail or str(error)),
    )
