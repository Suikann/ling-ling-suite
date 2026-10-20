# -*- coding: utf-8 -*-
"""
發譜服務

發譜到 HuaXia（spec #82、ADR 0003）按下「發譜」之後、開啟發譜預覽之前的檢查：

- 專案層級（project_blocks，擋下整個發譜）：專案有未存檔的改動（只在按下時檢查）、專案不是國樂團
  （HuaXia 的聲部組只接受國樂團那五組）。
- 群組層級（check_groups，擋下那個群組，預覽裡寫出原因）：還沒重新命名（core.naming.is_renamed，與存入譜庫同一條規則；
  發譜從輸出位置讀檔）、本專案有的聲部沒填最少人數、樂器表有重名的列。未同步的值（還沒進譜庫）不擋，只標出。
- HuaXia 那一側（PublishService.prepare，連網，要在背景執行緒呼叫）：重查幹部身分（不是幹部就登出）、
  HuaXia 發譜函式的介面版本（沒有或不符時整個發譜不能用），兩者都過了才讀可以挑的曲目（未封存的）與它們的聲部、
  樂譜，以及可以掛的公演（本學期、還沒結束、類型含公演，upcoming_concerts）。

發譜預覽各步的規則也在這裡：「聲部」步的配對與要寫的內容（plan_voices），「檔案」步每份檔要取代的
與別人放的（plan_files）；「只在譜庫」與本機找不到的檔在 plan_files 跳過。各步的結果由 group_publish 合成
一個群組這次發譜要寫進 HuaXia 的全部內容（GroupPublish）。預覽這次的選擇（對到的曲目、聲部的決定、不發的檔、
壓縮結果、要掛的公演）與「下一步」「發譜」按不按得下去由 PublishPreview 持有與判斷，步驟是 PublishStep。

使用範例：
    from services.publish_service import PublishService, check_groups, project_blocks
    if not project_blocks(project):
        readiness = PublishService(huaxia_auth).prepare()   # 背景執行緒
        if readiness.ready:
            checks = check_groups(project)
"""
import datetime
import hashlib
import itertools
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Collection, Dict, FrozenSet, List, NamedTuple, Optional, Set, Tuple

from core.constants import CONCERT_KIND, OTHER_SECTION, CompressionStatus, OrchestraType
from core.models import CatalogChange, Group, Headcount, HuaXiaLink, Project
from core.naming import is_renamed
from core.voice_names import duplicate_rows, same_voice, voice_key
from services.huaxia_auth_service import HuaXiaAuthService, NotOfficer
from services.compression_service import CompressionResult, CompressionService
from services.file_service import FileService
from services.huaxia_client import (
    PUBLISH_INTERFACE_VERSION, HuaXiaEvent, HuaXiaPart, HuaXiaPiece, HuaXiaScore, lingling_storage_path,
)

# 總譜在泠靈 Storage 路徑裡的登記位置；分譜的是聲部比對鍵的 SHA-256 前 _VOICE_SLOT_LENGTH 碼（storage_slot）
SCORE_STORAGE_SLOT = "score"
_VOICE_SLOT_LENGTH = 12


class ProjectBlock(Enum):
    """擋下整個發譜的原因"""

    UNSAVED = "unsaved"                              # 專案有未存檔的改動
    NOT_CHINESE_ORCHESTRA = "not_chinese_orchestra"  # 專案不是國樂團


class FunctionProblem(Enum):
    """HuaXia 發譜函式不能用的原因"""

    MISSING = "missing"                    # HuaXia 沒有發譜函式（合併請求還沒合併生效）
    VERSION_MISMATCH = "version_mismatch"  # 介面版本與這版泠靈要的不同


@dataclass(frozen=True)
class PublishReadiness:
    """按下發譜時 HuaXia 那一側的檢查結果（PublishService.prepare）

    Attributes:
        not_officer: 不是幹部時的分類（已登出）；是幹部時為 None
        function_problem: 發譜函式不能用的原因；能用時為 None
        function_version: HuaXia 回報的介面版本；沒有發譜函式或沒查到時為 None
        pieces: 可以挑的曲目（未封存，依曲名）；不能發時為空
        parts: 可以挑的曲目上已有的聲部，以曲目編號為鍵（依 HuaXia 的排序）；沒有聲部的曲目不在裡面
        scores: 可以挑的曲目上登記的樂譜，以曲目編號為鍵（依登記的先後）；沒有樂譜的曲目不在裡面
        concerts: 可以掛的公演：本學期、還沒結束、類型含公演的場次，依開始時間
        attached: 可以挑的曲目已經掛著的公演（concerts 裡的），以曲目編號為鍵；沒掛的曲目不在裡面
        discord_name: 登入的 Discord 帳號名稱
    """

    not_officer: Optional[NotOfficer] = None
    function_problem: Optional[FunctionProblem] = None
    function_version: Optional[int] = None
    pieces: List[HuaXiaPiece] = field(default_factory=list)
    parts: Dict[str, List[HuaXiaPart]] = field(default_factory=dict)
    scores: Dict[str, List[HuaXiaScore]] = field(default_factory=dict)
    concerts: List[HuaXiaEvent] = field(default_factory=list)
    attached: Dict[str, Set[str]] = field(default_factory=dict)
    discord_name: str = ""

    @property
    def ready(self) -> bool:
        """可以開啟發譜預覽"""
        return self.not_officer is None and self.function_problem is None


