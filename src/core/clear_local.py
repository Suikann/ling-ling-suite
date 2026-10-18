# -*- coding: utf-8 -*-
"""
清除本機檔案的計畫

清除本機檔案在確認譜庫裡有內容相同的一份之後，永久刪掉本機的總譜與分譜，連同清空後變空的資料夾。
這裡是純邏輯：專案每個群組有哪些檔、各自對到哪個聲部與輸出位置（clear_sources），依本機 MD5 與譜庫那處
登記的 Drive 檔歸類每份檔（classify），以及刪掉一份檔之後要試著刪掉哪些資料夾（emptied_folders）。
讀檔、算 MD5、刪檔與碰 Google 在 services/clear_local_service.py 與譜庫服務。

每份檔的歸類（ClearStatus）：
- 本機找得到：
  - 群組沒連到譜庫，或分譜沒配到聲部：還沒存入
  - 那處的這個聲部（總譜為總譜列）沒登記 Drive 檔，或登記的檔已不在 Drive、在垃圾桶裡、不在版本資料夾裡
    （例如別人分享進來、擁有者刪得掉的檔）：譜庫裡沒有
  - 登記的檔的 MD5 跟本機不同：跟譜庫裡的不一樣，請先重新存入
  - 相同：可清除
- 本機找不到：群組記著它在譜庫裡有一份（Group.has_catalog_copy）時是只在譜庫，否則是找不到

空資料夾往上刪到輸出位置為止（輸出位置本身也刪）；沒指定輸出位置（就地改名）時，輸出位置是重新命名前
來源檔所在的資料夾。家目錄、磁碟根目錄與系統的標準使用者資料夾就算空了也不刪。

使用範例：
    from core.clear_local import clear_sources, classify, emptied_folders
    for sources in clear_sources(project):
        for source in sources.sources:
            status = classify(source, md5, place)
"""
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Collection, Dict, Iterable, List, Optional, Tuple

from core.deposit import SCORE_VOICE, PlaceFiles
from core.models import CatalogLink, FileInfo, Group, Project
from core.naming import UnsafeFolderNameError, name_group, settings_for
from core.paths import is_inside, name_key, path_key, same_path

# 家目錄下系統的標準使用者資料夾（英文名稱）；各平台在地化的名稱由呼叫端另外給
STANDARD_USER_FOLDER_NAMES = (
    "Desktop", "Documents", "Downloads", "Music", "Pictures", "Videos", "Movies", "Public", "Templates",
)


class ClearStatus(Enum):
    """一份檔在清除本機檔案時的歸類；值是介面字串鍵的一段"""
    CLEARABLE = "clearable"            # 譜庫裡有內容相同的一份，可清除
    DIFFERENT = "different"            # 跟譜庫裡的不一樣，請先重新存入
    NOT_ARCHIVED = "not_archived"      # 還沒存入：群組沒連到譜庫，或分譜沒配到聲部
    NOT_IN_CATALOG = "not_in_catalog"  # 譜庫裡沒有：沒登記 Drive 檔，或登記的檔已不在、在垃圾桶裡
    CATALOG_ONLY = "catalog_only"      # 本機已經沒有、譜庫裡有：只在譜庫
    MISSING = "missing"                # 本機找不到，譜庫裡也沒有記著的一份


@dataclass(frozen=True)
class ClearSource:
    """群組裡的一份檔

    Attributes:
        path: 本機路徑
        voice: 配到的聲部名稱；總譜為 SCORE_VOICE，沒配到聲部的分譜為 None
        location: 這份檔的輸出位置（往上刪空資料夾刪到這裡為止）
        catalog_copy: 群組記著它在譜庫裡有一份（存入指紋）
    """
    path: str
    voice: Optional[str]
    location: str
    catalog_copy: bool = False


@dataclass(frozen=True)
class ClearSources:
    """一個群組的檔

    Attributes:
        group_id: 群組 ID
        group_name: 群組名稱
        score_label: 總譜標籤（明細的聲部欄寫它）
        link: 群組連到譜庫的哪裡；沒連時為 None
        sources: 總譜在前，接著依序的分譜
    """
    group_id: str
    group_name: str
    score_label: str
    link: Optional[CatalogLink]
    sources: List[ClearSource] = field(default_factory=list)


@dataclass(frozen=True)
class ClearItem:
    """清除計畫裡的一份檔

    Attributes:
        source: 群組裡的這份檔
        status: 歸類
        md5: 本機內容的 MD5；本機找不到時為空字串
        size: 本機檔案的大小（位元組）；本機找不到時為 0
    """
    source: ClearSource
    status: ClearStatus
    md5: str = ""
    size: int = 0

    @property
    def path(self) -> str:
        """本機路徑"""
        return self.source.path


