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
- 其餘不動（KeptBecause）：已經撤銷過、之後又存入過（存入紀錄裡之後還有動到這個檔的存入，跳過的不算）、
  之後 Drive 上的內容改過、Drive 上已沒有這個檔或在垃圾桶裡、那次只改了檔名或位置、那次沒有改動、那次中斷沒做到

存入前先寫的「要做」列（DEPOSIT_PENDING）有同一次存入做了的列時以做了的為準；只有「要做」列的檔（中途停下，
或登記寫不進去）照 Drive 現況判斷那次做到了沒有（_settled）：新上傳的以預先要到的 ID 找得到檔就是做到了，
寫入後的版本是那個檔的第一個版本；更新內容的，目前版本不是被蓋掉的版本、而 Drive 的版本裡緊接在被蓋掉的版本之後的
就是寫入後的版本。所以登記寫不進去時，蓋掉的內容照樣撤得掉。
本機已清除（只在譜庫）的檔要撤銷時，先下載回原位並核對，才動 Drive 上的那份。

撤銷不毀掉唯一的一份（RevertBlock）：要丟進垃圾桶或還原的檔，Drive 上那份可能是那次存入內容唯一的一份，所以
群組要在開著的專案裡（存入紀錄的群組 ID；舊的列沒有時以群組名稱認），而且每份這樣的檔本機都有內容相同的一份
（MD5 相同），或本機已清除、會先下載回原位；不然整個群組不撤銷。只移除登記的檔不動 Drive，不必有本機那份。

「一起撤掉編制」沿用改連的判斷（core/catalog_revert.py 的 revert_rows），只看那次存入之前這個群組寫進那處的編制；
這次會移除登記的聲部不算已登記 Drive 檔案，所以那次新增、連同分譜一起存入的聲部也撤得掉。

使用範例：
    from core.deposit_revert import deposits, revert_preview
    history = deposits(catalog.deposit_log(), edition_id)
    local = LocalProject.of(project, catalog_only_paths)
    preview = revert_preview(catalog.revert_reading(deposit_id, orchestra_type), deposit_id, local, local_md5s)
