# -*- coding: utf-8 -*-
"""
資料模型

定義專案、群組、檔案資訊等核心資料結構。
"""
import math
import os
import uuid
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import (
    TYPE_CHECKING, Any, Callable, ClassVar, Dict, FrozenSet, Iterable, List, NamedTuple, Optional, Set, Tuple,
)
from core.constants import (
    DEFAULT_MASTER_TEMPLATE, DEFAULT_ORCHESTRA_TYPE, DEFAULT_PARTS_SUBFOLDER_NAME, DEFAULT_SUBFOLDER_TEMPLATE,
    HUAXIA_DURATION_STEP_MINUTES, INSTRUMENTATION_FILE_VERSION, HuaXiaEnsemble, LEGACY_PROJECT_FILE_VERSIONS, PROJECT_FILE_VERSION, OperationKind,
    OrchestraType, PartsOutputMode, WorkspaceStatus,
)
from core.paths import path_key
from core.sections import (
    guess_orchestra_type, migrate_sections, put_section, section_for, section_options, stored_section,
)
from core.template_engine import (
    convert_template_language, detect_piece_name, detect_score_index, extract_instruments_from_filenames,
)
from core.voice_names import (
    VoiceKey, distinct_voices, duplicate_rows, instrument_key, match_by_voice, same_instrument, same_voice,
    voice_index, voice_key,
)

if TYPE_CHECKING:
    from core.catalog_models import CatalogPlace
    from core.catalog_sync import SyncOutcome
    from core.deposit import DepositRecord


class DuplicateVoiceError(ValueError):
    """樂器表新增或改名會讓兩列成為同一聲部（重名）時丟出

    Attributes:
        existing: 樂器表裡已有、與新名稱同一聲部的那一列的寫法
    """

    def __init__(self, existing: str):
        super().__init__(f"樂器表已有同一聲部：{existing}")
        self.existing = existing


class DuplicateVoicesError(ValueError):
    """群組的樂器表有重名的列（舊專案留下的）、不能連到譜庫時丟出

    Attributes:
        rows: 與另一列同一聲部的每一列（從 0 起算，見 core.voice_names.duplicate_rows）
    """

    def __init__(self, rows: List[int]):
        super().__init__(f"樂器表有重名的列：{rows}")
        self.rows = rows


class FiledVoicesError(ValueError):
    """在連到譜庫的群組刪除（或改名成別的聲部）已有 Drive 檔案的聲部：等於從樂譜刪掉它的檔，要到譜庫視窗處理"""

    def __init__(self, voices: List[str]):
        """
        Args:
            voices: 已有 Drive 檔案、不能刪除的聲部，依樂器表順序
        """
        super().__init__("、".join(voices))
        self.voices = voices


class ReadOnlyProjectError(Exception):
    """存檔唯讀的專案（由較新版本的程式建立的專案檔）時丟出"""

    def __init__(self):
        super().__init__("這個專案檔由較新版本的泠靈建立，不能存檔")


@dataclass
class FileInfo:
    """檔案資訊

    Attributes:
        original_path: 本機路徑
        display_name: 顯示名稱
        deposit_md5: 存入指紋：最後一次存入譜庫時，譜庫裡那份內容的 MD5；沒存入過為空字串
    """
    original_path: str
    display_name: str
    deposit_md5: str = ""

    def to_data(self) -> Dict[str, str]:
        """專案檔中存的內容；沒存入過的不寫存入指紋"""
        data = {"original_path": self.original_path, "display_name": self.display_name}
        if self.deposit_md5:
            data["deposit_md5"] = self.deposit_md5
        return data

    @classmethod
    def from_data(cls, data: Dict[str, str]) -> "FileInfo":
        """由專案檔中的內容還原"""
        return cls(
            original_path=data["original_path"], display_name=data["display_name"],
            deposit_md5=data.get("deposit_md5", ""),
        )


class VoicePart(NamedTuple):
    """樂器表裡一個本專案有的聲部與配到的分譜

    Attributes:
        index: 聲部在樂器表中的位置（從 0 起算；序號依它）
        voice: 聲部名稱
        file: 配到的分譜；分譜比本專案有的聲部少時，後面的聲部為 None
    """
    index: int
    voice: str
    file: Optional[FileInfo]


class Headcount(NamedTuple):
    """一個聲部的人數範圍（樂譜的要求，不是這次排了幾個人）

    寫入群組前經 normalized 整理，所以群組裡存的一律是：0 當成留空；最多不大於最少時留空（等於最少）；
    「以上」沒有最多。

    Attributes:
        minimum: 最少人數；None 為未填
        maximum: 最多人數；None 為等於最少
        open_ended: 是否為「最少人數以上」
    """
    minimum: Optional[int] = None
    maximum: Optional[int] = None
    open_ended: bool = False

    @property
    def filled(self) -> bool:
        """是否已填最少人數（發譜時必填）"""
        return self.minimum is not None

    def normalized(self) -> "Headcount":
        """整理成群組裡存的形式：0 為留空，最多不大於最少或為「以上」時留空"""
        minimum = self.minimum or None
        maximum = self.maximum or None
        if self.open_ended or (minimum is not None and maximum is not None and maximum <= minimum):
            maximum = None
        return Headcount(minimum, maximum, bool(self.open_ended))

    def text(self) -> str:
        """HuaXia 的人數記法：「6」「6-8」「2↑」；未填為空字串"""
        if self.minimum is None:
            return ""
        if self.open_ended:
            return f"{self.minimum}↑"
        if self.maximum is not None:
            return f"{self.minimum}-{self.maximum}"
        return str(self.minimum)

    def to_data(self) -> Dict[str, Any]:
        """專案檔中存的內容"""
        return {"minimum": self.minimum, "maximum": self.maximum, "open_ended": self.open_ended}

    @classmethod
    def from_data(cls, data: Dict[str, Any]) -> "Headcount":
        """由專案檔中的內容還原"""
        return cls(data.get("minimum"), data.get("maximum"), bool(data.get("open_ended", False))).normalized()


def sum_headcounts(headcounts: Iterable[Headcount]) -> Headcount:
    """幾個聲部的人數合計：最少相加、最多相加（沒有最多的以最少計），有任一個「以上」就是以上；未填的不算"""
    filled = [h for h in headcounts if h.filled]
    if not filled:
        return Headcount()
    minimum = sum(h.minimum for h in filled)
    maximum = sum(h.maximum or h.minimum for h in filled)
    return Headcount(minimum, maximum, any(h.open_ended for h in filled)).normalized()


def format_duration(seconds: Optional[int]) -> str:
    """時長的「分:秒」寫法（秒補成兩位，例如 7:05）；未填為空字串"""
    if not seconds:
        return ""
    minutes, rest = divmod(seconds, 60)
    return f"{minutes}:{rest:02d}"


def duration_minutes(seconds: Optional[int]) -> Optional[float]:
    """時長（秒）換算成 HuaXia 的分鐘數，四捨五入到 HUAXIA_DURATION_STEP_MINUTES；未填為 None"""
    if not seconds:
        return None
    steps = math.floor(seconds / 60 / HUAXIA_DURATION_STEP_MINUTES + 0.5)
    return steps * HUAXIA_DURATION_STEP_MINUTES


class CatalogChangeKind(Enum):
    """群組相對於譜庫快照的一種改動"""
    VOICES = "voices"          # 聲部清單（增刪、名稱、順序）
    HEADCOUNT = "headcount"    # 一個聲部的人數
    DURATION = "duration"      # 時長
    SECTION = "section"        # 一種樂器在社團對照裡的聲部組


class CatalogChange(NamedTuple):
    """一項待寫進譜庫的改動（群組目前的值相對於連線時的快照）

    Attributes:
        kind: 改動的種類
        voice: 聲部（人數）或樂器的一個聲部寫法（聲部組）；聲部清單與時長為空字串
        old: 快照裡的值：聲部清單為名稱的 tuple，人數為 Headcount（快照沒有這個聲部時為 None），
            時長為秒數或 None，聲部組為名稱（社團對照沒有時為空字串）
        new: 群組目前的值，形式同 old
    """
    kind: CatalogChangeKind
    voice: str
    old: Any
    new: Any


@dataclass
class CatalogSnapshot:
    """群組連到的那處在譜庫裡的編制與時長，連同社團對照裡群組各樂器的聲部組（最後一次從譜庫讀到或寫進去的）

    Attributes:
        voices: 聲部名稱與人數，依譜庫的順序
        duration_seconds: 時長（秒）；未填為 None
        sections: 社團對照裡有的、群組樂器表裡各樂器的聲部組（樂器的一個聲部寫法 → 聲部組）；沒有的樂器不列
        filed: 那處已有 Drive 檔案的聲部（不能從群組刪除）
    """
    voices: List[Tuple[str, Headcount]] = field(default_factory=list)
    duration_seconds: Optional[int] = None
    sections: Dict[str, str] = field(default_factory=dict)
    filed: List[str] = field(default_factory=list)

    def to_data(self) -> Dict[str, Any]:
        """專案檔中存的內容"""
        return {
            "voices": [{"name": name, "headcount": headcount.to_data()} for name, headcount in self.voices],
            "duration_seconds": self.duration_seconds,
            "sections": dict(self.sections),
            "filed": list(self.filed),
        }

    @classmethod
    def from_data(cls, data: Dict[str, Any]) -> "CatalogSnapshot":
        """由專案檔中的內容還原"""
        return cls(
            voices=[(v["name"], Headcount.from_data(v.get("headcount") or {})) for v in data.get("voices", [])],
            duration_seconds=data.get("duration_seconds") or None,
            sections=dict(data.get("sections") or {}),
            filed=list(data.get("filed") or []),
        )


@dataclass
class CatalogLink:
    """群組連到譜庫的哪一處（版本的整首或一個樂章），以及那處的快照

    曲名、版本與樂章的名稱記下來給群組面板顯示，不必連網；譜庫裡改名時，下次讀到那處再更新。

    Attributes:
        edition_id: 版本 ID
        movement_id: 樂章 ID；整首為空字串
        piece_title: 曲名
        edition_label: 版本的標示
        movement_name: 樂章名稱；整首為空字串
        snapshot: 那處的快照，待寫入的改動相對於它計算（Project.catalog_changes）
        other_pieces: 群組各樂器在譜庫裡被其他曲目用到幾首（樂器的一個聲部寫法 → 曲目數），聲部組旁寫影響範圍用；
            讀到那處時更新
        unsynced: 上次存檔時寫不進譜庫（未同步）；下次寫回成功時清掉
        confirm_voices: 開啟專案時譜庫的聲部清單變了，選了「用專案的（存檔時寫回）」：寫回前先在衝突畫面確認，
            群組的聲部清單要蓋掉譜庫的；寫回成功時清掉
    """
    edition_id: str
    movement_id: str = ""
    piece_title: str = ""
    edition_label: str = ""
    movement_name: str = ""
    snapshot: CatalogSnapshot = field(default_factory=CatalogSnapshot)
    other_pieces: Dict[str, int] = field(default_factory=dict)
    unsynced: bool = False
    confirm_voices: bool = False

    def other_pieces_using(self, voice: str) -> int:
        """聲部的樂器在譜庫裡被其他曲目用到幾首（同一樂器的規則）；不知道時為 0"""
        key = instrument_key(voice)
        return next((count for name, count in self.other_pieces.items() if instrument_key(name) == key), 0)

    def to_data(self) -> Dict[str, Any]:
        """專案檔中存的內容"""
        return {
            "edition_id": self.edition_id,
            "movement_id": self.movement_id,
            "piece_title": self.piece_title,
            "edition_label": self.edition_label,
            "movement_name": self.movement_name,
            "snapshot": self.snapshot.to_data(),
            "other_pieces": dict(self.other_pieces),
            "unsynced": self.unsynced,
            "confirm_voices": self.confirm_voices,
        }

    @classmethod
    def from_data(cls, data: Dict[str, Any]) -> "CatalogLink":
        """由專案檔中的內容還原"""
        return cls(
            edition_id=data.get("edition_id", ""),
            movement_id=data.get("movement_id", ""),
            piece_title=data.get("piece_title", ""),
            edition_label=data.get("edition_label", ""),
            movement_name=data.get("movement_name", ""),
            snapshot=CatalogSnapshot.from_data(data.get("snapshot") or {}),
            other_pieces={str(k): int(v) for k, v in (data.get("other_pieces") or {}).items()},
            unsynced=bool(data.get("unsynced")),
            confirm_voices=bool(data.get("confirm_voices")),
        )