@dataclass(frozen=True)
class GroupCheck:
    """一個群組發譜前的檢查結果

    Attributes:
        group: 群組
        not_renamed: 還沒重新命名（總譜與分譜不全在命名格式算出的位置，或沒有任何要發的檔）
        unfilled: 本專案有、沒填最少人數的聲部，依樂器表順序
        duplicate_rows: 樂器表裡重名的列（從 0 起算）
        unsynced: 還沒進譜庫的值（只標出，不擋）
    """

    group: Group
    not_renamed: bool = False
    unfilled: List[str] = field(default_factory=list)
    duplicate_rows: List[int] = field(default_factory=list)
    unsynced: List[CatalogChange] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        """這個群組不能發"""
        return self.not_renamed or bool(self.unfilled) or bool(self.duplicate_rows)


def project_blocks(project: Project) -> List[ProjectBlock]:
    """擋下整個發譜的原因（依檢查順序）；沒有時可以發"""
    blocks = []
    if project.is_modified():
        blocks.append(ProjectBlock.UNSAVED)
    if project.orchestra_type is not OrchestraType.CHINESE:
        blocks.append(ProjectBlock.NOT_CHINESE_ORCHESTRA)
    return blocks


def check_groups(project: Project, group_ids: Optional[Collection[str]] = None) -> List[GroupCheck]:
    """專案每個群組（依專案中的順序）發譜前的檢查結果；不改專案

    Args:
        project: 專案
        group_ids: 只發這幾個群組（「重新發譜沒發成的群組」）；省略時為全部群組。已不在專案裡的略過

    Returns:
        各群組的檢查結果
    """
    return [
        GroupCheck(
            group=group,
            not_renamed=not is_renamed(project, group),
            unfilled=[
                voice for voice, present, headcount in zip(group.instruments, group.present, group.headcounts)
                if present and not headcount.filled
            ],
            duplicate_rows=duplicate_rows(group.instruments),
            unsynced=project.unsynced_changes(group),
        )
        for group in project.groups if group_ids is None or group.id in group_ids
    ]


class PartMatch(Enum):
    """泠靈的聲部怎麼對到 HuaXia 的聲部（依配對順序）"""

    CHOSEN = "chosen"        # 記住的手動配對（HuaXiaLink.part_ids）
    NAME = "name"            # 泠靈的「同一聲部」規則對到唯一一個
    AMBIGUOUS = "ambiguous"  # 名稱規則對到不只一個，等譜務挑
    NONE = "none"            # 沒對上（或這次選了新建）


@dataclass
class VoiceDecisions:
    """譜務這次在「聲部」步的決定（不記在專案；手動挑的配對另經 Project.choose_huaxia_part 記住）

    Attributes:
        new: 這次改選新建的列（樂器表的列，從 0 起算），不套用名稱規則
        keep: 取消「覆寫」、保留 HuaXia 的值的列
        create_absent: 本專案沒有、要在 HuaXia 建的列（預設不建）
    """

    new: Set[int] = field(default_factory=set)
    keep: Set[int] = field(default_factory=set)
    create_absent: Set[int] = field(default_factory=set)


class PartCreate(NamedTuple):
    """要在 HuaXia 曲目上新建的聲部（piece_parts 的欄位）

    Attributes:
        name: 聲部名稱（泠靈的寫法）
        needed: 人數的 HuaXia 記法（「6」「6-8」「2↑」）
        needed_min: 最少人數
        section: 聲部組；「其他」寫成空字串，交給 HuaXia 的觸發器 guess_section
        sort: 排序：接在曲目現有聲部之後，依樂器表順序
    """
    name: str
    needed: str
    needed_min: int
    section: str
    sort: int


class PartUpdate(NamedTuple):
    """要覆寫的 HuaXia 現有聲部；名稱沿用 HuaXia 的，不改

    Attributes:
        part_id: HuaXia 的聲部編號
        name: HuaXia 的聲部名稱（顯示與紀錄用）
        needed: 新的人數記法；不改人數時為 None
        needed_min: 新的最少人數；不改人數時為 None
        section: 新的聲部組；不改時為 None
    """
    part_id: str
    name: str
    needed: Optional[str]
    needed_min: Optional[int]
    section: Optional[str]


@dataclass(frozen=True)
class VoiceRow:
    """樂器表的一列對到 HuaXia 的結果

    Attributes:
        index: 樂器表的列（從 0 起算）
        voice: 泠靈的聲部名稱
        present: 本專案有
        headcount: 泠靈的人數範圍
        section: 泠靈的聲部組
        match: 怎麼對到的
        part: 對到的 HuaXia 聲部；沒對上時為 None
        options: 可以改選的 HuaXia 聲部，依 HuaXia 的排序：名稱規則對到多個時只有那幾個，否則是還沒配給
            其他聲部的（記住的手動配對與名稱規則都算）
        can_create: 可以選新建（名稱規則對到多個時不行）
        headcount_differs: 對到的 HuaXia 聲部人數與泠靈不同（本專案有的列才比）
        section_differs: 對到的 HuaXia 聲部的聲部組與泠靈不同（泠靈是「其他」時不比）
        overwrite: 有差異且要以泠靈的值覆寫
        create: 要在 HuaXia 新建
    """

    index: int
    voice: str
    present: bool
    headcount: Headcount
    section: str
    match: PartMatch
    part: Optional[HuaXiaPart]
    options: List[HuaXiaPart]
    can_create: bool
    headcount_differs: bool = False
    section_differs: bool = False
    overwrite: bool = False
    create: bool = False


