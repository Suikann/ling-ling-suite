# -*- coding: utf-8 -*-
"""
常數定義

定義模板變數、預設值與應用程式路徑。
"""
import os
from dataclasses import dataclass
from enum import Enum
from typing import List, Tuple

APP_NAME = "LingLingSuite"
APP_DISPLAY_NAME = "泠靈小工具"
APP_VERSION = "1.1.0-alpha"


def _get_user_data_dir() -> str:
    """取得使用者資料目錄

    Windows 使用 %APPDATA%，其他平台遵循 XDG 規範（$XDG_CONFIG_HOME 或 ~/.config）。

    Returns:
        使用者資料目錄的絕對路徑
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, APP_NAME)


APPDATA_DIR = _get_user_data_dir()
UNDO_DIR = os.path.join(APPDATA_DIR, "undo")
REDO_DIR = os.path.join(APPDATA_DIR, "redo")
BACKUP_DIR = os.path.join(APPDATA_DIR, "backups")
WORKSPACE_DIR = os.path.join(APPDATA_DIR, "workspace")
WORKSPACE_META_FILE = "meta.json"
WORKSPACE_FOLDER_HASH_LENGTH = 8

# 原子寫入：暫名副檔名，以及 os.replace() 遇 PermissionError（檔案被同步客戶端／防毒短暫鎖住）時的重試次數與間隔（秒）
ATOMIC_WRITE_TEMP_SUFFIX = ".tmp"
ATOMIC_WRITE_RETRIES = 5
ATOMIC_WRITE_RETRY_INTERVAL = 0.2

# 兩階段搬移：來源同時是其他項目目標的檔案，第一階段先改成「<原檔名>{後綴}」讓出位置
RENAME_STAGING_SUFFIX = ".moving"
# 批次搬移的進行中紀錄：執行前寫入、每完成一步更新，成功或回滾後刪除；啟動時若仍存在即為上次中途中斷
MOVE_JOURNAL_FILE = os.path.join(APPDATA_DIR, "pending_move.json")
# 單一實例鎖：程式執行期間由作業系統鎖住，程式結束（含當機、強制結束）時自動解除；檔案本身留著不刪
INSTANCE_LOCK_FILE = os.path.join(APPDATA_DIR, "instance.lock")

# 使用者偏好：檔案位置、各鍵的預設值；最近專案清單由專案存取（services/project_access.py）維護，最多記這麼多筆
PREFERENCES_FILE = os.path.join(APPDATA_DIR, "preferences.json")
RECENT_PROJECTS_KEY = "recent_projects"
MAX_RECENT_PROJECTS = 8
DEFAULT_PREFERENCES = {
    "language": "zh_TW",
    "appearance_mode": "Dark",
    RECENT_PROJECTS_KEY: [],
    "catalog_spreadsheet_id": "",
    "catalog_root_folder_id": "",
}

# 檔名不得含有的字元（Windows 最嚴），一律換成底線；Windows 保留名（CON、NUL 等）與尾端點空白不處理，這類名字不應出現
FILENAME_ILLEGAL_CHARS = '<>:"/\\|?*'
PDF_EXTENSION = ".pdf"
# 不能當資料夾名稱的名字：指向目前或上一層目錄，命名結果是這些時拒絕，檔案才不會落到輸出位置外
RELATIVE_DIR_NAMES = (".", "..")
# 分割輸出：區段名清理後為空時的檔名
SPLIT_FALLBACK_NAME = "Part"
# 分割執行中在輸出資料夾裡暫用的子資料夾「<本次代號>{後綴}」：新分譜先全部寫進前者，
# 被取代的檔案先挪進後者（保留原檔名），都就位後才把後者的檔移到資源回收桶；失敗時依此撤回
SPLIT_STAGING_DIR_SUFFIX = ".splitting"
SPLIT_REPLACED_DIR_SUFFIX = ".replaced"


class OperationKind(str, Enum):
    """操作種類：記在復原紀錄與批次搬移的進行中紀錄裡

    復原與重做本身也是一種：它們中途失敗留下搬不回去的檔案時，殘留紀錄標示的就是它們。
    """
    RENAME = "rename"
    UNDO = "undo"
    REDO = "redo"
    SPLIT = "split"
    ROTATE = "rotate"


# 以整批搬移完成的操作種類：這類紀錄的復原與重做就是把檔案在兩個位置之間搬來搬去
MOVE_OPERATIONS = frozenset({OperationKind.RENAME, OperationKind.UNDO, OperationKind.REDO})


class RenameProblem(str, Enum):
    """重新命名預檢以來源為鍵的問題種類

    搬移引擎的執行前驗證用同一套規則判定其中與搬移有關的種類（復原、重做沒有輸出位置，不檢查邊界）。
    """
    MISSING_SOURCE = "missing_source"
    SUFFIXED = "suffixed"
    EXTRA_FILE = "extra_file"
    OUTSIDE_OUTPUT = "outside_output"
    EMPTY_NAME = "empty_name"
    DUPLICATE_SOURCE = "duplicate_source"
    DUPLICATE_TARGET = "duplicate_target"
    TARGET_OCCUPIED = "target_occupied"
    STAGING_TAKEN = "staging_taken"


# 預檢不阻擋的問題：來源已不在的從計畫丟掉、重複的目標加後綴、多於聲部數的分譜不改名；其餘一律阻擋
NON_BLOCKING_RENAME_PROBLEMS = frozenset({
    RenameProblem.MISSING_SOURCE, RenameProblem.SUFFIXED, RenameProblem.EXTRA_FILE,
})


class WorkspaceStatus(str, Enum):
    """工作區子資料夾相對於專案的引用狀態"""
    IN_USE = "in_use"
    OWNED_BY_OTHER = "owned_by_other"
    OWNER_UNREADABLE = "owner_unreadable"
    ORPHAN = "orphan"
    UNKNOWN_SOURCE = "unknown_source"


PROJECT_EXTENSION = ".llproj"
# 檔案進入群組時，檔名（不含副檔名、不分大小寫）含這些字樣的第一個檔案自動設為總譜
SCORE_KEYWORDS = ("score", "full score", "conductor", "總譜", "指揮譜", "full")
DEFAULT_MASTER_TEMPLATE = "{序號}-{曲名}-{樂器}.pdf"
DEFAULT_MASTER_TEMPLATE_EN = "{Number}-{PieceName}-{Instrument}.pdf"
DEFAULT_SUBFOLDER_TEMPLATE = "{曲名} - 第{樂章編號}樂章"
DEFAULT_SUBFOLDER_TEMPLATE_EN = "{PieceName} - Movement {MovementNum}"
DEFAULT_PARTS_SUBFOLDER_NAME = "Parts"


class PartsOutputMode(str, Enum):
    """分譜存放模式（專案檔存的是值）"""
    ROOT = "root"
    PARTS = "parts"
    SECTION = "section"


class VariableLevel(str, Enum):
    """模板變數的層級"""
    GROUP = "group"
    FILE = "file"


@dataclass(frozen=True)
class TemplateVariable:
    """模板變數定義

    Attributes:
        name: 中文名稱
        name_en: 英文名稱
        level: 群組層級（同一群組的每個檔案都相同）或逐檔不同
        source: 值的來源；群組層級為 Group 的欄位名稱，逐檔為 TEMPLATE_SLOT_SOURCES 之一
        description: 說明
    """
    name: str
    name_en: str
    level: VariableLevel
    source: str
    description: str


# 逐檔變數的值來源（依序為命名模組傳入的值）：這一格的序號、這一格的聲部（總譜為總譜標籤）
TEMPLATE_SLOT_SOURCES = ("number", "voice")

TEMPLATE_VARIABLES: List[TemplateVariable] = [
    TemplateVariable("序號", "Number", VariableLevel.FILE, "number", "聲部在樂器表中的位置，至少兩位數；總譜為 00"),
    TemplateVariable("樂器", "Instrument", VariableLevel.FILE, "voice", "樂器表，依排序對應；總譜為總譜標籤"),
    TemplateVariable("曲名", "PieceName", VariableLevel.GROUP, "piece_name", "從檔名共同部分自動偵測，使用者可覆寫"),
    TemplateVariable("樂章編號", "MovementNum", VariableLevel.GROUP, "movement_number", "使用者輸入"),
    TemplateVariable("樂章名稱", "MovementName", VariableLevel.GROUP, "movement_name", "使用者輸入"),
    TemplateVariable("作曲家", "Composer", VariableLevel.GROUP, "composer", "使用者輸入"),
    TemplateVariable("曲種", "Genre", VariableLevel.GROUP, "genre", "使用者輸入，例如交響曲、協奏曲"),
]
# 序號的最少位數（分譜為 01、02…，超過 99 個聲部才用三位）與總譜的序號
MIN_NUMBER_WIDTH = 2
SCORE_NUMBER = "00"


@dataclass(frozen=True)
class InstrumentPreset:
    """預設編制表"""
    name: str
    name_en: str
    instruments: tuple


INSTRUMENT_PRESETS: List[InstrumentPreset] = [
    InstrumentPreset(
        name="管弦樂團",
        name_en="Orchestra",
        instruments=(
            "Flute 1", "Flute 2",
            "Oboe 1", "Oboe 2",
            "Clarinet in Bb 1", "Clarinet in Bb 2",
            "Bassoon 1", "Bassoon 2",
            "Horn in F 1", "Horn in F 2", "Horn in F 3", "Horn in F 4",
            "Trumpet in Bb 1", "Trumpet in Bb 2",
            "Trombone 1", "Trombone 2", "Bass Trombone",
            "Tuba",
            "Timpani",
            "Percussion",
            "Violin I", "Violin II",
            "Viola",
            "Violoncello",
            "Contrabass",
        ),
    ),
    InstrumentPreset(
        name="管樂團",
        name_en="Concert Band",
        instruments=(
            "Piccolo",
            "Flute 1", "Flute 2",
            "Oboe",
            "English Horn",
            "Clarinet in Bb 1", "Clarinet in Bb 2", "Clarinet in Bb 3",
            "Bass Clarinet",
            "Bassoon",
            "Alto Saxophone 1", "Alto Saxophone 2",
            "Tenor Saxophone",
            "Baritone Saxophone",
            "Trumpet in Bb 1", "Trumpet in Bb 2", "Trumpet in Bb 3",
            "Horn in F 1", "Horn in F 2", "Horn in F 3", "Horn in F 4",
            "Trombone 1", "Trombone 2", "Bass Trombone",
            "Euphonium",
            "Tuba",
            "String Bass",
            "Timpani",
            "Percussion 1", "Percussion 2",
        ),
    ),
    InstrumentPreset(
        name="弦樂團",
        name_en="String Orchestra",
        instruments=(
            "Violin I", "Violin II",
            "Viola",
            "Violoncello",
            "Contrabass",
        ),
    ),
    InstrumentPreset(
        name="國樂合奏",
        name_en="Chinese Orchestra",
        instruments=(
            # 吹管
            "梆笛", "曲笛", "新笛",
            "高音笙", "中音笙", "低音笙",
            "高音嗩吶", "中音嗩吶", "次中音嗩吶", "低音嗩吶",
            # 彈撥
            "柳琴",
            "琵琶",
            "中阮", "大阮",
            "揚琴",
            "古箏",
            # 打擊
            "打擊 I", "打擊 II",
            # 弦樂
            "高胡",
            "二胡 I", "二胡 II",
            "中胡",
            "大提琴",
            "低音提琴",
        ),
    ),
    InstrumentPreset(
        name="絲竹室內樂",
        name_en="Silk and Bamboo Ensemble",
        instruments=(
            # 吹管
            "曲笛", "簫", "笙",
            # 彈撥
            "琵琶", "中阮", "揚琴", "古箏",
            # 弦樂
            "二胡", "中胡",
        ),
    ),
]

SECTION_KEYWORDS: List[Tuple[str, str, List[str]]] = [
    ("吹管", "Winds", [
        "笛", "曲笛", "梆笛", "新笛", "簫", "笙", "嗩吶", "管子",
    ]),
    ("彈撥", "Plucked Strings", [
        "琵琶", "阮", "柳琴", "揚琴", "箏", "三弦",
    ]),
    ("木管", "Woodwinds", [
        "flute", "piccolo", "oboe", "english horn", "cor anglais",
        "clarinet", "bassoon", "contrabassoon", "saxophone", "sax",
    ]),
    ("銅管", "Brass", [
        "horn", "trumpet", "cornet", "trombone", "tuba", "euphonium",
    ]),
    ("打擊", "Percussion", [
        "timpani", "percussion", "xylophone", "marimba", "vibraphone",
        "glockenspiel", "drum", "打擊", "鼓", "鑼", "鈸",
    ]),
    ("鍵盤", "Keyboard", [
        "piano", "keyboard", "organ", "celesta", "harpsichord", "harp",
    ]),
    ("弦樂", "Strings", [
        "violin", "viola", "violoncello", "cello", "contrabass",
        "double bass", "string bass", "胡", "高胡", "二胡", "中胡",
        "大提琴", "低音提琴",
    ]),
]

# 偵測不到聲部組時的名稱
OTHER_SECTION = "其他"
OTHER_SECTION_EN = "Other"

SECTION_NAMES_EN = {
    **{section_zh: section_en for section_zh, section_en, _ in SECTION_KEYWORDS},
    OTHER_SECTION: OTHER_SECTION_EN,
}

DEFAULT_HEADCOUNT = 1


def detect_instrument_section(instrument_name: str, english: bool = False) -> str:
    """根據聲部名稱自動偵測所屬聲部組

    Args:
        instrument_name: 聲部名稱
        english: 為 True 時回傳英文名稱

    Returns:
        聲部組名稱，偵測不到時回傳「其他」（英文為 Other）
    """
    name_lower = instrument_name.lower()
    for section_zh, section_en, keywords in SECTION_KEYWORDS:
        for kw in keywords:
            if kw.lower() in name_lower:
                return section_en if english else section_zh
    return OTHER_SECTION_EN if english else OTHER_SECTION
