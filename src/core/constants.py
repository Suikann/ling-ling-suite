# -*- coding: utf-8 -*-
"""
常數定義

定義模板變數、預設值與應用程式路徑。
"""
import os
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Tuple

APP_NAME = "LingLingSuite"
APP_DISPLAY_NAME = "泠靈小工具"
# repo 根目錄（src/ 的上層）：從原始碼執行時內附的 Google 用戶端檔放在這裡
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 版號檔：CD 把 git tag 寫進 repo 根目錄的這個檔（不進 git），LingLingSuite.spec 收進資源目錄的根層
VERSION_FILE = "version.txt"
APP_REPOSITORY_URL = "https://github.com/Suikann/ling-ling-suite"
# 視窗管理器認得的識別名：Qt 的應用程式名稱與 desktop file name（Wayland 的 app_id、X11 的 WM_CLASS），
# 隨 AppImage 附的 .desktop 檔名與 StartupWMClass 要與它一致
APP_ID = "lingling-suite"


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
# 系統安全儲存裡的服務名稱：Google 與 HuaXia 的續期權杖各存一筆
GOOGLE_KEYRING_SERVICE = "LingLingSuite Google"
HUAXIA_KEYRING_SERVICE = "LingLingSuite HuaXia"
# 舊版以明文存 Google 權杖的檔案：新版每次啟動都刪掉，任何情況都不讀它
LEGACY_TOKEN_FILE = os.path.join(APPDATA_DIR, "google_token.json")
# 暫存資料夾：壓縮檔先寫在這裡，檢查通過才交出去，啟動時清空上次留下的殘檔；目前給獨立壓縮用，設計成發譜（spec #82）也能沿用
TEMP_DIR = os.path.join(APPDATA_DIR, "temp")
# 紀錄檔：譜庫的讀寫失敗與重要動作，一天一個檔（<年-月-日>.log）；保留這麼多天，啟動時刪掉更早的
LOG_DIR = os.path.join(APPDATA_DIR, "logs")
LOG_FILE_EXTENSION = ".log"
LOG_RETENTION_DAYS = 30

# 介面語言代碼：繁體中文（預設）與英文
LOCALE_ZH_TW = "zh_TW"
LOCALE_EN = "en"

# 使用者偏好：檔案位置、各鍵的預設值；最近專案清單由專案存取（services/project_access.py）維護，最多記這麼多筆
PREFERENCES_FILE = os.path.join(APPDATA_DIR, "preferences.json")
RECENT_PROJECTS_KEY = "recent_projects"
MAX_RECENT_PROJECTS = 8
# 上次登入的 Google 帳號 email：譜庫設定顯示它，下次登入帶 login_hint；不是祕密，不放安全儲存
GOOGLE_ACCOUNT_EMAIL_KEY = "google_account_email"
# 上次讀到的社團設定裡啟用的發譜平台（PublishingPlatform 的值）；空字串為沒有或從沒讀到過，發譜功能隱藏
CLUB_PUBLISHING_PLATFORM_KEY = "club_publishing_platform"
# 收回提醒的摘要：已發譜、還沒收回也沒標不收回的曲目與它們掛的公演結束時間（core/publish_log.py 的 reminder_summary）；
# 每次讀寫發譜紀錄時重算，開泠靈時只讀它判斷要不要提醒
RECALL_REMINDER_KEY = "recall_reminder"
# 譜庫設定：譜庫試算表的 ID 與譜庫根資料夾的 Drive ID；按下譜庫設定的「儲存設定」才寫入
CATALOG_SPREADSHEET_ID_KEY = "catalog_spreadsheet_id"
CATALOG_ROOT_FOLDER_ID_KEY = "catalog_root_folder_id"
DEFAULT_PREFERENCES = {
    "language": LOCALE_ZH_TW,
    "appearance_mode": "Dark",
    RECENT_PROJECTS_KEY: [],
    CATALOG_SPREADSHEET_ID_KEY: "",
    CATALOG_ROOT_FOLDER_ID_KEY: "",
    GOOGLE_ACCOUNT_EMAIL_KEY: "",
    CLUB_PUBLISHING_PLATFORM_KEY: "",
    RECALL_REMINDER_KEY: [],
}

