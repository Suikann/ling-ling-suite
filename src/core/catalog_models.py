# -*- coding: utf-8 -*-
"""
譜庫資料模型

定義譜庫系統的核心資料結構：試算表各工作表的一列、Drive 上的檔案與資料夾內容，以及群組連線時讀到的一處編制。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.models import Headcount
from core.voice_names import voice_key


def _same_group(row_group_id: str, row_group: str, group_id: str, group_name: str) -> bool:
    """紀錄的一列是不是這個群組的：列有群組 ID 時比 ID（同名的群組分得開、改名後也認得），沒有時（舊的列）比群組名稱"""
    return row_group_id == group_id if row_group_id else row_group == group_name


def sort_order_key(value: str) -> Tuple[int, int]:
    """排序欄（sort_order）的排序鍵：數字依大小排，空白或不是數字的排在最後（排序穩定，同值時保留工作表的順序）"""
    value = value.strip()
    return (0, int(value)) if value.isdigit() else (1, 0)


@dataclass(frozen=True)
class Composer:
    """作曲家"""
    id: str = ""
    name: str = ""
    name_short: str = ""
    nationality: str = ""
    era: str = ""
    notes: str = ""


@dataclass(frozen=True)
class Piece:
    """曲目"""
    id: str = ""
    composer_id: str = ""
    title: str = ""
    title_short: str = ""
    opus: str = ""
    catalog_number: str = ""
    genre: str = ""
    instrumentation: str = ""
    duration_minutes: str = ""
    difficulty_level: str = ""
    notes: str = ""
    active_edition_id: str = ""


@dataclass(frozen=True)
class Edition:
    """版本"""
    id: str = ""
    piece_id: str = ""
    publisher: str = ""
    edition_label: str = ""
    catalog_number: str = ""
    year: str = ""
    drive_folder_id: str = ""
    notes: str = ""


@dataclass(frozen=True)
class Movement:
    """樂章"""
    id: str = ""
    piece_id: str = ""
    movement_number: str = ""
    name: str = ""
    tempo_marking: str = ""
    key_signature: str = ""
    sort_order: str = ""
    notes: str = ""


@dataclass(frozen=True)
class Part:
    """分譜"""
    id: str = ""
    edition_id: str = ""
    movement_id: str = ""
    instrument_name: str = ""
    sort_order: str = ""
    status: str = "unknown"
    drive_file_id: str = ""
    file_name: str = ""
    notes: str = ""
    headcount_min: str = ""
    headcount_max: str = ""
    headcount_open_ended: str = ""
    is_score: str = ""


@dataclass(frozen=True)
class InstrumentationLogEntry:
    """編制寫入紀錄的一列：一次寫回裡一格的改動（欄位的寫法見 core/catalog_sync.py 的 WriteSource、SyncField）"""
    id: str = ""
    time: str = ""
    operator: str = ""
    source: str = ""
    edition_id: str = ""
    movement_id: str = ""
    voice: str = ""
    field: str = ""
    old_value: str = ""
    new_value: str = ""
    group: str = ""
    group_id: str = ""

    def written_by(self, group_id: str, group_name: str) -> bool:
        """這一列是不是這個群組寫的：有群組 ID 時比 ID，舊的列（沒有群組 ID）比群組名稱"""
        return _same_group(self.group_id, self.group, group_id, group_name)


@dataclass(frozen=True)
class DepositLogEntry:
    """存入紀錄的一列：一次存入（或撤銷存入）裡的一份檔（欄位見 core/catalog_constants.py 的 SHEET_DEPOSIT_LOG）

    存入時 action 是 core/deposit.py 的 DepositAction 的值、reverts 空白；撤銷存入時 action 是
    core/deposit_revert.py 的 RevertAction 的值、reverts 是被撤銷的那次存入編號。
    """
    id: str = ""
    deposit_id: str = ""
    time: str = ""
    operator: str = ""
    edition_id: str = ""
    movement_id: str = ""
    group: str = ""
    voice: str = ""
    local_path: str = ""
    drive_file_id: str = ""
    action: str = ""
    revision_after: str = ""
    revision_before: str = ""
    reverts: str = ""
    group_id: str = ""

    @property
    def group_key(self) -> str:
        """認群組用的鍵：群組 ID；舊的列（沒有群組 ID）是群組名稱"""
        return self.group_id or self.group

    def written_by(self, group_id: str, group_name: str) -> bool:
        """這一列是不是這個群組存入的：有群組 ID 時比 ID，舊的列（沒有群組 ID）比群組名稱"""
        return _same_group(self.group_id, self.group, group_id, group_name)


@dataclass(frozen=True)
class Performance:
    """演出紀錄"""
    id: str = ""
    piece_id: str = ""
    edition_id: str = ""
    performance_date: str = ""
    venue: str = ""
    conductor: str = ""
    ensemble: str = ""
    status: str = "planned"
    program_order: str = ""
    notes: str = ""


@dataclass
class PieceDetail:
    """曲目完整資訊（含關聯資料）"""
    piece: Piece = field(default_factory=Piece)
    composer: Optional[Composer] = None
    editions: List[Edition] = field(default_factory=list)
    movements: List[Movement] = field(default_factory=list)
    parts: List[Part] = field(default_factory=list)
    performances: List[Performance] = field(default_factory=list)


@dataclass(frozen=True)
class DriveFile:
    """Drive 上的一個檔案或資料夾

    Attributes:
        id: 檔案 ID
        name: 名稱
        mime_type: mimeType
        parents: 所在的資料夾
        md5: 內容的 MD5（md5Checksum）；資料夾與 Google 文件為空字串
        head_revision: 目前內容的版本 ID（headRevisionId）；資料夾與 Google 文件為空字串
        trashed: 是否在垃圾桶裡
    """
    id: str
    name: str
    mime_type: str = ""
    parents: Tuple[str, ...] = ()
    md5: str = ""
    head_revision: str = ""
    trashed: bool = False


@dataclass(frozen=True)
class DriveRevision:
    """Drive 檔案留著的一個版本（舊內容約 30 天或最近 100 個版本之後會被 Drive 清掉，設了 keepForever 的除外）

    Attributes:
        id: 版本 ID
        md5: 這個版本內容的 MD5（md5Checksum）
        keep_forever: 是否設成永久保留
    """
    id: str
    md5: str = ""
    keep_forever: bool = False


@dataclass(frozen=True)
class DriveListing:
    """Drive 列出檔案的一頁結果；next_page_token 為空字串表示沒有下一頁"""
    files: List[DriveFile] = field(default_factory=list)
    next_page_token: str = ""


@dataclass(frozen=True)
class FolderContents:
    """一個 Drive 資料夾裡的子資料夾與 PDF（各依名稱排序）"""
    folders: List[DriveFile] = field(default_factory=list)
    pdfs: List[DriveFile] = field(default_factory=list)


@dataclass(frozen=True)
class EditionFiles:
    """版本資料夾裡的檔：直接放在資料夾裡的 PDF，以及每個子資料夾與其中的 PDF"""
    root_pdfs: List[DriveFile] = field(default_factory=list)
    subfolders: List[Tuple[DriveFile, List[DriveFile]]] = field(default_factory=list)


@dataclass(frozen=True)
class CatalogOverview:
    """譜庫視窗開啟時要的全部：曲目、作曲家、哪個資料夾屬於哪首曲目、譜庫根資料夾的內容，
    以及各樂器表用過的聲部名稱（依使用次數排序，給自動完成）"""
    pieces: List[Piece] = field(default_factory=list)
    composers: List[Composer] = field(default_factory=list)
    folder_pieces: Dict[str, Piece] = field(default_factory=dict)
    root: FolderContents = field(default_factory=FolderContents)
    voice_names: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class LinkTargets:
    """群組連線時可以挑的曲目、版本與樂章（連線對話框依序挑曲目 → 版本 → 樂章或整首），
    以及各樂器表用過的聲部名稱（依使用次數排序，給自動完成）"""
    pieces: List[Piece] = field(default_factory=list)
    editions: List[Edition] = field(default_factory=list)
    movements: List[Movement] = field(default_factory=list)
    voice_names: List[str] = field(default_factory=list)

    def editions_of(self, piece_id: str) -> List[Edition]:
        """曲目的版本，依工作表的順序"""
        return [e for e in self.editions if e.piece_id == piece_id]

    def movements_of(self, piece_id: str) -> List[Movement]:
        """曲目的樂章，依排序欄（不是數字的排在最後，同值時依工作表的順序）"""
        return sorted(
            (m for m in self.movements if m.piece_id == piece_id), key=lambda m: sort_order_key(m.sort_order),
        )


@dataclass(frozen=True)
class CatalogPlace:
    """譜庫裡的一處（一個版本的整首或其中一個樂章）：編制、時長，連同社團的聲部組對照

    Attributes:
        edition_id: 版本 ID
        movement_id: 樂章 ID；整首為空字串
        piece_title: 曲名
        edition_label: 版本的標示（沒有時為出版者，都沒有時為空字串）
        movement_name: 樂章名稱（沒有名稱時為樂章編號）；整首為空字串
        voices: 聲部名稱與人數，依分譜工作表的順序；不含總譜列
        duration_seconds: 時長（秒）；未填為 None
        sections: 社團的聲部組對照裡這個樂團類型的部分：樂器（第一次寫入的寫法）→ 聲部組
        filed: 已有 Drive 檔案的聲部
        other_pieces: 譜庫裡每種樂器被這首曲目以外的幾首曲目用到（樂器名稱 → 曲目數）
    """
    edition_id: str
    movement_id: str = ""
    piece_title: str = ""
    edition_label: str = ""
    movement_name: str = ""
    voices: List[Tuple[str, Headcount]] = field(default_factory=list)
    duration_seconds: Optional[int] = None
    sections: Dict[str, str] = field(default_factory=dict)
    filed: List[str] = field(default_factory=list)
    other_pieces: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class SectionMapEntry:
    """聲部組對照裡的一種樂器（譜庫視窗編輯全社團的對照時的一項）

    Attributes:
        instrument: 樂器：社團對照裡的寫法（第一次寫入的），還沒歸類的是分譜工作表第一次出現時去掉編號的名稱
        section: 聲部組；還沒歸類為空字串
        pieces: 譜庫裡用到這種樂器的曲目數（總譜列不算）
    """
    instrument: str
    section: str = ""
    pieces: int = 0


@dataclass(frozen=True)
class Registration:
    """分譜列登記的 Drive 檔案"""
    file_id: str
    file_name: str = ""


@dataclass(frozen=True)
class FolderPdf:
    """版本資料夾（含子資料夾）裡的一份 PDF，連同它在版本資料夾裡的路徑（子資料夾以「/」分隔）"""
    file: DriveFile
    path: str


@dataclass(frozen=True)
class EditionPage:
    """譜庫視窗的版本頁要的全部：一處的編制、各聲部與總譜列登記的檔，以及版本資料夾裡的檔（全部與沒登記的）

    Attributes:
        place: 這一處（版本的整首或一個樂章）的編制、時長與社團的聲部組對照
        registrations: 登記了 Drive 檔案的聲部（譜庫的寫法）與登記的檔；沒登記的聲部不在裡面
        score: 總譜列登記的檔；沒有總譜列或沒登記時為 None
        unregistered: 版本資料夾（含子資料夾）裡沒被任何分譜列登記的 PDF，依路徑排序
        pdfs: 版本資料夾（含子資料夾）裡全部的 PDF，不分登記與否，依路徑排序（一般下載從這裡挑）
    """
    place: CatalogPlace
    registrations: Dict[str, Registration] = field(default_factory=dict)
    score: Optional[Registration] = None
    unregistered: List[FolderPdf] = field(default_factory=list)
    pdfs: List[FolderPdf] = field(default_factory=list)

    def registration(self, voice: str) -> Optional[Registration]:
        """聲部登記的檔（同一聲部的規則比對寫法）；沒登記時為 None，狀態就是缺"""
        key = voice_key(voice)
        return next((r for name, r in self.registrations.items() if voice_key(name) == key), None)