@dataclass(frozen=True)
class VoicePlan:
    """一個群組的聲部對到 HuaXia 的結果與要寫的內容；只有新建與覆寫，沒有刪除

    Attributes:
        rows: 樂器表每一列，依樂器表順序
        huaxia_only: 這首曲目在 HuaXia 上沒被配對的聲部（不動）
        conflicts: 被兩個以上泠靈聲部指到的 HuaXia 聲部與那些聲部
        undecided: 名稱規則對到多個、還沒挑的聲部
        unfilled: 本專案沒有、要建卻沒填最少人數的聲部
        creates: 要新建的聲部
        updates: 要覆寫的聲部
    """

    rows: List[VoiceRow]
    huaxia_only: List[HuaXiaPart]
    conflicts: List[Tuple[HuaXiaPart, List[str]]]
    undecided: List[str]
    unfilled: List[str]
    creates: List[PartCreate]
    updates: List[PartUpdate]

    @property
    def blocked(self) -> bool:
        """這個群組還不能發：有衝突、沒挑或要建的聲部沒填最少人數"""
        return bool(self.conflicts or self.undecided or self.unfilled)


def plan_voices(
    project: Project, group: Group, parts: List[HuaXiaPart], decisions: Optional[VoiceDecisions] = None,
) -> VoicePlan:
    """把群組的每個聲部對到它對到的 HuaXia 曲目上的聲部，算出要新建與覆寫的內容；不改專案

    配對順序：記住的手動配對（那個 HuaXia 聲部已刪除就跳過）→ 泠靈的「同一聲部」規則（被記住的配對佔走的不算；
    對到多個時不自動選）→ 都沒有就是沒對上，本專案有的預設新建，本專案沒有的照 decisions 才建。
    這次要發的聲部（本專案有，或要在 HuaXia 建的）先配，這次不發的只在剩下的 HuaXia 聲部裡配、只供顯示：
    不占要發的聲部可以挑的，也不算衝突。

    Args:
        project: 專案（聲部組以它的對照為準）
        group: 群組
        parts: 群組對到的 HuaXia 曲目上已有的聲部；新建、HuaXia 上還沒有的曲目為空
        decisions: 譜務這次的決定；省略時全照預設

    Returns:
        配對結果與要寫的內容
    """
    decisions = decisions or VoiceDecisions()
    link = group.huaxia_link
    by_id = {part.id: part for part in parts}
    rows = list(zip(group.instruments, group.present, group.headcounts))
    # 這次要發的聲部（本專案有，或本專案沒有、要在 HuaXia 建的）先配；這次不發的只在剩下的聲部裡配（顯示用），
    # 不占要發的聲部可以挑的 HuaXia 聲部
    active = [
        index for index, (_voice, present, _headcount) in enumerate(rows) if present or index in decisions.create_absent
    ]
    inactive = [index for index in range(len(rows)) if index not in active]
    candidates: Dict[int, List[HuaXiaPart]] = {}
    matches: Dict[int, Tuple[PartMatch, Optional[HuaXiaPart]]] = {}
    taken: Set[str] = set()
    for phase in (active, inactive):
        chosen: Dict[int, HuaXiaPart] = {}
        for index in phase:
            part_id = link.part_for(rows[index][0]) if link else None
            if index not in decisions.new and part_id in by_id and part_id not in taken:
                chosen[index] = by_id[part_id]
        for index in phase:
            candidates[index] = [
                part for part in parts
                if same_voice(part.name, rows[index][0]) and part.id not in taken
                and all(i == index or mine.id != part.id for i, mine in chosen.items())
            ]
            if index in chosen:
                matches[index] = (PartMatch.CHOSEN, chosen[index])
            elif index in decisions.new or not candidates[index]:
                matches[index] = (PartMatch.NONE, None)
            elif len(candidates[index]) == 1:
                matches[index] = (PartMatch.NAME, candidates[index][0])
            else:
                matches[index] = (PartMatch.AMBIGUOUS, None)
        taken |= {matches[index][1].id for index in phase if matches[index][1] is not None}
    unpaired = [part for part in parts if part.id not in taken]
    paired = {index: matches[index][1].id for index in active if matches[index][1] is not None}
    assigned = list(paired.values())
    next_sort = max((part.sort for part in parts), default=0)
    voice_rows, creates, updates, undecided, unfilled = [], [], [], [], []
    for index, (voice, present, headcount) in enumerate(rows):
        match, part = matches[index]
        section = project.section_of(voice)
        several = len(candidates[index]) > 1 and index not in decisions.new
        options = candidates[index] if several else [
            option for option in parts if all(i == index or found != option.id for i, found in paired.items())
        ]
        row = VoiceRow(index, voice, present, headcount, section, match, part, options, can_create=not several)
        if part is not None and present:
            headcount_differs = part.needed.strip() != headcount.text() or part.needed_min != headcount.minimum
            section_differs = section != OTHER_SECTION and part.section != section
            overwrite = (headcount_differs or section_differs) and index not in decisions.keep
            row = replace(
                row, headcount_differs=headcount_differs, section_differs=section_differs, overwrite=overwrite,
            )
            if overwrite:
                updates.append(PartUpdate(
                    part.id, part.name, headcount.text() if headcount_differs else None,
                    headcount.minimum if headcount_differs else None, section if section_differs else None,
                ))
        elif part is None and match is PartMatch.AMBIGUOUS:
            if present:
                undecided.append(voice)
        elif part is None and (present or index in decisions.create_absent):
            row = replace(row, create=True)
            if not headcount.filled:
                unfilled.append(voice)
            else:
                next_sort += 1
                creates.append(PartCreate(
                    voice, headcount.text(), headcount.minimum, "" if section == OTHER_SECTION else section, next_sort,
                ))
        voice_rows.append(row)
    conflicts = [
        (part, [row.voice for row in voice_rows if row.part is not None and row.part.id == part.id])
        for part in parts if assigned.count(part.id) > 1
    ]
    return VoicePlan(voice_rows, unpaired, conflicts, undecided, unfilled, creates, updates)