class CatalogListChange(NamedTuple):
    """開啟專案時讀到的譜庫那處，聲部清單與群組的不同（Project.catalog_list_change）

    Attributes:
        voices: 譜庫現在的聲部名稱與人數，依譜庫的順序
        added: 只在譜庫的聲部（譜庫的寫法，依譜庫的順序）
        removed: 只在群組的聲部（依樂器表順序）
        moved: 兩邊都有、在譜庫裡換了相對順序的聲部（群組的寫法，依樂器表順序）
    """
    voices: List[Tuple[str, "Headcount"]]
    added: List[str]
    removed: List[str]
    moved: List[str]


class NewHuaXiaPiece(NamedTuple):
    """發譜時要在 HuaXia 新建的曲目的欄位（pieces 的同名欄位；備註留空）

    Attributes:
        title: 曲名
        composer: 作曲家
        duration_min: 時長（分鐘）；未填為 None
        ensemble: 合奏或絲竹
    """
    title: str
    composer: str
    duration_min: Optional[float]
    ensemble: HuaXiaEnsemble

    @classmethod
    def for_group(cls, group: "Group") -> "NewHuaXiaPiece":
        """新建曲目的預設值：曲名＋樂章名稱（沒有樂章名稱用樂章編號，整首只用曲名）、群組的作曲家與時長、合奏

        曲名沒填時用群組名稱；時長由秒換算成分鐘，四捨五入到 HUAXIA_DURATION_STEP_MINUTES。
        """
        movement = group.movement_name.strip() or group.movement_number.strip()
        title = " ".join(part for part in (group.piece_name.strip() or group.name.strip(), movement) if part)
        return cls(title, group.composer, duration_minutes(group.duration_seconds), HuaXiaEnsemble.TUTTI)

    def to_data(self) -> Dict[str, Any]:
        """專案檔中存的內容"""
        return {
            "title": self.title, "composer": self.composer, "duration_min": self.duration_min,
            "ensemble": self.ensemble.value,
        }

    @classmethod
    def from_data(cls, data: Dict[str, Any]) -> "NewHuaXiaPiece":
        """由專案檔中的內容還原；不認得的合奏／絲竹當成合奏"""
        try:
            ensemble = HuaXiaEnsemble(data.get("ensemble"))
        except ValueError:
            ensemble = HuaXiaEnsemble.TUTTI
        return cls(data.get("title", ""), data.get("composer", ""), data.get("duration_min"), ensemble)


@dataclass
class HuaXiaLink:
    """群組對到的 HuaXia 曲目：挑選現有的，或發譜時新建的

    只記曲目編號與新建的欄位，不記登入資訊。曲名記下來給群組面板顯示，不必連網。

    Attributes:
        piece_id: HuaXia 的曲目編號（pieces.id）；新建的曲目在發譜一開始才產生，之前為空字串
        piece_title: 曲名
        new_piece: 新建時的欄位；挑選現有曲目時為 None
    """
    piece_id: str = ""
    piece_title: str = ""
    new_piece: Optional[NewHuaXiaPiece] = None

    def to_data(self) -> Dict[str, Any]:
        """專案檔中存的內容"""
        return {
            "piece_id": self.piece_id,
            "piece_title": self.piece_title,
            "new_piece": self.new_piece.to_data() if self.new_piece else None,
        }

    @classmethod
    def from_data(cls, data: Dict[str, Any]) -> "HuaXiaLink":
        """由專案檔中的內容還原"""
        new_piece = data.get("new_piece")
        return cls(
            piece_id=data.get("piece_id", ""),
            piece_title=data.get("piece_title", ""),
            new_piece=NewHuaXiaPiece.from_data(new_piece) if new_piece else None,
        )


class LinkChoice(Enum):
    """群組與譜庫那處都已有編制時，合併差異表上整張選用哪一邊"""
    CATALOG = "catalog"    # 用譜庫的：換成譜庫的編制，分譜依聲部名稱帶著走
    GROUP = "group"        # 用群組的：編制與配對不變，下次存檔覆寫譜庫


class MergeDifference(Enum):
    """合併差異表一列的差異"""
    SAME = "same"                  # 兩邊都有、人數相同
    HEADCOUNT = "headcount"        # 兩邊都有、人數不同
    GROUP_ONLY = "group_only"      # 只在群組
    CATALOG_ONLY = "catalog_only"  # 只在譜庫


class MergeRow(NamedTuple):
    """合併差異表的一列：一個聲部在兩邊的人數與聲部組，以及用譜庫的時它配到的分譜（Project.merge_rows）

    Attributes:
        catalog_voice: 譜庫的寫法；只在群組時為空字串
        group_voice: 群組樂器表的寫法；只在譜庫時為空字串
        catalog_headcount: 譜庫的人數；只在群組時為 None
        group_headcount: 群組的人數；只在譜庫時為 None
        section: 連線後的聲部組（社團對照有就是社團的，否則是專案的）
        replaced_section: 社團的聲部組蓋掉的專案的值；兩邊相同或社團沒有時為空字串
        part: 用譜庫的時配到的分譜；只在群組的聲部為會移到未分組的分譜；沒有時為 None
        present: 用譜庫的時本專案有沒有這個聲部；只在群組的聲部為 False
    """
    catalog_voice: str
    group_voice: str
    catalog_headcount: Optional[Headcount]
    group_headcount: Optional[Headcount]
    section: str
    replaced_section: str
    part: Optional[FileInfo]
    present: bool

    @property
    def difference(self) -> MergeDifference:
        """這一列兩邊的差異"""
        if self.catalog_headcount is None:
            return MergeDifference.GROUP_ONLY
        if self.group_headcount is None:
            return MergeDifference.CATALOG_ONLY
        return MergeDifference.SAME if self.catalog_headcount == self.group_headcount else MergeDifference.HEADCOUNT


class LinkResult(NamedTuple):
    """群組連到譜庫的結果（Project.link_group）

    Attributes:
        needs_merge: 群組與譜庫那處都已有編制，要由合併差異表決定，群組沒有連上、沒有改動
        guessed: 分譜檔名認不出聲部、依位置猜的聲部，依樂器表順序
        moved: 譜庫的聲部不夠配、移到未分組的分譜，依原順序
    """
    needs_merge: bool = False
    guessed: List[str] = []
    moved: List[FileInfo] = []


@dataclass
class Group:
    """群組：一首曲目，或其中一個樂章，及其分譜與總譜

    樂器表（instruments）與逐列對應的「本專案有」（present）、人數（headcounts）是聲部的有序清單；
    分譜依序配給本專案有的聲部，第 N 份分譜配第 N 個本專案有的聲部（pairing），本專案沒有的聲部配對時跳過。
    """
    EDITABLE_FIELDS: ClassVar[FrozenSet[str]] = frozenset({
        "name", "piece_name", "movement_number", "movement_name", "composer", "genre",
        "score_label", "use_small_template", "small_template",
    })

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    files: List[FileInfo] = field(default_factory=list)
    instruments: List[str] = field(default_factory=list)
    present: List[bool] = field(default_factory=list)
    headcounts: List[Headcount] = field(default_factory=list)
    duration_seconds: Optional[int] = None
    selected_instruments: List[int] = field(default_factory=list)
    piece_name: str = ""
    movement_number: str = ""
    movement_name: str = ""
    composer: str = ""
    genre: str = ""
    score_file: Optional["FileInfo"] = None
    score_label: str = ""
    use_small_template: bool = False
    small_template: str = ""
    catalog_link: Optional[CatalogLink] = None
    huaxia_link: Optional[HuaXiaLink] = None

    def __post_init__(self):
        self.present = _present_flags(self.present, len(self.instruments))
        self.headcounts = _headcount_rows(self.headcounts, len(self.instruments))

    def to_data(self) -> Dict[str, Any]:
        """專案檔中存的內容；串列一律複製，之後改動群組不會改到這份內容"""
        return {
            "id": self.id,
            "name": self.name,
            "files": [f.to_data() for f in self.files],
            "score_file": self.score_file.to_data() if self.score_file else None,
            "instruments": list(self.instruments),
            "present": list(self.present),
            "headcounts": [h.to_data() for h in self.headcounts],
            "duration_seconds": self.duration_seconds,
            "score_label": self.score_label,
            "selected_instruments": list(self.selected_instruments),
            "piece_name": self.piece_name,
            "movement_number": self.movement_number,
            "movement_name": self.movement_name,
            "composer": self.composer,
            "genre": self.genre,
            "use_small_template": self.use_small_template,
            "small_template": self.small_template,
            "catalog_link": self.catalog_link.to_data() if self.catalog_link else None,
            "huaxia_link": self.huaxia_link.to_data() if self.huaxia_link else None,
        }

    @classmethod
    def from_data(cls, data: Dict[str, Any]) -> "Group":
        """由專案檔中的內容還原（不做舊格式遷移，遷移由 Project.from_data 負責）"""
        score_data = data.get("score_file")
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            files=[FileInfo.from_data(f) for f in data.get("files", [])],
            score_file=FileInfo.from_data(score_data) if score_data else None,
            instruments=data.get("instruments", []),
            present=data.get("present", []),
            headcounts=[Headcount.from_data(h) for h in data.get("headcounts", [])],
            duration_seconds=data.get("duration_seconds") or None,
            score_label=data.get("score_label", ""),
            selected_instruments=data.get("selected_instruments", []),
            piece_name=data.get("piece_name", ""),
            movement_number=data.get("movement_number", ""),
            movement_name=data.get("movement_name", ""),
            composer=data.get("composer", ""),
            genre=data.get("genre", ""),
            use_small_template=data.get("use_small_template", False),
            small_template=data.get("small_template", ""),
            catalog_link=CatalogLink.from_data(data["catalog_link"]) if data.get("catalog_link") else None,
            huaxia_link=HuaXiaLink.from_data(data["huaxia_link"]) if data.get("huaxia_link") else None,
        )

    def update(self, **fields: Any) -> None:
        """修改群組的文字與小模板欄位（EDITABLE_FIELDS）；不發通知，專案裡的群組經由 Project.update_group 修改

        Args:
            fields: 欄位名稱與新值

        Raises:
            ValueError: 含有不能以此方式修改的欄位（檔案、總譜、樂器表各有專用操作）
        """
        unknown = set(fields) - Group.EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"不能以 update 修改的群組欄位：{sorted(unknown)}")
        for name, value in fields.items():
            setattr(self, name, value)

    def assign_instruments(
        self, instruments: Iterable[str], present: Optional[Iterable[bool]] = None,
        headcounts: Optional[Iterable[Headcount]] = None,
    ) -> None:
        """換成新的樂器表（聲部清單）與逐列的「本專案有」、人數；不發通知，由專案的操作呼叫

        沒給「本專案有」時全都有；沒給人數時，與原樂器表同一聲部的列沿用原本的人數，其餘未填。
        舊欄位 selected_instruments 跟著維持全選。
        """
        instruments = list(instruments)
        if headcounts is None:
            headcounts = _or_unfilled(match_by_voice(zip(self.instruments, self.headcounts), instruments))
        self.instruments = instruments
        self.present = _present_flags(list(present) if present is not None else [], len(self.instruments))
        self.headcounts = _headcount_rows(list(headcounts), len(self.instruments))
        self.selected_instruments = list(range(len(self.instruments)))

    def pairing(self) -> List[VoicePart]:
        """本專案有的聲部（依樂器表順序）與各自配到的分譜：第 N 份分譜配第 N 個"""
        voices = [(i, name) for i, name in enumerate(self.instruments) if self.present[i]]
        return [
            VoicePart(i, name, self.files[n] if n < len(self.files) else None) for n, (i, name) in enumerate(voices)
        ]

    def extra_files(self) -> List[FileInfo]:
        """多於本專案有的聲部數、沒有配到聲部的分譜（依原順序）"""
        return self.files[sum(self.present):]


