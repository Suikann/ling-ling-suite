# -*- coding: utf-8 -*-
"""
撤掉一個群組寫進譜庫某處的編制：哪些撤得掉、撤掉後是什麼

群組改連到別處後，原本那處還留著它寫進去的編制。依編制寫入紀錄，把這個群組在那處寫過的聲部逐一分成三種（RevertOutcome）：
它新增、之後沒人改過的聲部刪掉；它改過人數、之後沒人改過的改回原值；其餘不動，並寫出原因（KeepReason）。
撤銷存入的「一起撤掉編制」共用這套判斷與確認框的四欄表。

- 只撤聲部的增刪與人數；順序、時長與聲部組不撤，這個群組刪掉的聲部也不加回去
- 紀錄的哪些列是這個群組寫的，以群組 ID 認（同名的群組分得開，群組改名後也認得）；沒有群組 ID 的舊列以群組名稱認
- 「之後沒人改過」以紀錄判斷：這個群組在那處對一個聲部的寫入之後，同一聲部的增刪或人數還有別人（別的群組、譜庫視窗）
  的寫入，就是之後被改過，寫出最後是誰在何時改的；紀錄裡沒有、譜庫現在的值卻跟它寫的不同，是在試算表上被改過
- 新增的聲部已登記 Drive 檔案時不刪
- 這個群組撤掉過的（紀錄的來源是撤銷）不再列

這裡只有純邏輯；讀紀錄與那處在 services/catalog_service.py 的 CatalogService.revert_rows，
寫入走存檔寫回的同一套比對（core/catalog_sync.py 的 place_writes）。

使用範例：
    from core.catalog_revert import pending_revert, revert_count
    rows = catalog.revert_rows(link.edition_id, link.movement_id, group.id, group.name, project.orchestra_type)
    if revert_count(rows):
        project.queue_catalog_revert(group, pending_revert(link.edition_id, link.movement_id, rows))
"""
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, NamedTuple, Optional, Sequence, Tuple

from core.catalog_models import InstrumentationLogEntry
from core.catalog_sync import SyncField, WriteSource
from core.models import CatalogRevert, Headcount
from core.voice_names import VoiceKey, voice_key

# 撤得掉的欄位：聲部的增刪與人數
_FIELDS = (SyncField.VOICE.value, SyncField.HEADCOUNT.value)


class RevertOutcome(Enum):
    """一個聲部撤掉後的結果"""
    REMOVE = "remove"    # 刪掉這個聲部
    RESTORE = "restore"  # 人數改回原值
    KEEP = "keep"        # 不動


class KeepReason(Enum):
    """不動的原因"""
    CHANGED = "changed"  # 之後被別人改過，紀錄寫著誰在何時改的
    EDITED = "edited"    # 譜庫現在的值跟這個群組寫的不同，紀錄裡沒有（在試算表上改的）
    FILED = "filed"      # 新增的聲部已登記 Drive 檔案


class RevertRow(NamedTuple):
    """四欄表的一列：一個聲部那次寫的、譜庫現在的與撤掉後的

    Attributes:
        voice: 聲部（這個群組最後寫的寫法）
        added: 這個群組新增了這個聲部
        written: 這個群組最後寫的人數；只新增、沒寫人數時為未填
        current: 譜庫現在的人數；譜庫已沒有這個聲部時為 None
        outcome: 撤掉後的結果
        original: 改回的人數（RESTORE）
        keep_reason: 不動的原因（KEEP）
        changed_by: 之後改過的人（KeepReason.CHANGED）
        changed_at: 之後改過的時間，紀錄裡的寫法（KeepReason.CHANGED）
    """
    voice: str
    added: bool
    written: Headcount
    current: Optional[Headcount]
    outcome: RevertOutcome
    original: Headcount = Headcount()
    keep_reason: Optional[KeepReason] = None
    changed_by: str = ""
    changed_at: str = ""


@dataclass
class _Run:
    """這個群組對一個聲部接連的寫入（中間沒有別人的寫入）"""
    voice: str
    added: bool = False
    deleted: bool = False
    original: Optional[str] = None
    written: Optional[str] = None
    changed: Optional[InstrumentationLogEntry] = None