def storage_slot(voice: str) -> str:
    """一份檔在泠靈 Storage 路徑裡的登記位置：總譜（voice 為空字串）是 SCORE_STORAGE_SLOT，分譜是聲部比對鍵
    （core.voice_names.voice_key）的 SHA-256 前 12 碼，同一聲部的各種寫法都得到同一個"""
    if not voice:
        return SCORE_STORAGE_SLOT
    base, number = voice_key(voice)
    key = f"{base}\x00{'' if number is None else number}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:_VOICE_SLOT_LENGTH]


class Fingerprints:
    """本機檔內容的 SHA-256；記著算過的，檔案的大小與修改時間沒變就不重算（預覽每次重排都要用）"""

    def __init__(self, file_service: FileService):
        """
        Args:
            file_service: 讀檔案內容與大小、修改時間
        """
        self._files = file_service
        self._known: Dict[str, Tuple[Tuple[int, int], str]] = {}

    def sha256(self, path: str) -> str:
        """這份檔目前內容的 SHA-256（十六進位小寫）

        Raises:
            OSError: 檔案讀不到
        """
        stamp = self._files.stamp(path)
        known = self._known.get(path)
        if known is None or known[0] != stamp:
            known = (stamp, self._files.sha256(path))
            self._known[path] = known
        return known[1]


@dataclass(frozen=True)
class PublishFile:
    """群組要發的一份本機檔（總譜或一個聲部的分譜）與它在 HuaXia 上要取代的

    泠靈的 Storage 路徑由曲目編號、內容與登記位置決定（path_for），每筆樂譜登記各有自己的 Storage 檔：
    內容相同的兩份檔（例如兩個聲部的 PDF 相同）也各傳一份。同一個聲部底下泠靈發的樂譜裡，路徑就是這次這份的
    那份就是這份檔本身：內容沒變（unchanged），不重傳也不取代它；其餘的都取代。

    Attributes:
        voice: 泠靈的聲部名稱；總譜為空字串
        part: 對到的 HuaXia 聲部；總譜、或聲部要在 HuaXia 新建時為 None
        path: 本機的檔（輸出位置）
        size: 檔案大小（位元組）；本機找不到時為 None
        piece_id: 群組對到的 HuaXia 曲目編號；新建、還沒產生編號時為空字串
        sha256: 要傳的內容的 SHA-256：本機那份的，改傳壓縮檔時是壓縮檔的（group_publish）；本機找不到時為 None
        lingling: 同一個聲部（總譜是聲部空白）底下、泠靈發的樂譜
        others: 同一個聲部底下其他人放的樂譜（一律不動，只標出）
        catalog_only: 本機找不到、譜庫裡有一份（只在譜庫），這次跳過
        elsewhere: 曲目上其他樂譜登記（不是這個聲部底下泠靈發的）指著的 Storage 路徑，這份檔不能用
    """

    voice: str
    part: Optional[HuaXiaPart]
    path: str
    size: Optional[int]
    piece_id: str = ""
    sha256: Optional[str] = None
    lingling: List[HuaXiaScore] = field(default_factory=list)
    others: List[HuaXiaScore] = field(default_factory=list)
    catalog_only: bool = False
    elsewhere: FrozenSet[str] = frozenset()

    @property
    def storage_path(self) -> Optional[str]:
        """這次的內容在 Storage 的路徑（path_for）；曲目還沒有編號或本機找不到時為 None"""
        if not self.piece_id or self.sha256 is None:
            return None
        return self.path_for(self.sha256)

    def path_for(self, sha256: str) -> str:
        """這個內容發到這個聲部（總譜是聲部空白）時的 Storage 路徑

        登記位置是 storage_slot：同一個聲部重發同樣的內容得到同一個路徑。這個路徑已有別的登記指著時（例如聲部改對到
        別的 HuaXia 聲部，舊的登記還在），在登記位置後面加上 2、3…，直到沒有別的登記指著，不讓兩筆登記共用一個檔。

        Args:
            sha256: 內容的 SHA-256（十六進位小寫）
        """
        base = storage_slot(self.voice)
        for n in itertools.count(1):
            path = lingling_storage_path(self.piece_id, sha256, base if n == 1 else f"{base}{n}")
            if path not in self.elsewhere:
                return path
        raise AssertionError("unreachable")

    @property
    def unchanged(self) -> bool:
        """HuaXia 上這個聲部已登記著內容相同的那份（泠靈上次發的），不必重傳"""
        path = self.storage_path
        return path is not None and any(score.file_path == path for score in self.lingling)

    @property
    def replaces(self) -> List[HuaXiaScore]:
        """要取代（撤掉登記）的：同一個聲部底下泠靈發的、內容不是這次這份的樂譜"""
        path = self.storage_path
        return [score for score in self.lingling if path is None or score.file_path != path]

    @property
    def score(self) -> bool:
        """是總譜"""
        return self.voice == ""

    @property
    def missing(self) -> bool:
        """本機找不到，這次跳過（只在譜庫的也算）"""
        return self.size is None


@dataclass(frozen=True)
class FilePlan:
    """一個群組要發的檔：本專案有的聲部的分譜（依樂器表順序），接著總譜"""

    files: List[PublishFile]

    @property
    def publishing(self) -> List[PublishFile]:
        """本機找得到、這次要發的檔"""
        return [file for file in self.files if not file.missing]

    @property
    def skipped(self) -> List[PublishFile]:
        """本機找不到、這次跳過的檔"""
        return [file for file in self.files if file.missing]


