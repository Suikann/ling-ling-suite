# -*- coding: utf-8 -*-
"""
譜庫常數定義

定義譜庫系統使用的常數、狀態列舉與試算表結構。
"""
from enum import Enum


class PartStatus(str, Enum):
    """分譜狀態"""
    AVAILABLE = "available"
    MISSING = "missing"
    DAMAGED = "damaged"
    UNKNOWN = "unknown"


class PerformanceStatus(str, Enum):
    """演出狀態"""
    PLANNED = "planned"
    REHEARSING = "rehearsing"
    PERFORMED = "performed"
    CANCELLED = "cancelled"


PART_STATUS_LABELS = {
    PartStatus.AVAILABLE: ("有", "Available"),
    PartStatus.MISSING: ("缺", "Missing"),
    PartStatus.DAMAGED: ("損壞", "Damaged"),
    PartStatus.UNKNOWN: ("未知", "Unknown"),
}

PERFORMANCE_STATUS_LABELS = {
    PerformanceStatus.PLANNED: ("已規劃", "Planned"),
    PerformanceStatus.REHEARSING: ("排練中", "Rehearsing"),
    PerformanceStatus.PERFORMED: ("已演出", "Performed"),
    PerformanceStatus.CANCELLED: ("已取消", "Cancelled"),
}

CATALOG_SPREADSHEET_NAME = "泠靈譜庫目錄"

SHEET_COMPOSERS = "作曲家"
SHEET_PIECES = "曲目"
SHEET_EDITIONS = "版本"
SHEET_MOVEMENTS = "樂章"
SHEET_PARTS = "分譜"
SHEET_PERFORMANCES = "演出紀錄"

SHEET_HEADERS = {
    SHEET_COMPOSERS: [
        "id", "name", "name_short", "nationality", "era", "notes",
    ],
    SHEET_PIECES: [
        "id", "composer_id", "title", "title_short", "opus",
        "catalog_number", "genre", "duration_minutes",
        "difficulty_level", "notes", "active_edition_id",
    ],
    SHEET_EDITIONS: [
        "id", "piece_id", "publisher", "edition_label",
        "catalog_number", "year", "drive_folder_id", "notes",
    ],
    SHEET_MOVEMENTS: [
        "id", "piece_id", "movement_number", "name",
        "tempo_marking", "key_signature", "sort_order", "notes",
    ],
    SHEET_PARTS: [
        "id", "edition_id", "movement_id", "instrument_name",
        "sort_order", "status", "drive_file_id", "file_name", "notes",
    ],
    SHEET_PERFORMANCES: [
        "id", "piece_id", "edition_id", "performance_date",
        "venue", "conductor", "ensemble", "status",
        "program_order", "notes",
    ],
}

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

TOKEN_FILE = "google_token.json"
CLIENT_SECRETS_FILE = "client_secret.json"