"""
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Collection, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from core.catalog_models import CatalogPlace, DepositLogEntry, DriveFile, DriveRevision, InstrumentationLogEntry
from core.catalog_revert import RevertRow, revert_rows
from core.deposit import DepositAction
from core.models import Headcount, Project
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


class RevertBlock(Enum):
    """整個群組不撤銷的原因：撤銷會毀掉那次存入內容唯一的一份"""
    NOT_OPEN = "not_open"            # 存入時的專案沒開著，核對不到本機那份
    NO_LOCAL_COPY = "no_local_copy"  # 本機沒有內容相同的一份（找不到、或之後改過）


class KeptBecause(Enum):
    """一份檔不動的原因"""
    REVERTED = "reverted"        # 已經撤銷過
    REDEPOSITED = "redeposited"  # 之後又存入過
    CHANGED = "changed"          # 之後 Drive 上的內容改過
    GONE = "gone"                # Drive 上已沒有這個檔，或在垃圾桶裡
    MOVED = "moved"              # 那次只改了檔名或位置
    SKIPPED = "skipped"          # 那次沒有改動
    UNFINISHED = "unfinished"    # 那次中斷，沒做到這一份


@dataclass(frozen=True)
class Deposit:
    """存入紀錄裡的一次存入（或一次撤銷存入）

    Attributes:
        deposit_id: 存入編號
        time: 時間（紀錄的寫法）
        operator: 操作者
        entries: 這次的每份檔，依紀錄的順序（有做了的列時是做了的那列，不然是「要做」列）
        reverts: 撤銷存入時被撤銷的存入編號；存入為空字串
    """
    deposit_id: str
    time: str
    operator: str
    entries: List[DepositLogEntry] = field(default_factory=list)
    reverts: str = ""

    @property
    def unfinished(self) -> List[DepositLogEntry]:
        """只有「要做」列、沒有做了的列的檔（那次中途停下，或登記寫不進去）"""
        return [entry for entry in self.entries if entry.pending]

    @property
    def completed(self) -> int:
        """這次做了（寫進存入紀錄）的份數"""
        return sum(1 for entry in self.entries if not entry.pending)

    @property
    def groups(self) -> List[str]:
        """這次的群組名稱，依紀錄的順序；同一個群組只列一次"""
        return list({entry.group_key: entry.group for entry in self.entries}.values())


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
class LocalProject:
    """撤銷時開著的專案：那次存入的群組在不在裡面、哪些檔本機已清除（呼叫端在主執行緒以專案與磁碟現況判定）

    Attributes:
        group_ids: 專案裡的群組 ID
        group_names: 專案裡的群組名稱（存入紀錄的舊列沒有群組 ID 時以名稱認）
        catalog_only: 本機已清除、只在譜庫的檔的路徑比對鍵（core.paths.path_key）
    """
    group_ids: FrozenSet[str] = frozenset()
    group_names: FrozenSet[str] = frozenset()
    catalog_only: FrozenSet[str] = frozenset()

    @classmethod
    def of(cls, project: Project, catalog_only: Iterable[str]) -> "LocalProject":
        """開著的專案

        Args:
            project: 開著的專案
            catalog_only: 那次存入的檔裡，本機已清除、只在譜庫的本機路徑（Project.catalog_only_paths）
        """
        return cls(
            frozenset(group.id for group in project.groups), frozenset(group.name for group in project.groups),
            frozenset(path_key(path) for path in catalog_only),
        )

    def has_group(self, entry: DepositLogEntry) -> bool:
        """存入這一列的群組在不在這個專案裡"""
        return entry.group_id in self.group_ids if entry.group_id else entry.group in self.group_names


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

    @property
    def destroys(self) -> bool:
        """撤銷時會動到 Drive 上的內容（丟進垃圾桶或還原舊版本），本機要有那次存入的內容"""
        return (
            self.action is RevertAction.RESTORE
            or (self.action is RevertAction.TRASH and self.file is not None and not self.file.trashed)
        )


@dataclass(frozen=True)
class RevertGroup:
    """那次存入的一個群組

    Attributes:
        group_id: 群組 ID（存入紀錄的群組 ID 欄；舊的列沒有時為空字串）
        name: 群組名稱（紀錄的「群組」欄）
        edition_id: 連到的版本 ID
        movement_id: 連到的樂章 ID；整首為空字串
        place: 那處的曲名、版本、樂章；已不在譜庫時為 None
        files: 每份檔的下場，依紀錄的順序
        instrumentation: 一起撤掉編制時，這個群組在那次存入之前寫進那處的聲部（core/catalog_revert.py 的四欄表）
        block: 整個群組不撤銷的原因；可以撤銷時為 None
        uncovered: 本機沒有內容相同的一份、又不能先下載回來的檔（RevertBlock.NO_LOCAL_COPY）
    """
    group_id: str
    name: str
    edition_id: str
    movement_id: str
    place: Optional[CatalogPlace]
    files: List[RevertFile] = field(default_factory=list)
    instrumentation: List[RevertRow] = field(default_factory=list)
    block: Optional[RevertBlock] = None
    uncovered: List[RevertFile] = field(default_factory=list)

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

    def kept_locally(self) -> List[RevertFile]:
        """要動 Drive 上的內容、本機留著那次存入內容的檔（撤銷前一刻再核對一次 MD5）"""
        return [file for file in self.reverting() if file.destroys and not file.download]


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
    result = []
    for deposit_id, rows in grouped.items():
        done = {_file_key(row) for row in rows if not row.pending}
        kept = [row for row in rows if not row.pending or _file_key(row) not in done]
        if not edition_id or any(row.edition_id == edition_id for row in kept):
            result.append(Deposit(deposit_id, kept[0].time, kept[0].operator, kept, kept[0].reverts))
    return list(reversed(result))


def _file_key(entry: DepositLogEntry) -> Tuple[str, str, str, str]:
    """同一次存入裡認出同一份檔：處、聲部與 Drive 檔案（「要做」列與做了的列相同）"""
    return entry.edition_id, entry.movement_id, entry.voice, entry.drive_file_id


def interrupted(entries: Sequence[DepositLogEntry], group_ids: Collection[str], group_names: Collection[str]) -> Optional[int]:
    """這些群組最近一次存入有沒有中斷（中途停下，或登記寫不進去）：存入預覽的「上次中斷」提示

    Args:
        entries: 存入紀錄的全部列，依紀錄的順序
        group_ids: 專案的群組 ID
        group_names: 專案的群組名稱（舊的列沒有群組 ID 時以名稱認）

    Returns:
        中斷時是那次做了幾份；最近一次沒有中斷、或還沒存入過時為 None
    """
    def ours(entry: DepositLogEntry) -> bool:
        """這一列是不是這個專案的群組存入的"""
        return entry.group_id in group_ids if entry.group_id else entry.group in group_names
    last = next((d for d in deposits(entries) if not d.reverts and any(ours(e) for e in d.entries)), None)
    return last.completed if last is not None and last.unfinished else None


def needs_revisions(entry: DepositLogEntry, file: Optional[DriveFile]) -> bool:
    """這份檔要不要讀 Drive 留著的版本：那次更新了內容，而 Drive 上那份還在、內容沒再變過；
    只有「要做」列的新上傳與更新內容，Drive 上那份還在時也要（判斷那次做到了沒有）"""
    if file is None or file.trashed:
        return False
    if entry.pending:
        return entry.action == DepositAction.NEW.value or (
            entry.action == DepositAction.UPDATE.value and file.head_revision != entry.revision_before
        )
    return entry.action == DepositAction.UPDATE.value and file.head_revision == entry.revision_after


def revert_preview(
    reading: RevertReading, deposit_id: str, local: LocalProject, local_md5s: Mapping[str, str],
) -> RevertPreview:
    """依讀到的譜庫現況，決定撤銷那次存入時每個群組每份檔的下場與不撤銷的群組（規則見模組說明）

    Args:
        reading: 讀到的譜庫現況
        deposit_id: 要撤銷的存入編號
        local: 開著的專案
        local_md5s: 那次存入的檔裡本機有的：路徑比對鍵（core.paths.path_key）→ 本機檔案的 MD5

    Returns:
        那次存入與它的每個群組；存入紀錄裡沒有這個存入編號時沒有群組
    """
    cleared = local.catalog_only
    deposit = next((d for d in deposits(reading.entries) if d.deposit_id == deposit_id), None)
    if deposit is None:
        return RevertPreview(Deposit(deposit_id, "", ""))
    start = reading.entries.index(deposit.entries[0])
    # 之後動到同一個檔的存入與撤銷：跳過的與只有「要做」列的不算（之後的內容變動另以版本 ID 判斷）
    afterwards = [
        entry for entry in reading.entries[start:]
        if entry.deposit_id != deposit_id and not entry.pending and entry.action != DepositAction.SKIP.value
    ]
    files: Dict[Tuple[str, str, str], List[RevertFile]] = {}
    for entry in deposit.entries:
        later = [other for other in afterwards if other.drive_file_id == entry.drive_file_id]
        file = reading.files.get(entry.drive_file_id)
        revisions = reading.revisions.get(entry.drive_file_id)
        settled = _settled(entry, file, revisions)
        if settled is None:
            outcome = RevertFile(entry, RevertAction.KEEP, file, kept=KeptBecause.UNFINISHED)
        else:
            outcome = replace(_classify(settled, file, revisions, later), entry=entry)
        if outcome.action in REVERTING and path_key(entry.local_path) in cleared:
            outcome = replace(outcome, download=True)
        files.setdefault((entry.group_key, entry.edition_id, entry.movement_id), []).append(outcome)
    groups = [
        _guard(_group(group_files[0].entry, group_files, reading, deposit.time), local, local_md5s)
        for group_files in files.values()
    ]
    return RevertPreview(deposit, groups)


def _guard(group: RevertGroup, local: LocalProject, local_md5s: Mapping[str, str]) -> RevertGroup:
    """撤銷會毀掉唯一的一份時整個群組不撤銷：群組不在開著的專案裡，或要動 Drive 內容的檔本機沒有內容相同的一份"""
    destroying = [file for file in group.reverting() if file.destroys]
    if not destroying:
        return group
    if not local.has_group(destroying[0].entry):
        return replace(group, block=RevertBlock.NOT_OPEN)
    uncovered = [
        file for file in destroying
        if not file.download and local_md5s.get(path_key(file.entry.local_path)) != file.file.md5
    ]
    return replace(group, block=RevertBlock.NO_LOCAL_COPY, uncovered=uncovered) if uncovered else group


def _settled(
    entry: DepositLogEntry, file: Optional[DriveFile], revisions: Optional[List[DriveRevision]],
) -> Optional[DepositLogEntry]:
    """這一列當成做了的列來判斷：做了的列原樣；只有「要做」列時照 Drive 現況補上寫入後的版本，那次沒做到時為 None"""
    if not entry.pending:
        return entry
    if entry.action == DepositAction.NEW.value:
        if file is None:
            return None
        return replace(entry, revision_after=revisions[0].id if revisions else file.head_revision)
    if entry.action == DepositAction.UPDATE.value:
        if file is None or file.trashed:
            return entry
        if file.head_revision == entry.revision_before:
            return None
        ids = [revision.id for revision in revisions or []]
        position = ids.index(entry.revision_before) if entry.revision_before in ids else -1
        after = ids[position + 1] if 0 <= position < len(ids) - 1 else file.head_revision
        return replace(entry, revision_after=after)
    if entry.action == DepositAction.REGISTER.value:
        # 只登記的檔，登記沒寫進去就是沒做到
        return None
    return entry


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


def _group(first: DepositLogEntry, files: List[RevertFile], reading: RevertReading, until: str) -> RevertGroup:
    """一個群組（以它的第一列認出是誰、連到哪裡）：每份檔的下場，加上那次存入之前它寫進那處、撤得掉的編制"""
    edition_id, movement_id = first.edition_id, first.movement_id
    here = reading.places.get((edition_id, movement_id), RevertPlace(None))
    if here.place is None:
        return RevertGroup(first.group_id, first.group, edition_id, movement_id, None, files)
    leaving = {file.entry.drive_file_id for file in files if file.action in UNREGISTERING}
    filed = [voice for file_id, voice in here.registered.items() if file_id not in leaving]
    voices: List[Tuple[str, Headcount]] = list(here.place.voices)
    rows = revert_rows(
        reading.instrumentation, edition_id, movement_id, voices, filed, first.group_id, first.group, until,
    )
    return RevertGroup(first.group_id, first.group, edition_id, movement_id, here.place, files, rows)
