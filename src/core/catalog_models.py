# -*- coding: utf-8 -*-
"""
譜庫資料模型

定義譜庫系統的核心資料結構：試算表各工作表的一列、Drive 上的檔案與資料夾內容，以及群組連線時讀到的一處編制。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core.models import Headcount


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
    """Drive 上的一個檔案或資料夾"""
    id: str
    name: str
    mime_type: str = ""
    parents: Tuple[str, ...] = ()
    md5: str = ""


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
    """群組連線時可以挑的曲目、版本與樂章（連線對話框依序挑曲目 → 版本 → 樂章或整首）"""
    pieces: List[Piece] = field(default_factory=list)
    editions: List[Edition] = field(default_factory=list)
    movements: List[Movement] = field(default_factory=list)

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
    """
    edition_id: str
    movement_id: str = ""
    piece_title: str = ""
    edition_label: str = ""
    movement_name: str = ""
    voices: List[Tuple[str, Headcount]] = field(default_factory=list)
    duration_seconds: Optional[int] = None
    sections: Dict[str, str] = field(default_factory=dict)
