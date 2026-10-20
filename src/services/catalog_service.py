# -*- coding: utf-8 -*-
"""
譜庫服務

讀寫譜庫的唯一入口：UI 與其他服務只呼叫它，它對下只經過試算表與 Drive 兩個關口（services/catalog_ports.py）。
失敗時丟出帶錯誤類別的 CatalogError（core/catalog_errors.py），不以空值或空清單代表失敗。

所有讀寫共用的規則都在這裡：遇到 429（達到用量上限）以指數退避自動重試；以名稱搜尋 Drive 時先跳脫反斜線、
再跳脫單引號（drive_literal）；新增列時抽的 8 碼 ID 跟同一張工作表既有的撞號就重抽，同一批列重送前先以它們的 ID
檢查哪些已經寫進去，不產生重複列（new_rows、add_rows）；建譜庫試算表時建好就記下 ID，搬進資料夾失敗後重送不建第二份（PendingCatalog）。試算表的值一律以 RAW 寫入由通事負責。

網路請求會卡住呼叫的執行緒，UI 要在背景執行緒呼叫。

使用範例：
    from services.catalog_service import CatalogService
    catalog = CatalogService(sheets, drive)          # 兩個關口；真的 Google 見 services/google_catalog.py
    overview = catalog.overview(root_folder_id)      # 曲目、作曲家、根資料夾內容、用過的聲部名稱
    detail = catalog.piece_detail(piece_id)
    platform = catalog.publishing_platform()         # 社團設定啟用的發譜平台，沒有時為 None
    place = catalog.read_place(edition_id, movement_id, OrchestraType.CHINESE)  # 群組要連的一處
    outcome = catalog.sync_instrumentation(place_writes(project), project.orchestra_type, email, WriteSource.SAVE, {})
    rows = catalog.revert_rows(edition_id, movement_id, group.id, group.name, OrchestraType.CHINESE)  # 改連時撤得掉的編制
    page = catalog.edition_page(edition_id, movement_id, OrchestraType.CHINESE)  # 版本頁：登記與沒登記的檔
    catalog.register_files(PendingRegistration(edition_id, movement_id, {"琵琶": pdf}))  # 失敗時以同一個物件重送
    places = catalog.place_files([("e1", "m1")])  # 存入前：那處的登記與版本資料夾裡的檔
    deposit_id = catalog.begin_deposit(planned, email)  # 存入前：每份要做的檔寫一列「要做」
    catalog.record_deposits(deposit_id, records, email)  # 存入後：登記分譜列並寫存入紀錄（一次寫入）
    content = catalog.download_file(drive_file)      # 從譜庫下載：一個檔的內容，呼叫端拿 md5 核對
    history = catalog.deposit_log()                  # 存入紀錄的全部列（撤銷存入挑一次存入）
    reading = catalog.revert_reading(deposit_id, OrchestraType.CHINESE)  # 撤銷存入前：那次每份檔與各處的現況
    catalog.record_revert(deposit_id, done, email)   # 撤銷存入後：移除登記並寫存入紀錄（一次寫入）
    rows = catalog.new_rows(SHEET_PIECES, [{"title": "春江花月夜"}])
    ids = catalog.add_rows(rows)                     # 失敗時以同一個 rows 重送，不會重複新增
"""
import hashlib
import time
import uuid
from dataclasses import fields, replace
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Type, TypeVar

from core.catalog_constants import (
    CATALOG_TRUE, CATALOG_TRUE_VALUES, CLUB_SETTING_PUBLISHING_PLATFORM, DEPOSIT_PENDING, DRIVE_FOLDER_MIME,
    DRIVE_PDF_MIME, DRIVE_ROOT_ALIAS,
    DRIVE_SPREADSHEET_MIME, PUBLISHING_PLATFORM_NAMES, SHEET_CLUB_SETTINGS, SHEET_COMPOSERS, SHEET_DURATIONS,
    SHEET_DEPOSIT_LOG, SHEET_EDITIONS, SHEET_HEADERS, SHEET_INSTRUMENTATION_LOG, SHEET_MOVEMENTS, SHEET_PARTS, SHEET_PERFORMANCES,
    SHEET_PIECES, SHEET_PUBLISH_LOG, SHEET_SECTIONS, PublishingPlatform,
)
from core.catalog_errors import CatalogError, CatalogErrorKind
from core.catalog_headers import catalog_header_locale, header_locale, header_row, localized_sheets
from core.constants import CATALOG_ROOT_FOLDER_ID_KEY, LOCALE_EN, OrchestraType
from core.voice_suggestions import ranked_by_usage
from core.catalog_models import (
    CatalogOverview, CatalogPlace, Composer, DepositLogEntry, DriveFile, DriveRevision, Edition, EditionFiles,
    EditionPage, FolderContents, FolderPdf, InstrumentationLogEntry, LinkTargets, Movement, Part, Performance, Piece,
    PieceDetail, Registration, SectionMapEntry, sort_order_key,
)
from core.catalog_revert import RevertRow, revert_rows
from core.catalog_sync import (
    CurrentPlace, Decision, ItemReview, PlaceReview, PlaceWrite, SyncField, SyncItem, SyncOutcome, Verdict, WriteSource,
    final_voices, review_items, value_text,
)
from core.deposit import SCORE_VOICE, DepositRecord, PlaceFiles, folder_key
from core.deposit_revert import UNREGISTERING, RevertFile, RevertPlace, RevertReading, needs_revisions
from core.log_entry import now_text
from core.models import Headcount
from core.publish_log import PublishRecord
from core.sections import stored_section
from core.voice_names import instrument_key, instrument_name, voice_key
from services.catalog_ports import CellWrite, DrivePort, SheetsPort, sheet_range

T = TypeVar("T")

# Drive 的 files.generateIds 一次最多給幾個 ID
DRIVE_IDS_PER_REQUEST = 1000
# 遇到 429 時最多自動重試幾次，以及第一次等幾秒（之後每次加倍）
DEFAULT_RATE_LIMIT_RETRIES = 5
DEFAULT_FIRST_WAIT_SECONDS = 1.0


def drive_literal(value: str) -> str:
    """把字串寫成 Drive 查詢字串裡的字串值：先跳脫反斜線、再跳脫單引號，前後加單引號

    Args:
        value: 原本的字串（例如檔名）

    Returns:
        可以直接放進查詢字串的字串值
    """
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def _random_id() -> str:
    """抽一個 8 碼的 ID（十六進位）"""
    return uuid.uuid4().hex[:8]


def _column_letter(index: int) -> str:
    """從 0 起算的欄號轉成欄位字母（0 → A、26 → AA）"""
    letters = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _whole_sheet(sheet: str) -> str:
    """整張工作表的範圍"""
    return sheet_range(sheet, "A:ZZ")


def _numbered_records(rows: List[List[str]], sheet: str) -> List[Tuple[int, Dict[str, str]]]:
    """把工作表讀到的列（含標題列）依程式的欄位常數轉成欄位名稱對值，連同從 1 起算的列號；標題列與空列略過

    其他列轉資料模型的寫法（_models、_numbered、_records）都從這裡來。
    """
    headers = SHEET_HEADERS[sheet]
    return [
        (index + 1, {header: (row[i] if i < len(row) else "") for i, header in enumerate(headers)})
        for index, row in enumerate(rows) if index > 0 and any(row)
    ]


def _numbered(rows: List[List[str]], sheet: str, model_class: Type[T]) -> List[Tuple[int, T]]:
    """把工作表讀到的列轉成資料模型（只取模型有的欄位），連同從 1 起算的列號；標題列與空列略過"""
    names = {f.name for f in fields(model_class)}
    return [
        (row, model_class(**{name: value for name, value in record.items() if name in names}))
        for row, record in _numbered_records(rows, sheet)
    ]


def _models(rows: List[List[str]], sheet: str, model_class: Type[T]) -> List[T]:
    """把工作表讀到的列轉成資料模型；標題列與空列略過"""
    return [model for _, model in _numbered(rows, sheet, model_class)]


def _records(rows: List[List[str]], sheet: str) -> List[Dict[str, str]]:
    """把工作表讀到的列轉成欄位名稱對值；標題列與空列略過"""
    return [record for _, record in _numbered_records(rows, sheet)]


def _is_true(value: str) -> bool:
    """是非欄的值是否為「是」（寫法見 CATALOG_TRUE_VALUES）"""
    return value.strip().upper() in CATALOG_TRUE_VALUES


def _count(value: str) -> Optional[int]:
    """人數或秒數欄的值；空白或不是正整數時為 None"""
    value = value.strip()
    return int(value) if value.isdigit() and int(value) > 0 else None


def _voice_parts(parts: List[Tuple[int, Part]]) -> List[Tuple[int, Part]]:
    """一處認得的聲部列（連同列號）：依排序欄排，略過總譜列與沒有名稱的列；同一聲部有兩列時只認排在前面那一列"""
    seen = set()
    voices = []
    for row, part in sorted(parts, key=lambda pair: sort_order_key(pair[1].sort_order)):
        key = voice_key(part.instrument_name)
        if _is_true(part.is_score) or not part.instrument_name.strip() or key in seen:
            continue
        seen.add(key)
        voices.append((row, part))
    return voices


def _score_part(parts: List[Tuple[int, Part]]) -> Optional[Tuple[int, Part]]:
    """一處的總譜列與列號：有幾列時只認排在前面那一列；沒有時為 None"""
    return next(((row, part) for row, part in parts if _is_true(part.is_score)), None)


def _register_row(batch: "_Batch", target: Tuple[int, Part], file: DriveFile) -> str:
    """把分譜列改登記成 file（已經登記這一份時不寫），回傳被換掉的舊檔 ID（沒有時為空字串）"""
    row, part = target
    old = part.drive_file_id.strip()
    if old != file.id:
        batch.set(SHEET_PARTS, row, "drive_file_id", file.id)
    if part.file_name.strip() != file.name:
        batch.set(SHEET_PARTS, row, "file_name", file.name)
    return old if old != file.id else ""


def _registered_by(part: Part, entry: DepositLogEntry) -> bool:
    """分譜列是不是存入紀錄這一列登記的：同一處、登記同一個 Drive 檔，而且是同一聲部（總譜列對總譜）"""
    return (
        (part.edition_id, part.movement_id) == (entry.edition_id, entry.movement_id)
        and part.drive_file_id.strip() == entry.drive_file_id
        and (_is_true(part.is_score) or voice_key(part.instrument_name) == voice_key(entry.voice))
    )


def _deposit_row(
    deposit_id: str, stamp: str, operator: str, record: DepositRecord, revision_after: str, state: str,
) -> Dict[str, str]:
    """存入紀錄的一列：一份檔的「要做」列（state 為 DEPOSIT_PENDING、沒有寫入後的版本）或做了的列"""
    return {
        "deposit_id": deposit_id, "time": stamp, "operator": operator, "edition_id": record.edition_id,
        "movement_id": record.movement_id, "group": record.group_name, "voice": record.voice or record.score_label,
        "local_path": record.local_path, "drive_file_id": record.drive_file.id, "action": record.action.value,
        "revision_after": revision_after, "revision_before": record.revision_before, "group_id": record.group_id,
        "state": state,
    }


def _at(parts: List[Tuple[int, Part]], edition_id: str, movement_id: str) -> List[Tuple[int, Part]]:
    """一處（版本、樂章）的分譜列連同列號"""
    return [(row, part) for row, part in parts if (part.edition_id, part.movement_id) == (edition_id, movement_id)]


def _add_score_row(batch: "_Batch", edition_id: str, movement_id: str, file: DriveFile) -> None:
    """一處還沒有總譜列時新增一列登記 file：只以 is_score 標記，聲部名稱留空"""
    batch.add(SHEET_PARTS, {
        "edition_id": edition_id, "movement_id": movement_id, "is_score": CATALOG_TRUE,
        "drive_file_id": file.id, "file_name": file.name,
    })


def _relocation(file: DriveFile, folder_id: str) -> Tuple[str, List[str]]:
    """把檔搬進 folder_id 要加進的資料夾（已經在裡面時為空字串）與要離開的資料夾"""
    add = folder_id if folder_id not in file.parents else ""
    return add, [parent for parent in file.parents if parent != folder_id]


def _headcount(part: Part) -> Headcount:
    """分譜列記的人數（整理過）"""
    return Headcount(
        _count(part.headcount_min), _count(part.headcount_max), _is_true(part.headcount_open_ended),
    ).normalized()


def _headcount_cells(headcount: Headcount) -> Dict[str, str]:
    """人數寫進分譜列的三欄：最少、最多（留空＝未填或等於最少）、是否「以上」"""
    return {
        "headcount_min": str(headcount.minimum) if headcount.minimum else "",
        "headcount_max": str(headcount.maximum) if headcount.maximum else "",
        "headcount_open_ended": CATALOG_TRUE if headcount.open_ended else "",
    }