# 檔名不得含有的字元（Windows 最嚴），一律換成底線；Windows 保留名（CON、NUL 等）與尾端點空白不處理，這類名字不應出現
FILENAME_ILLEGAL_CHARS = '<>:"/\\|?*'
PDF_EXTENSION = ".pdf"
# 匯出樂器表的文字檔副檔名
TEXT_EXTENSION = ".txt"
# 不能當資料夾名稱的名字：指向目前或上一層目錄，命名結果是這些時拒絕，檔案才不會落到輸出位置外
RELATIVE_DIR_NAMES = (".", "..")
# 分割輸出：區段名清理後為空時的檔名
SPLIT_FALLBACK_NAME = "Part"
# 分割執行中在輸出資料夾裡暫用的子資料夾「<本次代號>{後綴}」：新分譜先全部寫進前者，
# 位置會被新分譜佔用的被取代檔案先挪進後者（保留原檔名），都就位後才把後者的檔（與其餘從原處）移到資源回收桶；
# 失敗時依此撤回
SPLIT_STAGING_DIR_SUFFIX = ".splitting"
SPLIT_REPLACED_DIR_SUFFIX = ".replaced"
# 分割與旋轉的區段標示色，第 n 段依序循環取用；頁面預覽、分割與旋轉對話框共用
SECTION_COLORS = (
    "#3B82F6", "#10B981", "#F59E0B", "#EF4444",
    "#8B5CF6", "#EC4899", "#06B6D4", "#F97316",
)
# 標出還沒進譜庫（未同步、未存入）的值與檔的文字顏色；編制表、群組面板與發譜預覽共用
UNSYNCED_COLOR = "#e67e22"
# 撤銷存入、撤回與收回畫面依結果上色的（字色，底色），同原型 #63、#65：紅是會不見的，黃是要注意的，藍是只動一部分或
# 補充說明，灰是不動的；灰的字色也是這些畫面說明文字的顏色
ALERT_COLORS = ("#ff8a8a", "#5a2129")
WARNING_COLORS = ("#f2c14e", "#5b4613")
INFO_COLORS = ("#8fb8ff", "#253c63")
MUTED_COLORS = ("#7a7f93", "#24262f")
# 會做成的事的綠色字（存入的新上傳、可以清除的檔）
POSITIVE_TEXT_COLOR = "#7ddc9a"
# 擋下動作的紅色提示列（例如未存檔時不能存入、清除，撤銷會毀掉唯一一份的群組）；存入、清除、撤銷存入與撤回的預覽共用
ALERT_BANNER_STYLE = (
    f"background-color: {ALERT_COLORS[1]}; color: {ALERT_COLORS[0]}; border-radius: 5px; padding: 6px 10px;"
)
# 提示列的藍底說明（撤銷存入要先下載回原位、撤回後留下空曲目）
INFO_BANNER_STYLE = f"background-color: {INFO_COLORS[1]}; color: {INFO_COLORS[0]}; border-radius: 5px; padding: 6px 10px;"
# 要注意的黃色提示列（上次存入中途停下）
WARNING_BANNER_STYLE = (
    f"background-color: {WARNING_COLORS[1]}; color: {WARNING_COLORS[0]}; border-radius: 5px; padding: 6px 10px;"
)
# 發譜時覆寫 HuaXia 聲部可能改的欄位（piece_parts 的欄位；發譜紀錄 parts_updated 記它們的［原值，新值］，撤回不改回）
HUAXIA_PART_FIELDS = ("needed", "needed_min", "section")
# 關閉視窗時選了取消背景工作，最多等它停下幾秒（停在檔與檔、頁與頁之間，通常不到一秒）；
# 逾時就放棄它（之後不再送回任何結果）再關閉，暫存檔留待下次啟動清掉。主視窗與譜庫視窗共用
CLOSE_WAIT_SECONDS = 10


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
    CATALOG_ONLY = "catalog_only"
    SUFFIXED = "suffixed"
    EXTRA_FILE = "extra_file"
    OUTSIDE_OUTPUT = "outside_output"
    EMPTY_NAME = "empty_name"
    DUPLICATE_SOURCE = "duplicate_source"
    DUPLICATE_TARGET = "duplicate_target"
    TARGET_OCCUPIED = "target_occupied"
    STAGING_TAKEN = "staging_taken"


class ProblemPath(str, Enum):
    """執行前驗證拒絕時，訊息列出該項的哪一條路徑"""
    SOURCE = "source"
    TARGET = "target"
    STAGING = "staging"


# 壓縮：輸出要壓到這個大小（位元組）以下，即 HuaXia 的單檔上限 20MB
COMPRESS_SIZE_LIMIT = 20 * 1000 * 1000
# 壓縮的預設輸出資料夾：原資料夾名加上這個後綴，放在原資料夾旁
COMPRESSED_FOLDER_SUFFIX = "（壓縮）"
COMPRESSED_FOLDER_SUFFIX_EN = " (compressed)"


class CompressionLevel(Enum):
    """壓縮的級數，依序試到大小上限以下為止；值為降到的 dpi，無損為 0"""
    LOSSLESS = 0
    DPI_300 = 300
    DPI_200 = 200