@dataclass
class UndoMapping:
    """搬移對照：檔案從 original 搬到 renamed"""
    original: str
    renamed: str


@dataclass
class UndoRecord:
    """復原紀錄（復原與重做堆疊裡的一筆）

    Attributes:
        id: 紀錄的唯一識別，在復原與重做堆疊之間轉移時不變；舊版紀錄沒有，載入時以檔名代替
        timestamp: 建立時間（只供人閱讀，不作為鍵）
        description: 確認復原、重做時顯示的描述（建立時依當時的介面語言寫入）
        operation_type: 操作種類；殘留紀錄標示的是留下它的操作（重新命名、復原或重做）
        residual: 是否為殘留紀錄（操作中途失敗、記下搬不回原位的檔案）
        mappings: 整批搬移類的搬移對照（原位置到新位置）
        created_directories: 這次操作新建的目錄
        created_files: 這次操作產生的檔案（分割的分譜、旋轉另存的檔）
        backup_path: 旋轉覆蓋原檔前的備份
        original_path: 旋轉的來源檔；分割的合併譜
        workspace_meta: 來源位於工作區的項目，其子資料夾到 meta.json 內容的快照；
            復原時子資料夾已被清理掃描刪掉的話，用它把 meta 寫回
        replaced_files: 分割時被取代的檔案移到資源回收桶時所在的位置（復原不找回）
        placement: 專案怎麼安置分割的結果（復原時退回分割前的專案）；舊版紀錄沒有時為 None
    """
    id: str = ""
    timestamp: str = ""
    description: str = ""
    operation_type: OperationKind = OperationKind.RENAME
    residual: bool = False
    mappings: List[UndoMapping] = field(default_factory=list)
    created_directories: List[str] = field(default_factory=list)
    created_files: List[str] = field(default_factory=list)
    backup_path: str = ""
    original_path: str = ""
    workspace_meta: Dict[str, Dict] = field(default_factory=dict)
    replaced_files: List[str] = field(default_factory=list)
    placement: Optional["SplitPlacement"] = None

    def to_data(self) -> Dict[str, Any]:
        """紀錄檔中存的內容"""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "description": self.description,
            "operation_type": self.operation_type.value,
            "residual": self.residual,
            "mappings": [{"original": m.original, "renamed": m.renamed} for m in self.mappings],
            "created_directories": list(self.created_directories),
            "created_files": list(self.created_files),
            "backup_path": self.backup_path,
            "original_path": self.original_path,
            "workspace_meta": dict(self.workspace_meta),
            "replaced_files": list(self.replaced_files),
            "placement": self.placement.to_data() if self.placement else None,
        }

    @classmethod
    def from_data(cls, data: Dict[str, Any], fallback_id: str) -> "UndoRecord":
        """由紀錄檔中的內容還原；舊版紀錄缺少的欄位補預設值

        Args:
            data: 紀錄檔中的內容
            fallback_id: 舊版紀錄沒有 id 時使用的識別

        Raises:
            ValueError: 操作種類不認得，或缺少必要欄位
        """
        try:
            record = cls(
                id=data.get("id") or fallback_id,
                timestamp=data.get("timestamp", ""),
                description=data.get("description", ""),
                operation_type=OperationKind(data.get("operation_type", OperationKind.RENAME.value)),
                residual=bool(data.get("residual", False)),
                mappings=[UndoMapping(m["original"], m["renamed"]) for m in data.get("mappings", [])],
                created_directories=data.get("created_directories", []),
                created_files=data.get("created_files", []),
                backup_path=data.get("backup_path", ""),
                original_path=data.get("original_path", ""),
                workspace_meta=data.get("workspace_meta", {}),
                replaced_files=data.get("replaced_files", []),
                placement=SplitPlacement.from_data(data["placement"]) if data.get("placement") else None,
            )
        except (KeyError, TypeError) as e:
            raise ValueError(str(e)) from e
        if (record.operation_type == OperationKind.ROTATE and record.original_path
                and not record.backup_path and not record.created_files):
            # 舊版旋轉另存的紀錄只把另存出的檔記在 original_path
            record.created_files = [record.original_path]
        return record


@dataclass
class WorkspaceEntry:
    """工作區子資料夾的摘要，供清理對話框顯示"""
    folder: str
    source_name: str
    source_path: str
    project_path: str
    file_count: int
    total_bytes: int
    modified_at: float
    status: WorkspaceStatus


@dataclass
class WorkspaceOwner:
    """工作區子資料夾所屬的另一個專案（供重新分割提示）"""
    project_path: str
    exists: bool


@dataclass
class WorkspaceScan:
    """清理前的掃描結果"""
    entries: List[WorkspaceEntry] = field(default_factory=list)
    missing_projects: List[str] = field(default_factory=list)
    unreadable_projects: List[str] = field(default_factory=list)


@dataclass
class RenameEntry:
    """重新命名計畫項目

    Attributes:
        original_path: 來源
        new_path: 目標
        group_id: 所屬群組
        output_directory: 專案的輸出位置；空字串表示沒指定（放在來源檔所在的資料夾）
    """
    original_path: str
    new_path: str
    group_id: Optional[str] = None
    output_directory: str = ""

    def output_location(self) -> str:
        """這一項實際的輸出位置：目標必須在它之內；沒指定輸出位置時為來源檔所在的資料夾"""
        return self.output_directory or os.path.dirname(self.original_path)


@dataclass
class SplitEntry:
    """分割計畫項目：一個區段要輸出的頁面、顯示名稱與輸出路徑"""
    pages: List[int]
    display_name: str
    output_path: str


@dataclass
class FilePosition:
    """檔案引用在專案裡的位置

    Attributes:
        group_id: 所在群組的 id；未分組為空字串
        score: 是否為該群組的總譜
        index: 在該群組分譜清單（未分組時為未分組清單）中的位置，從 0 起算；總譜為 0
    """
    group_id: str = ""
    score: bool = False
    index: int = 0

    def to_data(self) -> Dict[str, Any]:
        """紀錄檔中存的內容"""
        return {"group_id": self.group_id, "score": self.score, "index": self.index}

    @classmethod
    def from_data(cls, data: Dict[str, Any]) -> "FilePosition":
        """由紀錄檔中的內容還原"""
        return cls(group_id=data["group_id"], score=bool(data["score"]), index=int(data["index"]))


@dataclass
class SplitPlacement:
    """Project.apply_split 怎麼把分割結果放進專案；復原分割時據以退回分割前的專案

    Attributes:
        group_id: 接手新分譜的群組
        created_group: 接手的群組是否為這次分割另建的（復原時移除）
        instruments: 接手的群組在分割前的樂器表；分割沒有改變它與「本專案有」時為 None
        present: 接手的群組在分割前逐列的「本專案有」（與 instruments 一起記）；舊版紀錄沒有時為 None（全都有）
        headcounts: 接手的群組在分割前逐列的人數（與 instruments 一起記）；舊版紀錄沒有時為 None（同一聲部沿用分割後的人數）
        piece_name: 接手的群組在分割前的曲名（分割可能自動偵測填入）；分割沒有改變它時為 None
        source: 合併譜在分割前的位置；專案沒有引用合併譜時為 None
    """
    group_id: str
    created_group: bool
    instruments: Optional[List[str]] = None
    piece_name: Optional[str] = None
    source: Optional[FilePosition] = None
    present: Optional[List[bool]] = None
    headcounts: Optional[List[Headcount]] = None

    def to_data(self) -> Dict[str, Any]:
        """紀錄檔中存的內容"""
        return {
            "group_id": self.group_id,
            "created_group": self.created_group,
            "instruments": list(self.instruments) if self.instruments is not None else None,
            "piece_name": self.piece_name,
            "source": self.source.to_data() if self.source else None,
            "present": list(self.present) if self.present is not None else None,
            "headcounts": [h.to_data() for h in self.headcounts] if self.headcounts is not None else None,
        }

    @classmethod
    def from_data(cls, data: Dict[str, Any]) -> "SplitPlacement":
        """由紀錄檔中的內容還原"""
        source = data.get("source")
        headcounts = data.get("headcounts")
        return cls(
            group_id=data["group_id"],
            created_group=bool(data["created_group"]),
            instruments=data.get("instruments"),
            piece_name=data.get("piece_name"),
            source=FilePosition.from_data(source) if source else None,
            present=data.get("present"),
            headcounts=[Headcount.from_data(h) for h in headcounts] if headcounts is not None else None,
        )


@dataclass
class SplitRecord:
    """一次分割：交給搬移歷程記成復原紀錄，復原時再交回專案退回（Project.revert_split）

    Attributes:
        source_path: 合併譜；舊版紀錄沒有時為空字串
        created_files: 產生的分譜
        created_directories: 這次新建的目錄
        replaced_files: 被取代的檔案（上次的分譜，或指定資料夾內的同名檔案）移到資源回收桶時所在的位置；復原不找回
        placement: 專案怎麼安置這次分割的結果；舊版紀錄沒有時為 None
    """
    source_path: str
    created_files: List[str]
    created_directories: List[str]
    replaced_files: List[str]
    placement: Optional[SplitPlacement] = None


@dataclass
class SplitResult:
    """一次分割的結果，交給專案套用（Project.apply_split）

    Attributes:
        source_path: 合併譜
        parts: 產生的分譜，依分段順序
        voices: 各分譜的聲部名稱（使用者輸入的分段名稱）
        replaced: 被取代的檔案（原本的路徑）；專案裡指向它們的引用要移除
        trashed: 被取代的檔案移到資源回收桶時所在的位置（依 replaced 的順序，移不進去的不列）；
            位置會被新分譜佔用的檔案先挪到暫用子資料夾才移，其餘從原處移
        created_directories: 這次新建的目錄
    """
    source_path: str
    parts: List[FileInfo]
    voices: List[str]
    replaced: List[str]
    trashed: List[str] = field(default_factory=list)
    created_directories: List[str] = field(default_factory=list)

    def record(self, placement: Optional[SplitPlacement] = None) -> SplitRecord:
        """交給搬移歷程的分割紀錄

        Args:
            placement: Project.apply_split 回報的安置方式

        Returns:
            分割紀錄
        """
        return SplitRecord(
            source_path=self.source_path,
            created_files=[p.original_path for p in self.parts],
            created_directories=list(self.created_directories),
            replaced_files=list(self.trashed),
            placement=placement,
        )