def _used_voice_names(part_rows: List[List[str]]) -> List[str]:
    """分譜工作表用過的聲部名稱，依使用次數排序（給自動完成）"""
    return ranked_by_usage(part.instrument_name for part in _models(part_rows, SHEET_PARTS, Part))


def _club_sections(records: List[Dict[str, str]], orchestra_type: OrchestraType) -> Dict[str, str]:
    """社團聲部組對照裡這個樂團類型的部分：樂器 → 聲部組；同一樂器有幾列時只認排在前面那一列"""
    sections: Dict[str, str] = {}
    seen = set()
    for record in records:
        instrument, section = record["instrument"].strip(), record["section"].strip()
        if record["orchestra_type"].strip() != orchestra_type.value or not instrument or not section:
            continue
        if instrument_key(instrument) not in seen:
            seen.add(instrument_key(instrument))
            sections[instrument] = section
    return sections


def _place_sheets(titles: Sequence[str]) -> List[str]:
    """讀一處或聲部組對照要讀的工作表：曲目、版本、樂章、分譜，以及已經建立的時長與聲部組對照"""
    return [SHEET_PIECES, SHEET_EDITIONS, SHEET_MOVEMENTS, SHEET_PARTS] + [
        sheet for sheet in (SHEET_DURATIONS, SHEET_SECTIONS) if sheet in titles
    ]


def _tables_locale(tables: Mapping[str, List[List[str]]]) -> Optional[str]:
    """讀到的各工作表裡第一張認得出語言的標題列的語言；都認不出時為 None"""
    found = (header_locale(sheet, rows[0]) for sheet, rows in tables.items() if rows)
    return next((locale for locale in found if locale), None)


class _Batch:
    """一次寫回要送出的全部格子：改既有列的格子、接在各工作表最後的新列與缺的標題，最後以一個請求送出

    缺的標題沿用那張工作表已有標題的語言；還沒有標題列的工作表用 locale（譜庫已有的語言），不混用。

    只新增的紀錄表（發譜紀錄、存入紀錄、編制寫入紀錄）以 append 新增：新列由 Google 接在寫入那一刻的最後一列之後
    （SheetsPort.write_cells 的 appends），讀與寫之間別台電腦或背景工作新增的列不會被蓋掉。其他工作表的新列以 add
    寫在讀到的最後一列之後。
    """

    def __init__(
        self, tables: Dict[str, List[List[str]]], draw: Callable[[Set[str], int], List[str]],
        locale: Optional[str] = None,
    ):
        """
        Args:
            tables: 寫入前一刻讀到的各工作表（含標題列；沒有的工作表不在裡面）
            draw: 抽新 ID 的函式，參數為不可用的 ID 與要抽幾個
            locale: 還沒有標題列的工作表用的標題語言；省略時照 tables 裡已有的標題列，都認不出時為英文
        """
        self._tables = tables
        self._locale = locale or _tables_locale(tables) or LOCALE_EN
        self._draw = draw
        self._next_row = {sheet: max(len(rows), 1) + 1 for sheet, rows in tables.items()}
        self._taken = {sheet: {row[0] for row in rows[1:] if row} for sheet, rows in tables.items()}
        self.cells: List[CellWrite] = []
        self.appends: Dict[str, List[List[str]]] = {}
        self.touched: Set[str] = set()

    def set(self, sheet: str, row: int, column: str, value: str) -> None:
        """改既有一列的一格"""
        self._touch(sheet)
        self.cells.append((sheet_range(sheet, f"{_column_letter(SHEET_HEADERS[sheet].index(column))}{row}"), [[value]]))

    def clear(self, sheet: str, row: int) -> None:
        """清空既有的一列（刪掉這筆紀錄，不刪列，其他列的列號不變）"""
        self._touch(sheet)
        self.cells.append((sheet_range(sheet, f"A{row}"), [[""] * len(SHEET_HEADERS[sheet])]))

    def add(self, sheet: str, record: Mapping[str, str]) -> int:
        """在工作表最後新增一列（抽一個不撞號的 ID），回傳列號"""
        self._touch(sheet)
        row = self._next_row.setdefault(sheet, 2)
        self._next_row[sheet] = row + 1
        self.cells.append((sheet_range(sheet, f"A{row}"), [self._values(sheet, record)]))
        return row

    def append(self, sheet: str, record: Mapping[str, str]) -> None:
        """在只新增的紀錄表最後新增一列（抽一個不撞號的 ID）；送出時由 Google 接在那一刻的最後一列之後"""
        self._touch(sheet)
        self.appends.setdefault(sheet, []).append(self._values(sheet, record))

    def _values(self, sheet: str, record: Mapping[str, str]) -> List[str]:
        """新的一列依 SHEET_HEADERS 排好的值，id 欄是新抽、不撞號的 ID"""
        taken = self._taken.setdefault(sheet, set())
        record_id = self._draw(taken, 1)[0]
        taken.add(record_id)
        return [record_id if header == "id" else str(record.get(header, "")) for header in SHEET_HEADERS[sheet]]

    def _touch(self, sheet: str) -> None:
        """第一次寫到這張工作表時補上缺的標題（工作表還沒建立，或分譜工作表還沒有新增的欄）"""
        if sheet in self.touched:
            return
        self.touched.add(sheet)
        existing = self._tables.get(sheet, [])
        present = existing[0] if existing else []
        headers = header_row(sheet, header_locale(sheet, present) or self._locale)
        if len(present) < len(headers):
            start = len(present)
            self.cells.append((sheet_range(sheet, f"{_column_letter(start)}1"), [headers[start:]]))


class _PlaceTables:
    """讀一處或寫回時讀到的各工作表：找出一處現在的編制與列號，把一處要寫的格子放進 _Batch"""

    def __init__(self, tables: Dict[str, List[List[str]]], orchestra_type: OrchestraType):
        """
        Args:
            tables: 寫入前一刻讀到的各工作表（含標題列；還沒建立的工作表不在裡面）
            orchestra_type: 專案的樂團類型
        """
        self._orchestra_type = orchestra_type
        self.pieces = {p.id: p for p in _models(tables[SHEET_PIECES], SHEET_PIECES, Piece)}
        self.editions = {e.id: e for e in _models(tables[SHEET_EDITIONS], SHEET_EDITIONS, Edition)}
        self.movements = {m.id: m for m in _models(tables[SHEET_MOVEMENTS], SHEET_MOVEMENTS, Movement)}
        self._parts = _numbered(tables[SHEET_PARTS], SHEET_PARTS, Part)
        self._durations = _numbered_records(tables.get(SHEET_DURATIONS, []), SHEET_DURATIONS)
        self._sections = [
            (row, record) for row, record in _numbered_records(tables.get(SHEET_SECTIONS, []), SHEET_SECTIONS)
            if record["orchestra_type"].strip() == orchestra_type.value and record["instrument"].strip()
        ]

    def current(self, edition_id: str, movement_id: str) -> CurrentPlace:
        """一處現在的編制、時長、社團對照與已有 Drive 檔案的聲部

        Raises:
            CatalogError: 版本或樂章已不在譜庫
        """
        if edition_id not in self.editions:
            raise CatalogError(CatalogErrorKind.GONE, f"edition {edition_id} is no longer in the catalog")
        if movement_id and movement_id not in self.movements:
            raise CatalogError(CatalogErrorKind.GONE, f"movement {movement_id} is no longer in the catalog")
        parts = self._voice_rows(edition_id, movement_id)
        duration = self._duration_row(edition_id, movement_id)
        return CurrentPlace(
            voices=[(part.instrument_name, _headcount(part)) for _, part in parts],
            duration_seconds=_count(duration[1]["seconds"]) if duration else None,
            sections=_club_sections([record for _, record in self._sections], self._orchestra_type),
            filed=[part.instrument_name for _, part in parts if part.drive_file_id.strip()],
        )

    def club(self) -> CurrentPlace:
        """只有社團對照的「一處」：譜庫視窗編輯聲部組對照時，寫入前比對用"""
        return CurrentPlace(
            voices=[], duration_seconds=None,
            sections=_club_sections([record for _, record in self._sections], self._orchestra_type), filed=[],
        )

    def registrations(self, edition_id: str, movement_id: str) -> Dict[str, Registration]:
        """一處登記了 Drive 檔案的聲部與登記的檔"""
        return {
            part.instrument_name: Registration(part.drive_file_id.strip(), part.file_name)
            for _, part in self._voice_rows(edition_id, movement_id) if part.drive_file_id.strip()
        }

    def registered_voices(self, edition_id: str, movement_id: str) -> Dict[str, str]:
        """一處的聲部列（不含總譜列）登記的 Drive 檔案 ID → 聲部"""
        return {
            registration.file_id: voice for voice, registration in self.registrations(edition_id, movement_id).items()
        }

    def score(self, edition_id: str, movement_id: str) -> Optional[Registration]:
        """一處的總譜列登記的檔；沒有總譜列或沒登記時為 None"""
        row = self._score_row(edition_id, movement_id)
        part = row[1] if row else None
        return Registration(part.drive_file_id.strip(), part.file_name) if part and part.drive_file_id.strip() else None

    def registered_file_ids(self) -> Set[str]:
        """所有分譜列（不分版本與樂章，含總譜列）登記的 Drive 檔案"""
        return {part.drive_file_id.strip() for _, part in self._parts if part.drive_file_id.strip()}

    def other_pieces(self, edition_id: str) -> Dict[str, int]:
        """譜庫裡每種樂器（第一次出現的寫法）被這個版本的曲目以外的幾首曲目用到（總譜列不算）"""
        piece_id = self.editions[edition_id].piece_id if edition_id in self.editions else ""
        names, pieces = self._instrument_pieces()
        return {names[key]: len(used - {piece_id}) for key, used in pieces.items()}

    def section_entries(self) -> List[SectionMapEntry]:
        """社團對照裡這個樂團類型的每種樂器，再接上分譜工作表用到、對照裡還沒有的樂器，各連同用到幾首

        對照裡的樂器依工作表的順序、寫法是第一次寫入的（同一樂器有幾列時認排在前面那一列），聲部組照讀取的規則
        （_club_sections：空白的不算）；還沒有的依分譜工作表第一次出現的順序，寫法去掉結尾編號，聲部組空白。
        """
        names, pieces = self._instrument_pieces()
        club = _club_sections([record for _, record in self._sections], self._orchestra_type)
        entries = []
        seen: Set[str] = set()
        for _, record in self._sections:
            instrument = record["instrument"].strip()
            key = instrument_key(instrument)
            if key not in seen:
                seen.add(key)
                entries.append(SectionMapEntry(instrument, stored_section(instrument, club), len(pieces.get(key, ()))))
        entries += [SectionMapEntry(names[key], "", len(used)) for key, used in pieces.items() if key not in seen]
        return entries

    def _instrument_pieces(self) -> Tuple[Dict[str, str], Dict[str, Set[str]]]:
        """分譜工作表用到的每種樂器（instrument_key）：第一次出現時去掉編號的寫法，與用到它的曲目 ID（總譜列不算）"""
        pieces: Dict[str, Set[str]] = {}
        names: Dict[str, str] = {}
        for _, part in self._parts:
            edition = self.editions.get(part.edition_id)
            if edition is None or _is_true(part.is_score) or not part.instrument_name.strip():
                continue
            key = instrument_key(part.instrument_name)
            names.setdefault(key, instrument_name(part.instrument_name))
            pieces.setdefault(key, set()).add(edition.piece_id)
        return names, pieces

    def write_place(self, batch: "_Batch", place: PlaceReview, written_sections: Set[str]) -> List[ItemReview]:
        """把一處要寫的格子放進 batch，回傳真的寫了的格子（每格一列編制寫入紀錄）

        Args:
            batch: 要送出的格子
            place: 這處的比對結果（沒有待選邊的衝突）
            written_sections: 這次已經寫過的樂器（instrument_key）；同一樂器在別的群組又出現時不再寫
        """
        write = place.write
        written = []
        parts = {voice_key(part.instrument_name): (row, part) for row, part in
                 self._voice_rows(write.edition_id, write.movement_id)}
        headcounts = {
            voice_key(r.item.voice): r.item.new for r in place.items if r.item.field is SyncField.HEADCOUNT
        }
        writing = [r for r in place.items if r.verdict is Verdict.WRITE]
        created = []
        for review in writing:
            item = review.item
            key = voice_key(item.voice)
            if item.field is SyncField.VOICE and not item.new:
                batch.clear(SHEET_PARTS, parts[key][0])
            elif item.field in (SyncField.VOICE, SyncField.HEADCOUNT) and key not in parts:
                created.append(key)
            elif item.field is SyncField.HEADCOUNT:
                row, part = parts[key]
                for column, value in _headcount_cells(item.new).items():
                    if getattr(part, column).strip() != value:
                        batch.set(SHEET_PARTS, row, column, value)
            elif item.field is SyncField.DURATION:
                self._write_duration(batch, write, item.new)
            elif item.field is SyncField.SECTION:
                if instrument_key(item.voice) in written_sections:
                    continue
                written_sections.add(instrument_key(item.voice))
                self._write_section(batch, item.voice, item.new)
            written.append(review)
        renumber = bool(created) or any(r.item.field is SyncField.ORDER for r in writing)
        if renumber:
            for order, name in enumerate(final_voices(write, place.items), start=1):
                key = voice_key(name)
                if key in created:
                    batch.add(SHEET_PARTS, {
                        "edition_id": write.edition_id, "movement_id": write.movement_id, "instrument_name": name,
                        "sort_order": str(order), **_headcount_cells(headcounts.get(key, Headcount())),
                    })
                elif key in parts and parts[key][1].sort_order.strip() != str(order):
                    batch.set(SHEET_PARTS, parts[key][0], "sort_order", str(order))
        return written

    def _write_duration(self, batch: "_Batch", write: PlaceWrite, seconds: Optional[int]) -> None:
        """時長：這處已有一列就改秒數，沒有就新增一列"""
        value = str(seconds) if seconds else ""
        existing = self._duration_row(write.edition_id, write.movement_id)
        if existing is not None:
            batch.set(SHEET_DURATIONS, existing[0], "seconds", value)
        else:
            batch.add(SHEET_DURATIONS, {
                "edition_id": write.edition_id, "movement_id": write.movement_id, "seconds": value,
            })

    def _write_section(self, batch: "_Batch", voice: str, section: str) -> None:
        """聲部組：社團對照已有這種樂器（同一樂器）就改它的值（樂器的寫法不變），沒有就以去掉編號的樂器名稱新增"""
        key = instrument_key(voice)
        existing = next((row for row, record in self._sections if instrument_key(record["instrument"]) == key), None)
        if existing is not None:
            batch.set(SHEET_SECTIONS, existing, "section", section)
        else:
            batch.add(SHEET_SECTIONS, {
                "orchestra_type": self._orchestra_type.value, "instrument": instrument_name(voice), "section": section,
            })

    def _voice_rows(self, edition_id: str, movement_id: str) -> List[Tuple[int, Part]]:
        """一處認得的聲部列與列號"""
        return _voice_parts(_at(self._parts, edition_id, movement_id))

    def _score_row(self, edition_id: str, movement_id: str) -> Optional[Tuple[int, Part]]:
        """一處的總譜列與列號；有幾列時只認排在前面那一列"""
        return _score_part(_at(self._parts, edition_id, movement_id))

    def _duration_row(self, edition_id: str, movement_id: str) -> Optional[Tuple[int, Dict[str, str]]]:
        """一處的時長列與列號；有幾列時只認第一列"""
        return next((
            (row, record) for row, record in self._durations
            if record["edition_id"] == edition_id and record["movement_id"] == movement_id
        ), None)


