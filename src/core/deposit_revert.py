# -*- coding: utf-8 -*-
"""
撤銷存入的判斷

存錯版本或樂章時，從存入紀錄挑一次存入、再挑其中的群組，撤掉那次的結果。這裡是純邏輯：
存入紀錄分成一次一次的存入（deposits），以及依讀到的譜庫現況決定那次每份檔撤銷後的下場（revert_preview）。
讀譜庫在 services/catalog_service.py 的 CatalogService.revert_reading，照決定去做在 services/deposit_revert_service.py。

每份檔的下場（RevertAction）只看 Drive 檔案現在的版本 ID 是不是還是那次寫入的版本：
- 那次新上傳的：移除分譜列的登記，檔丟進 Drive 垃圾桶（不永久刪除）；已經在垃圾桶裡的只移除登記
- 那次更新內容的：被蓋掉的舊版本 Drive 還留著就還原存入前的內容，已經被 Drive 清掉的無法還原
- 那次登記的是 Drive 上原有、內容相同的同名檔：只移除登記，檔不動
- 其餘不動（KeptBecause）：已經撤銷過、之後又存入過（存入紀錄裡之後還有這個檔）、之後 Drive 上的內容改過、
  Drive 上已沒有這個檔或在垃圾桶裡、那次只改了檔名或位置、那次沒有改動
本機已清除（只在譜庫）的檔要撤銷時，先下載回原位並核對，才動 Drive 上的那份。

「一起撤掉編制」沿用改連的判斷（core/catalog_revert.py 的 revert_rows），只看那次存入之前這個群組寫進那處的編制；
這次會移除登記的聲部不算已登記 Drive 檔案，所以那次新增、連同分譜一起存入的聲部也撤得掉。

使用範例：
    from core.deposit_revert import deposits, revert_preview
    history = deposits(catalog.deposit_log(), edition_id)
    preview = revert_preview(catalog.revert_reading(deposit_id, orchestra_type), deposit_id, catalog_only)
"""
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Collection, Dict, List, Optional, Sequence, Tuple

from core.catalog_models import CatalogPlace, DepositLogEntry, DriveFile, DriveRevision, InstrumentationLogEntry
from core.catalog_revert import RevertRow, revert_rows
from core.deposit import DepositAction
from core.models import Headcount
from core.paths import path_key


class RevertAction(Enum):
    """撤銷存入時一份檔的下場；做了的（TRASH、RESTORE、UNREGISTER）把值寫進存入紀錄的動作欄"""
    TRASH = "trash"            # 那次新上傳的：移除登記、丟進 Drive 垃圾桶
    RESTORE = "restore"        # 那次更新內容的：還原存入前的內容
    UNREGISTER = "unregister"  # 那次登記的是 Drive 上原有的檔：只移除登記
    LOST = "lost"              # 那次更新內容的，舊內容已被 Drive 刪除：無法還原
    KEEP = "keep"              # 不動


# 撤銷時要做的下場
REVERTING = frozenset({RevertAction.TRASH, RevertAction.RESTORE, RevertAction.UNREGISTER})
# 要移除分譜列登記的下場
UNREGISTERING = frozenset({RevertAction.TRASH, RevertAction.UNREGISTER})


class KeptBecause(Enum):
    """一份檔不動的原因"""
    REVERTED = "reverted"        # 已經撤銷過
    REDEPOSITED = "redeposited"  # 之後又存入過
    CHANGED = "changed"          # 之後 Drive 上的內容改過
    GONE = "gone"                # Drive 上已沒有這個檔，或在垃圾桶裡
    MOVED = "moved"              # 那次只改了檔名或位置
    SKIPPED = "skipped"          # 那次沒有改動


@dataclass(frozen=True)
class Deposit:
    """存入紀錄裡的一次存入（或一次撤銷存入）

    Attributes:
        deposit_id: 存入編號
        time: 時間（紀錄的寫法）
        operator: 操作者
        entries: 這次的每份檔，依紀錄的順序
        reverts: 撤銷存入時被撤銷的存入編號；存入為空字串
    """
    deposit_id: str
    time: str
    operator: str
    entries: List[DepositLogEntry] = field(default_factory=list)
    reverts: str = ""

    @property
    def groups(self) -> List[str]:
        """這次的群組名稱，依紀錄的順序、不重複"""
        return list(dict.fromkeys(entry.group for entry in self.entries))