@dataclass
class DriveRenameEntry:
    """Drive 重新命名計畫項目"""
    file_id: str = ""
    original_name: str = ""
    new_name: str = ""
    folder_id: str = ""
    group_name: str = ""


class FileRef(NamedTuple):
    """專案引用到的一個檔案

    Attributes:
        group: 所屬群組；未分組檔案為 None
        file: 檔案資訊
    """
    group: Optional[Group]
    file: FileInfo


@dataclass
class Project:
    """專案資料"""
    instruments: List[str] = field(default_factory=list)
    master_template: str = DEFAULT_MASTER_TEMPLATE
    groups: List[Group] = field(default_factory=list)
    ungrouped_files: List[FileInfo] = field(default_factory=list)
    use_subfolders: bool = False
    subfolder_template: str = DEFAULT_SUBFOLDER_TEMPLATE
    use_parts_subfolder: bool = False
    parts_subfolder_name: str = DEFAULT_PARTS_SUBFOLDER_NAME
    parts_output_mode: str = PartsOutputMode.ROOT
    output_directory: str = ""
    instrument_sections: Dict[str, str] = field(default_factory=dict)
    orchestra_type: OrchestraType = DEFAULT_ORCHESTRA_TYPE
    OUTPUT_SETTINGS: ClassVar[FrozenSet[str]] = frozenset({
        "output_directory", "use_subfolders", "subfolder_template", "parts_output_mode", "parts_subfolder_name",
    })
    # 由較新版本的程式建立的專案檔：唯讀開啟，不能存檔（不在專案檔內容裡）
    read_only: bool = field(default=False, init=False, repr=False, compare=False)
    _saved: Optional[Dict[str, Any]] = field(default=None, init=False, repr=False, compare=False)
    _listeners: List[Callable[[], None]] = field(default_factory=list, init=False, repr=False, compare=False)
    # 開啟專案時讀到、聲部清單與群組不同而還沒決定的譜庫那處（群組 ID → 那處；不在專案檔內容裡）
    _catalog_lists: Dict[str, "CatalogPlace"] = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self):
        self._saved = self.to_data()

    # --- 已變更通知 ---

    def subscribe(self, listener: Callable[[], None]) -> None:
        """訂閱「已變更」通知：每次編輯操作或存檔、開啟後呼叫 listener（不帶參數）"""
        self._listeners.append(listener)

    def _changed(self) -> None:
        """通知訂閱者專案內容或已存檔狀態可能變了"""
        for listener in list(self._listeners):
            listener()

    # --- 內容與未存檔 ---

    def to_data(self) -> Dict[str, Any]:
        """專案檔中存的內容（不含存檔格式的版本號）；串列與字典一律複製

        存檔寫出的就是這份內容，未存檔判定也拿它和快照比對，兩者不會分歧。
        """
        return {
            "instruments": list(self.instruments),
            "master_template": self.master_template,
            "use_subfolders": self.use_subfolders,
            "subfolder_template": self.subfolder_template,
            "use_parts_subfolder": self.use_parts_subfolder,
            "parts_subfolder_name": self.parts_subfolder_name,
            "parts_output_mode": self.parts_output_mode,
            "output_directory": self.output_directory,
            "instrument_sections": dict(self.instrument_sections),
            "orchestra_type": self.orchestra_type.value,
            "ungrouped_files": [f.to_data() for f in self.ungrouped_files],
            "groups": [g.to_data() for g in self.groups],
        }

    @classmethod
    def from_data(cls, data: Dict[str, Any], score_label: str) -> "Project":
        """由專案檔中的內容還原，完成舊格式遷移後以還原結果作為已存檔的快照

        群組的遷移只在舊版程式寫的專案檔（version 在 LEGACY_PROJECT_FILE_VERSIONS）做，
        新版存的檔裡留空的總譜標籤與樂器表是使用者清掉的，不補回。格式 3 以前的檔（含上述舊版）
        另把聲部組轉成以樂器為鍵的對照（沒存樂團類型時依聲部組判定），專案層級的人數展開到各群組。
        version 比 PROJECT_FILE_VERSION 新時唯讀。

        Args:
            data: 專案檔中的內容（含 version）
            score_label: 舊版專案檔裡總譜標籤留空的群組要補上的標籤（依目前介面語言）

        Returns:
            判為已存檔的專案
        """
        project = cls(
            instruments=data.get("instruments", []),
            master_template=data.get("master_template", ""),
            use_subfolders=data.get("use_subfolders", False),
            subfolder_template=data.get("subfolder_template", ""),
            use_parts_subfolder=data.get("use_parts_subfolder", False),
            parts_subfolder_name=data.get("parts_subfolder_name", DEFAULT_PARTS_SUBFOLDER_NAME),
            parts_output_mode=data.get("parts_output_mode", PartsOutputMode.ROOT),
            output_directory=data.get("output_directory", ""),
            instrument_sections=data.get("instrument_sections", {}),
            orchestra_type=_orchestra_type(data.get("orchestra_type")),
            ungrouped_files=[FileInfo.from_data(f) for f in data.get("ungrouped_files", [])],
            groups=[Group.from_data(g) for g in data.get("groups", [])],
        )
        if "parts_output_mode" not in data and project.use_parts_subfolder:
            project.parts_output_mode = PartsOutputMode.PARTS
        version = data.get("version")
        legacy = version in LEGACY_PROJECT_FILE_VERSIONS
        if legacy:
            for group in project.groups:
                project._migrate_group(group, score_label)
        if legacy or (isinstance(version, int) and version < INSTRUMENTATION_FILE_VERSION):
            project._migrate_sections(guess_type="orchestra_type" not in data)
            project._migrate_headcounts(data.get("instrument_headcounts") or {})
        project.read_only = isinstance(version, int) and version > PROJECT_FILE_VERSION
        project.mark_saved()
        return project

    def _migrate_sections(self, guess_type: bool) -> None:
        """格式 3 以前的聲部組遷移：判定樂團類型（guess_type 時），聲部組轉成以樂器為鍵的對照"""
        voices = [voice for group in self.groups for voice in group.instruments]
        if guess_type:
            self.orchestra_type = guess_orchestra_type(voices, self.instrument_sections)
        self.instrument_sections = migrate_sections(voices, self.instrument_sections, self.orchestra_type)

    def _migrate_headcounts(self, headcounts: Dict[str, Any]) -> None:
        """格式 3 以前的人數遷移：專案層級的人數 n 以同一聲部的規則展開到每個群組，成為最少＝最多＝n

        同一聲部在所有群組套用同一個值（舊對照裡有幾種寫法時取第一個有值的）；0 與沒有值為未填，
        樂器表裡已沒有的殘留鍵丟掉。
        """
        counts = [
            (voice, Headcount(count)) for voice, count in headcounts.items()
            if isinstance(count, int) and not isinstance(count, bool) and count > 0
        ]
        for group in self.groups:
            group.headcounts = _or_unfilled(match_by_voice(counts, group.instruments))

    def _migrate_group(self, group: Group, score_label: str) -> None:
        """舊格式群組的遷移：補上總譜標籤、承接專案層級的樂器表、把勾選的子集收成群組自己的樂器表"""
        if not group.score_label:
            group.score_label = score_label
        instruments = group.instruments or list(self.instruments)
        selected = group.selected_instruments
        if selected:
            instruments = [instruments[i] for i in selected if i < len(instruments)]
        group.assign_instruments(instruments)

    def is_modified(self) -> bool:
        """目前內容是否與上次存檔或開啟時的快照不同"""
        return self.to_data() != self._saved

    def mark_saved(self) -> None:
        """以目前內容作為已存檔的快照（存檔或開啟後呼叫）"""
        self._saved = self.to_data()
        self._changed()

    # --- 專案設定 ---

    def set_master_template(self, template: str) -> None:
        """設定通用大模板"""
        self.master_template = template
        self._changed()

    def set_output_settings(self, **settings: Any) -> None:
        """修改輸出設定（Project.OUTPUT_SETTINGS）；舊欄位 use_parts_subfolder 跟著分譜存放模式維持

        Raises:
            ValueError: 含有不屬於輸出設定的欄位
        """
        unknown = set(settings) - Project.OUTPUT_SETTINGS
        if unknown:
            raise ValueError(f"不是輸出設定的欄位：{sorted(unknown)}")
        for name, value in settings.items():
            setattr(self, name, value)
        self.use_parts_subfolder = self.parts_output_mode == PartsOutputMode.PARTS
        self._changed()

    def update_sections(self, sections: Dict[str, str]) -> None:
        """一次寫入多個聲部的聲部組，寫進以樂器為鍵的對照（見 set_section），沒提到的不變

        Args:
            sections: 聲部名稱到聲部組名稱
        """
        for voice, section in sections.items():
            put_section(self.instrument_sections, voice, section)
        self._changed()

    # --- 樂團類型與聲部組 ---

    def set_orchestra_type(self, orchestra_type: OrchestraType) -> None:
        """設定樂團類型；已存的聲部組不變，沒存的聲部改依新的樂團類型偵測"""
        self.orchestra_type = orchestra_type
        self._changed()

    def section_options(self) -> Tuple[str, ...]:
        """這個專案的樂團類型的聲部組選項，「其他」在最後"""
        return section_options(self.orchestra_type)

    def section_of(self, voice: str) -> str:
        """聲部的聲部組：它的樂器存了就用存的，否則依樂團類型偵測"""
        return section_for(voice, self.instrument_sections, self.orchestra_type)

    def same_instrument_voices(self, voice: str) -> List[str]:
        """專案各群組的樂器表裡與聲部同一樂器的其他編號（改這個聲部的聲部組時跟著改的）

        同一聲部只列第一次出現的寫法，依群組與樂器表順序；與聲部本身同一聲部的不列。
        """
        return distinct_voices(
            other for group in self.groups for other in group.instruments
            if same_instrument(other, voice) and not same_voice(other, voice)
        )

    def set_section(self, voice: str, section: str) -> List[str]:
        """改聲部所屬樂器的聲部組；對照以樂器為鍵，同一樂器的各個聲部（各群組都是）一起改

        Args:
            voice: 聲部名稱
            section: 聲部組名稱

        Returns:
            專案裡跟著改的其他編號（same_instrument_voices）
        """
        put_section(self.instrument_sections, voice, section)
        self._changed()
        return self.same_instrument_voices(voice)

    def convert_template_language(self, locale: str) -> None:
        """切換介面語言時，把大模板、子資料夾模板與各群組小模板的變數名稱換成該語言"""
        self.master_template = convert_template_language(self.master_template, locale)
        self.subfolder_template = convert_template_language(self.subfolder_template, locale)
        for group in self.groups:
            group.small_template = convert_template_language(group.small_template, locale)
        self._changed()

    # --- 群組與檔案 ---

    def add_group(self, name: str, score_label: str, files: Iterable[FileInfo] = ()) -> Group:
        """新增群組；帶入的檔案從原本所在處（通常是未分組）移入，並自動偵測總譜與曲名

        Args:
            name: 群組名稱
            score_label: 總譜標籤（依建立時的介面語言）
            files: 要放進群組的檔案

        Returns:
            新群組
        """
        group = Group(name=name, score_label=score_label)
        self.groups.append(group)
        self._move_into(group, files)
        self._changed()
        return group

    def add_groups(self, groups: Iterable[Group], score_label: str) -> None:
        """加入匯入資料夾建立的群組；總譜標籤留空的補上，並自動偵測總譜與曲名

        Args:
            groups: 匯入建立的群組（已帶有名稱與檔案）
            score_label: 總譜標籤（依建立時的介面語言）
        """
        for group in groups:
            if not group.score_label:
                group.score_label = score_label
            self.groups.append(group)
            self._detect_on_entry(group)
        self._changed()

    def add_files(self, files: Iterable[FileInfo], group: Optional[Group] = None) -> None:
        """加入新匯入的檔案；加進群組時自動偵測總譜與曲名

        Args:
            files: 新檔案
            group: 目標群組；None 表示加進未分組
        """
        if group is None:
            self.ungrouped_files.extend(files)
        else:
            group.files.extend(files)
            self._detect_on_entry(group)
        self._changed()

    def move_to_group(self, files: Iterable[FileInfo], group: Group) -> None:
        """把專案內的檔案（未分組或其他群組）搬進群組的分譜尾端，並自動偵測總譜與曲名"""
        self._move_into(group, files)
        self._changed()

    def apply_split(self, result: SplitResult, score_label: str) -> SplitPlacement:
        """套用分割結果：移除被取代的引用、把合併譜移出所在的群組，新分譜交給接手的群組

        接手的群組依序為：引用被取代檔案的群組（重新分割，不另建群組、不留下空群組）、
        引用合併譜的群組（不論它是分譜還是總譜）；都沒有時另建一個以合併譜檔名命名的群組。
        未分組裡的合併譜留在未分組。新分譜的聲部名稱成為接手群組的樂器表，並自動偵測總譜與曲名。

        Args:
            result: 分割模組的執行結果
            score_label: 另建群組時的總譜標籤（依建立時的介面語言）

        Returns:
            安置方式（接手的群組、合併譜原本的位置、被改掉的群組欄位）；連同分割紀錄交給搬移歷程，復原時退回
        """
        group = self._group_referencing(result.replaced) or self._group_referencing([result.source_path])
        self._detach_paths(result.replaced)
        source_position = self._position_of(result.source_path)
        for ref in self._refs_to([result.source_path]):
            if ref.group is not None:
                self._detach(ref)
        created = group is None
        if created:
            name = os.path.splitext(os.path.basename(result.source_path))[0]
            group = Group(name=name, score_label=score_label)
            self.groups.append(group)
        instruments, present, headcounts = list(group.instruments), list(group.present), list(group.headcounts)
        piece_name = group.piece_name
        group.files.extend(result.parts)
        group.assign_instruments(result.voices)
        self._detect_on_entry(group)
        self._changed()
        voices_changed = (instruments, present) != (group.instruments, group.present)
        return SplitPlacement(
            group_id=group.id, created_group=created,
            instruments=instruments if voices_changed else None,
            piece_name=piece_name if piece_name != group.piece_name else None,
            source=source_position,
            present=present if voices_changed else None,
            headcounts=headcounts if voices_changed else None,
        )

    def revert_split(self, record: SplitRecord) -> None:
        """退回一次分割（套用復原分割的結果）

        移除新分譜的引用；分割時另建的群組移除（之後才放進去的檔案移回未分組），
        接手的既有群組被分割改掉的樂器表（連同本專案有與人數）與曲名改回；合併譜已不在專案裡時放回原本的位置
        （同一個群組、同樣是分譜或總譜，分譜放回原本的順序；原本的群組已刪除就放回未分組）。
        被取代的檔案不找回。舊版紀錄沒有安置資訊，只移除新分譜的引用。

        Args:
            record: 搬移歷程復原的分割紀錄
        """
        self._detach_paths(record.created_files)
        placement = record.placement
        if placement is not None:
            group = self._group_by_id(placement.group_id)
            if group is not None and placement.created_group:
                self._remove_group(group)
            elif group is not None:
                if placement.instruments is not None:
                    group.assign_instruments(placement.instruments, placement.present, placement.headcounts)
                if placement.piece_name is not None:
                    group.piece_name = placement.piece_name
            if placement.source is not None and not self.references_any([record.source_path]):
                self._put_back(FileInfo(record.source_path, os.path.basename(record.source_path)), placement.source)
        self._changed()

    def update_group(self, group: Group, **fields: Any) -> None:
        """修改群組的文字與小模板欄位（規則見 Group.update）並發出已變更通知

        Args:
            group: 要修改的群組
            fields: 欄位名稱與新值

        Raises:
            ValueError: 含有不能以此方式修改的欄位（檔案、總譜、樂器表各有專用操作）
        """
        group.update(**fields)
        self._changed()

    def delete_group(self, group: Group) -> None:
        """刪除群組，總譜與分譜移回未分組（總譜在前）"""
        self._remove_group(group)
        self._changed()

    def _remove_group(self, group: Group) -> None:
        """移除群組，總譜與分譜移回未分組尾端（總譜在前；不發通知）"""
        self.groups = [g for g in self.groups if g is not group]
        if group.score_file:
            self.ungrouped_files.append(group.score_file)
        self.ungrouped_files.extend(group.files)

    def move_to_ungrouped(self, files: Iterable[FileInfo]) -> None:
        """把群組裡的檔案移回未分組的尾端"""
        files = list(files)
        self._detach_files(files)
        self.ungrouped_files.extend(files)
        self._changed()

    def set_score(self, group: Group, file: FileInfo) -> None:
        """把群組的一份分譜設為總譜，原本的總譜放回分譜尾端"""
        self._assign_score(group, file)
        self._changed()

    def clear_score(self, group: Group) -> None:
        """清除群組的總譜，該檔放回分譜的最前面"""
        if group.score_file:
            group.files.insert(0, group.score_file)
            group.score_file = None
            self._changed()

    def reorder_files(self, group: Group, files: List[FileInfo]) -> None:
        """依新順序排列群組的分譜

        Raises:
            ValueError: 新順序與群組現有的分譜不是同一批檔案
        """
        if sorted(map(id, files)) != sorted(map(id, group.files)):
            raise ValueError("新順序必須是群組現有的同一批分譜")
        group.files = list(files)
        self._changed()

    def set_instruments(self, group: Group, instruments: Iterable[str]) -> None:
        """設定群組的樂器表（聲部清單）；舊欄位 selected_instruments 跟著維持全選"""
        group.assign_instruments(instruments)
        self._changed()

    def add_instrument(self, group: Group, name: str) -> None:
        """在群組樂器表的最後加上一個聲部

        Raises:
            DuplicateVoiceError: 樂器表已有同一聲部（另一種寫法也算），樂器表不變
        """
        _refuse_duplicate_voice(group.instruments, name)
        group.assign_instruments(group.instruments + [name], group.present + [True], group.headcounts + [Headcount()])
        self._changed()

    def rename_instrument(self, group: Group, index: int, name: str) -> None:
        """把群組樂器表第 index 列改成 name，位置不變

        Raises:
            DuplicateVoiceError: 樂器表的其他列已有同一聲部，樂器表不變；改成自己的另一種寫法不算
            FiledVoicesError: 連到譜庫的群組裡這個聲部已有 Drive 檔案，改成別的聲部等於刪掉它，樂器表不變
        """
        others = group.instruments[:index] + group.instruments[index + 1:]
        _refuse_duplicate_voice(others, name)
        if not same_voice(group.instruments[index], name):
            _refuse_filed_voices(group, [index])
        instruments = list(group.instruments)
        instruments[index] = name
        group.assign_instruments(instruments, group.present, group.headcounts)
        self._changed()

    def remove_instruments(self, group: Group, rows: Iterable[int]) -> List[FileInfo]:
        """移除樂器表的幾列；其他聲部的分譜跟著各自的聲部，被移除的聲部的分譜移到未分組尾端

        Args:
            group: 群組
            rows: 要移除的列（從 0 起算）

        Returns:
            移到未分組的分譜，依原順序

        Raises:
            FiledVoicesError: 群組連到譜庫，要移除的聲部有的已有 Drive 檔案（等於從樂譜刪除），樂器表不變
        """
        removing = set(rows)
        _refuse_filed_voices(group, removing)
        current = _voice_rows(group)
        moved = [row.part for i, row in enumerate(current) if i in removing and row.part is not None]
        _place_rows(group, [row for i, row in enumerate(current) if i not in removing])
        self.ungrouped_files.extend(moved)
        self._changed()
        return moved

    @staticmethod
    def filed_voices(group: Group, rows: Iterable[int]) -> List[str]:
        """要移除的列裡，連到譜庫而已有 Drive 檔案、不能移除的聲部（移除前先查，remove_instruments 會擋下它們）"""
        return _filed_voices(group, rows)

    def set_headcount(self, group: Group, index: int, headcount: Headcount) -> None:
        """設定群組樂器表第 index 列的人數範圍（整理規則見 Headcount.normalized）

        Args:
            group: 群組
            index: 列（從 0 起算）
            headcount: 新的人數範圍
        """
        group.headcounts[index] = headcount.normalized()
        self._changed()

    def copy_instrumentation(self, group: Group, source: Group) -> List[str]:
        """從另一個群組複製編制：每個聲部以同一聲部的規則（voice_key）對到來源的一列，複製它的人數

        對應不到的聲部人數留空；來源有重名時取第一列。聲部組是專案層級、以樂器為鍵的對照，
        同一個專案的群組本來就共用，所以不必另外複製。

        Args:
            group: 要寫入的群組
            source: 複製來源的群組

        Returns:
            對應不到、人數已留空的聲部，依樂器表順序
        """
        matched = match_by_voice(zip(source.instruments, source.headcounts), group.instruments)
        group.headcounts = _or_unfilled(matched)
        self._changed()
        return [voice for voice, headcount in zip(group.instruments, matched) if headcount is None]

    def set_huaxia_link(self, group: Group, link: Optional[HuaXiaLink]) -> None:
        """設定群組對到的 HuaXia 曲目（發譜預覽的「曲目」步）；None 為還沒對到"""
        group.huaxia_link = link
        self._changed()

    def set_duration(self, group: Group, seconds: Optional[int]) -> None:
        """設定群組的時長（秒）；0 與 None 為未填"""
        group.duration_seconds = seconds or None
        self._changed()

    def set_present(self, group: Group, index: int, present: bool) -> List[FileInfo]:
        """設定樂器表第 index 列「本專案有」與否（只存在專案）

        取消勾選時，它的分譜移到未分組尾端，其他聲部的分譜不動。重新勾選的聲部沒有分譜，
        分譜依序配給本專案有的聲部，所以它配到下一份，後面的各往後配一個聲部。

        Args:
            group: 群組
            index: 列（從 0 起算）
            present: 本專案有沒有這份分譜

        Returns:
            移到未分組的分譜（取消勾選有分譜的聲部時一份，其餘為空）
        """
        if present:
            flags = list(group.present)
            flags[index] = True
            group.assign_instruments(group.instruments, flags, group.headcounts)
            self._changed()
            return []
        rows = _voice_rows(group)
        moved = [rows[index].part] if rows[index].part is not None else []
        rows[index] = rows[index]._replace(present=False, part=None)
        _place_rows(group, rows)
        self.ungrouped_files.extend(moved)
        self._changed()
        return moved

    def move_instruments(self, group: Group, rows: Iterable[int], target: int) -> None:
        """把樂器表的幾列（依原順序）移到第 target 列之前（target 為列數時移到最後），分譜跟著各自的聲部走

        沒有分譜的聲部移到有分譜的聲部之前時改為本專案沒有，後面的分譜才不會錯位（見 _place_rows）。

        Args:
            group: 群組
            rows: 要移動的列（從 0 起算）
            target: 移動前的列號，移到它之前
        """
        moving = sorted(set(rows))
        current = _voice_rows(group)
        rest = [row for i, row in enumerate(current) if i not in moving]
        at = target - sum(1 for i in moving if i < target)
        _place_rows(group, rest[:at] + [current[i] for i in moving] + rest[at:])
        self._changed()

    # --- 連到譜庫 ---

    def link_group(self, group: Group, place: "CatalogPlace", choice: Optional[LinkChoice] = None) -> LinkResult:
        """把群組連到譜庫的一處（版本的整首或一個樂章）：編制與時長以譜庫為定本，記下連線與那處的快照

        - 那處還沒有任何聲部：群組的編制留著，成為下次存檔要寫進去的改動（catalog_changes）
        - 群組還沒有樂器表：樂器表與人數換成譜庫的；分譜依檔名取出的聲部名稱以同一聲部的規則配給譜庫的聲部，
          認不出的依原順序填進剩下的聲部（回報為依位置猜的），聲部不夠配的分譜移到未分組；
          群組有分譜時，沒配到分譜的聲部為本專案沒有
        - 兩邊都有編制：沒給 choice 時不連，回報 needs_merge，由合併差異表（merge_rows）決定；
          用譜庫的（LinkChoice.CATALOG）照 apply_catalog_voices 依名稱配對，
          對不到的分譜移到未分組；用群組的（LinkChoice.GROUP）編制與配對不變，與快照的差異成為改動
        - 時長：譜庫有就用譜庫的，沒有時群組的成為改動
        - 聲部組：社團對照有的樂器以社團的為準寫進專案的對照；沒有的樂器沿用專案的值，成為改動

        Args:
            group: 要連線的群組
            place: 譜庫服務讀到的那一處（CatalogService.read_place）
            choice: 兩邊都有編制時選用哪一邊；省略時不連、回報 needs_merge

        Returns:
            連線結果

        Raises:
            DuplicateVoicesError: 樂器表有重名的列，群組不變
        """
        duplicates = duplicate_rows(group.instruments)
        if duplicates:
            raise DuplicateVoicesError(duplicates)
        if place.voices and group.instruments:
            if choice is None:
                return LinkResult(needs_merge=True)
            moved = _follow_voices_by_name(group, place.voices) if choice is LinkChoice.CATALOG else []
            result = LinkResult(moved=moved)
        else:
            result = _take_catalog_voices(group, place.voices) if place.voices else LinkResult()
        self.ungrouped_files.extend(result.moved)
        if place.duration_seconds:
            group.duration_seconds = place.duration_seconds
        club_sections = {}
        for voice in _distinct_instruments(group.instruments):
            club = stored_section(voice, place.sections)
            if club:
                put_section(self.instrument_sections, voice, club)
                club_sections[voice] = club
        group.catalog_link = CatalogLink(
            edition_id=place.edition_id,
            movement_id=place.movement_id,
            piece_title=place.piece_title,
            edition_label=place.edition_label,
            movement_name=place.movement_name,
            snapshot=CatalogSnapshot(list(place.voices), place.duration_seconds, club_sections, list(place.filed)),
            other_pieces=_other_pieces(group.instruments, place.other_pieces),
        )
        self._changed()
        return result

    def apply_catalog_voices(self, group: Group, voices: List[Tuple[str, Headcount]]) -> List[FileInfo]:
        """把群組的樂器表換成譜庫的聲部清單，分譜依聲部名稱帶著走（依名稱配對）

        每份分譜以它在樂器表的聲部名稱，依同一聲部的規則對到新的聲部；新的聲部用譜庫的寫法與人數。
        樂器表沒有的聲部在群組有分譜時為本專案沒有，後面的分譜才不會錯位；新清單沒有的聲部，它的分譜移到未分組尾端。
        原本就沒配到聲部的分譜不動。

        Args:
            group: 群組
            voices: 譜庫的聲部名稱與人數，依譜庫的順序

        Returns:
            移到未分組的分譜，依原樂器表的順序
        """
        moved = _follow_voices_by_name(group, voices)
        self.ungrouped_files.extend(moved)
        self._changed()
        return moved

    def merge_rows(self, group: Group, place: "CatalogPlace") -> List[MergeRow]:
        """群組與譜庫那處的合併差異表（不改群組）：依譜庫的順序，只在群組的聲部排在群組裡它前一個聲部之後

        每列的分譜照用譜庫的（link_group 的 LinkChoice.CATALOG）實際套用的結果；聲部組照連線的規則，社團對照有的
        樂器以社團的為準。

        Args:
            group: 要連線的群組
            place: 譜庫服務讀到的那一處

        Returns:
            每個聲部一列
        """
        current = _voice_rows(group)
        group_names = [row.name for row in current]
        catalog_names = [name for name, _ in place.voices]
        rows, _ = _rows_by_name(group, place.voices)
        after: Dict[int, List[MergeRow]] = {}
        last = -1
        for row in current:
            index = voice_index(catalog_names, row.name)
            if index is not None:
                last = index
                continue
            after.setdefault(last, []).append(MergeRow(
                "", row.name, None, row.headcount, *self._merged_section(row.name, place.sections), row.part, False,
            ))
        merged = list(after.get(-1, []))
        for index, (row, present) in enumerate(zip(rows, _settled_present(rows))):
            own = voice_index(group_names, row.name)
            merged.append(MergeRow(
                row.name, group_names[own] if own is not None else "", row.headcount,
                current[own].headcount if own is not None else None,
                *self._merged_section(row.name, place.sections), row.part, present,
            ))
            merged.extend(after.get(index, []))
        return merged

    def _merged_section(self, voice: str, club_sections: Dict[str, str]) -> Tuple[str, str]:
        """連線後聲部的聲部組，與社團的值蓋掉的專案的值（沒蓋掉時為空字串）"""
        club, own = stored_section(voice, club_sections), self.section_of(voice)
        if not club:
            return own, ""
        return club, own if own != club else ""

    def catalog_changes(self, group: Group) -> List[CatalogChange]:
        """群組待寫進譜庫的改動：目前的編制、時長與聲部組相對於連線快照的差異；沒連到譜庫時為空清單

        依序為聲部清單（同一聲部的另一種寫法不算改動）、各聲部的人數（快照沒有的聲部只在有填時列出）、
        時長、各樂器的聲部組（社團對照沒有的樂器以專案的值列出）。
        """
        link = group.catalog_link
        if link is None:
            return []
        snapshot = link.snapshot
        changes = []
        old_voices = tuple(name for name, _ in snapshot.voices)
        if [voice_key(v) for v in old_voices] != [voice_key(v) for v in group.instruments]:
            changes.append(CatalogChange(CatalogChangeKind.VOICES, "", old_voices, tuple(group.instruments)))
        olds = match_by_voice(snapshot.voices, group.instruments)
        for voice, old, new in zip(group.instruments, olds, group.headcounts):
            if (old is None and new.filled) or (old is not None and old != new):
                changes.append(CatalogChange(CatalogChangeKind.HEADCOUNT, voice, old, new))
        if snapshot.duration_seconds != group.duration_seconds:
            changes.append(CatalogChange(
                CatalogChangeKind.DURATION, "", snapshot.duration_seconds, group.duration_seconds,
            ))
        for voice in _distinct_instruments(group.instruments):
            old, new = stored_section(voice, snapshot.sections), self.section_of(voice)
            if old != new:
                changes.append(CatalogChange(CatalogChangeKind.SECTION, voice, old, new))
        return changes

    def unsynced_changes(self, group: Group) -> List[CatalogChange]:
        """群組未同步的值：上次存檔寫不進譜庫時，群組待寫入的改動（catalog_changes）；沒標未同步時為空清單

        改動的原值是譜庫最後一次讀到或寫進去的值。
        """
        link = group.catalog_link
        return self.catalog_changes(group) if link is not None and link.unsynced else []

    def unsynced_groups(self) -> List[Group]:
        """有未同步的值的群組（依群組順序）"""
        return [group for group in self.groups if self.unsynced_changes(group)]

    def mark_catalog_unsynced(self, group_ids: Iterable[str]) -> None:
        """存檔時寫不進譜庫：這些群組標成未同步（記在專案檔），下次寫回成功時清掉

        Args:
            group_ids: 寫不進去的群組 ID；沒連到譜庫的略過
        """
        for group_id in group_ids:
            group = self._group_by_id(group_id)
            if group is not None and group.catalog_link is not None:
                group.catalog_link = replace(group.catalog_link, unsynced=True)
        self._changed()

    def mark_catalog_synced(self, outcome: "SyncOutcome") -> None:
        """編制寫回譜庫之後：選「用譜庫的」的格子改成譜庫的值，快照改成寫完後的那處

        選「用譜庫的」的人數、時長、聲部組與順序照譜庫的值改群組（聲部組改的是專案的對照）；人數衝突而譜庫已刪掉那個聲部時，
        群組也拿掉它，分譜移到未分組。快照改成群組現在的編制、時長與聲部組，加上無法寫回、還留在譜庫的聲部，
        之後 catalog_changes 只剩無法寫回的改動；未同步的標記清掉。

        Args:
            outcome: 已寫入的結果（CatalogService.sync_instrumentation）
        """
        from core.catalog_sync import SyncField, Verdict, final_voices
        for place in outcome.places:
            group = self._group_by_id(place.write.group_id)
            if group is None or group.catalog_link is None:
                continue
            taken = [review for review in place.items if review.verdict is Verdict.CATALOG]
            for review in taken:
                item = review.item
                if item.field is SyncField.HEADCOUNT and review.theirs is not None:
                    index = voice_index(group.instruments, item.voice)
                    if index is not None:
                        group.headcounts[index] = review.theirs
                elif item.field is SyncField.DURATION:
                    group.duration_seconds = review.theirs
                elif item.field is SyncField.SECTION and review.theirs:
                    put_section(self.instrument_sections, item.voice, review.theirs)
            rows = {voice_key(row.name): row for row in _voice_rows(group)}
            kept = [rows[voice_key(name)] for name in final_voices(place.write, place.items) if voice_key(name) in rows]
            if [row.name for row in kept] != group.instruments:
                kept_keys = {voice_key(row.name) for row in kept}
                _place_rows(group, kept)
                self.ungrouped_files.extend(
                    row.part for key, row in rows.items() if key not in kept_keys and row.part is not None
                )
            unsynced_sections = {
                instrument_key(r.item.voice) for r in taken if r.item.field is SyncField.SECTION and not r.theirs
            }
            blocked = {voice_key(r.item.voice) for r in place.items if r.verdict is Verdict.BLOCKED}
            other_pieces = _other_pieces(group.instruments, place.other_pieces)
            link = replace(group.catalog_link, other_pieces=other_pieces, unsynced=False, confirm_voices=False)
            group.catalog_link = replace(link, snapshot=CatalogSnapshot(
                voices=_with_blocked(
                    list(zip(group.instruments, group.headcounts)), group.catalog_link.snapshot.voices, blocked,
                ),
                duration_seconds=group.duration_seconds,
                sections={
                    voice: self.section_of(voice) for voice in _distinct_instruments(group.instruments)
                    if instrument_key(voice) not in unsynced_sections
                },
                filed=list(place.filed),
            ))
        self._changed()

    def refresh_catalog(self, group: Group, place: "CatalogPlace") -> None:
        """開啟專案時從譜庫更新快照：人數與時長的變動直接套用，聲部清單變了時記下、等譜務決定

        - 人數與時長：譜庫的值跟快照不同時，群組還是快照的值就改成譜庫的，快照跟著改；群組另有還沒寫回的值時
          兩邊都不動，存檔時照衝突處理
        - 聲部清單（名稱、增刪、順序）：譜庫的跟群組的一樣時快照換成譜庫的；譜庫的變了、跟群組不同時不套用，
          記下讀到的清單（catalog_list_change），由 take_catalog_list 或 keep_project_list 決定，
          還沒決定時配對與命名照專案的清單
        - 曲名、版本與樂章的名稱、已有 Drive 檔案的聲部、各樂器被其他曲目用到幾首換成讀到的

        Args:
            group: 連到譜庫的群組；沒連線時不動
            place: 譜庫服務讀到的那一處（CatalogService.read_places）
        """
        link = group.catalog_link
        if link is None:
            return
        snapshot = link.snapshot
        catalog_keys = [voice_key(name) for name, _ in place.voices]
        if catalog_keys == [voice_key(name) for name in group.instruments]:
            olds = match_by_voice(snapshot.voices, [name for name, _ in place.voices])
            voices = [
                (name, self._refreshed_headcount(group, name, old or Headcount(), headcount))
                for (name, headcount), old in zip(place.voices, olds)
            ]
        else:
            theirs = match_by_voice(place.voices, [name for name, _ in snapshot.voices])
            voices = [
                (name, self._refreshed_headcount(group, name, old, new or old))
                for (name, old), new in zip(snapshot.voices, theirs)
            ]
        listed = catalog_keys in ([voice_key(name) for name, _ in voices], [voice_key(n) for n in group.instruments])
        if listed:
            self._catalog_lists.pop(group.id, None)
        else:
            self._catalog_lists[group.id] = place
        duration = snapshot.duration_seconds
        if place.duration_seconds != duration:
            if group.duration_seconds == duration:
                group.duration_seconds = place.duration_seconds
            if group.duration_seconds == place.duration_seconds:
                duration = place.duration_seconds
        group.catalog_link = replace(
            link, piece_title=place.piece_title, edition_label=place.edition_label,
            movement_name=place.movement_name, other_pieces=_other_pieces(group.instruments, place.other_pieces),
            snapshot=replace(snapshot, voices=voices, duration_seconds=duration, filed=list(place.filed)),
        )
        self._changed()

    @staticmethod
    def _refreshed_headcount(group: Group, voice: str, old: Headcount, new: Headcount) -> Headcount:
        """更新快照時一個聲部的人數：群組是快照的原值就改成譜庫的；回傳快照的新值（群組另有改動時留著原值）"""
        index = voice_index(group.instruments, voice)
        if index is None or new == old:
            return new
        if group.headcounts[index] == old:
            group.headcounts[index] = new
        return new if group.headcounts[index] == new else old

    def catalog_list_change(self, group: Group) -> Optional[CatalogListChange]:
        """開啟專案時讀到的譜庫聲部清單與群組的不同；沒有不同或已決定時為 None"""
        place = self._catalog_lists.get(group.id)
        if place is None or group.catalog_link is None:
            return None
        voices = place.voices
        names = [name for name, _ in voices]
        if [voice_key(name) for name in names] == [voice_key(name) for name in group.instruments]:
            return None
        return CatalogListChange(
            voices=list(voices),
            added=[name for name in names if voice_index(group.instruments, name) is None],
            removed=[name for name in group.instruments if voice_index(names, name) is None],
            moved=_moved_voices(group.instruments, names),
        )

    def take_catalog_list(self, group: Group) -> List[FileInfo]:
        """「套用譜庫的」：群組的聲部清單換成開啟時讀到的譜庫清單，分譜依名稱配對（apply_catalog_voices 的規則）

        群組原有的聲部保留群組的人數（還沒寫回的人數存檔時照衝突處理），譜庫新增的聲部用譜庫的人數；
        快照的聲部清單換成譜庫的。新增的樂器在社團對照有聲部組時照連線的規則以社團的為準。

        Returns:
            對不到聲部、移到未分組的分譜（依原樂器表的順序）；沒有待決定的清單時為空清單
        """
        place = self._catalog_lists.pop(group.id, None)
        if place is None:
            return []
        known = {instrument_key(voice) for voice in group.instruments}
        own = match_by_voice(zip(group.instruments, group.headcounts), [name for name, _ in place.voices])
        moved = _follow_voices_by_name(
            group, [(name, mine or headcount) for (name, headcount), mine in zip(place.voices, own)],
        )
        self.ungrouped_files.extend(moved)
        self._adopt_catalog_list(group, place.voices, confirm=False)
        sections = dict(group.catalog_link.snapshot.sections)
        for voice in _distinct_instruments(group.instruments):
            club = stored_section(voice, place.sections)
            if club and instrument_key(voice) not in known:
                put_section(self.instrument_sections, voice, club)
                sections[voice] = club
        group.catalog_link = replace(group.catalog_link, snapshot=replace(group.catalog_link.snapshot, sections=sections))
        self._changed()
        return moved

    def keep_project_list(self, group: Group) -> None:
        """「用專案的（存檔時寫回）」：快照的聲部清單換成開啟時讀到的譜庫清單，群組的清單因此成為待寫入的改動

        群組與配對不變；下次存檔先在衝突畫面確認再寫回（CatalogLink.confirm_voices），
        要刪掉的聲部在譜庫已有 Drive 檔案時無法寫回。沒有待決定的清單時不動。
        """
        place = self._catalog_lists.pop(group.id, None)
        if place is None:
            return
        self._adopt_catalog_list(group, place.voices, confirm=True)
        self._changed()

    @staticmethod
    def _adopt_catalog_list(group: Group, voices: List[Tuple[str, Headcount]], confirm: bool) -> None:
        """快照的聲部清單換成譜庫的；快照已有的聲部留著快照的人數（還沒寫回的人數存檔時才比對得出衝突）"""
        link = group.catalog_link
        olds = match_by_voice(link.snapshot.voices, [name for name, _ in voices])
        group.catalog_link = replace(link, confirm_voices=confirm, snapshot=replace(
            link.snapshot, voices=[(name, old or headcount) for (name, headcount), old in zip(voices, olds)],
        ))

    def record_deposit(self, records: Iterable["DepositRecord"]) -> None:
        """存入譜庫之後：每份存進去的檔記下存入指紋（譜庫裡那份的 MD5），快照記下這些聲部已有 Drive 檔案

        Args:
            records: 存入了的每份檔（DepositService.deposit 的結果）；本機路徑以 core.paths 判定同一性
        """
        records = list(records)
        fingerprints = {path_key(record.local_path): record.drive_file.md5 for record in records}
        for ref in self.file_refs():
            md5 = fingerprints.get(path_key(ref.file.original_path))
            if md5:
                ref.file.deposit_md5 = md5
        for group in self.groups:
            link = group.catalog_link
            voices = [record.voice for record in records if record.group_id == group.id and record.voice]
            if link is None or not voices:
                continue
            filed = list(link.snapshot.filed)
            filed += [voice for voice in voices if not any(same_voice(voice, known) for known in filed)]
            group.catalog_link = replace(link, snapshot=replace(link.snapshot, filed=filed))
        self._changed()

    def link_movements(self, groups: Iterable[Group], piece_name: str) -> None:
        """把群組連成同一首曲目的各樂章：曲名一致、樂章編號依序，沒有樂章名稱的以群組名稱補上"""
        for number, group in enumerate(groups, start=1):
            group.piece_name = piece_name
            group.movement_number = str(number)
            if not group.movement_name:
                group.movement_name = group.name
        self._changed()

    def guess_piece_name(self, group: Group) -> str:
        """使用者要求重猜曲名：由分譜檔名的共同部分偵測，猜得到就取代目前的曲名

        Returns:
            偵測到的曲名；猜不到時為空字串，曲名不變
        """
        detected = detect_piece_name([f.display_name for f in group.files])
        if detected:
            group.piece_name = detected
            self._changed()
        return detected

    def _move_into(self, group: Group, files: Iterable[FileInfo]) -> None:
        """把檔案從專案中原本的位置拿掉、放進群組分譜尾端，並自動偵測"""
        files = list(files)
        if not files:
            return
        self._detach_files(files)
        group.files.extend(files)
        self._detect_on_entry(group)

    @staticmethod
    def _assign_score(group: Group, file: FileInfo) -> None:
        """把檔案設為群組的總譜（原本在分譜清單裡就拿出來），原本的總譜放回分譜尾端"""
        if group.score_file:
            group.files.append(group.score_file)
        group.files = [f for f in group.files if f is not file]
        group.score_file = file

    def _detach_files(self, files: Iterable[FileInfo]) -> None:
        """把指定的檔案資訊（依物件本身判定）從所在的群組或未分組拿掉"""
        targets = {id(f) for f in files}
        for ref in self.file_refs():
            if id(ref.file) in targets:
                self._detach(ref)

    @staticmethod
    def _detect_on_entry(group: Group) -> None:
        """檔案進入群組後的自動偵測：沒有總譜才猜總譜、沒有曲名才猜曲名（由分譜檔名）"""
        if group.score_file is None:
            index = detect_score_index([f.display_name for f in group.files])
            if index is not None:
                group.score_file = group.files.pop(index)
        if not group.piece_name.strip():
            group.piece_name = detect_piece_name([f.display_name for f in group.files])

    # --- 檔案引用 ---

    def file_refs(self) -> List[FileRef]:
        """專案引用到的所有檔案（分譜、總譜、未分組的唯一走訪）

        Returns:
            依群組順序列出各群組的總譜與分譜（總譜在前），最後是未分組檔案
        """
        refs: List[FileRef] = []
        for group in self.groups:
            if group.score_file:
                refs.append(FileRef(group, group.score_file))
            refs.extend(FileRef(group, f) for f in group.files)
        refs.extend(FileRef(None, f) for f in self.ungrouped_files)
        return refs

    def all_file_paths(self) -> List[str]:
        """專案引用到的所有檔案路徑，順序同 file_refs()"""
        return [ref.file.original_path for ref in self.file_refs() if ref.file.original_path]

    def references_any(self, paths: Iterable[str]) -> bool:
        """專案是否引用其中任一路徑（分譜、總譜或未分組），路徑以 core.paths 判定同一性"""
        return bool(self._refs_to(paths))

    def _refs_to(self, paths: Iterable[str]) -> List[FileRef]:
        """指向其中任一路徑的檔案引用，順序同 file_refs()"""
        keys = {path_key(p) for p in paths}
        return [ref for ref in self.file_refs() if path_key(ref.file.original_path) in keys]

    def replace_paths(self, mappings: Iterable[UndoMapping]) -> None:
        """依搬移結果（原路徑 → 新路徑）更新檔案引用的路徑與顯示名稱，路徑以 core.paths 判定同一性

        Args:
            mappings: 搬移對照
        """
        moved = {path_key(m.original): m.renamed for m in mappings}
        for ref in self.file_refs():
            new_path = moved.get(path_key(ref.file.original_path))
            if new_path is not None:
                ref.file.original_path = new_path
                ref.file.display_name = os.path.basename(new_path)
        self._changed()

    def remove_paths(self, paths: Iterable[str]) -> None:
        """移除指向指定路徑的檔案引用（分譜、總譜、未分組），路徑以 core.paths 判定同一性

        Args:
            paths: 要移除的檔案路徑
        """
        self._detach_paths(paths)
        self._changed()

    def _detach_paths(self, paths: Iterable[str]) -> None:
        """拿掉指向指定路徑的檔案引用（不發通知）"""
        for ref in self._refs_to(paths):
            self._detach(ref)

    def _group_referencing(self, paths: Iterable[str]) -> Optional[Group]:
        """第一個（依 file_refs 順序）引用到其中任一路徑的群組；沒有時為 None"""
        return next((ref.group for ref in self._refs_to(paths) if ref.group is not None), None)

    def _group_by_id(self, group_id: str) -> Optional[Group]:
        """指定 id 的群組；已不在專案裡時為 None"""
        return next((g for g in self.groups if g.id == group_id), None)

    def _position_of(self, path: str) -> Optional[FilePosition]:
        """第一個（依 file_refs 順序）指向指定路徑的引用所在的位置；專案沒有引用它時為 None"""
        ref = next(iter(self._refs_to([path])), None)
        if ref is None:
            return None
        if ref.group is None:
            return FilePosition(index=_index_of(self.ungrouped_files, ref.file))
        if ref.group.score_file is ref.file:
            return FilePosition(group_id=ref.group.id, score=True)
        return FilePosition(group_id=ref.group.id, index=_index_of(ref.group.files, ref.file))

    def _put_back(self, file: FileInfo, position: FilePosition) -> None:
        """把檔案引用放回記下的位置；原本的群組已不在專案裡時放回未分組尾端"""
        group = self._group_by_id(position.group_id) if position.group_id else None
        if group is None:
            index = position.index if not position.group_id else len(self.ungrouped_files)
            self.ungrouped_files.insert(index, file)
        elif position.score:
            self._assign_score(group, file)
        else:
            group.files.insert(position.index, file)

    def _detach(self, ref: FileRef) -> None:
        """把一個檔案引用從所在的群組或未分組清單拿掉"""
        if ref.group is None:
            self.ungrouped_files = [f for f in self.ungrouped_files if f is not ref.file]
        elif ref.group.score_file is ref.file:
            ref.group.score_file = None
        else:
            ref.group.files = [f for f in ref.group.files if f is not ref.file]