def _log_record(
    stamp: str, operator: str, source: WriteSource, write: PlaceWrite, review: ItemReview,
) -> Dict[str, str]:
    """編制寫入紀錄的一列：一格改動的原值（寫入前譜庫的值）與新值"""
    item = review.item
    if item.field is SyncField.VOICE:
        old, new = (item.voice if review.theirs else ""), (item.voice if item.new else "")
    else:
        old, new = value_text(item.field, review.theirs), value_text(item.field, item.new)
    return {
        "time": stamp, "operator": operator, "source": source.value, "edition_id": write.edition_id,
        "movement_id": write.movement_id, "voice": item.voice, "field": item.field.value, "old_value": old,
        "new_value": new, "group": write.group_name, "group_id": write.group_id if write.edition_id else "",
    }


class PendingRows:
    """要新增到一張工作表的一批列

    ID 在第一次送出前由 CatalogService.add_rows 抽好，之後不變：送出失敗時以同一個物件重送，
    已經寫進去的列就認得出來、不再新增。
    """

    def __init__(self, sheet: str, records: Sequence[Mapping[str, str]]):
        """
        Args:
            sheet: 工作表名稱
            records: 每一列的欄位值（欄位名稱見 SHEET_HEADERS），不含 id
        """
        self.sheet = sheet
        self._records: List[Dict[str, str]] = [dict(record) for record in records]
        self._ids: List[str] = []
        self._sent = False

    @property
    def ids(self) -> List[str]:
        """各列的 ID（還沒送出過時為空清單）"""
        return list(self._ids)

    def to_send(self, existing: Set[str], draw: Callable[[Set[str], int], List[str]]) -> List[List[str]]:
        """這一次要送出的列（依 SHEET_HEADERS 的欄位排好的值）

        第一次送出前以 draw 抽 ID（不跟 existing 撞號）；之後只回傳 ID 不在 existing 的列。

        Args:
            existing: 工作表目前所有的 ID
            draw: 抽 ID 的函式，參數為不可用的 ID 與要抽幾個

        Returns:
            要新增的列；全部都已經在工作表裡時為空清單
        """
        if not self._sent:
            self._ids = draw(existing, len(self._records))
        headers = SHEET_HEADERS[self.sheet]
        return [
            [record_id if header == "id" else str(record.get(header, "")) for header in headers]
            for record_id, record in zip(self._ids, self._records)
            if record_id not in existing
        ]

    def mark_sent(self) -> None:
        """記下已經送出過（之後重送時以 ID 認出已寫進去的列）"""
        self._sent = True


class PendingCatalog:
    """要建立的譜庫總表：建好後記下 ID，CatalogService.create_catalog 重送時沿用，不建第二份"""

    def __init__(self, name: str, folder_id: str, locale: str):
        """
        Args:
            name: 試算表名稱
            folder_id: 譜庫資料夾的 Drive ID
            locale: 標題列的語言（建立時的介面語言）
        """
        self.name = name
        self.folder_id = folder_id
        self.locale = locale
        self.spreadsheet_id = ""


class PendingPlace:
    """要在譜庫新建的一處：還沒有的曲目（連同作曲家）、版本（連同 Drive 資料夾）與樂章

    已有的部分給 ID，其餘依曲名、作曲家、樂章編號與名稱新建。CatalogService.create_place 建好一部分就記下來：
    之後中途失敗時以同一個物件重送，只補上缺的，不產生重複的列與資料夾（版本資料夾以預先要到的 ID 建立，
    回應沒收到而其實已建好時重送取那一個）。
    """

    def __init__(
        self, title: str, composer: str = "", root_folder_id: str = "", piece_id: str = "", edition_id: str = "",
        movement_id: str = "", new_movement: bool = False, movement_number: str = "", movement_name: str = "",
    ):
        """
        Args:
            title: 曲名（新建曲目的曲名，也是新版本資料夾的名稱）
            composer: 作曲家（新建曲目時以名稱找已有的作曲家，沒有就新增）；空字串表示不填
            root_folder_id: 譜庫根資料夾的 Drive ID（新版本的資料夾建在這裡）
            piece_id: 已有的曲目 ID；空字串表示新建
            edition_id: 已有的版本 ID；空字串表示新建
            movement_id: 已有的樂章 ID；空字串表示整首或新建（見 new_movement）
            new_movement: 是否新建樂章（否則 movement_id 空白就是整首）
            movement_number: 新樂章的編號；空字串時接在已有的樂章之後
            movement_name: 新樂章的名稱
        """
        self.title = title
        self.composer = composer
        self.root_folder_id = root_folder_id
        self.piece_id = piece_id
        self.edition_id = edition_id
        self.movement_id = movement_id
        self.new_movement = new_movement and not movement_id
        self.movement_number = movement_number
        self.movement_name = movement_name
        self.folder_id = ""
        # 新版本資料夾預先要到的 ID：回應沒收到而重送時 Drive 回已經存在，取那一個，不建第二個
        self._folder_file_id = ""
        self.composer_id = ""
        self._composer_rows: Optional[PendingRows] = None
        self._piece_rows: Optional[PendingRows] = None
        self._edition_rows: Optional[PendingRows] = None
        self._movement_rows: Optional[PendingRows] = None
        self._activate_edition = False


class PendingRegistration:
    """要登記到一處的檔：聲部各一份，總譜列一份；版本頁按儲存時送出

    CatalogService.register_files 寫入登記時記下被換掉的舊檔，丟進垃圾桶一份就拿掉一份：之後中途失敗時以同一個物件
    重送，已寫好的登記不再寫，還沒丟的舊檔接著丟。
    """

    def __init__(
        self, edition_id: str, movement_id: str, voices: Mapping[str, DriveFile], score: Optional[DriveFile] = None,
    ):
        """
        Args:
            edition_id: 版本 ID
            movement_id: 樂章 ID；整首為空字串
            voices: 聲部（譜庫的寫法）與要登記給它的檔
            score: 要登記給總譜列的檔；不登記時為 None
        """
        self.edition_id = edition_id
        self.movement_id = movement_id
        self.voices: Dict[str, DriveFile] = dict(voices)
        self.score = score
        # 被換掉、還沒丟進垃圾桶的舊檔
        self.replaced: List[str] = []