@dataclass(frozen=True)
class RevertPlace:
    """撤銷時讀到的譜庫裡的一處

    Attributes:
        place: 那處的曲名、版本、樂章、聲部與人數；那處已不在譜庫時為 None
        registered: 分譜列（不含總譜列）登記的 Drive 檔案 ID → 聲部
    """
    place: Optional[CatalogPlace]
    registered: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RevertReading:
    """撤銷一次存入要讀的譜庫現況（CatalogService.revert_reading）

    Attributes:
        entries: 存入紀錄的全部列，依紀錄的順序
        files: 那次每份檔的 Drive 檔案 ID → 現在的樣子；Drive 上已沒有時為 None
        revisions: 那次更新內容、內容沒再變過的檔 → Drive 留著的版本（needs_revisions 為 True 的才讀）
        places: 那次存入的各處（版本 ID，樂章 ID）→ 現況
        instrumentation: 編制寫入紀錄的全部列，依寫入的先後
    """
    entries: List[DepositLogEntry]
    files: Dict[str, Optional[DriveFile]] = field(default_factory=dict)
    revisions: Dict[str, List[DriveRevision]] = field(default_factory=dict)
    places: Dict[Tuple[str, str], RevertPlace] = field(default_factory=dict)
    instrumentation: List[InstrumentationLogEntry] = field(default_factory=list)


@dataclass(frozen=True)
class RevertFile:
    """那次存入的一份檔撤銷後的下場

    Attributes:
        entry: 存入紀錄裡的那一列
        action: 下場
        file: Drive 上現在的樣子；已沒有時為 None
        restore_to: 要還原成的舊版本（RESTORE）
        kept: 不動的原因（KEEP）
        later: 之後又存入或撤銷過這個檔的那一列（KeptBecause.REDEPOSITED、REVERTED）
        download: 本機那份已清除（只在譜庫），撤銷前要先下載回原位
    """
    entry: DepositLogEntry
    action: RevertAction
    file: Optional[DriveFile] = None
    restore_to: Optional[DriveRevision] = None
    kept: Optional[KeptBecause] = None
    later: Optional[DepositLogEntry] = None
    download: bool = False


@dataclass(frozen=True)
class RevertGroup:
    """那次存入的一個群組

    Attributes:
        name: 群組名稱（紀錄的「群組」欄）
        edition_id: 連到的版本 ID
        movement_id: 連到的樂章 ID；整首為空字串
        place: 那處的曲名、版本、樂章；已不在譜庫時為 None
        files: 每份檔的下場，依紀錄的順序
        instrumentation: 一起撤掉編制時，這個群組在那次存入之前寫進那處的聲部（core/catalog_revert.py 的四欄表）
    """
    name: str
    edition_id: str
    movement_id: str
    place: Optional[CatalogPlace]
    files: List[RevertFile] = field(default_factory=list)
    instrumentation: List[RevertRow] = field(default_factory=list)

    def counts(self) -> Dict[RevertAction, int]:
        """各下場的份數（沒有的不列）"""
        result: Dict[RevertAction, int] = {}
        for file in self.files:
            result[file.action] = result.get(file.action, 0) + 1
        return result

    def reverting(self) -> List[RevertFile]:
        """撤銷時要做的檔"""
        return [file for file in self.files if file.action in REVERTING]

    def downloads(self) -> List[RevertFile]:
        """撤銷前要先下載回原位的檔"""
        return [file for file in self.reverting() if file.download]


@dataclass(frozen=True)
class RevertPreview:
    """撤銷一次存入的預覽：那次存入與它的每個群組（依紀錄的順序）"""
    deposit: Deposit
    groups: List[RevertGroup] = field(default_factory=list)


@dataclass(frozen=True)
class RevertChoice:
    """選了要撤銷的一個群組，與要不要一起撤掉它寫進那處的編制"""
    group: RevertGroup
    instrumentation: bool = False


def deposits(entries: Sequence[DepositLogEntry], edition_id: str = "") -> List[Deposit]:
    """把存入紀錄分成一次一次的存入與撤銷存入，新的在前

    Args:
        entries: 存入紀錄的全部列，依紀錄的順序
        edition_id: 只列動到這個版本的；空字串表示全部

    Returns:
        每個存入編號一個
    """
    grouped: Dict[str, List[DepositLogEntry]] = {}
    for entry in entries:
        if entry.deposit_id:
            grouped.setdefault(entry.deposit_id, []).append(entry)
    result = [
        Deposit(deposit_id, rows[0].time, rows[0].operator, rows, rows[0].reverts)
        for deposit_id, rows in grouped.items()
        if not edition_id or any(row.edition_id == edition_id for row in rows)
    ]
    return list(reversed(result))


def needs_revisions(entry: DepositLogEntry, file: Optional[DriveFile]) -> bool:
    """這份檔要不要讀 Drive 留著的版本：那次更新了內容，而 Drive 上那份還在、內容沒再變過"""
    return (
        entry.action == DepositAction.UPDATE.value and file is not None and not file.trashed
        and file.head_revision == entry.revision_after
    )