def revert_rows(
    entries: Sequence[InstrumentationLogEntry], edition_id: str, movement_id: str,
    voices: Sequence[Tuple[str, Headcount]], filed: Sequence[str], group_id: str, group_name: str, until: str = "",
) -> List[RevertRow]:
    """這個群組寫進一處的編制，每個聲部一列，依譜庫現在的順序（譜庫已沒有的排在最後）

    Args:
        entries: 編制寫入紀錄的全部列，依寫入的先後
        edition_id: 那處的版本 ID
        movement_id: 那處的樂章 ID；整首為空字串
        voices: 那處現在的聲部與人數，依譜庫的順序
        filed: 那處已登記 Drive 檔案的聲部
        group_id: 群組 ID（紀錄的群組 ID 欄）
        group_name: 群組名稱（紀錄的「群組」欄；沒有群組 ID 的舊列以它認）
        until: 只看這個時間（紀錄的寫法）以前這個群組的寫入，之後它自己的寫入也算之後被改過（撤銷存入只撤那次存入
            之前寫的）；空字串表示不限

    Returns:
        每個這個群組寫過的聲部一列；它刪掉的聲部與人數寫回原值的不列
    """
    runs: Dict[VoiceKey, _Run] = {}
    for entry in entries:
        if (entry.edition_id, entry.movement_id) != (edition_id, movement_id) or entry.field not in _FIELDS:
            continue
        key = voice_key(entry.voice)
        mine = entry.written_by(group_id, group_name) and (not until or entry.time <= until)
        if mine and entry.source == WriteSource.UNDO.value:
            runs.pop(key, None)
        elif mine and entry.source == WriteSource.SAVE.value:
            run = runs.get(key)
            if run is None or run.changed is not None:
                run = runs[key] = _Run(entry.voice)
            run.voice = entry.voice
            if entry.field == SyncField.VOICE.value:
                run.added, run.deleted = bool(entry.new_value), not entry.new_value
            else:
                run.original = entry.old_value if run.original is None else run.original
                run.written = entry.new_value
        elif key in runs:
            runs[key].changed = entry
    current = {voice_key(name): headcount for name, headcount in voices}
    order = [voice_key(name) for name, _ in voices]
    filed_keys = {voice_key(name) for name in filed}
    rows = []
    for key, run in runs.items():
        row = None if run.deleted else _row(run, current.get(key), key in filed_keys)
        if row is not None:
            rows.append(row)
    return sorted(rows, key=lambda row: _position(order, voice_key(row.voice)))


def _row(run: _Run, current: Optional[Headcount], filed: bool) -> Optional[RevertRow]:
    """一個聲部的那一列；人數寫回原值、沒什麼好撤的時為 None"""
    written = Headcount.from_text(run.written or "")
    row = RevertRow(run.voice, run.added, written, current, RevertOutcome.KEEP)
    if run.changed is not None:
        return row._replace(
            keep_reason=KeepReason.CHANGED, changed_by=run.changed.operator, changed_at=run.changed.time,
        )
    if current is None or (run.written is not None and current != written):
        return row._replace(keep_reason=KeepReason.EDITED)
    if run.added:
        return row._replace(keep_reason=KeepReason.FILED) if filed else row._replace(outcome=RevertOutcome.REMOVE)
    original = Headcount.from_text(run.original or "")
    if original == written:
        return None
    return row._replace(outcome=RevertOutcome.RESTORE, original=original)


def _position(order: List[VoiceKey], key: VoiceKey) -> int:
    """聲部在譜庫現在的順序；譜庫已沒有時排在最後"""
    return order.index(key) if key in order else len(order)


def revert_count(rows: Sequence[RevertRow]) -> int:
    """撤得掉的聲部數（刪掉與改回原值）"""
    return sum(1 for row in rows if row.outcome is not RevertOutcome.KEEP)


def pending_revert(edition_id: str, movement_id: str, rows: Sequence[RevertRow]) -> CatalogRevert:
    """選了「撤掉」：記成存檔時要寫進那處的撤掉（只含撤得掉的聲部）

    Args:
        edition_id: 那處的版本 ID
        movement_id: 那處的樂章 ID；整首為空字串
        rows: revert_rows 的結果

    Returns:
        要寫的撤掉：刪掉的聲部，與改回原值的聲部連同那次寫的人數
    """
    return CatalogRevert(
        edition_id, movement_id,
        remove=[row.voice for row in rows if row.outcome is RevertOutcome.REMOVE],
        restore=[(row.voice, row.written, row.original) for row in rows if row.outcome is RevertOutcome.RESTORE],
    )
