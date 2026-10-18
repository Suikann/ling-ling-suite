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
一個群組這次發譜要寫進 HuaXia 的全部內容（GroupPublish）。

使用範例：
    from services.publish_service import PublishService, check_groups, project_blocks
    if not project_blocks(project):
        readiness = PublishService(huaxia_auth).prepare()   # 背景執行緒
        if readiness.ready:
            checks = check_groups(project)
"""
import datetime
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Dict, List, NamedTuple, Optional, Set, Tuple

from core.constants import OTHER_SECTION, CompressionStatus, OrchestraType
from core.models import CatalogChange, Group, Headcount, HuaXiaLink, Project
from core.naming import is_renamed
from core.voice_names import duplicate_rows, same_voice
from services.huaxia_auth_service import HuaXiaAuthService, NotOfficer
from services.compression_service import CompressionResult
from services.file_service import FileService
from services.huaxia_client import PUBLISH_INTERFACE_VERSION, HuaXiaEvent, HuaXiaPart, HuaXiaPiece, HuaXiaScore

# HuaXia 場次類型裡的「公演」（events.kinds）
CONCERT_KIND = "concert"


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
        discord_name: 登入的 Discord 帳號名稱
    """

    not_officer: Optional[NotOfficer] = None
    function_problem: Optional[FunctionProblem] = None
    function_version: Optional[int] = None
    pieces: List[HuaXiaPiece] = field(default_factory=list)
    parts: Dict[str, List[HuaXiaPart]] = field(default_factory=dict)
    scores: Dict[str, List[HuaXiaScore]] = field(default_factory=dict)
    concerts: List[HuaXiaEvent] = field(default_factory=list)
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


