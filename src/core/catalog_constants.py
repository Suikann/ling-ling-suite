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
# 發譜紀錄：每次發譜一個發譜編號、每個群組一列，只新增、不修改；先寫「開始發譜」，結束後補「發譜結果」。
# 動作寫 core/publish_log.py 的 PublishAction 的值，清單與物件的欄寫 JSON（PublishRecord.cells）
SHEET_PUBLISH_LOG = "發譜紀錄"

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
    # 新增的欄只加在後面
    SHEET_PUBLISH_LOG: [
        "id", "publish_id", "time", "operator", "action", "group_id", "group", "piece_id", "piece_title",
        "new_piece", "parts_created", "parts_updated", "files_uploaded", "scores_added", "scores_removed",
        "files_deleted", "events", "failure", "parts_deleted",
    ],
}

# 標題列的中文欄名（英文標題列就是 SHEET_HEADERS 的欄位鍵）；程式照欄位位置讀寫，不看標題文字。
# 不在這裡的工作表（社團設定）兩種語言的標題列相同。見 core/catalog_headers.py
SHEET_HEADER_NAMES_ZH = {
    SHEET_COMPOSERS: {
        "id": "編號", "name": "全名", "name_short": "簡稱", "nationality": "國籍", "era": "時期", "notes": "備註",
    },
    SHEET_PIECES: {
        "id": "編號", "composer_id": "作曲家編號", "title": "曲名", "title_short": "簡稱", "opus": "作品號",
        "catalog_number": "目錄號", "genre": "曲種", "instrumentation": "編制",
        "duration_minutes": "時長（分鐘）", "difficulty_level": "難度", "notes": "備註",
        "active_edition_id": "使用中的版本編號",
    },
    SHEET_EDITIONS: {
        "id": "編號", "piece_id": "曲目編號", "publisher": "出版社", "edition_label": "版本標籤",
        "catalog_number": "出版目錄號", "year": "年份", "drive_folder_id": "Drive 資料夾 ID", "notes": "備註",
    },
    SHEET_MOVEMENTS: {
        "id": "編號", "piece_id": "曲目編號", "movement_number": "樂章序號", "name": "樂章名稱",
        "tempo_marking": "速度標記", "key_signature": "調性", "sort_order": "排序", "notes": "備註",
    },
    SHEET_PARTS: {
        "id": "編號", "edition_id": "版本編號", "movement_id": "樂章編號", "instrument_name": "聲部",
        "sort_order": "排序", "status": "狀態", "drive_file_id": "Drive 檔案 ID", "file_name": "檔名", "notes": "備註",
        "headcount_min": "最少人數", "headcount_max": "最多人數", "headcount_open_ended": "人數以上",
        "is_score": "總譜",
    },
    SHEET_PERFORMANCES: {
        "id": "編號", "piece_id": "曲目編號", "edition_id": "版本編號", "performance_date": "演出日期",
        "venue": "場地", "conductor": "指揮", "ensemble": "演出團體", "status": "狀態",
        "program_order": "節目序", "notes": "備註",
    },
    SHEET_DURATIONS: {"id": "編號", "edition_id": "版本編號", "movement_id": "樂章編號", "seconds": "秒數"},
    SHEET_SECTIONS: {"id": "編號", "orchestra_type": "樂團類型", "instrument": "樂器", "section": "聲部組"},
    SHEET_INSTRUMENTATION_LOG: {
        "id": "編號", "time": "時間", "operator": "操作者", "source": "來源", "edition_id": "版本編號",
        "movement_id": "樂章編號", "voice": "聲部", "field": "項目", "old_value": "原值", "new_value": "新值",
        "group": "群組", "group_id": "群組編號",
    },
    SHEET_DEPOSIT_LOG: {
        "id": "編號", "deposit_id": "存入編號", "time": "時間", "operator": "操作者", "edition_id": "版本編號",
        "movement_id": "樂章編號", "group": "群組", "voice": "聲部", "local_path": "本機路徑",
        "drive_file_id": "Drive 檔案 ID", "action": "動作", "revision_after": "寫入後的 Drive 版本 ID",
        "revision_before": "寫入前的 Drive 版本 ID", "reverts": "撤銷的存入編號", "group_id": "群組編號",
        "state": "狀態",
    },
    SHEET_PUBLISH_LOG: {
        "id": "編號", "publish_id": "發譜編號", "time": "時間", "operator": "操作者", "action": "動作",
        "group_id": "群組編號", "group": "群組", "piece_id": "HuaXia 曲目編號", "piece_title": "曲名",
        "new_piece": "新建的曲目", "parts_created": "新建的聲部", "parts_updated": "覆寫的聲部",
        "files_uploaded": "上傳的檔案", "scores_added": "登記的樂譜", "scores_removed": "撤掉登記的樂譜",
        "files_deleted": "刪掉的檔案", "events": "掛上的公演", "failure": "失敗原因", "parts_deleted": "刪掉的聲部",
    },
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
# Drive 根資料夾（我的雲端硬碟）的別名：請求裡可以代替它的 ID，回應的 parents 卻是真正的 ID，所以不拿來存或比對
DRIVE_ROOT_ALIAS = "root"

GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CLIENT_SECRETS_FILE = "client_secret.json"
