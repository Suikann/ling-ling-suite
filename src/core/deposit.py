# -*- coding: utf-8 -*-
"""
存入譜庫的計畫

存入譜庫把已重新命名、已連到譜庫的群組的總譜與分譜上傳到那個版本的 Drive 資料夾，並登記到分譜工作表的聲部。
這裡是純邏輯：哪些群組能存入、每份檔放到版本資料夾下的哪裡（deposit_sources），以及依本機檔案的 MD5、
譜庫那處現在的登記與 Drive 上的檔決定每份檔的動作（plan_group）。讀檔、算 MD5 與碰 Google 在
services/deposit_service.py 與譜庫服務。

放在 Drive 的哪裡：版本資料夾就是群組的資料夾，底下的子資料夾與本機相同。本機的檔在輸出位置下依命名格式放
（core/naming.py），開啟子資料夾輸出時，群組的子資料夾那一層不帶到 Drive，分譜資料夾、聲部組資料夾照帶。

每份檔的動作（DepositAction）：
- 聲部已登記了 Drive 上還在的檔：MD5 相同、檔名與資料夾也相同就跳過；MD5 相同但檔名或資料夾不同就搬移；
  MD5 不同就更新同一個檔的內容，檔名與資料夾一併改成跟本機一致（檔案 ID 不變）
- 聲部沒登記，但目標資料夾有沒登記給任何一列的同名檔：MD5 相同就直接登記，不同就更新它的內容後登記
- 都沒有：新上傳
- 譜庫那處的分譜工作表沒有這個聲部（還沒寫回編制）：不存入

使用範例：
    from core.deposit import deposit_sources, plan_group
    for sources in deposit_sources(project):
        group = plan_group(sources, {path: (md5, size)}, place_files)
        group.counts()
"""
import os
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Dict, FrozenSet, List, Mapping, Optional, Sequence, Tuple

from core.catalog_models import DriveFile
from core.models import CatalogLink, Group, Project
from core.naming import SlotName, UnsafeFolderNameError, name_group, settings_for
from core.paths import name_key, same_path
from core.voice_names import voice_key

# 總譜在存入計畫裡的聲部名稱（分譜工作表的總譜列另以 is_score 標記）
SCORE_VOICE = ""


class DepositAction(Enum):
    """一份檔存入時要做的事；值寫進存入紀錄"""
    NEW = "new"              # 新上傳
    UPDATE = "update"        # 更新內容（連同改名、搬子資料夾）
    MOVE = "move"            # 內容相同，改名或搬子資料夾
    REGISTER = "register"    # 目標資料夾已有內容相同的同名檔，直接登記
    SKIP = "skip"            # 內容、檔名與資料夾都相同
    UNLISTED = "unlisted"    # 譜庫那處沒有這個聲部，不存入


# 要上傳內容的動作（預覽的合計算這些的大小）
UPLOADING = frozenset({DepositAction.NEW, DepositAction.UPDATE})
# 會碰 Drive 或登記、要算進進度的動作（不存入的不算）
DEPOSITING = frozenset(set(DepositAction) - {DepositAction.UNLISTED})


class DepositBlock(Enum):
    """整個群組不存入的原因"""
    NOT_LINKED = "not_linked"        # 沒連到譜庫
    NOT_RENAMED = "not_renamed"      # 還沒照命名格式重新命名
    NO_FILES = "no_files"            # 沒有總譜，也沒有配到聲部的分譜
    MISSING_FILES = "missing_files"  # 有本機找不到的檔
    NO_FOLDER = "no_folder"          # 版本沒有 Drive 資料夾


def folder_key(folders: Sequence[str]) -> Tuple[str, ...]:
    """版本資料夾下一條子資料夾路徑的比對鍵（名稱不分大小寫，同 core.paths.name_key）；版本資料夾本身是空的"""
    return tuple(name_key(name) for name in folders)


@dataclass(frozen=True)
class DepositSource:
    """群組裡要存入的一份檔

    Attributes:
        voice: 聲部名稱；總譜為 SCORE_VOICE
        local_path: 本機路徑
        folders: 版本資料夾下的子資料夾（由外而內），空表示直接放在版本資料夾
        file_name: Drive 上的檔名（與本機相同）
    """
    voice: str
    local_path: str
    folders: Tuple[str, ...]
    file_name: str

    @property
    def is_score(self) -> bool:
        """是不是總譜"""
        return self.voice == SCORE_VOICE

    def drive_path(self) -> str:
        """在版本資料夾下的路徑（以 / 分隔）"""
        return "/".join((*self.folders, self.file_name))


@dataclass(frozen=True)
class GroupSources:
    """一個群組要存入的檔，或整個群組不存入的原因

    Attributes:
        group_id: 群組 ID
        group_name: 群組名稱
        score_label: 總譜標籤（譜庫還沒有總譜列時，新增的那列寫它）
        link: 群組連到譜庫的哪裡；沒連時為 None
        block: 整個群組不存入的原因；可以存入時為 None
        sources: 要存入的檔（總譜在前，接著依序的分譜）
    """
    group_id: str
    group_name: str
    score_label: str
    link: Optional[CatalogLink]
    block: Optional[DepositBlock] = None
    sources: List[DepositSource] = field(default_factory=list)

    def blocked(self, block: DepositBlock) -> "GroupSources":
        """同一個群組，改成整個不存入"""
        return replace(self, block=block, sources=[])