def check_groups(project: Project) -> List[GroupCheck]:
    """專案每個群組（依專案中的順序）發譜前的檢查結果；不改專案"""
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
        for group in project.groups
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
        options: 可以改選的 HuaXia 聲部，依 HuaXia 的排序：名稱規則對到多個時只有那幾個，否則是名稱規則沒配給
            其他聲部的（別的聲部手動挑過的也列，挑了會成為衝突）
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

    Args:
        project: 專案（聲部組以它的對照為準）
        group: 群組；對到新建的曲目時 parts 為空
        parts: 群組對到的 HuaXia 曲目上已有的聲部
        decisions: 譜務這次的決定；省略時全照預設

    Returns:
        配對結果與要寫的內容
    """
    decisions = decisions or VoiceDecisions()
    link = group.huaxia_link
    by_id = {part.id: part for part in parts}
    rows = list(zip(group.instruments, group.present, group.headcounts))
    chosen: Dict[int, HuaXiaPart] = {}
    for index, (voice, _present, _headcount) in enumerate(rows):
        part_id = link.part_for(voice) if link else None
        if index not in decisions.new and part_id in by_id:
            chosen[index] = by_id[part_id]
    candidates = {
        index: [
            part for part in parts
            if same_voice(part.name, voice) and all(i == index or mine.id != part.id for i, mine in chosen.items())
        ]
        for index, (voice, _present, _headcount) in enumerate(rows)
    }
    matches: Dict[int, Tuple[PartMatch, Optional[HuaXiaPart]]] = {}
    for index in range(len(rows)):
        if index in chosen:
            matches[index] = (PartMatch.CHOSEN, chosen[index])
        elif index in decisions.new or not candidates[index]:
            matches[index] = (PartMatch.NONE, None)
        elif len(candidates[index]) == 1:
            matches[index] = (PartMatch.NAME, candidates[index][0])
        else:
            matches[index] = (PartMatch.AMBIGUOUS, None)
    assigned = [part.id for _match, part in matches.values() if part is not None]
    unpaired = [part for part in parts if part.id not in assigned]
    named = {index: part.id for index, (match, part) in matches.items() if match is PartMatch.NAME}
    next_sort = max((part.sort for part in parts), default=0)
    voice_rows, creates, updates, undecided, unfilled = [], [], [], [], []
    for index, (voice, present, headcount) in enumerate(rows):
        match, part = matches[index]
        section = project.section_of(voice)
        several = len(candidates[index]) > 1 and index not in decisions.new
        options = candidates[index] if several else [
            option for option in parts if all(i == index or found != option.id for i, found in named.items())
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


@dataclass(frozen=True)
class PublishFile:
    """群組要發的一份本機檔（總譜或一個聲部的分譜）與它在 HuaXia 上要取代的

    Attributes:
        voice: 泠靈的聲部名稱；總譜為空字串
        part: 對到的 HuaXia 聲部；總譜、或聲部要在 HuaXia 新建時為 None
        path: 本機的檔（輸出位置）
        size: 檔案大小（位元組）；本機找不到時為 None
        replaces: 要取代的：同一個聲部（總譜是聲部空白）底下、泠靈發的樂譜
        others: 同一個聲部底下其他人放的樂譜（一律不動，只標出）
        catalog_only: 本機找不到、譜庫裡有一份（只在譜庫），這次跳過
    """

    voice: str
    part: Optional[HuaXiaPart]
    path: str
    size: Optional[int]
    replaces: List[HuaXiaScore] = field(default_factory=list)
    others: List[HuaXiaScore] = field(default_factory=list)
    catalog_only: bool = False

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


def plan_files(group: Group, voices: VoicePlan, scores: List[HuaXiaScore], file_service: FileService) -> FilePlan:
    """群組每份要發的檔在 HuaXia 上要取代哪份、同聲部有哪些別人放的檔；不改專案

    分譜依本專案有的聲部配到的分譜（Group.pairing），對到 voices 裡那一列的 HuaXia 聲部；要取代的是那個聲部底下、
    泠靈發的樂譜（以泠靈的 Storage 路徑格式辨認，不看樂譜名稱），總譜是聲部空白（不含舊的分組樂譜）、泠靈發的。
    要在 HuaXia 新建的聲部沒有要取代的。本機找不到的檔跳過，群組記著譜庫裡有一份的標成只在譜庫。

    Args:
        group: 群組（已重新命名，檔在輸出位置）
        voices: 群組的聲部對到 HuaXia 的結果（plan_voices）
        scores: 群組對到的 HuaXia 曲目上登記的樂譜；新建的曲目為空
        file_service: 讀本機檔案大小

    Returns:
        各份檔與要取代的
    """
    parts = {row.index: row.part for row in voices.rows}
    entries = [(voice.voice, parts.get(voice.index), voice.file) for voice in group.pairing() if voice.file]
    if group.score_file is not None:
        entries.append(("", None, group.score_file))
    files = []
    for voice, part, file in entries:
        if voice and part is None:
            under = []
        elif voice:
            under = [score for score in scores if score.part_id == part.id]
        else:
            under = [score for score in scores if score.part_id is None and not score.section]
        found = file_service.file_exists(file.original_path)
        files.append(PublishFile(
            voice, part, file.original_path, file_service.file_size(file.original_path) if found else None,
            [score for score in under if score.lingling], [score for score in under if not score.lingling],
            catalog_only=not found and group.has_catalog_copy(file),
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
        too_large: 超過大小上限、還沒有壓縮好的檔（壓縮中、壓縮失敗或仍超過）；有的話還不能發
        skipped: 本機找不到、這次跳過的檔（含只在譜庫的）
        dropped: 譜務選了「不發這份」的檔
        events: 要掛的公演
    """

    group: Group
    link: HuaXiaLink
    voices: VoicePlan
    uploads: List[Upload]
    too_large: List[PublishFile]
    skipped: List[PublishFile]
    dropped: List[PublishFile]
    events: List[HuaXiaEvent]

    @property
    def replaces(self) -> List[HuaXiaScore]:
        """要撤掉登記並刪掉檔案本體的樂譜（泠靈發的舊檔）"""
        return [score for upload in self.uploads for score in upload.file.replaces]

    @property
    def size(self) -> int:
        """要傳的檔的總大小（位元組）"""
        return sum(upload.size for upload in self.uploads)


