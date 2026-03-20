# -*- coding: utf-8 -*-
"""
譜庫資料模型

定義譜庫系統的核心資料結構。
"""
from dataclasses import dataclass, field
from typing import List, Optional


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