def plan_files(
    group: Group, voices: VoicePlan, scores: List[HuaXiaScore], file_service: FileService,
    fingerprints: Optional[Fingerprints] = None,
) -> FilePlan:
    """群組每份要發的檔在 HuaXia 上要取代哪份、同聲部有哪些別人放的檔；不改專案

    分譜依本專案有的聲部配到的分譜（Group.pairing），對到 voices 裡那一列的 HuaXia 聲部；要取代的是那個聲部底下、
    泠靈發的樂譜（以泠靈的 Storage 路徑格式辨認，不看樂譜名稱），總譜是聲部空白（不含舊的分組樂譜）、泠靈發的；
    其中內容與本機這份相同的那份不取代，這份檔也不重傳（PublishFile.unchanged）。曲目上其他登記指著的路徑記在
    PublishFile.elsewhere，這份檔不用它們（每筆登記各有自己的 Storage 檔）。
    要在 HuaXia 新建的聲部沒有要取代的。本機找不到的檔跳過，群組記著譜庫裡有一份的標成只在譜庫。

    Args:
        group: 群組（已重新命名，檔在輸出位置）
        voices: 群組的聲部對到 HuaXia 的結果（plan_voices）
        scores: 群組對到的 HuaXia 曲目上登記的樂譜；新建、HuaXia 上還沒有的曲目為空
        file_service: 讀本機檔案大小
        fingerprints: 算本機檔內容的 SHA-256（預覽傳同一份以免重算）；省略時以 file_service 建一份

    Returns:
        各份檔與要取代的
    """
    fingerprints = fingerprints or Fingerprints(file_service)
    piece_id = group.huaxia_link.piece_id if group.huaxia_link else ""
    parts = {row.index: row.part for row in voices.rows}
    entries = [(voice.voice, parts.get(voice.index), voice.file) for voice in group.pairing() if voice.file]
    if group.score_file is not None:
        entries.append(("", None, group.score_file))
    registered = {score.file_path for score in scores if score.file_path}
    files = []
    for voice, part, file in entries:
        if voice and part is None:
            under = []
        elif voice:
            under = [score for score in scores if score.part_id == part.id]
        else:
            under = [score for score in scores if score.part_id is None and not score.section]
        mine = [score for score in under if score.lingling]
        path = file.original_path
        found = file_service.file_exists(path)
        files.append(PublishFile(
            voice, part, path, file_service.file_size(path) if found else None, piece_id,
            fingerprints.sha256(path) if found else None, mine, [score for score in under if not score.lingling],
            catalog_only=not found and group.has_catalog_copy(file),
            elsewhere=frozenset(registered - {score.file_path for score in mine}),
        ))
    return FilePlan(files)


class Upload(NamedTuple):
    """一份要傳到 HuaXia 的檔

    Attributes:
        file: 群組裡的那份檔與它要取代的
        path: 要傳的檔：壓縮過的是暫存資料夾裡的壓縮檔，否則是本機那份
        size: 要傳的檔的大小（位元組）
    """
    file: PublishFile
    path: str
    size: int


@dataclass(frozen=True)
class GroupPublish:
    """一個群組這次發譜會寫進 HuaXia 的全部內容（發譜預覽確認後交給發譜）

    Attributes:
        group: 群組
        link: 對到的 HuaXia 曲目（現有的或新建的）
        voices: 要新建與覆寫的聲部（plan_voices）
        uploads: 要傳的檔，依檔案計畫的順序
        unchanged: HuaXia 上已登記著內容相同的那份、不必重傳的檔
        too_large: 超過大小上限、還沒有壓縮好的檔（壓縮中、壓縮失敗或仍超過）；有的話還不能發
        skipped: 本機找不到、這次跳過的檔（含只在譜庫的）
        dropped: 譜務選了「不發這份」的檔
        events: 要掛的公演
        deletes: 清舊檔（第 3 段）要刪的 Storage 檔（plan_deletions 算出；之前為空）
    """

    group: Group
    link: HuaXiaLink
    voices: VoicePlan
    uploads: List[Upload]
    unchanged: List[PublishFile]
    too_large: List[PublishFile]
    skipped: List[PublishFile]
    dropped: List[PublishFile]
    events: List[HuaXiaEvent]
    deletes: List[str] = field(default_factory=list)

    @property
    def replaces(self) -> List[HuaXiaScore]:
        """要撤掉登記的樂譜（泠靈發的舊檔）；檔案本體要不要刪看 deletes"""
        return [score for file in self.sent for score in file.replaces]

    @property
    def sent(self) -> List[PublishFile]:
        """切換後在 HuaXia 上登記著的檔：要傳的與不必重傳的"""
        return [upload.file for upload in self.uploads] + self.unchanged

    @property
    def size(self) -> int:
        """要傳的檔的總大小（位元組）"""
        return sum(upload.size for upload in self.uploads)


def oversized(files: FilePlan, size_limit: int) -> List[PublishFile]:
    """要發的檔裡超過大小上限、要先壓縮的"""
    return [file for file in files.publishing if file.size > size_limit]