def revert_preview(reading: RevertReading, deposit_id: str, catalog_only: Collection[str]) -> RevertPreview:
    """依讀到的譜庫現況，決定撤銷那次存入時每個群組每份檔的下場（規則見模組說明）

    Args:
        reading: 讀到的譜庫現況
        deposit_id: 要撤銷的存入編號
        catalog_only: 本機已清除、只在譜庫的檔的本機路徑（呼叫端以專案判定；路徑以 core.paths 判定同一性）

    Returns:
        那次存入與它的每個群組；存入紀錄裡沒有這個存入編號時沒有群組
    """
    cleared = {path_key(path) for path in catalog_only}
    deposit = next((d for d in deposits(reading.entries) if d.deposit_id == deposit_id), None)
    if deposit is None:
        return RevertPreview(Deposit(deposit_id, "", ""))
    start = reading.entries.index(deposit.entries[0])
    afterwards = [entry for entry in reading.entries[start:] if entry.deposit_id != deposit_id]
    files: Dict[Tuple[str, str, str], List[RevertFile]] = {}
    for entry in deposit.entries:
        later = [other for other in afterwards if other.drive_file_id == entry.drive_file_id]
        outcome = _classify(entry, reading.files.get(entry.drive_file_id), reading.revisions.get(entry.drive_file_id), later)
        if outcome.action in REVERTING and path_key(entry.local_path) in cleared:
            outcome = replace(outcome, download=True)
        files.setdefault((entry.group, entry.edition_id, entry.movement_id), []).append(outcome)
    groups = [
        _group(name, edition_id, movement_id, group_files, reading, deposit.time)
        for (name, edition_id, movement_id), group_files in files.items()
    ]
    return RevertPreview(deposit, groups)


def _classify(
    entry: DepositLogEntry, file: Optional[DriveFile], revisions: Optional[List[DriveRevision]],
    later: Sequence[DepositLogEntry],
) -> RevertFile:
    """一份檔的下場"""
    reverted = next((other for other in later if other.reverts == entry.deposit_id), None)
    if reverted is not None:
        return RevertFile(entry, RevertAction.KEEP, file, kept=KeptBecause.REVERTED, later=reverted)
    if later:
        return RevertFile(entry, RevertAction.KEEP, file, kept=KeptBecause.REDEPOSITED, later=later[-1])
    if entry.action == DepositAction.MOVE.value:
        return RevertFile(entry, RevertAction.KEEP, file, kept=KeptBecause.MOVED)
    if entry.action not in (DepositAction.NEW.value, DepositAction.UPDATE.value, DepositAction.REGISTER.value):
        return RevertFile(entry, RevertAction.KEEP, file, kept=KeptBecause.SKIPPED)
    if file is None:
        return RevertFile(entry, RevertAction.KEEP, file, kept=KeptBecause.GONE)
    if entry.action == DepositAction.NEW.value and file.trashed:
        # 上次撤銷丟進垃圾桶後沒寫進紀錄（例如登記寫不進去）：只補移除登記
        return RevertFile(entry, RevertAction.TRASH, file)
    if file.trashed:
        return RevertFile(entry, RevertAction.KEEP, file, kept=KeptBecause.GONE)
    if file.head_revision != entry.revision_after:
        return RevertFile(entry, RevertAction.KEEP, file, kept=KeptBecause.CHANGED)
    if entry.action == DepositAction.NEW.value:
        return RevertFile(entry, RevertAction.TRASH, file)
    if entry.action == DepositAction.REGISTER.value:
        return RevertFile(entry, RevertAction.UNREGISTER, file)
    previous = next((revision for revision in revisions or [] if revision.id == entry.revision_before), None)
    if previous is None:
        return RevertFile(entry, RevertAction.LOST, file)
    return RevertFile(entry, RevertAction.RESTORE, file, restore_to=previous)


def _group(
    name: str, edition_id: str, movement_id: str, files: List[RevertFile], reading: RevertReading, until: str,
) -> RevertGroup:
    """一個群組：每份檔的下場，加上那次存入之前它寫進那處、撤得掉的編制"""
    here = reading.places.get((edition_id, movement_id), RevertPlace(None))
    if here.place is None:
        return RevertGroup(name, edition_id, movement_id, None, files)
    leaving = {file.entry.drive_file_id for file in files if file.action in UNREGISTERING}
    filed = [voice for file_id, voice in here.registered.items() if file_id not in leaving]
    voices: List[Tuple[str, Headcount]] = list(here.place.voices)
    rows = revert_rows(reading.instrumentation, edition_id, movement_id, voices, filed, name, until)
    return RevertGroup(name, edition_id, movement_id, here.place, files, rows)
