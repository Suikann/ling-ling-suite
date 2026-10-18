# -*- coding: utf-8 -*-
"""
譜庫常數定義

定義譜庫系統使用的常數、狀態列舉與試算表結構。
"""
from enum import Enum


class PerformanceStatus(str, Enum):
    """演出狀態"""
    PLANNED = "planned"
    REHEARSING = "rehearsing"
    PERFORMED = "performed"
    CANCELLED = "cancelled"


PERFORMANCE_STATUS_LABELS = {
    PerformanceStatus.PLANNED: ("已規劃", "Planned"),
    PerformanceStatus.REHEARSING: ("排練中", "Rehearsing"),
    PerformanceStatus.PERFORMED: ("已演出", "Performed"),
    PerformanceStatus.CANCELLED: ("已取消", "Cancelled"),
}

SHEET_COMPOSERS = "作曲家"
SHEET_PIECES = "曲目"
SHEET_EDITIONS = "版本"
SHEET_MOVEMENTS = "樂章"
SHEET_PARTS = "分譜"
SHEET_PERFORMANCES = "演出紀錄"
# 社團設定：「鍵、值」兩欄，由譜務在試算表上手動填寫
SHEET_CLUB_SETTINGS = "社團設定"
# 時長：每個（版本、樂章或整首）一列，記秒數；樂章欄空白就是整首
SHEET_DURATIONS = "時長"
# 聲部組對照：（樂團類型、樂器）→ 聲部組，全社團共用；樂團類型寫 OrchestraType 的值
SHEET_SECTIONS = "聲部組對照"
# 編制寫入紀錄：每次寫回譜庫的每一格改動一列，只新增、不修改；來源與欄位寫 WriteSource、SyncField 的值
SHEET_INSTRUMENTATION_LOG = "編制寫入紀錄"
# 存入紀錄：一次存入一個存入編號、每份檔一列，只新增、不修改；動作寫 DepositAction 的值，
# 版本 ID 是 Drive 檔案寫入後與被蓋掉的版本（headRevisionId；沒寫內容時兩者相同）。
# 撤銷存入也是一個新的存入編號、每份撤掉的檔一列：動作寫 RevertAction 的值，reverts 是被撤銷的那次存入編號。
# 存入時先寫「要做」的列（狀態欄見 DEPOSIT_PENDING），做完再寫做了的列
SHEET_DEPOSIT_LOG = "存入紀錄"

SHEET_HEADERS = {
    SHEET_COMPOSERS: [
        "id", "name", "name_short", "nationality", "era", "notes",
    ],
    SHEET_PIECES: [
        "id", "composer_id", "title", "title_short", "opus",
        "catalog_number", "genre", "instrumentation",
        "duration_minutes", "difficulty_level", "notes",
        "active_edition_id",
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
        # 新增的欄只加在後面：人數（最少留空＝未填，最多留空＝等於最少）、是否「以上」、是否為總譜列
        "headcount_min", "headcount_max", "headcount_open_ended", "is_score",
    ],
    SHEET_PERFORMANCES: [
        "id", "piece_id", "edition_id", "performance_date",
        "venue", "conductor", "ensemble", "status",
        "program_order", "notes",
    ],
    SHEET_CLUB_SETTINGS: ["鍵", "值"],
    SHEET_DURATIONS: ["id", "edition_id", "movement_id", "seconds"],
    SHEET_SECTIONS: ["id", "orchestra_type", "instrument", "section"],
    SHEET_INSTRUMENTATION_LOG: [
        "id", "time", "operator", "source", "edition_id", "movement_id", "voice", "field", "old_value", "new_value",
        "group",
        # 新增的欄只加在後面：群組 ID（同名的群組分得開，群組改名後也認得出；舊的列空白，以群組名稱認）
        "group_id",
    ],
    SHEET_DEPOSIT_LOG: [
        "id", "deposit_id", "time", "operator", "edition_id", "movement_id", "group", "voice", "local_path",
        "drive_file_id", "action", "revision_after", "revision_before",
        # 新增的欄只加在後面：撤銷存入的列記被撤銷的存入編號
        "reverts",
        # 群組 ID（同名的群組分得開，群組改名後也認得出；舊的列空白，以群組名稱認）
        "group_id",
        # 存入前先寫的「要做」列是 DEPOSIT_PENDING；做完寫的列（與舊的列）空白
        "state",
    ],
}

# 存入紀錄的狀態欄：動 Drive 之前先寫一列「要做」，做完再另寫一列（只新增、不修改）。
# 只有「要做」、沒有做完那列的檔，是中途停下或登記寫不進去的：撤銷時照 Drive 現況判斷那次做到了沒有
DEPOSIT_PENDING = "pending"

# 是非欄（是否「以上」、是否為總譜列）寫入時的「是」；讀取時這些寫法都算是，其餘（含空白）算否
CATALOG_TRUE = "TRUE"
CATALOG_TRUE_VALUES = frozenset({"TRUE", "1", "Y", "YES", "是"})


class PublishingPlatform(str, Enum):
    """社團用的發譜平台；值是記在偏好設定裡的寫法"""
    HUAXIA = "huaxia"


# 社團設定裡記發譜平台的鍵，以及值的寫法對應的平台（其他寫法都當成沒有）
CLUB_SETTING_PUBLISHING_PLATFORM = "發譜平台"
PUBLISHING_PLATFORM_NAMES = {
    "華夏國樂社": PublishingPlatform.HUAXIA,
}

# Drive 的資料夾與 PDF 的 mimeType
DRIVE_FOLDER_MIME = "application/vnd.google-apps.folder"
DRIVE_PDF_MIME = "application/pdf"
# Google 試算表在 Drive 上的 mimeType
DRIVE_SPREADSHEET_MIME = "application/vnd.google-apps.spreadsheet"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CLIENT_SECRETS_FILE = "client_secret.json"