class _VoiceRow(NamedTuple):
    """樂器表的一列連同它的人數與配到的分譜，重排樂器表時一起移動"""
    name: str
    present: bool
    headcount: Headcount
    part: Optional[FileInfo]


def _orchestra_type(value: Any) -> OrchestraType:
    """專案檔存的樂團類型；沒有這一欄或不認得（較新版本的值）時為預設的樂團類型"""
    try:
        return OrchestraType(value)
    except ValueError:
        return DEFAULT_ORCHESTRA_TYPE


def _present_flags(present: List[bool], count: int) -> List[bool]:
    """逐列的「本專案有」補齊或截到樂器表的列數；沒記的列為有"""
    return [bool(flag) for flag in present[:count]] + [True] * (count - len(present))


def _headcount_rows(headcounts: List[Headcount], count: int) -> List[Headcount]:
    """逐列的人數補齊或截到樂器表的列數；沒記的列為未填"""
    return list(headcounts[:count]) + [Headcount()] * (count - len(headcounts))


def _voice_rows(group: Group) -> List[_VoiceRow]:
    """群組樂器表的每一列與它配到的分譜"""
    parts = {pair.index: pair.file for pair in group.pairing()}
    return [
        _VoiceRow(name, group.present[i], group.headcounts[i], parts.get(i)) for i, name in enumerate(group.instruments)
    ]