@dataclass(frozen=True)
class PlaceFiles:
    """譜庫裡一處（版本的整首或一個樂章）現在的登記與版本資料夾裡的檔

    Attributes:
        edition_id: 版本 ID
        movement_id: 樂章 ID；整首為空字串
        folder_id: 版本的 Drive 資料夾；版本沒有資料夾時為空字串
        voices: 分譜工作表裡這處的聲部（不含總譜列）
        registered: 已登記、Drive 上還在（不在垃圾桶）的檔：聲部名稱（總譜為 SCORE_VOICE）與它的檔
        folders: 版本資料夾與底下的子資料夾：比對鍵（folder_key）→ 資料夾 ID；版本資料夾本身的鍵是空的
        files: 各資料夾裡的 PDF：比對鍵 → 檔
        claimed: 分譜工作表任何一列（不限這處）登記了的 Drive 檔案 ID
    """
    edition_id: str
    movement_id: str
    folder_id: str
    voices: List[str] = field(default_factory=list)
    registered: List[Tuple[str, DriveFile]] = field(default_factory=list)
    folders: Dict[Tuple[str, ...], str] = field(default_factory=dict)
    files: Dict[Tuple[str, ...], List[DriveFile]] = field(default_factory=dict)
    claimed: FrozenSet[str] = frozenset()

    def registered_file(self, voice: str) -> Optional[DriveFile]:
        """聲部（總譜為 SCORE_VOICE）已登記、Drive 上還在的檔；以同一聲部的規則比對"""
        if voice == SCORE_VOICE:
            return next((file for name, file in self.registered if name == SCORE_VOICE), None)
        key = voice_key(voice)
        return next((file for name, file in self.registered if name != SCORE_VOICE and voice_key(name) == key), None)

    def lists(self, voice: str) -> bool:
        """這處的分譜工作表有沒有這個聲部（總譜一律算有）"""
        return voice == SCORE_VOICE or any(voice_key(name) == voice_key(voice) for name in self.voices)


@dataclass(frozen=True)
class DepositItem:
    """存入計畫裡的一份檔

    Attributes:
        source: 要存入的檔
        md5: 本機內容的 MD5
        size: 本機檔案的大小（位元組）
        action: 要做的事
        target: 要更新、搬移、登記或跳過的 Drive 檔案；新上傳與不存入為 None
    """
    source: DepositSource
    md5: str
    size: int
    action: DepositAction
    target: Optional[DriveFile] = None

    @property
    def renamed(self) -> bool:
        """Drive 上那份的檔名跟本機不同（會改名）"""
        return self.target is not None and self.target.name != self.source.file_name


@dataclass(frozen=True)
class GroupDeposit:
    """存入計畫裡的一個群組

    Attributes:
        sources: 群組要存入的檔與不存入的原因
        items: 每份檔的動作；整個群組不存入時為空
        place: 群組連到的那處現在的樣子；整個群組不存入時可能為 None
    """
    sources: GroupSources
    items: List[DepositItem] = field(default_factory=list)
    place: Optional[PlaceFiles] = None

    @property
    def block(self) -> Optional[DepositBlock]:
        """整個群組不存入的原因；可以存入時為 None"""
        return self.sources.block

    def counts(self) -> Dict[DepositAction, int]:
        """各動作的份數（沒有的不列）"""
        result: Dict[DepositAction, int] = {}
        for item in self.items:
            result[item.action] = result.get(item.action, 0) + 1
        return result


@dataclass(frozen=True)
class DepositPlan:
    """一次存入的計畫：專案裡每個群組各一個（依專案的順序）"""
    groups: List[GroupDeposit] = field(default_factory=list)

    def work(self) -> List[Tuple[GroupDeposit, DepositItem]]:
        """要做的每一份（不存入的群組與聲部不算），依群組與檔案的順序"""
        return [
            (group, item) for group in self.groups if group.block is None
            for item in group.items if item.action in DEPOSITING
        ]

    def upload_count(self) -> int:
        """要上傳內容的份數（新上傳與更新）"""
        return sum(1 for _, item in self.work() if item.action in UPLOADING)

    def upload_size(self) -> int:
        """要上傳的內容合計多大（位元組）"""
        return sum(item.size for _, item in self.work() if item.action in UPLOADING)

    def move_count(self) -> int:
        """要搬移的份數"""
        return sum(1 for _, item in self.work() if item.action is DepositAction.MOVE)