@dataclass(frozen=True)
class GroupClear:
    """清除計畫裡的一個群組

    Attributes:
        sources: 群組的檔
        items: 每份檔的歸類，順序同 sources
    """
    sources: ClearSources
    items: List[ClearItem] = field(default_factory=list)

    @property
    def group_id(self) -> str:
        """群組 ID"""
        return self.sources.group_id

    @property
    def place_key(self) -> Optional[Tuple[str, str]]:
        """群組連到的那處（版本 ID，樂章 ID）；沒連到譜庫時為 None"""
        link = self.sources.link
        return (link.edition_id, link.movement_id) if link is not None else None

    @property
    def clearable(self) -> List[ClearItem]:
        """可清除的檔"""
        return [item for item in self.items if item.status is ClearStatus.CLEARABLE]

    @property
    def choosable(self) -> bool:
        """群組能不能勾：有可清除的檔才能"""
        return bool(self.clearable)

    @property
    def cleared(self) -> bool:
        """已清除：群組有檔，而且全都只在譜庫"""
        return bool(self.items) and all(item.status is ClearStatus.CATALOG_ONLY for item in self.items)

    def counts(self) -> Dict[ClearStatus, int]:
        """各歸類的份數（沒有的不列）"""
        result: Dict[ClearStatus, int] = {}
        for item in self.items:
            result[item.status] = result.get(item.status, 0) + 1
        return result

    def freed_size(self) -> int:
        """清除這個群組能釋放多少空間（位元組）"""
        return sum(item.size for item in self.clearable)


@dataclass(frozen=True)
class ClearPlan:
    """一次清除的計畫：專案裡每個群組各一個（依專案的順序）"""
    groups: List[GroupClear] = field(default_factory=list)

    def chosen(self, group_ids: Collection[str]) -> List[ClearItem]:
        """勾了的群組裡可清除的檔（依群組與檔案的順序）"""
        return [item for group in self.groups if group.group_id in group_ids for item in group.clearable]


def clear_sources(project: Project) -> List[ClearSources]:
    """專案每個群組的總譜與分譜，連同各自配到的聲部與輸出位置

    Args:
        project: 專案

    Returns:
        依專案的群組順序，每個群組一個
    """
    return [_group_sources(project, group) for group in project.groups]


def classify(source: ClearSource, md5: str, place: Optional[PlaceFiles]) -> ClearStatus:
    """本機找得到的一份檔的歸類（規則見模組說明）

    Args:
        source: 群組裡的這份檔
        md5: 本機內容的 MD5
        place: 群組連到的那處現在的登記；群組沒連到譜庫時為 None

    Returns:
        可清除、跟譜庫不一樣、還沒存入或譜庫裡沒有
    """
    if place is None or source.voice is None:
        return ClearStatus.NOT_ARCHIVED
    registered = place.registered_file(source.voice)
    if registered is None or not place.holds(registered):
        return ClearStatus.NOT_IN_CATALOG
    return ClearStatus.CLEARABLE if registered.md5 == md5 else ClearStatus.DIFFERENT


def standard_user_folders(home: str) -> List[str]:
    """家目錄與它底下系統的標準使用者資料夾（英文名稱）：就算空了也不刪"""
    return [home] + [os.path.join(home, name) for name in STANDARD_USER_FOLDER_NAMES]


def emptied_folders(path: str, location: str, protected: Iterable[str]) -> List[str]:
    """刪掉一份檔之後要試著刪掉的資料夾：由內而外，從它所在的資料夾往上到輸出位置為止（含輸出位置）

    碰到受保護的資料夾或磁碟根目錄就停下，它與它的上層都不刪；檔案不在輸出位置之內時不刪任何資料夾。

    Args:
        path: 刪掉的檔
        location: 這份檔的輸出位置
        protected: 就算空了也不刪的資料夾（家目錄、標準使用者資料夾）

    Returns:
        資料夾路徑，由內而外；只在空了的時候才刪
    """
    keys = {path_key(folder) for folder in protected}
    folder = os.path.dirname(os.path.abspath(path))
    if not (same_path(folder, location) or is_inside(folder, location)):
        return []
    result = []
    while path_key(folder) not in keys and os.path.dirname(folder) != folder:
        result.append(folder)
        if same_path(folder, location):
            break
        folder = os.path.dirname(folder)
    return result


def _group_sources(project: Project, group: Group) -> ClearSources:
    """一個群組的檔：總譜與分譜，連同聲部與輸出位置"""
    voices = {id(pair.file): pair.voice for pair in group.pairing() if pair.file is not None}
    files = ([group.score_file] if group.score_file else []) + list(group.files)
    locations = _named_locations(project, group)
    sources = [
        ClearSource(
            file.original_path,
            SCORE_VOICE if file is group.score_file else voices.get(id(file)),
            locations.get(id(file)) or _location(project, file),
            group.has_catalog_copy(file),
        )
        for file in files
    ]
    return ClearSources(group.id, group.name, group.score_label, group.catalog_link, sources)


def _location(project: Project, file: FileInfo) -> str:
    """不在命名結果位置上的檔的輸出位置：在專案的輸出位置之內就是它，否則是檔案所在的資料夾"""
    if project.output_directory and is_inside(file.original_path, project.output_directory):
        return project.output_directory
    return os.path.dirname(os.path.abspath(file.original_path))


def _named_locations(project: Project, group: Group) -> Dict[int, str]:
    """就地改名、檔案在命名結果位置上的檔的輸出位置：路徑去掉命名結果的資料夾與檔名（以檔案資訊的 id 為鍵）

    有指定輸出位置時不算（由 _location 決定）；命名不出來時沒有任何一份。
    """
    if project.output_directory:
        return {}
    try:
        naming = name_group(group, settings_for(project, group))
    except (KeyError, UnsafeFolderNameError):
        return {}
    result = {}
    for named in naming.files:
        wanted = [name_key(part) for part in (*named.name.folders, named.name.file_name)]
        path = os.path.normpath(os.path.abspath(named.file.original_path))
        if [name_key(part) for part in path.split(os.sep)[-len(wanted):]] == wanted:
            for _ in wanted:
                path = os.path.dirname(path)
            result[id(named.file)] = path
    return result