def _place_rows(group: Group, rows: List[_VoiceRow]) -> None:
    """依序以這些列為群組的樂器表，分譜跟著各自的聲部，沒配到聲部的分譜留在最後

    沒有分譜、本專案有的聲部排在有分譜的聲部之前時改為本專案沒有：分譜依序配給本專案有的聲部，
    否則後面的分譜都會往前錯一位。
    """
    extras = group.extra_files()
    group.assign_instruments([row.name for row in rows], _settled_present(rows), [row.headcount for row in rows])
    group.files = [row.part for row in rows if row.part is not None] + extras


def _settled_present(rows: List[_VoiceRow]) -> List[bool]:
    """依序以這些列為樂器表時逐列的「本專案有」：沒有分譜的聲部排在有分譜的聲部之前時為本專案沒有（見 _place_rows）"""
    last_part = max((i for i, row in enumerate(rows) if row.part is not None), default=-1)
    return [row.present and (row.part is not None or i > last_part) for i, row in enumerate(rows)]


def _rows_by_name(group: Group, voices: List[Tuple[str, Headcount]]) -> Tuple[List[_VoiceRow], List[FileInfo]]:
    """換成這份聲部清單時的各列與對不到的分譜：分譜以它在樂器表的聲部名稱、依同一聲部的規則對到新的聲部

    新的聲部用它的寫法與人數；樂器表有同一聲部時沿用它的「本專案有」與分譜，沒有時群組有分譜就是本專案沒有。
    樂器表裡新清單沒有的聲部，它的分譜就是對不到的分譜（依樂器表順序）；沒配到聲部的分譜不動。
    """
    current = _voice_rows(group)
    names = [row.name for row in current]
    has_files = bool(group.files)
    rows = []
    taken = set()
    for name, headcount in voices:
        index = voice_index(names, name)
        if index is None:
            rows.append(_VoiceRow(name, not has_files, headcount, None))
        else:
            taken.add(index)
            rows.append(_VoiceRow(name, current[index].present, headcount, current[index].part))
    unmatched = [row.part for i, row in enumerate(current) if i not in taken and row.part is not None]
    return rows, unmatched