class CompressionStatus(str, Enum):
    """一份 PDF 壓縮的結果"""
    SMALL_ENOUGH = "small_enough"
    COMPRESSED = "compressed"
    STILL_TOO_LARGE = "still_too_large"
    FAILED = "failed"
    UNCOMPRESSIBLE = "uncompressible"
    CANCELLED = "cancelled"


class CompressionProblem(str, Enum):
    """壓縮失敗或無法壓縮的原因

    前兩種是原檔打不開（無法壓縮）；其餘是壓縮檔沒通過檢查或寫不出來（壓縮失敗）。
    """
    ENCRYPTED = "encrypted"
    UNREADABLE = "unreadable"
    WRITE_FAILED = "write_failed"
    OUTPUT_UNREADABLE = "output_unreadable"
    REPAIRED = "repaired"
    PAGE_COUNT = "page_count"
    RENDER_ERROR = "render_error"
    WARNINGS = "warnings"
    CHANNELS = "channels"
    PSNR = "psnr"


@dataclass(frozen=True)
class RenameProblemRule:
    """一種重新命名問題在預檢、預覽與執行前驗證的處理方式

    Attributes:
        blocking: 預檢是否阻擋執行
        highlight: 預覽是否把這一項標成紅色（阻擋的，以及加了後綴的）
        preview_key: 預覽顯示的訊息鍵（可用 count 與 files）：阻擋的列為警告，不阻擋的列為提醒
        refusal_key: 執行前驗證拒絕時的錯誤訊息鍵（可用 files）；空字串表示只有預檢判定，執行前驗證不檢查
        refusal_lists: 拒絕訊息列出該項的哪一條路徑
        refusal_error: 執行前驗證拒絕時拋出的例外類別
    """
    blocking: bool
    highlight: bool
    preview_key: str
    refusal_key: str = ""
    refusal_lists: ProblemPath = ProblemPath.SOURCE
    refusal_error: type = FileExistsError


# 每種重新命名問題的處理方式，依執行前驗證的檢查順序（第一種有問題的種類決定拒絕的訊息）。
# 不阻擋的：來源已不在的從計畫丟掉（只在譜庫的另列一種）、重複的目標加後綴、多於聲部數的分譜不改名；其餘一律阻擋
RENAME_PROBLEM_RULES: Dict[RenameProblem, RenameProblemRule] = {
    RenameProblem.OUTSIDE_OUTPUT: RenameProblemRule(
        True, True, "preview.outside_output_warning", "rename.error.outside_output", ProblemPath.TARGET, ValueError,
    ),
    RenameProblem.EMPTY_NAME: RenameProblemRule(
        True, True, "preview.empty_name_warning", "rename.error.empty_name", ProblemPath.SOURCE, ValueError,
    ),
    RenameProblem.MISSING_SOURCE: RenameProblemRule(
        False, False, "preview.missing_warning", "rename.error.source_missing", ProblemPath.SOURCE, FileNotFoundError,
    ),
    RenameProblem.CATALOG_ONLY: RenameProblemRule(False, False, "preview.catalog_only_note"),
    RenameProblem.DUPLICATE_SOURCE: RenameProblemRule(
        True, True, "preview.duplicate_source_warning", "rename.error.duplicate_source", ProblemPath.SOURCE,
    ),
    RenameProblem.DUPLICATE_TARGET: RenameProblemRule(
        True, True, "preview.duplicate_target_warning", "rename.error.duplicate_target", ProblemPath.TARGET,
    ),
    RenameProblem.TARGET_OCCUPIED: RenameProblemRule(
        True, True, "preview.occupied_warning", "rename.error.target_exists", ProblemPath.TARGET,
    ),
    RenameProblem.STAGING_TAKEN: RenameProblemRule(
        True, True, "preview.staging_warning", "rename.error.staging_exists", ProblemPath.STAGING,
    ),
    RenameProblem.SUFFIXED: RenameProblemRule(False, True, "preview.conflict_warning"),
    RenameProblem.EXTRA_FILE: RenameProblemRule(False, False, "preview.extra_files_warning"),
}


class WorkspaceStatus(str, Enum):
    """工作區子資料夾相對於專案的引用狀態"""
    IN_USE = "in_use"
    OWNED_BY_OTHER = "owned_by_other"
    OWNER_UNREADABLE = "owner_unreadable"
    ORPHAN = "orphan"
    UNKNOWN_SOURCE = "unknown_source"