@dataclass(frozen=True)
class DepositRecord:
    """存入紀錄的一列：一份檔存入了什麼（也拿來登記分譜列）

    Attributes:
        edition_id: 版本 ID
        movement_id: 樂章 ID；整首為空字串
        group_id: 群組 ID
        group_name: 群組名稱
        voice: 聲部名稱；總譜為 SCORE_VOICE
        score_label: 總譜標籤（譜庫還沒有總譜列時新增的那列寫它）
        local_path: 本機路徑
        drive_file: 存入後 Drive 上的那份
        action: 做了的事
        revision_before: 被蓋掉的 Drive 版本 ID；沒有蓋掉內容時與存入後的相同，新上傳為空字串
    """
    edition_id: str
    movement_id: str
    group_id: str
    group_name: str
    voice: str
    score_label: str
    local_path: str
    drive_file: DriveFile
    action: DepositAction
    revision_before: str = ""


def deposit_sources(project: Project) -> List[GroupSources]:
    """專案每個群組要存入的檔，或整個群組不存入的原因（沒連到譜庫、還沒重新命名、沒有檔）

    已重新命名＝群組的總譜與每份配到聲部的分譜都在命名格式算出的位置：指定了輸出位置時就是那條路徑，
    沒指定（就地改名）時檔名與上層資料夾跟命名結果相同。多於聲部數的分譜不改名，也不存入。

    Args:
        project: 專案

    Returns:
        依專案的群組順序，每個群組一個
    """
    return [_group_sources(project, group) for group in project.groups]


def plan_group(
    sources: GroupSources, local: Mapping[str, Tuple[str, int]], place: Optional[PlaceFiles],
) -> GroupDeposit:
    """依本機檔案的 MD5 與那處現在的樣子，決定群組每份檔的動作（規則見模組說明）

    Args:
        sources: 群組要存入的檔（deposit_sources）
        local: 本機路徑 → （MD5，大小）；要存入的檔都要有
        place: 群組連到的那處現在的樣子；群組不存入時可以是 None

    Returns:
        群組的計畫；整個群組不存入時沒有動作（版本沒有 Drive 資料夾時原因改為 NO_FOLDER）
    """
    if sources.block is not None or place is None:
        return GroupDeposit(sources, [], place)
    if not place.folder_id:
        return GroupDeposit(sources.blocked(DepositBlock.NO_FOLDER), [], place)
    items = []
    for source in sources.sources:
        md5, size = local[source.local_path]
        items.append(DepositItem(source, md5, size, *_action(source, md5, place)))
    return GroupDeposit(sources, items, place)


def _action(source: DepositSource, md5: str, place: PlaceFiles) -> Tuple[DepositAction, Optional[DriveFile]]:
    """一份檔的動作與要動的 Drive 檔案"""
    if not place.lists(source.voice):
        return DepositAction.UNLISTED, None
    folder_id = place.folders.get(folder_key(source.folders), "")
    registered = place.registered_file(source.voice)
    if registered is not None:
        placed = registered.name == source.file_name and bool(folder_id) and registered.parents == (folder_id,)
        if registered.md5 != md5:
            return DepositAction.UPDATE, registered
        return (DepositAction.SKIP if placed else DepositAction.MOVE), registered
    same_name = next((
        file for file in place.files.get(folder_key(source.folders), [])
        if name_key(file.name) == name_key(source.file_name) and file.id not in place.claimed
    ), None)
    if same_name is not None:
        return (DepositAction.REGISTER if same_name.md5 == md5 else DepositAction.UPDATE), same_name
    return DepositAction.NEW, None


def _group_sources(project: Project, group: Group) -> GroupSources:
    """一個群組要存入的檔，或不存入的原因"""
    base = GroupSources(group.id, group.name, group.score_label, group.catalog_link)
    if group.catalog_link is None:
        return base.blocked(DepositBlock.NOT_LINKED)
    settings = settings_for(project, group)
    try:
        local = name_group(group, settings)
        # 拿掉群組的子資料夾那一層，算出的資料夾就是版本資料夾底下的子資料夾
        drive = name_group(group, replace(settings, subfolder_template=""))
    except (KeyError, UnsafeFolderNameError):
        return base.blocked(DepositBlock.NOT_RENAMED)
    if not local.files:
        return base.blocked(DepositBlock.NO_FILES)
    voices = {pair.file.original_path: pair.voice for pair in group.pairing() if pair.file is not None}
    sources = []
    for named, placed in zip(local.files, drive.files):
        path = named.file.original_path
        if not _is_at(path, named.name, project.output_directory):
            return base.blocked(DepositBlock.NOT_RENAMED)
        voice = SCORE_VOICE if named.file is group.score_file else voices[path]
        sources.append(DepositSource(voice, path, placed.name.folders, placed.name.file_name))
    return replace(base, sources=sources)


def _is_at(path: str, name: SlotName, output_directory: str) -> bool:
    """本機的檔是不是在命名結果的位置：有輸出位置時比整條路徑，沒有時比檔名與上層資料夾（不分大小寫）"""
    if output_directory:
        return same_path(path, os.path.join(output_directory, name.relative_path()))
    wanted = [name_key(part) for part in (*name.folders, name.file_name)]
    parts = [name_key(part) for part in os.path.normpath(os.path.abspath(path)).split(os.sep)]
    return parts[-len(wanted):] == wanted