class CatalogService:
    """譜庫的讀寫：經試算表與 Drive 兩個關口，失敗時丟出 CatalogError"""

    def __init__(
        self,
        sheets: SheetsPort,
        drive: DrivePort,
        sleep: Callable[[float], None] = time.sleep,
        rate_limit_retries: int = DEFAULT_RATE_LIMIT_RETRIES,
        first_wait: float = DEFAULT_FIRST_WAIT_SECONDS,
        new_id: Callable[[], str] = _random_id,
    ):
        """
        Args:
            sheets: 試算表關口
            drive: Drive 關口
            sleep: 遇到 429 時等待的函式（秒）；測試傳入只記錄的替身
            rate_limit_retries: 遇到 429 時最多自動重試幾次
            first_wait: 第一次重試前等幾秒，之後每次加倍
            new_id: 抽新 ID 的函式；測試傳入會撞號的替身
        """
        self._sheets = sheets
        self._drive = drive
        self._sleep = sleep
        self._rate_limit_retries = rate_limit_retries
        self._first_wait = first_wait
        self._new_id = new_id

    # --- 讀取 ---

    def overview(self, root_folder_id: str) -> CatalogOverview:
        """讀譜庫視窗開啟時要的全部：曲目、作曲家、哪個資料夾屬於哪首曲目、根資料夾的內容，與用過的聲部名稱

        試算表只讀一次（分譜工作表一起讀，聲部名稱不另外發請求），請求數不隨曲目數增加。

        Args:
            root_folder_id: 譜庫根資料夾的 Drive ID

        Returns:
            譜庫總覽

        Raises:
            CatalogError: 讀不到；沒設定根資料夾時為找不到
        """
        if not root_folder_id:
            raise CatalogError(CatalogErrorKind.NOT_FOUND, f"{CATALOG_ROOT_FOLDER_ID_KEY} is empty")
        pieces_rows, composer_rows, edition_rows, part_rows = self._read_sheets(
            SHEET_PIECES, SHEET_COMPOSERS, SHEET_EDITIONS, SHEET_PARTS,
        )
        pieces = _models(pieces_rows, SHEET_PIECES, Piece)
        pieces_by_id = {piece.id: piece for piece in pieces}
        folder_pieces = {
            edition.drive_folder_id: pieces_by_id[edition.piece_id]
            for edition in _models(edition_rows, SHEET_EDITIONS, Edition)
            if edition.drive_folder_id and edition.piece_id in pieces_by_id
        }
        return CatalogOverview(
            pieces=pieces,
            composers=_models(composer_rows, SHEET_COMPOSERS, Composer),
            folder_pieces=folder_pieces,
            root=self.folder_contents(root_folder_id),
            voice_names=_used_voice_names(part_rows),
        )

    def folder_contents(self, folder_id: str) -> FolderContents:
        """列出 Drive 資料夾裡的子資料夾與 PDF（各依名稱排序，不含垃圾桶裡的）

        Raises:
            CatalogError: 讀不到
        """
        children = self._children(folder_id)
        return FolderContents(
            folders=[f for f in children if f.mime_type == DRIVE_FOLDER_MIME],
            pdfs=[f for f in children if f.mime_type == DRIVE_PDF_MIME],
        )

    def piece_detail(self, piece_id: str) -> Optional[PieceDetail]:
        """讀一首曲目的完整資訊（作曲家、版本、樂章、分譜、演出紀錄），一次請求

        Args:
            piece_id: 曲目 ID

        Returns:
            曲目的完整資訊；譜庫裡沒有這首曲目時為 None

        Raises:
            CatalogError: 讀不到
        """
        piece_rows, composer_rows, edition_rows, movement_rows, part_rows, performance_rows = self._read_sheets(
            SHEET_PIECES, SHEET_COMPOSERS, SHEET_EDITIONS, SHEET_MOVEMENTS, SHEET_PARTS, SHEET_PERFORMANCES,
        )
        piece = next((p for p in _models(piece_rows, SHEET_PIECES, Piece) if p.id == piece_id), None)
        if piece is None:
            return None
        composer = next(
            (c for c in _models(composer_rows, SHEET_COMPOSERS, Composer) if c.id and c.id == piece.composer_id), None,
        )
        editions = [e for e in _models(edition_rows, SHEET_EDITIONS, Edition) if e.piece_id == piece_id]
        edition_ids = {e.id for e in editions}
        return PieceDetail(
            piece=piece,
            composer=composer,
            editions=editions,
            movements=[m for m in _models(movement_rows, SHEET_MOVEMENTS, Movement) if m.piece_id == piece_id],
            parts=[p for p in _models(part_rows, SHEET_PARTS, Part) if p.edition_id in edition_ids],
            performances=[
                p for p in _models(performance_rows, SHEET_PERFORMANCES, Performance) if p.piece_id == piece_id
            ],
        )

    def edition_files(self, folder_id: str) -> EditionFiles:
        """列出版本資料夾裡的 PDF：直接放在資料夾裡的，以及每個子資料夾裡的

        Args:
            folder_id: 版本資料夾的 Drive ID

        Returns:
            版本資料夾的檔

        Raises:
            CatalogError: 讀不到
        """
        top = self.folder_contents(folder_id)
        return EditionFiles(
            root_pdfs=top.pdfs,
            subfolders=[(folder, self.folder_contents(folder.id).pdfs) for folder in top.folders],
        )

    def find_by_name(self, folder_id: str, name: str) -> List[DriveFile]:
        """在 Drive 資料夾裡以名稱找檔案或資料夾（不含垃圾桶裡的）

        Args:
            folder_id: 資料夾的 Drive ID
            name: 完整的名稱

        Returns:
            名稱相同的檔案

        Raises:
            CatalogError: 讀不到
        """
        return self._children(folder_id, f"name = {drive_literal(name)}")

    def publishing_platform(self) -> Optional[PublishingPlatform]:
        """讀社團設定裡啟用的發譜平台

        先列出工作表名稱確認有社團設定（讀不存在的工作表會讓請求失敗），有才讀它，最多兩次請求。
        只決定發譜功能顯示與否，不是權限。

        Returns:
            「發譜平台」一列寫的平台；沒有這張工作表、沒有這一列或寫的是不認得的平台時為 None

        Raises:
            CatalogError: 讀不到
        """
        if SHEET_CLUB_SETTINGS not in self._call(self._sheets.sheet_titles):
            return None
        rows = self._call(lambda: self._sheets.read_ranges([sheet_range(SHEET_CLUB_SETTINGS, "A:B")]))[0]
        for row in rows:
            if row and row[0].strip() == CLUB_SETTING_PUBLISHING_PLATFORM:
                value = row[1].strip() if len(row) > 1 else ""
                return PUBLISHING_PLATFORM_NAMES.get(value)
        return None

    def link_targets(self) -> LinkTargets:
        """讀群組連線時可以挑的曲目、版本與樂章，連同用過的聲部名稱（一次請求）

        Raises:
            CatalogError: 讀不到
        """
        piece_rows, edition_rows, movement_rows, part_rows = self._read_sheets(
            SHEET_PIECES, SHEET_EDITIONS, SHEET_MOVEMENTS, SHEET_PARTS,
        )
        return LinkTargets(
            pieces=_models(piece_rows, SHEET_PIECES, Piece),
            editions=_models(edition_rows, SHEET_EDITIONS, Edition),
            movements=_models(movement_rows, SHEET_MOVEMENTS, Movement),
            voice_names=_used_voice_names(part_rows),
        )

    def read_place(self, edition_id: str, movement_id: str, orchestra_type: OrchestraType) -> CatalogPlace:
        """讀譜庫裡的一處（版本的整首或一個樂章）：編制、時長，連同社團的聲部組對照（兩次請求）

        先列出工作表名稱，時長與聲部組對照這兩張還沒建立時當成空的；分譜工作表沒有人數欄時人數未填。

        Args:
            edition_id: 版本 ID
            movement_id: 樂章 ID；整首為空字串
            orchestra_type: 專案的樂團類型（聲部組對照只取這一套）

        Returns:
            這一處

        Raises:
            CatalogError: 讀不到；版本或樂章已不在譜庫時為已經不在譜庫
        """
        return self._place(self._place_tables(orchestra_type), edition_id, movement_id)

    def read_places(
        self, places: Sequence[Tuple[str, str]], orchestra_type: OrchestraType,
    ) -> List[Optional[CatalogPlace]]:
        """讀譜庫裡的幾處（開啟專案時各群組連到的那處），內容同 read_place；請求數同樣是兩次，不隨處數增加

        一處已不在譜庫時只有那一處是 None，其他處照常讀出。

        Args:
            places: 每一處的（版本 ID、樂章 ID；整首為空字串）
            orchestra_type: 專案的樂團類型（聲部組對照只取這一套）

        Returns:
            依 places 順序的各處；版本或樂章已不在譜庫的那處為 None

        Raises:
            CatalogError: 讀不到
        """
        catalog = self._place_tables(orchestra_type)
        return [self._place_if_present(catalog, edition_id, movement_id) for edition_id, movement_id in places]

    def edition_page(self, edition_id: str, movement_id: str, orchestra_type: OrchestraType) -> EditionPage:
        """讀譜庫視窗的版本頁：一處的編制、各聲部與總譜列登記的檔，以及版本資料夾裡全部與沒登記的檔

        試算表兩次請求（同 read_place），再逐層列出版本資料夾與它的子資料夾。沒登記的檔是沒被任何分譜列
        （不分版本與樂章）登記的 PDF；版本沒有 Drive 資料夾時沒有。

        Args:
            edition_id: 版本 ID
            movement_id: 樂章 ID；整首為空字串
            orchestra_type: 樂團類型（聲部組對照只取這一套）

        Returns:
            版本頁

        Raises:
            CatalogError: 讀不到；版本或樂章已不在譜庫時為已經不在譜庫
        """
        catalog = self._place_tables(orchestra_type)
        place = self._place(catalog, edition_id, movement_id)
        registered = catalog.registered_file_ids()
        folder_id = catalog.editions[edition_id].drive_folder_id
        pdfs = self._pdfs_under(folder_id) if folder_id else []
        return EditionPage(
            place=place,
            registrations=catalog.registrations(edition_id, movement_id),
            score=catalog.score(edition_id, movement_id),
            unregistered=[pdf for pdf in pdfs if pdf.file.id not in registered],
            pdfs=pdfs,
        )

    def _place_tables(self, orchestra_type: OrchestraType) -> "_PlaceTables":
        """讀一處要的各工作表（列出工作表名稱、讀取各一次請求）；時長與聲部組對照還沒建立時當成空的"""
        return _PlaceTables(self._read_named(_place_sheets(self._call(self._sheets.sheet_titles))), orchestra_type)

    def _pdfs_under(self, folder_id: str) -> List[FolderPdf]:
        """資料夾與它所有子資料夾裡的 PDF，連同在資料夾裡的路徑，依路徑排序（每個資料夾一次列出）"""
        return sorted((
            FolderPdf(pdf, "/".join((*path, pdf.name))) for path, _, pdfs in self._folder_tree(folder_id) for pdf in pdfs
        ), key=lambda pdf: pdf.path)

    @staticmethod
    def _place(catalog: "_PlaceTables", edition_id: str, movement_id: str) -> CatalogPlace:
        """從讀到的各工作表組出一處"""
        current = catalog.current(edition_id, movement_id)
        edition = catalog.editions[edition_id]
        movement = catalog.movements.get(movement_id)
        piece = catalog.pieces.get(edition.piece_id, Piece())
        return CatalogPlace(
            edition_id=edition_id,
            movement_id=movement_id,
            piece_title=piece.title,
            edition_label=edition.edition_label or edition.publisher,
            movement_name=(movement.name or movement.movement_number) if movement else "",
            voices=current.voices,
            duration_seconds=current.duration_seconds,
            sections=current.sections,
            filed=current.filed,
            other_pieces=catalog.other_pieces(edition_id),
        )

    @classmethod
    def _place_if_present(cls, catalog: "_PlaceTables", edition_id: str, movement_id: str) -> Optional[CatalogPlace]:
        """從讀到的各工作表組出一處；版本或樂章已不在譜庫時為 None"""
        try:
            return cls._place(catalog, edition_id, movement_id)
        except CatalogError as error:
            if error.kind is not CatalogErrorKind.GONE:
                raise
            return None

    def revert_rows(
        self, edition_id: str, movement_id: str, group_id: str, group_name: str, orchestra_type: OrchestraType,
    ) -> List[RevertRow]:
        """讀出一個群組寫進一處的編制，分成撤掉後刪掉、改回原值與不動（改連時問要不要撤掉；兩次請求）

        判斷見 core/catalog_revert.py 的 revert_rows：依編制寫入紀錄，之後沒人改過的才撤得掉。

        Args:
            edition_id: 那處的版本 ID
            movement_id: 那處的樂章 ID；整首為空字串
            group_id: 群組 ID（編制寫入紀錄的群組 ID 欄）
            group_name: 群組名稱（編制寫入紀錄的「群組」欄；沒有群組 ID 的舊列以它認）
            orchestra_type: 專案的樂團類型

        Returns:
            每個這個群組寫過的聲部一列；還沒有編制寫入紀錄、或那處已不在譜庫時為空清單

        Raises:
            CatalogError: 讀不到
        """
        titles = self._call(self._sheets.sheet_titles)
        if SHEET_INSTRUMENTATION_LOG not in titles:
            return []
        tables = self._read_named(_place_sheets(titles) + [SHEET_INSTRUMENTATION_LOG])
        try:
            current = _PlaceTables(tables, orchestra_type).current(edition_id, movement_id)
        except CatalogError as error:
            if error.kind is CatalogErrorKind.GONE:
                return []
            raise
        entries = _models(tables[SHEET_INSTRUMENTATION_LOG], SHEET_INSTRUMENTATION_LOG, InstrumentationLogEntry)
        return revert_rows(entries, edition_id, movement_id, current.voices, current.filed, group_id, group_name)

    def section_map(self) -> Dict[OrchestraType, List[SectionMapEntry]]:
        """讀全社團的聲部組對照，每個樂團類型一套，連同譜庫裡用到、還沒歸類的樂器與各樂器用到幾首（兩次請求）

        Returns:
            樂團類型 → 這一套的樂器（見 _PlaceTables.section_entries）

        Raises:
            CatalogError: 讀不到
        """
        tables = self._read_named(_place_sheets(self._call(self._sheets.sheet_titles)))
        return {orchestra: _PlaceTables(tables, orchestra).section_entries() for orchestra in OrchestraType}

    def sync_instrumentation(
        self, writes: Sequence[PlaceWrite], orchestra_type: OrchestraType, operator: str, source: WriteSource,
        decisions: Mapping[str, Mapping[Tuple[SyncField, object], Decision]], review_only: bool = False,
    ) -> SyncOutcome:
        """把群組的編制改動寫回譜庫：寫入前一刻重讀、逐格比對，沒有待選邊的衝突才以一個請求寫入

        每一格依譜庫現在的值處理（core/catalog_sync.py 的 review_items）：等於快照的原值就寫、等於要寫的新值就略過、
        兩者都不是就是衝突；有衝突（或選邊時看到的譜庫值之後又變了）時什麼都不寫，交回比對結果讓使用者選邊後再呼叫。
        要刪掉的聲部在譜庫已有 Drive 檔案時不寫（無法寫回），其餘照寫。
        改連後的撤掉（PlaceWrite.revert）一起寫：譜庫已不是那次寫的值的格子不動、不算衝突，那處已不在譜庫時整處不動，
        編制寫入紀錄的來源記撤銷。

        寫入的格子、新增的列、刪掉的聲部（清空那一列）、缺的標題與每一格一列的編制寫入紀錄，全部以同一個
        values.batchUpdate 送出（全成或全不成）。請求數固定：列出工作表名稱、讀取各一次，還沒建立的時長、
        聲部組對照、編制寫入紀錄每張建一次，寫入一次；不隨聲部數或群組數增加。

        Args:
            writes: 各群組要寫回的改動（core/catalog_sync.py 的 place_writes）
            orchestra_type: 專案的樂團類型（聲部組對照寫這一套）
            operator: 操作者（登入的 email），寫進編制寫入紀錄
            source: 來源，寫進編制寫入紀錄
            decisions: 衝突的選擇：群組 ID → （SyncItem.key → Decision）
            review_only: 只比對、不寫（寫入前先讓使用者在衝突畫面確認全部改動時）

        Returns:
            比對結果，與是否已寫入

        Raises:
            CatalogError: 讀不到或寫不進去（沒有任何一格被寫入，以同樣的參數再呼叫一次即可）；
                連到的版本或樂章已不在譜庫時為已經不在譜庫
        """
        return self._sync([(write, orchestra_type) for write in writes], operator, source, decisions, review_only)

    def sync_section_map(
        self, items: Mapping[OrchestraType, Sequence[SyncItem]], operator: str,
        decisions: Mapping[str, Mapping[Tuple[SyncField, object], Decision]],
    ) -> SyncOutcome:
        """把譜庫視窗改過的聲部組對照寫回譜庫：與存檔寫回同一套比對與寫入（見 sync_instrumentation）

        每個樂團類型是一處（PlaceWrite 的 group_id 為樂團類型的值、沒有版本），只比對社團對照；
        樂器已在對照裡就只改聲部組（寫法不變），沒有就新增一列。編制寫入紀錄的來源記譜庫視窗。

        Args:
            items: 樂團類型 → 改過的樂器（core/catalog_sync.py 的 section_map_items）
            operator: 操作者（登入的 email），寫進編制寫入紀錄
            decisions: 衝突的選擇：樂團類型的值 → （SyncItem.key → Decision）

        Returns:
            比對結果，與是否已寫入

        Raises:
            CatalogError: 讀不到或寫不進去（沒有任何一格被寫入，以同樣的參數再呼叫一次即可）
        """
        writes = [
            (PlaceWrite(orchestra.value, "", "", "", [], list(changed)), orchestra)
            for orchestra, changed in items.items() if changed
        ]
        return self._sync(writes, operator, WriteSource.CATALOG_WINDOW, decisions)

    def _sync(
        self, writes: Sequence[Tuple[PlaceWrite, OrchestraType]], operator: str, source: WriteSource,
        decisions: Mapping[str, Mapping[Tuple[SyncField, object], Decision]], review_only: bool = False,
    ) -> SyncOutcome:
        """寫入前一刻重讀、逐格比對，沒有待選邊的衝突才以一個請求寫入（sync_instrumentation、sync_section_map 共用）

        Args:
            writes: 各處要寫的改動與它的樂團類型；沒有版本 ID 的只比對、寫入社團對照
            operator: 操作者，寫進編制寫入紀錄
            source: 來源，寫進編制寫入紀錄
            decisions: 衝突的選擇：PlaceWrite.group_id → （SyncItem.key → Decision）
            review_only: 只比對、不寫
        """
        titles, tables, _, locale = self._read_with_log(_place_sheets, SHEET_INSTRUMENTATION_LOG, "A:A")
        catalogs = {orchestra: _PlaceTables(tables, orchestra) for orchestra in {o for _, o in writes}}
        places = []
        for write, orchestra in writes:
            catalog = catalogs[orchestra]
            try:
                current = catalog.current(write.edition_id, write.movement_id) if write.edition_id else catalog.club()
            except CatalogError as error:
                if not (write.revert and error.kind is CatalogErrorKind.GONE):
                    raise
                # 要撤掉的那處已不在譜庫：沒有可撤的，整處不動
                places.append(PlaceReview(write, [ItemReview(item, None, Verdict.KEPT) for item in write.items]))
                continue
            reviews = review_items(write, current, dict(decisions.get(write.group_id, {})))
            places.append(PlaceReview(write, reviews, list(current.filed), catalog.other_pieces(write.edition_id)))
        outcome = SyncOutcome(places, written=False)
        if outcome.conflicts or review_only:
            return outcome
        batch = _Batch(tables, self._draw_ids, locale)
        stamp = now_text()
        written_sections: Dict[OrchestraType, Set[str]] = {orchestra: set() for orchestra in catalogs}
        for place, (_, orchestra) in zip(places, writes):
            logged = WriteSource.UNDO if place.write.revert else source
            for review in catalogs[orchestra].write_place(batch, place, written_sections[orchestra]):
                batch.append(SHEET_INSTRUMENTATION_LOG, _log_record(stamp, operator, logged, place.write, review))
        self._send(batch, titles)
        outcome.written = True
        return outcome

    def place_files(self, places: Sequence[Tuple[str, str]]) -> Dict[Tuple[str, str], PlaceFiles]:
        """讀各處（版本、樂章）的聲部與已登記的檔，以及版本資料夾裡的子資料夾與 PDF（存入、清除本機檔案、下載回原位共用）

        試算表只讀一次，請求數不隨聲部數增加；Drive 每個版本資料夾與底下的每個子資料夾各列一次，
        登記的檔不在版本資料夾裡時另取一次中繼資料。登記的檔已不在 Drive 或在垃圾桶裡時當成沒登記。

        Args:
            places: （版本 ID，樂章 ID；整首為空字串）

        Returns:
            每一處的樣子；版本或樂章已不在譜庫的那處不在裡面（其他處照常）

        Raises:
            CatalogError: 讀不到
        """
        edition_rows, movement_rows, part_rows = self._read_sheets(SHEET_EDITIONS, SHEET_MOVEMENTS, SHEET_PARTS)
        editions = {edition.id: edition for edition in _models(edition_rows, SHEET_EDITIONS, Edition)}
        movements = {movement.id for movement in _models(movement_rows, SHEET_MOVEMENTS, Movement)}
        parts = _numbered(part_rows, SHEET_PARTS, Part)
        claimed = frozenset(part.drive_file_id.strip() for _, part in parts if part.drive_file_id.strip())
        trees: Dict[str, Tuple[Dict[Tuple[str, ...], str], Dict[Tuple[str, ...], List[DriveFile]]]] = {}
        # 版本資料夾的樹：子資料夾以比對鍵（folder_key）認，同名（不分大小寫）的只認先列到的那個
        result = {}
        for edition_id, movement_id in places:
            if edition_id not in editions or (movement_id and movement_id not in movements):
                continue
            folder_id = editions[edition_id].drive_folder_id.strip()
            if folder_id and folder_id not in trees:
                folders_by_key: Dict[Tuple[str, ...], str] = {}
                pdfs_by_key: Dict[Tuple[str, ...], List[DriveFile]] = {}
                for path, current, pdfs in self._folder_tree(folder_id):
                    if folder_key(path) not in folders_by_key:
                        folders_by_key[folder_key(path)] = current
                        pdfs_by_key[folder_key(path)] = pdfs
                trees[folder_id] = (folders_by_key, pdfs_by_key)
            folders, files = trees.get(folder_id, ({}, {}))
            here = _at(parts, edition_id, movement_id)
            voices = _voice_parts(here)
            score = _score_part(here)
            listed = [(part.instrument_name, part) for _, part in voices] + ([(SCORE_VOICE, score[1])] if score else [])
            in_tree = {file.id: file for pdfs in files.values() for file in pdfs}
            registered = []
            for voice, part in listed:
                file_id = part.drive_file_id.strip()
                file = in_tree.get(file_id) or (self._live_file(file_id) if file_id else None)
                if file is not None:
                    registered.append((voice, file))
            result[(edition_id, movement_id)] = PlaceFiles(
                edition_id, movement_id, folder_id, [part.instrument_name for _, part in voices], registered,
                folders, files, claimed,
            )
        return result

    def begin_deposit(self, planned: Sequence[DepositRecord], operator: str) -> str:
        """存入動 Drive 之前先寫存入紀錄：每份要做的檔一列「要做」（DEPOSIT_PENDING），一個請求寫入，回傳這次的存入編號

        之後登記寫不進去（例如斷網）時，蓋掉的內容照樣有紀錄可撤銷：撤銷時只有「要做」列的檔照 Drive 現況判斷
        那次做到了沒有（core/deposit_revert.py）。請求數固定：列出工作表名稱、讀取、寫入各一次，
        存入紀錄還沒建立時另建一次。

        Args:
            planned: 要做的每份檔（drive_file 是要動的那份；新上傳是預先要到的 ID，revision_before 是被蓋掉的版本）
            operator: 操作者（登入的 email），寫進存入紀錄

        Returns:
            新抽的存入編號；做完以它呼叫 record_deposits

        Raises:
            CatalogError: 讀不到或寫不進去（沒有任何一列被寫入）；這時還沒動 Drive
        """
        titles, tables, log, locale = self._read_with_log(lambda _: [], SHEET_DEPOSIT_LOG, "A:B")
        batch = _Batch(tables, self._draw_ids, locale)
        deposit_id = self._draw_ids({row[1] for row in log if len(row) > 1}, 1)[0]
        stamp = now_text()
        for record in planned:
            batch.append(SHEET_DEPOSIT_LOG, _deposit_row(deposit_id, stamp, operator, record, "", DEPOSIT_PENDING))
        self._send(batch, titles)
        return deposit_id

    def record_deposits(self, deposit_id: str, records: Sequence[DepositRecord], operator: str) -> None:
        """存入後登記分譜列並寫存入紀錄：寫入前一刻才讀分譜工作表找列號，全部以一個請求寫入

        每份檔的分譜列（總譜為那處的總譜列，還沒有就新增一列，與 register_files 相同）改登記成存入後的 Drive 檔案與檔名，
        只送值變了的格子，被換掉的舊檔不動（新上傳只在原本登記的檔已不在 Drive 或在垃圾桶裡時發生）；
        聲部的列已被刪掉時不登記。每份檔一列做了的存入紀錄，存入編號是 begin_deposit 抽的。請求數固定：
        列出工作表名稱、讀取、寫入各一次。

        Args:
            deposit_id: begin_deposit 交回的存入編號
            records: 存入了的每份檔
            operator: 操作者（登入的 email），寫進存入紀錄

        Raises:
            CatalogError: 讀不到或寫不進去（沒有任何一格被寫入）
        """
        titles, tables, _, locale = self._read_with_log(lambda _: [SHEET_PARTS], SHEET_DEPOSIT_LOG, "A:A")
        batch = _Batch(tables, self._draw_ids, locale)
        parts = _numbered(tables[SHEET_PARTS], SHEET_PARTS, Part)
        stamp = now_text()
        added_scores: Set[Tuple[str, str]] = set()
        for record in records:
            file = record.drive_file
            here = _at(parts, record.edition_id, record.movement_id)
            if record.voice == SCORE_VOICE:
                target = _score_part(here)
            else:
                key = voice_key(record.voice)
                target = next((pair for pair in _voice_parts(here) if voice_key(pair[1].instrument_name) == key), None)
            if target is not None:
                _register_row(batch, target, file)
            elif record.voice == SCORE_VOICE and (record.edition_id, record.movement_id) not in added_scores:
                added_scores.add((record.edition_id, record.movement_id))
                _add_score_row(batch, record.edition_id, record.movement_id, file)
            batch.append(SHEET_DEPOSIT_LOG, _deposit_row(deposit_id, stamp, operator, record, file.head_revision, ""))
        self._send(batch, titles)

    def drive_ids(self, count: int) -> List[str]:
        """預先向 Drive 要 count 個檔案 ID；以它們建檔，重送時不會建出第二份

        Drive 一次最多給 DRIVE_IDS_PER_REQUEST 個，多的分幾次要（每次一個請求）。

        Raises:
            CatalogError: 要不到
        """
        ids: List[str] = []
        while len(ids) < count:
            wanted = min(DRIVE_IDS_PER_REQUEST, count - len(ids))
            ids += self._call(lambda: self._drive.generate_ids(wanted))
        return ids

    def upload_file(self, file_id: str, name: str, folder_id: str, content: bytes) -> DriveFile:
        """以預先要到的 ID 把 PDF 上傳到 Drive 資料夾；上一次其實已經上傳了（Drive 回已經存在）時取那一份

        Args:
            file_id: drive_ids 要到的 ID
            name: 檔名
            folder_id: 資料夾的 Drive ID
            content: 內容

        Returns:
            上傳後的檔案

        Raises:
            CatalogError: 上傳失敗；以同一個 ID 再呼叫一次不會產生第二份
        """
        try:
            return self._call(lambda: self._drive.upload(file_id, name, folder_id, content))
        except CatalogError as error:
            if error.kind is not CatalogErrorKind.EXISTS:
                raise
            return self._call(lambda: self._drive.get_file(file_id))

    def update_file(self, file: DriveFile, content: bytes, name: str, folder_id: str) -> DriveFile:
        """換掉 Drive 上一個檔的內容（檔案 ID 不變，舊內容成為版本），檔名與所在資料夾改成 name、folder_id（一次請求）

        Args:
            file: 列出或取得時拿到的檔案（以它的名稱與 parents 決定要不要改名、搬移）
            content: 新內容
            name: 檔名
            folder_id: 要放的資料夾

        Returns:
            更新後的檔案

        Raises:
            CatalogError: 更新失敗
        """
        add, remove = _relocation(file, folder_id)
        return self._call(lambda: self._drive.update_content(
            file.id, content, name if name != file.name else "", add, remove,
        ))

    def place_file(self, file: DriveFile, name: str, folder_id: str) -> DriveFile:
        """把 Drive 上的檔改名成 name、搬進 folder_id（一次請求；都已經是了時不送請求）

        Args:
            file: 列出或取得時拿到的檔案
            name: 檔名
            folder_id: 要放的資料夾

        Returns:
            改名與搬移後的檔案（內容與版本不變）

        Raises:
            CatalogError: 改不了
        """
        add, remove = _relocation(file, folder_id)
        if name == file.name and not add and not remove:
            return file
        self._call(lambda: self._drive.rename_or_move(file.id, name if name != file.name else "", add, remove))
        return replace(file, name=name, parents=(folder_id,))

    def download_file(self, file: DriveFile) -> bytes:
        """下載 Drive 上一個檔目前的內容（一次請求）；呼叫端拿 file.md5 核對

        Args:
            file: 列出或取得時拿到的檔案

        Returns:
            內容的原始位元組

        Raises:
            CatalogError: 下載失敗；檔案已不在 Drive 時為找不到
        """
        return self._call(lambda: self._drive.download(file.id))

    def deposit_log(self) -> List[DepositLogEntry]:
        """讀存入紀錄的全部列，依紀錄的順序（列出工作表名稱、讀取各一次請求）

        Returns:
            每一列；還沒有存入紀錄時為空清單

        Raises:
            CatalogError: 讀不到
        """
        if SHEET_DEPOSIT_LOG not in self._call(self._sheets.sheet_titles):
            return []
        return _models(self._read_sheets(SHEET_DEPOSIT_LOG)[0], SHEET_DEPOSIT_LOG, DepositLogEntry)

    def begin_publish(self, records: Sequence[PublishRecord], operator: str) -> str:
        """以新抽的發譜編號寫幾列發譜紀錄，一個請求寫入，回傳這個發譜編號

        發譜動 HuaXia 之前先以它寫每個群組一列「開始發譜」，寫不進去就不發譜（不會有沒留紀錄的發譜）；
        收回與不收回也以它寫，每次一個新的發譜編號。請求數固定：列出工作表名稱、讀取、寫入各一次，
        發譜紀錄還沒建立時另建一次。

        Args:
            records: 要寫的列（發譜是各群組的 PublishAction.START）
            operator: 操作者（登入 HuaXia 的 Discord 名稱）

        Returns:
            新抽的發譜編號；結束後以它呼叫 record_publish

        Raises:
            CatalogError: 讀不到或寫不進去（沒有任何一列被寫入）
        """
        titles, tables, log, locale = self._read_with_log(lambda _: [], SHEET_PUBLISH_LOG, "A:B")
        publish_id = self._draw_ids({row[1] for row in log if len(row) > 1}, 1)[0]
        self._append_publish(titles, tables, locale, publish_id, records, operator)
        return publish_id

    def record_publish(self, publish_id: str, records: Sequence[PublishRecord], operator: str) -> None:
        """發譜結束後補上結果：每個群組一列「發譜結果」（連同開始前清孤兒檔的列），發譜編號是 begin_publish 抽的；
        請求數固定（同 begin_publish）

        Raises:
            CatalogError: 讀不到或寫不進去（沒有任何一列被寫入）
        """
        titles, tables, _, locale = self._read_with_log(lambda _: [], SHEET_PUBLISH_LOG, "A:A")
        self._append_publish(titles, tables, locale, publish_id, records, operator)

    def publish_log(self) -> List[PublishRecord]:
        """讀發譜紀錄的全部列，依紀錄的順序（列出工作表名稱、讀取各一次請求）；動作不認得的列略過

        Returns:
            每一列（帶發譜編號、時間與操作者）；還沒有發譜紀錄時為空清單

        Raises:
            CatalogError: 讀不到
        """
        if SHEET_PUBLISH_LOG not in self._call(self._sheets.sheet_titles):
            return []
        rows = self._read_sheets(SHEET_PUBLISH_LOG)[0]
        records = (PublishRecord.from_cells(cells) for cells in _records(rows, SHEET_PUBLISH_LOG))
        return [record for record in records if record is not None]

    def _append_publish(
        self, titles: Set[str], tables: Dict[str, List[List[str]]], locale: Optional[str], publish_id: str,
        records: Sequence[PublishRecord], operator: str,
    ) -> None:
        """在發譜紀錄最後新增各群組的一列，以一個請求送出；locale 是新建發譜紀錄時的標題語言"""
        batch = _Batch(tables, self._draw_ids, locale)
        stamp = now_text()
        for record in records:
            batch.append(SHEET_PUBLISH_LOG, {
                **record.cells(), "publish_id": publish_id, "time": stamp, "operator": operator,
            })
        self._send(batch, titles)

    def revert_reading(self, deposit_id: str, orchestra_type: OrchestraType) -> RevertReading:
        """讀撤銷一次存入要的譜庫現況：存入紀錄、那次各處的編制與登記、編制寫入紀錄，以及那次每份檔在 Drive 上的樣子

        試算表列出工作表名稱、讀取各一次請求；Drive 每份檔取一次中繼資料，那次更新了內容、內容沒再變過的檔
        另列一次版本（下場怎麼決定見 core/deposit_revert.py）。

        Args:
            deposit_id: 要撤銷的存入編號
            orchestra_type: 專案的樂團類型（讀那處的編制用）

        Returns:
            讀到的現況；存入紀錄裡沒有這個存入編號時沒有檔與處

        Raises:
            CatalogError: 讀不到
        """
        titles = self._call(self._sheets.sheet_titles)
        logs = [sheet for sheet in (SHEET_DEPOSIT_LOG, SHEET_INSTRUMENTATION_LOG) if sheet in titles]
        tables = self._read_named(_place_sheets(titles) + logs)
        entries = _models(tables.get(SHEET_DEPOSIT_LOG, []), SHEET_DEPOSIT_LOG, DepositLogEntry)
        ours = [entry for entry in entries if entry.deposit_id == deposit_id]
        catalog = _PlaceTables(tables, orchestra_type)
        places = {}
        for key in dict.fromkeys((entry.edition_id, entry.movement_id) for entry in ours):
            places[key] = self._revert_place(catalog, *key)
        file_ids = dict.fromkeys(entry.drive_file_id for entry in ours if entry.drive_file_id)
        files = {file_id: self.drive_file(file_id) for file_id in file_ids}
        revisions: Dict[str, List[DriveRevision]] = {}
        for entry in ours:
            if entry.drive_file_id not in revisions and needs_revisions(entry, files.get(entry.drive_file_id)):
                revisions[entry.drive_file_id] = self._call(lambda: self._drive.list_revisions(entry.drive_file_id))
        instrumentation = _models(
            tables.get(SHEET_INSTRUMENTATION_LOG, []), SHEET_INSTRUMENTATION_LOG, InstrumentationLogEntry,
        )
        return RevertReading(entries, files, revisions, places, instrumentation)

    def _revert_place(self, catalog: "_PlaceTables", edition_id: str, movement_id: str) -> RevertPlace:
        """撤銷時的一處：曲名、版本、樂章、編制，與分譜列登記的檔；已不在譜庫時沒有內容"""
        try:
            place = self._place(catalog, edition_id, movement_id)
        except CatalogError as error:
            if error.kind is not CatalogErrorKind.GONE:
                raise
            return RevertPlace(None)
        return RevertPlace(place, catalog.registered_voices(edition_id, movement_id))

    def trash_file(self, file_id: str) -> None:
        """把 Drive 上的檔丟進垃圾桶（一次請求），約 30 天內可從垃圾桶救回

        Raises:
            CatalogError: 丟不進去
        """
        self._call(lambda: self._drive.move_to_trash(file_id))

    def restore_revision(self, file: DriveFile, revision: DriveRevision) -> DriveFile:
        """把 Drive 上的檔的內容還原成一個舊版本：先把那個版本設成永久保留，下載並核對 MD5，再傳回同一個檔（檔案 ID 不變）

        傳回之後舊版本的內容已是目前的內容，再把它的永久保留拿掉（不然它會一直佔著 Drive 的空間與每個檔 200 個的上限）；
        這一步失敗不影響還原，舊版本只是多留著。請求數固定四次。

        Args:
            file: 要還原的檔
            revision: 要還原成的版本（Drive 還留著的）

        Returns:
            還原後的檔（新的 headRevisionId）

        Raises:
            CatalogError: 還原不了，內容沒變；版本已被 Drive 清掉時為找不到，下載的內容跟版本的 MD5 不符時為其他
        """
        self._call(lambda: self._drive.keep_revision(file.id, revision.id, True))
        content = self._call(lambda: self._drive.download_revision(file.id, revision.id))
        if revision.md5 and hashlib.md5(content).hexdigest() != revision.md5:
            raise CatalogError(CatalogErrorKind.OTHER, f"revision {revision.id} of {file.id} does not match its MD5")
        restored = self._call(lambda: self._drive.update_content(file.id, content))
        try:
            self._call(lambda: self._drive.keep_revision(file.id, revision.id, False))
        except CatalogError:
            pass
        return restored

    def record_revert(self, deposit_id: str, done: Sequence[Tuple[RevertFile, str]], operator: str) -> None:
        """撤銷存入後移除登記並寫存入紀錄：寫入前一刻才讀分譜工作表找列號，全部以一個請求寫入

        丟進垃圾桶與只移除登記的檔，那次存入的那處、那個聲部（總譜為總譜列）登記它的分譜列清空 drive_file_id、file_name，
        同一個檔另外登記在別處的列不動；每份撤掉的檔一列存入紀錄，這一批共用一個新抽的存入編號，reverts 記被撤銷的
        存入編號，local_path 記本機那份現在的位置（RevertFile.local_path，存入之後重新命名過的是新的路徑）。
        只新增、不修改存入紀錄。請求數固定：列出工作表名稱、讀取、寫入各一次，存入紀錄還沒建立時另建一次。

        Args:
            deposit_id: 被撤銷的存入編號
            done: 撤掉了的每份檔與撤銷後 Drive 上那份的版本 ID
            operator: 操作者（登入的 email），寫進存入紀錄

        Raises:
            CatalogError: 讀不到或寫不進去（沒有任何一格被寫入）
        """
        titles, tables, log, locale = self._read_with_log(lambda _: [SHEET_PARTS], SHEET_DEPOSIT_LOG, "A:B")
        batch = _Batch(tables, self._draw_ids, locale)
        parts = _numbered(tables[SHEET_PARTS], SHEET_PARTS, Part)
        revert_id = self._draw_ids({row[1] for row in log if len(row) > 1}, 1)[0]
        stamp = now_text()
        for reverted, head in done:
            entry = reverted.entry
            if reverted.action in UNREGISTERING:
                for row, part in parts:
                    if _registered_by(part, entry):
                        batch.set(SHEET_PARTS, row, "drive_file_id", "")
                        batch.set(SHEET_PARTS, row, "file_name", "")
            batch.append(SHEET_DEPOSIT_LOG, {
                "deposit_id": revert_id, "time": stamp, "operator": operator, "edition_id": entry.edition_id,
                "movement_id": entry.movement_id, "group": entry.group, "voice": entry.voice,
                "local_path": reverted.local_path or entry.local_path, "drive_file_id": entry.drive_file_id,
                "action": reverted.action.value,
                "revision_after": head, "revision_before": reverted.file.head_revision if reverted.file else "",
                "reverts": deposit_id, "group_id": entry.group_id,
            })
        self._send(batch, titles)

    def my_drive(self) -> DriveFile:
        """我的雲端硬碟的根資料夾，帶它真正的 ID（存成譜庫根資料夾時用這個 ID，才比對得到檔案的 parents）

        Raises:
            CatalogError: 讀不到
        """
        return self._call(lambda: self._drive.get_file(DRIVE_ROOT_ALIAS))

    def subfolders(self, folder_id: str) -> List[DriveFile]:
        """列出 Drive 資料夾裡的子資料夾（依名稱排序，不含垃圾桶裡的）

        Raises:
            CatalogError: 讀不到
        """
        return self._children(folder_id, f"mimeType = {drive_literal(DRIVE_FOLDER_MIME)}")

    def spreadsheets_in(self, folder_id: str) -> List[DriveFile]:
        """列出 Drive 資料夾裡的 Google 試算表（依名稱排序，不含垃圾桶裡的），譜庫設定以此找譜庫試算表

        Raises:
            CatalogError: 讀不到
        """
        return self._children(folder_id, f"mimeType = {drive_literal(DRIVE_SPREADSHEET_MIME)}")

    # --- 寫入 ---

    def new_rows(self, sheet: str, records: Sequence[Mapping[str, str]]) -> PendingRows:
        """準備要新增到 sheet 的一批列；交給 add_rows 送出，失敗時以同一個物件重送

        Args:
            sheet: 工作表名稱
            records: 每一列的欄位值（欄位名稱見 SHEET_HEADERS），不含 id

        Returns:
            待新增的列
        """
        return PendingRows(sheet, records)

    def new_movement(self, piece_id: str, number: str = "", name: str = "") -> PendingRows:
        """準備一首曲目的新樂章：寫入前一刻才讀這首曲目已有的樂章，編號與排序接在它們之後（一次請求）

        Args:
            piece_id: 曲目 ID
            number: 樂章編號；空字串時接在已有的樂章之後
            name: 樂章名稱

        Returns:
            待新增的一列；交給 add_rows 送出，失敗時以同一個物件重送

        Raises:
            CatalogError: 讀不到
        """
        rows = self._read_sheets(SHEET_MOVEMENTS)[0]
        count = sum(1 for movement in _models(rows, SHEET_MOVEMENTS, Movement) if movement.piece_id == piece_id)
        order = str(count + 1)
        return self.new_rows(SHEET_MOVEMENTS, [{
            "piece_id": piece_id, "movement_number": number or order, "name": name, "sort_order": order,
        }])

    def add_rows(self, rows: PendingRows) -> List[str]:
        """新增一批列：先讀這張工作表的 ID 欄，再以一次請求新增

        第一次送出前抽 ID，跟既有的或同一批的撞號就重抽；重送時（上一次送出失敗）略過 ID 已經在工作表裡的列。
        遇到 429 時整個步驟（讀 ID、新增）照退避重來，所以也不會重複。

        Args:
            rows: new_rows 準備的列

        Returns:
            各列的 ID

        Raises:
            CatalogError: 寫不進去；以同一個 rows 再呼叫一次即可補齊
        """
        def send() -> None:
            """讀 ID 欄，送出還不在工作表裡的列"""
            values = rows.to_send(set(self._ids_by_row(rows.sheet)[1:]) - {""}, self._draw_ids)
            if values:
                rows.mark_sent()
                self._sheets.append_rows(rows.sheet, values)
        self._call(send)
        return rows.ids

    def register_files(self, pending: PendingRegistration) -> None:
        """把檔登記到一處的聲部與總譜列，換掉的舊檔丟進 Drive 垃圾桶

        寫入前一刻讀分譜工作表找列號，所有登記以一個請求寫入（已經登記同一份的不再寫）；這處還沒有總譜列時新增一列。
        登記寫好之後才丟舊檔，所以中途失敗時舊檔還在：登記還沒改，或登記已改、舊檔還沒丟，以同一個 pending 重送即補齊
        （回應沒收到而其實已寫入時，重送不再寫，照樣丟掉記下的舊檔）。

        Args:
            pending: 要登記的檔；上一次失敗時傳入同一個

        Raises:
            CatalogError: 讀不到或寫不進去；要登記的聲部已不在譜庫時為已經不在譜庫
        """
        def send() -> None:
            """找列號後寫入登記"""
            rows = self._sheets.read_ranges([_whole_sheet(SHEET_PARTS)])[0]
            parts = _numbered(rows, SHEET_PARTS, Part)
            here = _at(parts, pending.edition_id, pending.movement_id)
            voices = {voice_key(part.instrument_name): (row, part) for row, part in _voice_parts(here)}
            score = _score_part(here)
            batch = _Batch({SHEET_PARTS: rows}, self._draw_ids)
            targets = []
            for voice, file in pending.voices.items():
                if voice_key(voice) not in voices:
                    raise CatalogError(CatalogErrorKind.GONE, f"voice {voice} is no longer in the catalog")
                targets.append((voices[voice_key(voice)], file))
            if pending.score is not None and score is None:
                _add_score_row(batch, pending.edition_id, pending.movement_id, pending.score)
            elif pending.score is not None:
                targets.append((score, pending.score))
            replaced = [old for old in (_register_row(batch, target, file) for target, file in targets) if old]
            # 寫入前就記下：回應沒收到而其實已寫入時，重送讀到的已是新登記，舊檔只能從這裡知道
            pending.replaced += [file_id for file_id in replaced if file_id not in pending.replaced]
            if batch.cells:
                self._sheets.write_cells(batch.cells)
        self._call(send)
        while pending.replaced:
            self._call(lambda: self._drive.move_to_trash(pending.replaced[0]))
            pending.replaced.pop(0)

    def write_cells(self, data: Sequence[CellWrite]) -> None:
        """一次寫入多個範圍（一個請求，全成或全不成）

        Args:
            data: （A1 範圍，值）的清單；範圍以 services.catalog_ports.sheet_range 組出

        Raises:
            CatalogError: 寫不進去，沒有任何一格被寫入
        """
        self._call(lambda: self._sheets.write_cells(data))

    def update_record(self, sheet: str, record_id: str, values: Mapping[str, str]) -> None:
        """改一筆紀錄的幾個欄位：寫入前一刻才讀 ID 欄找列號，只寫給的欄位（一次寫入）

        Args:
            sheet: 工作表名稱
            record_id: 紀錄的 ID
            values: 要改的欄位與新值（欄位名稱見 SHEET_HEADERS）

        Raises:
            CatalogError: 寫不進去；工作表裡已經沒有這筆紀錄時為已經不在譜庫（重送不會成功）
        """
        headers = SHEET_HEADERS[sheet]

        def send() -> None:
            """找列號後寫入"""
            ids = self._ids_by_row(sheet)
            row = next((i + 1 for i, line_id in enumerate(ids) if i > 0 and line_id == record_id), None)
            if row is None:
                raise CatalogError(CatalogErrorKind.GONE, f"{record_id} is no longer in sheet {sheet}")
            self._sheets.write_cells([
                (sheet_range(sheet, f"{_column_letter(headers.index(name))}{row}"), [[str(value)]])
                for name, value in values.items()
            ])
        self._call(send)

    def create_catalog(self, pending: PendingCatalog) -> str:
        """建立新的譜庫總表（每張工作表有 pending.locale 語言的標題列並凍結）並搬進譜庫資料夾

        試算表建好就記在 pending：之後搬不進資料夾時，以同一個 pending 再呼叫一次只補搬，不再建第二份。

        Args:
            pending: 要建立的譜庫總表；上一次失敗時傳入同一個

        Returns:
            新試算表的 ID

        Raises:
            CatalogError: 建不起來或搬不進資料夾；以同一個 pending 再呼叫一次即可補齊
        """
        if not pending.spreadsheet_id:
            sheets = {sheet: header_row(sheet, pending.locale) for sheet in SHEET_HEADERS}
            pending.spreadsheet_id = self._call(lambda: self._sheets.create_spreadsheet(pending.name, sheets))
        self.move_file(self._call(lambda: self._drive.get_file(pending.spreadsheet_id)), pending.folder_id)
        return pending.spreadsheet_id

    def header_locale(self) -> Optional[str]:
        """譜庫總表標題列的語言（列出工作表名稱、讀各表第一列各一次請求）

        Returns:
            語言代碼；各工作表不一致或都認不出時為 None（見 core/catalog_headers.py）

        Raises:
            CatalogError: 讀不到
        """
        sheets = self._present_localized_sheets()
        if not sheets:
            return None
        rows = self._call(lambda: self._sheets.read_ranges([sheet_range(sheet, "1:1") for sheet in sheets]))
        return catalog_header_locale({sheet: (row[0] if row else []) for sheet, row in zip(sheets, rows)})

    def set_header_locale(self, locale: str) -> None:
        """把已經建立的各工作表的標題列整列改寫成 locale 的語言（列出工作表名稱、寫入各一次請求）

        標題列較短的舊工作表一併補上缺的欄；還沒建立的工作表不建，之後建立時沿用譜庫已有的語言。

        Raises:
            CatalogError: 讀不到或寫不進去（寫入全成或全不成）
        """
        sheets = self._present_localized_sheets()
        if sheets:
            data = [(sheet_range(sheet, "A1"), [header_row(sheet, locale)]) for sheet in sheets]
            self._call(lambda: self._sheets.write_cells(data))

    def _present_localized_sheets(self) -> List[str]:
        """已經建立、標題列分中英文的工作表（列出工作表名稱一次請求）"""
        titles = set(self._call(self._sheets.sheet_titles))
        return [sheet for sheet in localized_sheets() if sheet in titles]

    def create_place(self, pending: PendingPlace, orchestra_type: OrchestraType) -> CatalogPlace:
        """在譜庫新建群組要連的一處：建出還沒有的曲目、版本（連同根資料夾下的 Drive 資料夾）與樂章，再讀出那處

        新建的曲目以新版本為使用中的版本。建好一部分就記在 pending：中途失敗時以同一個 pending 再呼叫一次，
        只補上缺的。

        Args:
            pending: 要新建的一處；上一次失敗時傳入同一個
            orchestra_type: 專案的樂團類型（讀聲部組對照用）

        Returns:
            新建好的那一處（read_place 讀到的）

        Raises:
            CatalogError: 寫不進去；以同一個 pending 再呼叫一次即可補齊。要新建版本卻沒設定根資料夾時為找不到
        """
        if not pending.edition_id and not pending.root_folder_id:
            raise CatalogError(CatalogErrorKind.NOT_FOUND, f"{CATALOG_ROOT_FOLDER_ID_KEY} is empty")
        if not pending.piece_id:
            pending.piece_id = self._create_piece(pending)
        if not pending.edition_id:
            if not pending.folder_id:
                if not pending._folder_file_id:
                    pending._folder_file_id = self.drive_ids(1)[0]
                folder = self.create_folder(pending.title, pending.root_folder_id, pending._folder_file_id)
                pending.folder_id = folder.id
            if pending._edition_rows is None:
                pending._edition_rows = self.new_rows(
                    SHEET_EDITIONS, [{"piece_id": pending.piece_id, "drive_folder_id": pending.folder_id}],
                )
            pending.edition_id = self.add_rows(pending._edition_rows)[0]
        if pending._activate_edition:
            self.update_record(SHEET_PIECES, pending.piece_id, {"active_edition_id": pending.edition_id})
            pending._activate_edition = False
        if pending.new_movement and not pending.movement_id:
            if pending._movement_rows is None:
                pending._movement_rows = self.new_movement(
                    pending.piece_id, pending.movement_number, pending.movement_name,
                )
            pending.movement_id = self.add_rows(pending._movement_rows)[0]
        return self.read_place(pending.edition_id, pending.movement_id, orchestra_type)

    def _create_piece(self, pending: PendingPlace) -> str:
        """新增 pending 的曲目（作曲家以名稱找已有的，沒有就新增），回傳曲目 ID；之後要把新版本設為使用中"""
        composer = pending.composer.strip()
        if composer and not pending.composer_id:
            if pending._composer_rows is None:
                existing = next((
                    c for c in _models(self._read_sheets(SHEET_COMPOSERS)[0], SHEET_COMPOSERS, Composer)
                    if c.name.strip() == composer
                ), None)
                if existing is not None:
                    pending.composer_id = existing.id
                else:
                    pending._composer_rows = self.new_rows(SHEET_COMPOSERS, [{"name": composer}])
            if pending._composer_rows is not None:
                pending.composer_id = self.add_rows(pending._composer_rows)[0]
        if pending._piece_rows is None:
            pending._piece_rows = self.new_rows(
                SHEET_PIECES, [{"title": pending.title, "composer_id": pending.composer_id}],
            )
        piece_id = self.add_rows(pending._piece_rows)[0]
        pending._activate_edition = True
        return piece_id

    def create_folder(self, name: str, parent_id: str, file_id: str = "") -> DriveFile:
        """在 Drive 資料夾裡建立子資料夾

        Args:
            name: 資料夾名稱
            parent_id: 上層資料夾的 Drive ID
            file_id: 預先以 drive_ids 要到的 ID；給了時重送不會建出第二個（上一次其實已建好時取那一個）

        Raises:
            CatalogError: 建不起來
        """
        try:
            return self._call(lambda: self._drive.create_folder(name, parent_id, file_id))
        except CatalogError as error:
            if error.kind is not CatalogErrorKind.EXISTS or not file_id:
                raise
            return self._call(lambda: self._drive.get_file(file_id))

    def move_file(self, file: DriveFile, folder_id: str) -> None:
        """把 Drive 上的檔案或資料夾搬進 folder_id，離開原本所在的資料夾；已經在裡面時不送請求

        Args:
            file: 列出時拿到的檔案（以它的 parents 為原本所在的資料夾）
            folder_id: 目標資料夾的 Drive ID

        Raises:
            CatalogError: 搬不動
        """
        if file.parents == (folder_id,):
            return
        self._call(lambda: self._drive.rename_or_move(
            file.id, add_parent=folder_id, remove_parents=[p for p in file.parents if p != folder_id],
        ))

    def move_files(self, pending: List[DriveFile], folder_id: str) -> None:
        """依序把 pending 裡的檔案搬進 folder_id，搬好一份就從 pending 拿掉

        Args:
            pending: 還沒搬的檔案（列出時拿到的）；呼叫後只剩還沒搬的
            folder_id: 目標資料夾的 Drive ID

        Raises:
            CatalogError: 搬不動；pending[0] 是失敗的那份，以同一個 pending 再呼叫一次即補齊
        """
        while pending:
            self.move_file(pending[0], folder_id)
            pending.pop(0)

    def rename_file(self, file_id: str, name: str) -> None:
        """改 Drive 上檔案的名稱

        Raises:
            CatalogError: 改不了
        """
        self._call(lambda: self._drive.rename_or_move(file_id, name=name))

    def tag_folder_as_piece(self, folder_id: str, title: str) -> str:
        """把 Drive 資料夾標記成一首曲目：新增指向資料夾的版本，與以它為使用中版本的曲目

        先讀譜庫，已經有的就沿用：上一次中途失敗時以同樣的參數再呼叫一次，只補上缺的，不產生重複列。

        Args:
            folder_id: 資料夾的 Drive ID
            title: 曲名

        Returns:
            曲目 ID

        Raises:
            CatalogError: 寫不進去；以同樣的參數再呼叫一次即可補齊
        """
        piece_rows, edition_rows = self._read_sheets(SHEET_PIECES, SHEET_EDITIONS)
        edition = next(
            (e for e in _models(edition_rows, SHEET_EDITIONS, Edition) if e.drive_folder_id == folder_id), None,
        )
        if edition is None:
            edition_id = self.add_rows(self.new_rows(SHEET_EDITIONS, [{"drive_folder_id": folder_id}]))[0]
            edition = Edition(id=edition_id, drive_folder_id=folder_id)
        pieces = _models(piece_rows, SHEET_PIECES, Piece)
        piece = next((p for p in pieces if edition.piece_id and p.id == edition.piece_id), None) or next(
            (p for p in pieces if p.active_edition_id == edition.id), None,
        )
        piece_id = piece.id if piece else self.add_rows(
            self.new_rows(SHEET_PIECES, [{"title": title, "active_edition_id": edition.id}]),
        )[0]
        if edition.piece_id != piece_id:
            self.update_record(SHEET_EDITIONS, edition.id, {"piece_id": piece_id})
        return piece_id

    def delete_record(self, sheet: str, record_id: str) -> None:
        """刪掉一筆紀錄（同一個 ID 有幾列就刪幾列）；請求數固定

        Raises:
            CatalogError: 刪不掉，沒有任何一列被刪掉；以同樣的參數再呼叫一次即可
        """
        self._delete([sheet], lambda records: {sheet: {record_id}})

    def delete_piece(self, piece_id: str) -> None:
        """刪掉一首曲目的紀錄，連同它的版本、樂章、版本的分譜與演出紀錄（不動 Drive 上的檔）；請求數固定

        Raises:
            CatalogError: 刪不掉，沒有任何一列被刪掉；以同樣的參數再呼叫一次即可
        """
        def doomed(records: Dict[str, List[Dict[str, str]]]) -> Dict[str, Set[str]]:
            """曲目與連帶的紀錄"""
            editions = {r["id"] for r in records[SHEET_EDITIONS] if r["piece_id"] == piece_id}
            return {
                SHEET_PIECES: {piece_id},
                SHEET_EDITIONS: editions,
                SHEET_MOVEMENTS: {r["id"] for r in records[SHEET_MOVEMENTS] if r["piece_id"] == piece_id},
                SHEET_PARTS: {r["id"] for r in records[SHEET_PARTS] if r["edition_id"] in editions},
                SHEET_PERFORMANCES: {r["id"] for r in records[SHEET_PERFORMANCES] if r["piece_id"] == piece_id},
            }
        self._delete(
            [SHEET_PIECES, SHEET_EDITIONS, SHEET_MOVEMENTS, SHEET_PARTS, SHEET_PERFORMANCES], doomed,
        )

    def delete_movement(self, movement_id: str) -> None:
        """刪掉一個樂章，連同它的分譜；請求數固定

        Raises:
            CatalogError: 刪不掉，沒有任何一列被刪掉；以同樣的參數再呼叫一次即可
        """
        self._delete([SHEET_MOVEMENTS, SHEET_PARTS], lambda records: {
            SHEET_MOVEMENTS: {movement_id},
            SHEET_PARTS: {r["id"] for r in records[SHEET_PARTS] if r["movement_id"] == movement_id},
        })

    def _delete(
        self, sheets: Sequence[str], doomed: Callable[[Dict[str, List[Dict[str, str]]]], Dict[str, Set[str]]],
    ) -> None:
        """寫入前一刻才讀 sheets 的全部列，刪掉 doomed 選出的 ID 所在的列（讀、查 sheetId、刪各一次請求）

        Args:
            sheets: 要讀的工作表
            doomed: 以各工作表的紀錄選出各工作表要刪的 ID
        """
        def send() -> None:
            """找列號後刪除"""
            tables = dict(zip(sheets, self._sheets.read_ranges([_whole_sheet(sheet) for sheet in sheets])))
            ids = doomed({sheet: _records(rows, sheet) for sheet, rows in tables.items()})
            targets = [
                (sheet, index) for sheet in sheets for index, line in enumerate(tables[sheet])
                if index > 0 and line and line[0] in ids.get(sheet, set())
            ]
            if not targets:
                return
            sheet_ids = self._sheets.sheet_ids()
            missing = {sheet for sheet, _ in targets} - set(sheet_ids)
            if missing:
                raise CatalogError(CatalogErrorKind.OTHER, f"no sheet named {sorted(missing)}")
            self._sheets.delete_rows([(sheet_ids[sheet], index) for sheet, index in targets])
        self._call(send)

    def _draw_ids(self, taken: Set[str], count: int) -> List[str]:
        """抽 count 個不在 taken 裡、彼此也不重複的 ID"""
        drawn: List[str] = []
        while len(drawn) < count:
            candidate = self._new_id()
            if candidate not in taken and candidate not in drawn:
                drawn.append(candidate)
        return drawn

    # --- 關口的呼叫 ---

    def _read_with_log(
        self, sheets_for: Callable[[Set[str]], List[str]], log_sheet: str, log_columns: str,
    ) -> Tuple[Set[str], Dict[str, List[List[str]]], List[List[str]], Optional[str]]:
        """寫紀錄前一刻讀：列出工作表名稱一次，再以一次請求讀要的工作表與紀錄表

        紀錄只會越來越長：紀錄表只讀標題列與 log_columns 那幾欄（從 A 欄起；A 欄是 ID，找下一列、抽不撞號的 ID）。
        紀錄表還沒建立、要讀的工作表也認不出標題語言時，同一個請求多讀曲目工作表的標題列，新建的紀錄表沿用它的語言。

        Args:
            sheets_for: 依已有的工作表名稱決定要整張讀的工作表
            log_sheet: 紀錄表（編制寫入紀錄或存入紀錄）
            log_columns: 紀錄表要讀的欄（例如 "A:B"）

        Returns:
            （已有的工作表名稱，各工作表的列（紀錄表是標題列接上讀到的那幾欄；還沒建立時不在裡面），
            紀錄表讀到那幾欄的資料列（不含標題；還沒建立時為空），譜庫的標題語言（認不出時為 None），交給 _Batch）
        """
        titles = set(self._call(self._sheets.sheet_titles))
        sheets = sheets_for(titles)
        ranges = [_whole_sheet(sheet) for sheet in sheets]
        has_log = log_sheet in titles
        if has_log:
            ranges += [sheet_range(log_sheet, "1:1"), sheet_range(log_sheet, log_columns)]
        elif SHEET_PIECES in titles and SHEET_PIECES not in sheets:
            ranges.append(sheet_range(SHEET_PIECES, "1:1"))
        values = self._call(lambda: self._sheets.read_ranges(ranges))
        tables = dict(zip(sheets, values))
        if not has_log:
            pieces = values[len(sheets):]
            reference = {SHEET_PIECES: pieces[0]} if pieces else {}
            return titles, tables, [], _tables_locale(tables) or _tables_locale(reference)
        header, columns = values[-2:]
        tables[log_sheet] = header[:1] + columns[1:]
        return titles, tables, columns[1:], _tables_locale(tables)

    def _send(self, batch: "_Batch", titles: Set[str]) -> None:
        """送出一次寫入的全部格子與紀錄表的新列（一個請求）；寫到還沒建立的工作表時先各建一次。沒有要寫的時不送"""
        if not batch.cells and not batch.appends:
            return
        for sheet in sorted(batch.touched - titles, key=list(SHEET_HEADERS).index):
            self._call(lambda: self._sheets.add_sheet(sheet))
        self._call(lambda: self._sheets.write_cells(batch.cells, list(batch.appends.items())))

    def _read_named(self, sheets: Sequence[str]) -> Dict[str, List[List[str]]]:
        """以一次請求讀多張工作表的全部列（含標題列），依工作表名稱交回"""
        return dict(zip(sheets, self._read_sheets(*sheets)))

    def _read_sheets(self, *sheets: str) -> List[List[List[str]]]:
        """以一次請求讀多張工作表的全部列（含標題列）"""
        return self._call(lambda: self._sheets.read_ranges([_whole_sheet(sheet) for sheet in sheets]))

    def _ids_by_row(self, sheet: str) -> List[str]:
        """讀工作表的 ID 欄（一次請求）：第 i 個是第 i + 1 列的 ID，空列為空字串，第 0 個是標題

        不經 _call：呼叫端把讀 ID 與接著的寫入當成一個步驟，整個交給 _call 重試。
        """
        column = self._sheets.read_ranges([sheet_range(sheet, "A:A")])[0]
        return [line[0] if line else "" for line in column]

    def _children(self, folder_id: str, *conditions: str) -> List[DriveFile]:
        """列出 Drive 資料夾裡符合 conditions（Drive 查詢條件，以 and 連接）的全部檔案，不含垃圾桶裡的"""
        query = " and ".join([f"{drive_literal(folder_id)} in parents", *conditions, "trashed = false"])
        return self._list_files(query)

    def _folder_tree(self, folder_id: str) -> List[Tuple[Tuple[str, ...], str, List[DriveFile]]]:
        """Drive 資料夾與底下各層子資料夾，每個一項：（在資料夾裡的路徑，ID，裡面的 PDF）；資料夾本身的路徑是空的

        由外而內逐層列出，每個資料夾一次請求；子資料夾依名稱排序。
        """
        tree = []
        pending: List[Tuple[Tuple[str, ...], str]] = [((), folder_id)]
        while pending:
            path, current = pending.pop(0)
            contents = self.folder_contents(current)
            tree.append((path, current, contents.pdfs))
            pending += [(path + (folder.name,), folder.id) for folder in contents.folders]
        return tree

    def drive_file(self, file_id: str) -> Optional[DriveFile]:
        """Drive 上一個檔現在的樣子（一次請求；在垃圾桶裡的也取得到）；已經沒有時為 None

        Raises:
            CatalogError: 讀不到
        """
        try:
            return self._call(lambda: self._drive.get_file(file_id))
        except CatalogError as error:
            if error.kind is CatalogErrorKind.NOT_FOUND:
                return None
            raise

    def _live_file(self, file_id: str) -> Optional[DriveFile]:
        """Drive 上還在（不在垃圾桶）的檔；已經沒有或在垃圾桶裡時為 None"""
        file = self.drive_file(file_id)
        return None if file is None or file.trashed else file

    def _list_files(self, query: str) -> List[DriveFile]:
        """列出符合查詢的全部檔案（逐頁請求）"""
        files: List[DriveFile] = []
        token = ""
        while True:
            listing = self._call(lambda: self._drive.list_files(query, token))
            files.extend(listing.files)
            token = listing.next_page_token
            if not token:
                return files

    def _call(self, request: Callable[[], T]) -> T:
        """送出一次關口呼叫；遇到 429 依指數退避等一下再送，超過重試次數就丟出最後一次的錯誤"""
        for attempt in range(self._rate_limit_retries + 1):
            try:
                return request()
            except CatalogError as error:
                if error.kind is not CatalogErrorKind.RATE_LIMITED or attempt == self._rate_limit_retries:
                    raise
                self._sleep(self._first_wait * 2 ** attempt)
        raise AssertionError("unreachable")