def group_publish(
    group: Group, voices: VoicePlan, files: FilePlan, compressed: Dict[str, CompressionResult], dropped: Set[str],
    events: List[HuaXiaEvent], size_limit: int, fingerprints: Fingerprints,
) -> GroupPublish:
    """把一個群組在預覽各步的結果合成這次發譜要寫進 HuaXia 的內容

    超過大小上限的檔改傳壓縮好（CompressionStatus.COMPRESSED）的壓縮檔，內容指紋也換成壓縮檔的；還沒有壓縮好的
    列在 too_large。HuaXia 上已登記著內容相同那份的檔列在 unchanged，不傳。清舊檔要刪的由 plan_deletions 另算。

    Args:
        group: 群組（已對到曲目）
        voices: 聲部的配對與要寫的內容
        files: 要發的檔
        compressed: 壓縮結果，以本機檔的路徑為鍵
        dropped: 選了「不發這份」的本機檔路徑
        events: 要掛的公演
        size_limit: 單檔大小上限（位元組）
        fingerprints: 算壓縮檔內容的 SHA-256
    """
    uploads, unchanged, too_large, skipped_by_choice = [], [], [], []
    for file in files.publishing:
        if file.path in dropped:
            skipped_by_choice.append(file)
            continue
        path, size = file.path, file.size
        if file.size > size_limit:
            result = compressed.get(file.path)
            if result is None or result.status is not CompressionStatus.COMPRESSED:
                too_large.append(file)
                continue
            path, size = result.output_path, result.compressed_size
            file = replace(file, sha256=fingerprints.sha256(path))
        if file.unchanged:
            unchanged.append(file)
        else:
            uploads.append(Upload(file, path, size))
    return GroupPublish(
        group, group.huaxia_link, voices, uploads, unchanged, too_large, files.skipped, skipped_by_choice,
        list(events),
    )


def plan_deletions(plans: List[GroupPublish]) -> List[GroupPublish]:
    """算出各群組清舊檔（第 3 段）要刪的 Storage 檔，回傳填好 deletes 的各群組（順序不變）

    要刪的是被取代的樂譜的檔，扣掉這次發譜還要用到的（要傳的與不必重傳的），同一個檔只算在第一個取代它的群組。
    第 3 段刪之前再經 orphan_cleanup.delete_unregistered 確認那一刻沒有任何樂譜登記指著它。

    Args:
        plans: 這次發譜的各群組（group_publish）

    Returns:
        各群組，deletes 填好
    """
    sending = {file.storage_path for plan in plans for file in plan.sent if file.storage_path}
    taken: Set[str] = set()
    settled = []
    for plan in plans:
        deletes = []
        for score in plan.replaces:
            if score.file_path and score.file_path not in sending and score.file_path not in taken:
                taken.add(score.file_path)
                deletes.append(score.file_path)
        settled.append(replace(plan, deletes=deletes))
    return settled


class PublishStep(Enum):
    """發譜預覽的步驟，依順序"""

    PIECE = "piece"      # 曲目
    VOICES = "voices"    # 聲部
    FILES = "files"      # 檔案
    EVENTS = "events"    # 公演
    CONFIRM = "confirm"  # 確認

    def offset(self, delta: int) -> Optional["PublishStep"]:
        """往後（delta 為負時往前）數 delta 步的那一步；超出頭尾時為 None"""
        steps = list(PublishStep)
        index = steps.index(self) + delta
        return steps[index] if 0 <= index < len(steps) else None