PROJECT_EXTENSION = ".llproj"
# 專案檔的格式版本（寫在專案檔的 version 欄）；之後改變專案檔內容的意義時遞增。
# 比它新的專案檔由較新版本的程式建立，唯讀開啟、不能存檔（存檔會丟掉看不懂的欄位）
PROJECT_FILE_VERSION = 3
# 編制改版的格式版本（聲部組對照改以樂器為鍵、加上樂團類型、人數改為群組層級的範圍）：
# 比它舊的專案檔開啟時遷移聲部組、判定樂團類型，並把專案層級的人數展開到各群組
INSTRUMENTATION_FILE_VERSION = 3
# 舊版程式寫的 version（當時寫的是應用程式版本；None 為沒有這一欄）：只有開啟這些專案檔時才做舊格式遷移，
# 遷移結果存檔後就是新版的檔，使用者之後清掉的值不會在下次開啟時又被補回
LEGACY_PROJECT_FILE_VERSIONS = frozenset({None, "1.1.0-alpha"})
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
    """預設樂器表"""
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

class OrchestraType(str, Enum):
    """樂團類型（專案檔存的是值）：決定聲部組用哪一套"""
    CHINESE = "chinese"
    WESTERN = "western"


class HuaXiaEnsemble(str, Enum):
    """HuaXia 曲目屬於大團的合奏或絲竹（pieces.ensemble 的值，專案檔存的也是它）：決定誰看得到總譜"""
    TUTTI = "tutti"
    SIZHU = "sizhu"


# HuaXia 場次類型裡的「公演」（events.kinds）：發譜只掛公演，收回只看公演
CONCERT_KIND = "concert"
# HuaXia 曲目時長（pieces.duration_min，numeric）的間隔：HuaXia 編輯曲目的表單以 0.5 分鐘為一格，
# 寫成別的值會讓幹部在網頁上改曲目時被表單擋下
HUAXIA_DURATION_STEP_MINUTES = 0.5
# 時長欄位的上限（分鐘）；編制表與發譜預覽新建曲目的時長共用
MAX_DURATION_MINUTES = 999


# 各樂團類型的聲部組，依選單順序；兩套都另有 OTHER_SECTION
ORCHESTRA_SECTIONS: Dict[OrchestraType, Tuple[str, ...]] = {
    OrchestraType.CHINESE: ("吹管", "拉弦", "彈撥", "打擊", "低音"),
    OrchestraType.WESTERN: ("木管", "銅管", "打擊", "鍵盤", "弦樂"),
}
# 偵測聲部組的關鍵字（不分大小寫、部分相符），依比對順序：先比到的先算。
# 國樂團的低音排在拉弦之前，「革胡」才不會被「胡」歸進拉弦；管弦樂團的木管排在銅管之前，「English Horn」才不會歸進銅管
SECTION_KEYWORDS: Dict[OrchestraType, Tuple[Tuple[str, Tuple[str, ...]], ...]] = {
    OrchestraType.CHINESE: (
        ("吹管", ("笛", "簫", "笙", "嗩吶", "管子", "巴烏", "葫蘆絲", "塤")),
        ("彈撥", ("琵琶", "阮", "柳琴", "揚琴", "箏", "三弦", "豎琴")),
        ("低音", ("大提琴", "低音提琴", "革胡", "cello", "contrabass", "double bass")),
        ("拉弦", ("胡",)),
        ("打擊", ("打擊", "鼓", "鑼", "鈸", "timpani", "percussion")),
    ),
    OrchestraType.WESTERN: (
        ("木管", (
            "flute", "piccolo", "oboe", "english horn", "cor anglais",
            "clarinet", "bassoon", "contrabassoon", "saxophone", "sax",
        )),
        ("銅管", ("horn", "trumpet", "cornet", "trombone", "tuba", "euphonium")),
        ("打擊", (
            "timpani", "percussion", "xylophone", "marimba", "vibraphone",
            "glockenspiel", "drum", "打擊", "鼓", "鑼", "鈸",
        )),
        ("鍵盤", ("piano", "keyboard", "organ", "celesta", "harpsichord", "harp")),
        ("弦樂", (
            "violin", "viola", "violoncello", "cello", "contrabass",
            "double bass", "string bass", "胡", "大提琴", "低音提琴",
        )),
    ),
}
# 新專案的樂團類型
DEFAULT_ORCHESTRA_TYPE = OrchestraType.CHINESE

# 偵測不到聲部組時的名稱
OTHER_SECTION = "其他"

# 聲部組的英文名稱：介面為英文時顯示用；專案存的一律是中文名稱（舊版存的英文名稱在開啟時對回中文）
SECTION_NAMES_EN = {
    "吹管": "Winds",
    "拉弦": "Bowed Strings",
    "彈撥": "Plucked Strings",
    "低音": "Bass",
    "木管": "Woodwinds",
    "銅管": "Brass",
    "打擊": "Percussion",
    "鍵盤": "Keyboard",
    "弦樂": "Strings",
    OTHER_SECTION: "Other",
}