def oversized(files: FilePlan, size_limit: int) -> List[PublishFile]:
    """要發的檔裡超過大小上限、要先壓縮的"""
    return [file for file in files.publishing if file.size > size_limit]


def group_publish(
    group: Group, voices: VoicePlan, files: FilePlan, compressed: Dict[str, CompressionResult], dropped: Set[str],
    events: List[HuaXiaEvent], size_limit: int,
) -> GroupPublish:
    """把一個群組在預覽各步的結果合成這次發譜要寫進 HuaXia 的內容

    超過大小上限的檔改傳壓縮好（CompressionStatus.COMPRESSED）的壓縮檔；還沒有壓縮好的列在 too_large。

    Args:
        group: 群組（已對到曲目）
        voices: 聲部的配對與要寫的內容
        files: 要發的檔
        compressed: 壓縮結果，以本機檔的路徑為鍵
        dropped: 選了「不發這份」的本機檔路徑
        events: 要掛的公演
        size_limit: 單檔大小上限（位元組）
    """
    uploads, too_large, skipped_by_choice = [], [], []
    for file in files.publishing:
        if file.path in dropped:
            skipped_by_choice.append(file)
            continue
        if file.size > size_limit:
            result = compressed.get(file.path)
            if result is None or result.status is not CompressionStatus.COMPRESSED:
                too_large.append(file)
                continue
            uploads.append(Upload(file, result.output_path, result.compressed_size))
        else:
            uploads.append(Upload(file, file.path, file.size))
    return GroupPublish(
        group, group.huaxia_link, voices, uploads, too_large, files.skipped, skipped_by_choice, list(events),
    )


def upcoming_concerts(events: List[HuaXiaEvent], now: datetime.datetime) -> List[HuaXiaEvent]:
    """場次裡可以掛的公演：還沒結束、類型含公演（不含排練與其他類型），順序不變

    Args:
        events: 本學期的場次
        now: 現在的時間（帶時區）
    """
    return [event for event in events if CONCERT_KIND in event.kinds and event.ends_at > now]


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
        """重查幹部身分、查發譜函式的版本，都過了才讀可以挑的曲目、它們的聲部與樂譜，以及可以掛的公演；不是幹部時已登出

        Returns:
            檢查結果；不能發時寫明原因

        Raises:
            HuaXiaError: 沒有登入、登入已失效或連不上 HuaXia
        """
        not_officer = self._auth.recheck_officer()
        if not_officer is not None:
            return PublishReadiness(not_officer=not_officer)
        token = self._auth.access_token()
        version = self._client.publish_interface_version(token)
        if version is None:
            return PublishReadiness(function_problem=FunctionProblem.MISSING)
        if version != PUBLISH_INTERFACE_VERSION:
            return PublishReadiness(function_problem=FunctionProblem.VERSION_MISMATCH, function_version=version)
        pieces = self._client.pieces(token)
        parts: Dict[str, List[HuaXiaPart]] = {piece.id: [] for piece in pieces}
        for part in self._client.piece_parts(token):
            if part.piece_id in parts:
                parts[part.piece_id].append(part)
        scores: Dict[str, List[HuaXiaScore]] = {piece.id: [] for piece in pieces}
        for score in self._client.scores(token):
            if score.piece_id in scores:
                scores[score.piece_id].append(score)
        semester = self._client.current_semester(token)
        events = self._client.events(token, semester) if semester else []
        return PublishReadiness(
            function_version=version, pieces=pieces, parts={piece: found for piece, found in parts.items() if found},
            scores={piece: found for piece, found in scores.items() if found},
            concerts=upcoming_concerts(events, datetime.datetime.now(datetime.timezone.utc)),
            discord_name=self._auth.status().discord_name,
        )