def _follow_voices_by_name(group: Group, voices: List[Tuple[str, Headcount]]) -> List[FileInfo]:
    """把樂器表換成這份聲部清單，分譜依聲部名稱帶著走，對不到的拿出群組（不發通知）

    Returns:
        對不到聲部、拿出群組的分譜（呼叫端放進未分組）
    """
    rows, unmatched = _rows_by_name(group, voices)
    _place_rows(group, rows)
    return unmatched


def _take_catalog_voices(group: Group, voices: List[Tuple[str, Headcount]]) -> LinkResult:
    """群組還沒有樂器表時換成譜庫的聲部：分譜依檔名配聲部，認不出的依位置猜，聲部不夠配的拿出群組（不發通知）

    Returns:
        依位置猜的聲部與拿出群組的分譜（呼叫端放進未分組）
    """
    names = [name for name, _ in voices]
    files = list(group.files)
    slots: Dict[int, FileInfo] = {}
    unmatched: List[FileInfo] = []
    file_voices = extract_instruments_from_filenames([f.display_name for f in files]) if files else []
    for file, file_voice in zip(files, file_voices):
        index = voice_index(names, file_voice)
        if index is not None and index not in slots:
            slots[index] = file
        else:
            unmatched.append(file)
    free = [i for i in range(len(names)) if i not in slots]
    for index, file in zip(free, unmatched):
        slots[index] = file
    guessed = sorted(free[:len(unmatched)])
    present = [i in slots for i in range(len(names))] if files else None
    group.assign_instruments(names, present, [headcount for _, headcount in voices])
    group.files = [slots[i] for i in sorted(slots)]
    return LinkResult(guessed=[names[i] for i in guessed], moved=unmatched[len(free):])