class PublishPreview:
    """發譜預覽這次的選擇與各步的規則（不碰畫面）

    記著各群組在「聲部」步的決定、「檔案」步選了不發的檔與壓縮結果、「公演」步勾的公演；「曲目」步的選擇經
    Project.set_huaxia_link 立即寫進專案。voice_plan、file_plan、publish_plan 依目前的選擇算出各步的內容，
    step_ready、can_publish 判斷「下一步」「發譜」按不按得下去。
    """

    def __init__(
        self, project: Project, checks: List[GroupCheck], readiness: PublishReadiness, file_service: FileService,
        size_limit: int,
    ):
        """
        Args:
            project: 專案；「曲目」「聲部」步的選擇經它的編輯操作寫入
            checks: 各群組發譜前的檢查結果（check_groups）
            readiness: 按下發譜時 HuaXia 那一側的檢查結果
            file_service: 讀本機檔案
            size_limit: 單檔大小上限（位元組）
        """
        self.project = project
        self.checks = checks
        self.readiness = readiness
        self.size_limit = size_limit
        self._file_service = file_service
        self._fingerprints = Fingerprints(file_service)
        # 開啟預覽時各群組對到的曲目：改選別首再選回來時找回手動挑的聲部配對
        self._opened_links = {check.group.id: check.group.huaxia_link for check in checks}
        # 各群組這次在「聲部」步的決定，以群組 ID 為鍵；改選曲目時清掉
        self._decisions: Dict[str, VoiceDecisions] = {}
        # 壓縮結果，以本機檔的路徑為鍵；選了「不發這份」的本機檔路徑；勾選要掛的公演的編號
        self.compressed: Dict[str, CompressionResult] = {}
        self.dropped: Set[str] = set()
        self.chosen_events: Set[str] = set()

    @property
    def groups(self) -> List[Group]:
        """沒擋下的群組，依專案中的順序"""
        return [check.group for check in self.checks if not check.blocked]

    def has_piece(self, group: Group) -> bool:
        """群組已對到曲目：可以挑的現有曲目，或有曲名的新建曲目"""
        link = group.huaxia_link
        if link is None:
            return False
        if link.new_piece is not None:
            return bool(link.new_piece.title.strip())
        return any(piece.id == link.piece_id for piece in self.readiness.pieces)

    def sharing(self, group: Group) -> List[Group]:
        """沒擋下的其他群組裡，對到同一首 HuaXia 曲目的（一個群組對一首曲目，有的話兩邊都還不能發）

        現有曲目比曲目編號；新建、還沒產生編號的曲目各是一首新的，不算同一首。
        """
        piece_id = group.huaxia_link.piece_id if group.huaxia_link else ""
        if not piece_id:
            return []
        return [
            other for other in self.groups
            if other is not group and other.huaxia_link is not None and other.huaxia_link.piece_id == piece_id
        ]

    def set_piece(self, group: Group, link: Optional[HuaXiaLink]) -> None:
        """「曲目」步的選擇改了：寫進專案，換了曲目就清掉這次的聲部決定

        選回同一首現有曲目時沿用手動挑的聲部配對。新建的曲目沿用目前或開啟預覽時那份新建曲目已產生的編號與手動
        配對（發譜一開始產生編號，重發時沿用；改新建的欄位不換編號），不會在 HuaXia 上重複建曲目與聲部。
        """
        previous = group.huaxia_link
        for before in (previous, self._opened_links.get(group.id)):
            if link is None or before is None or (before.new_piece is None) != (link.new_piece is None):
                continue
            if link.new_piece is None and before.piece_id == link.piece_id:
                link.part_ids = dict(before.part_ids)
                break
            if link.new_piece is not None and before.piece_id:
                link.piece_id = before.piece_id
                link.part_ids = dict(before.part_ids)
                break
        if _piece_identity(previous) != _piece_identity(link):
            self._decisions.pop(group.id, None)
        self.project.set_huaxia_link(group, link)

    def assign_piece_ids(self) -> List[Group]:
        """按下「發譜」時替要新建曲目、還沒有編號的群組產生曲目編號，寫進專案（之後要自動存檔一次）

        已經有編號的（上次發到一半）沿用：HuaXia 的發譜函式遇到已存在的編號就沿用那首，重發不會重複建。

        Returns:
            這次產生了編號的群組
        """
        assigned = []
        for group in self.groups:
            link = group.huaxia_link
            if link is None or link.new_piece is None or link.piece_id:
                continue
            self.project.set_huaxia_link(group, HuaXiaLink(
                piece_id=str(uuid.uuid4()), piece_title=link.piece_title, new_piece=link.new_piece,
                part_ids=dict(link.part_ids),
            ))
            assigned.append(group)
        return assigned

    def decisions(self, group: Group) -> VoiceDecisions:
        """群組這次在「聲部」步的決定（還沒決定過時是全照預設的一份，之後改它就是改這次的決定）"""
        return self._decisions.setdefault(group.id, VoiceDecisions())

    def choose_part(self, group: Group, row: "VoiceRow", part_id: Optional[str]) -> None:
        """一個聲部改選了新建（part_id 為 None）或某個 HuaXia 聲部：記進專案；新建是忘掉記過的配對、這次不套名稱規則"""
        decisions = self.decisions(group)
        if part_id is None:
            decisions.new.add(row.index)
        else:
            decisions.new.discard(row.index)
        self.project.choose_huaxia_part(group, row.voice, part_id)

    def set_overwrite(self, group: Group, index: int, overwrite: bool) -> None:
        """樂器表第 index 列有差異時要不要以泠靈的值覆寫 HuaXia 的（預設覆寫）"""
        _toggle(self.decisions(group).keep, index, not overwrite)

    def set_create_absent(self, group: Group, index: int, create: bool) -> None:
        """樂器表第 index 列（本專案沒有、HuaXia 也沒有）要不要在 HuaXia 建（預設不建）"""
        _toggle(self.decisions(group).create_absent, index, create)

    def drop(self, path: str, dropped: bool) -> None:
        """「不發這份」：dropped 為不發這份本機檔"""
        _toggle(self.dropped, path, dropped)

    def choose_event(self, event_id: str, chosen: bool) -> None:
        """勾選或取消一場要掛的公演"""
        _toggle(self.chosen_events, event_id, chosen)

    def voice_plan(self, group: Group) -> VoicePlan:
        """群組目前的聲部配對與要寫的內容（依「曲目」步對到的曲目與「聲部」步的選擇）；群組要已對到曲目

        新建的曲目已有編號、而且 HuaXia 上已經有它（上次發到一半）時，用它已有的聲部。
        """
        parts = self.readiness.parts.get(group.huaxia_link.piece_id, [])
        return plan_voices(self.project, group, parts, self.decisions(group))

    def file_plan(self, group: Group) -> FilePlan:
        """群組要發的檔與各自要取代的（依目前的聲部配對）；群組要已對到曲目

        新建的曲目已有編號、而且 HuaXia 上已經有它時，用它已登記的樂譜。
        """
        scores = self.readiness.scores.get(group.huaxia_link.piece_id, [])
        return plan_files(group, self.voice_plan(group), scores, self._file_service, self._fingerprints)

    def publish_plan(self) -> List[GroupPublish]:
        """每個沒擋下的群組這次發譜要寫進 HuaXia 的全部內容（依目前的選擇，含清舊檔要刪的），依專案中的順序

        要掛的公演是勾了的、群組的曲目還沒掛著的。
        """
        chosen = [event for event in self.readiness.concerts if event.id in self.chosen_events]
        return plan_deletions([
            group_publish(
                group, self.voice_plan(group), self.file_plan(group), self.compressed, self.dropped,
                [event for event in chosen if not self.is_attached(group, event.id)],
                self.size_limit, self._fingerprints,
            )
            for group in self.groups
        ])

    def is_attached(self, group: Group, event_id: str) -> bool:
        """群組對到的曲目已經掛著這場公演"""
        link = group.huaxia_link
        return link is not None and event_id in self.readiness.attached.get(link.piece_id, set())

    def attached_groups(self, event_id: str) -> List[Group]:
        """沒擋下的群組裡，對到的曲目已經掛著這場公演的"""
        return [group for group in self.groups if self.is_attached(group, event_id)]

    def to_compress(self) -> List[str]:
        """沒擋下的群組裡超過大小上限、還沒壓縮過、也沒選不發的本機檔，依群組與檔案計畫的順序"""
        return [
            file.path for group in self.groups for file in oversized(self.file_plan(group), self.size_limit)
            if file.path not in self.compressed and file.path not in self.dropped
        ]

    def close(self, compressor: CompressionService, publishing: bool) -> None:
        """關閉預覽時收拾壓縮檔：按下「發譜」（publishing）時留下要傳的（交給發譜，publish_plan 照樣交出，
        發譜結束時由發譜丟掉），其餘的與取消時的全部丟掉

        Args:
            compressor: 產生這些壓縮檔的壓縮服務
            publishing: 是按下「發譜」關閉的
        """
        uploading = {upload.path for plan in self.publish_plan() for upload in plan.uploads} if publishing else set()
        kept = {}
        for path, result in self.compressed.items():
            if result.output_path in uploading:
                kept[path] = result
            else:
                compressor.discard(result)
        self.compressed = kept

    def step_ready(self, step: PublishStep) -> bool:
        """這一步是否都決定好了（「下一步」）：「曲目」要每個沒擋下的群組都對到曲目、沒有兩個群組對到同一首，
        「聲部」要沒有群組還不能發"""
        if step is PublishStep.PIECE:
            return all(self.has_piece(group) and not self.sharing(group) for group in self.groups)
        if step is PublishStep.VOICES:
            return not any(self.voice_plan(group).blocked for group in self.groups)
        return True

    def can_publish(self, compressing: bool) -> bool:
        """「發譜」按得下去：前兩步都決定好、壓縮做完（compressing 為 False），有群組可以發，
        而且超過上限的檔都壓縮好或選了不發"""
        if compressing or not self.step_ready(PublishStep.PIECE) or not self.step_ready(PublishStep.VOICES):
            return False
        plans = self.publish_plan()
        return bool(plans) and not any(plan.too_large for plan in plans)


def _toggle(items: set, item, on: bool) -> None:
    """on 時把 item 加進 items，否則拿掉"""
    if on:
        items.add(item)
    else:
        items.discard(item)


def _piece_identity(link: Optional[HuaXiaLink]):
    """群組對到哪首曲目（新建的都算同一首）；沒對到時為 None"""
    if link is None:
        return None
    return "new" if link.new_piece is not None else link.piece_id


def upcoming_concerts(events: List[HuaXiaEvent], now: datetime.datetime) -> List[HuaXiaEvent]:
    """場次裡可以掛的公演：還沒結束、類型含公演（不含排練與其他類型），順序不變

    Args:
        events: 本學期的場次
        now: 現在的時間（帶時區）
    """
    return [event for event in events if CONCERT_KIND in event.kinds and event.ends_at > now]


def recheck(auth: HuaXiaAuthService) -> PublishReadiness:
    """重查幹部身分（不是幹部就登出）與發譜函式的版本（按下發譜開預覽前、在預覽按下「發譜」開始前各一次）

    Args:
        auth: HuaXia 登入服務

    Returns:
        檢查結果（不讀曲目）；能發時帶發譜函式的版本與登入的 Discord 帳號名稱

    Raises:
        HuaXiaError: 沒有登入、登入已失效或連不上 HuaXia
    """
    not_officer = auth.recheck_officer()
    if not_officer is not None:
        return PublishReadiness(not_officer=not_officer)
    version = auth.client.publish_interface_version(auth.access_token())
    if version is None:
        return PublishReadiness(function_problem=FunctionProblem.MISSING)
    if version != PUBLISH_INTERFACE_VERSION:
        return PublishReadiness(function_problem=FunctionProblem.VERSION_MISMATCH, function_version=version)
    return PublishReadiness(function_version=version, discord_name=auth.status().discord_name)


class PublishService:
    """發譜到 HuaXia：按下發譜時 HuaXia 那一側的檢查"""

    def __init__(self, auth: HuaXiaAuthService):
        """
        Args:
            auth: HuaXia 登入服務（全程式一份）；請求經它的用戶端送出
        """
        self._auth = auth
        self._client = auth.client

    def prepare(self) -> PublishReadiness:
        """重查幹部身分、查發譜函式的版本，都過了才讀可以挑的曲目、它們的聲部與樂譜，以及可以掛的公演與曲目
        已經掛著哪幾場；不是幹部時已登出

        Returns:
            檢查結果；不能發時寫明原因

        Raises:
            HuaXiaError: 沒有登入、登入已失效或連不上 HuaXia
        """
        checked = recheck(self._auth)
        if not checked.ready:
            return checked
        token = self._auth.access_token()
        pieces = self._client.pieces(token)
        piece_ids = [piece.id for piece in pieces]
        parts: Dict[str, List[HuaXiaPart]] = {piece_id: [] for piece_id in piece_ids}
        for part in self._client.piece_parts(token, piece_ids):
            parts[part.piece_id].append(part)
        scores: Dict[str, List[HuaXiaScore]] = {piece_id: [] for piece_id in piece_ids}
        for score in self._client.scores(token, piece_ids):
            scores[score.piece_id].append(score)
        semester = self._client.current_semester(token)
        events = self._client.events(token, semester) if semester else []
        concerts = upcoming_concerts(events, datetime.datetime.now(datetime.timezone.utc))
        attached: Dict[str, Set[str]] = {}
        for event_id, piece_id in self._client.event_pieces(token, [event.id for event in concerts]):
            if piece_id in parts:
                attached.setdefault(piece_id, set()).add(event_id)
        return replace(
            checked, pieces=pieces, parts={piece: found for piece, found in parts.items() if found},
            scores={piece: found for piece, found in scores.items() if found}, concerts=concerts, attached=attached,
        )