def _with_blocked(
    voices: List[Tuple[str, Headcount]], previous: List[Tuple[str, Headcount]], blocked: Set[VoiceKey],
) -> List[Tuple[str, Headcount]]:
    """群組的聲部，加上無法寫回、還留在譜庫的聲部（放回它在舊快照裡的位置）"""
    result = list(voices)
    for index, (name, headcount) in enumerate(previous):
        if voice_key(name) in blocked:
            result.insert(min(index, len(result)), (name, headcount))
    return result


def _moved_voices(names: List[str], reference: List[str]) -> List[str]:
    """names 裡在 reference 換了相對順序的聲部（依 names 的順序）：兩邊都有的聲部中，
    不在最長的共同順序裡的那些"""
    ref_keys = [voice_key(name) for name in reference]
    shared = [name for name in names if voice_key(name) in ref_keys]
    order = [ref_keys.index(voice_key(name)) for name in shared]
    # 最長遞增子序列（聲部數不多，用平方的做法）：留在原相對順序的聲部
    longest: List[List[int]] = []
    for i, position in enumerate(order):
        best = max((longest[j] for j in range(i) if order[j] < position), key=len, default=[])
        longest.append(best + [i])
    kept = set(max(longest, key=len, default=[]))
    return [name for i, name in enumerate(shared) if i not in kept]


def _other_pieces(voices: Iterable[str], counts: Dict[str, int]) -> Dict[str, int]:
    """群組各樂器（第一次出現的寫法）被譜庫其他曲目用到幾首：從譜庫讀到的全部樂器裡挑出群組有的"""
    by_key = {instrument_key(name): count for name, count in counts.items()}
    return {
        voice: by_key[instrument_key(voice)]
        for voice in _distinct_instruments(voices) if instrument_key(voice) in by_key
    }


def _filed_voices(group: Group, rows: Iterable[int]) -> List[str]:
    """群組連到譜庫時，rows 裡已有 Drive 檔案的聲部（依樂器表順序）；沒連到譜庫時為空清單"""
    if group.catalog_link is None:
        return []
    filed = {voice_key(name) for name in group.catalog_link.snapshot.filed}
    return [group.instruments[i] for i in sorted(set(rows)) if voice_key(group.instruments[i]) in filed]


def _refuse_filed_voices(group: Group, rows: Iterable[int]) -> None:
    """群組連到譜庫、rows 裡有已有 Drive 檔案的聲部時丟出 FiledVoicesError"""
    voices = _filed_voices(group, rows)
    if voices:
        raise FiledVoicesError(voices)


def _distinct_instruments(voices: Iterable[str]) -> List[str]:
    """每種樂器第一次出現的聲部寫法，依原順序（聲部組以樂器為單位）"""
    seen = set()
    result = []
    for voice in voices:
        if instrument_key(voice) not in seen:
            seen.add(instrument_key(voice))
            result.append(voice)
    return result


def _refuse_duplicate_voice(instruments: Iterable[str], name: str) -> None:
    """name 與 instruments 裡任一列是同一聲部時丟出 DuplicateVoiceError"""
    instruments = list(instruments)
    index = voice_index(instruments, name)
    if index is not None:
        raise DuplicateVoiceError(instruments[index])


def _or_unfilled(headcounts: Iterable[Optional[Headcount]]) -> List[Headcount]:
    """對不到的人數（None）換成未填"""
    return [Headcount() if headcount is None else headcount for headcount in headcounts]


def _index_of(files: List[FileInfo], file: FileInfo) -> int:
    """檔案資訊（依物件本身判定）在清單中的位置"""
    return next(i for i, f in enumerate(files) if f is file)
