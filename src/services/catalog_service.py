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
    page = catalog.edition_page(edition_id, movement_id, OrchestraType.CHINESE)  # 版本頁：登記與沒登記的檔
    catalog.register_files(PendingRegistration(edition_id, movement_id, {"琵琶": pdf}))  # 失敗時以同一個物件重送
    rows = catalog.new_rows(SHEET_PIECES, [{"title": "春江花月夜"}])
    ids = catalog.add_rows(rows)                     # 失敗時以同一個 rows 重送，不會重複新增
"""
import time
import uuid
from dataclasses import fields
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Type, TypeVar

from core.catalog_constants import (
    CATALOG_TRUE, CATALOG_TRUE_VALUES, CLUB_SETTING_PUBLISHING_PLATFORM, DRIVE_FOLDER_MIME, DRIVE_PDF_MIME,
    DRIVE_SPREADSHEET_MIME, PUBLISHING_PLATFORM_NAMES, SHEET_CLUB_SETTINGS, SHEET_COMPOSERS, SHEET_DURATIONS,
    SHEET_EDITIONS, SHEET_HEADERS, SHEET_INSTRUMENTATION_LOG, SHEET_MOVEMENTS, SHEET_PARTS, SHEET_PERFORMANCES,
    SHEET_PIECES, SHEET_SECTIONS, PublishingPlatform,
)
from core.catalog_errors import CatalogError, CatalogErrorKind
from core.constants import CATALOG_ROOT_FOLDER_ID_KEY, OrchestraType
from core.voice_suggestions import ranked_by_usage
from core.catalog_models import (
    CatalogOverview, CatalogPlace, Composer, DriveFile, Edition, EditionFiles, EditionPage, FolderContents, FolderPdf,
    LinkTargets, Movement, Part, Performance, Piece, PieceDetail, Registration, SectionMapEntry, sort_order_key,
)
from core.catalog_sync import (
    CurrentPlace, Decision, ItemReview, PlaceReview, PlaceWrite, SyncField, SyncItem, SyncOutcome, Verdict, WriteSource,
    final_voices, review_items, value_text,
)
from core.log_entry import now
from core.models import Headcount
from core.sections import stored_section
from core.voice_names import instrument_key, instrument_name, voice_key
from services.catalog_ports import CellWrite, DrivePort, SheetsPort, sheet_range

T = TypeVar("T")

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


def _models(rows: List[List[str]], sheet: str, model_class: Type[T]) -> List[T]:
    """把工作表讀到的列（含標題列）依程式的欄位常數轉成資料模型；標題列與空列略過"""
    headers = SHEET_HEADERS[sheet]
    names = {f.name for f in fields(model_class)}
    result = []
    for row in rows[1:]:
        if not any(row):
            continue
        values = {
            header: (row[i] if i < len(row) else "")
            for i, header in enumerate(headers) if header in names
        }
        result.append(model_class(**values))
    return result


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


def _numbered(rows: List[List[str]], sheet: str, model_class: Type[T]) -> List[Tuple[int, T]]:
    """把工作表讀到的列（含標題列）轉成資料模型，連同從 1 起算的列號；標題列與空列略過"""
    headers = SHEET_HEADERS[sheet]
    names = {f.name for f in fields(model_class)}
    return [
        (index + 1, model_class(**{
            header: (row[i] if i < len(row) else "") for i, header in enumerate(headers) if header in names
        }))
        for index, row in enumerate(rows) if index > 0 and any(row)
    ]


def _records(rows: List[List[str]], sheet: str) -> List[Dict[str, str]]:
    """把工作表讀到的列（含標題列）轉成欄位名稱對值；標題列與空列略過"""
    headers = SHEET_HEADERS[sheet]
    return [
        {header: (row[i] if i < len(row) else "") for i, header in enumerate(headers)}
        for row in rows[1:] if any(row)
    ]


def _place_sheets(titles: Sequence[str]) -> List[str]:
    """讀一處或聲部組對照要讀的工作表：曲目、版本、樂章、分譜，以及已經建立的時長與聲部組對照"""
    return [SHEET_PIECES, SHEET_EDITIONS, SHEET_MOVEMENTS, SHEET_PARTS] + [
        sheet for sheet in (SHEET_DURATIONS, SHEET_SECTIONS) if sheet in titles
    ]


class _Batch:
    """一次寫回要送出的全部格子：改既有列的格子、接在各工作表最後的新列與缺的標題，最後以一個請求送出"""

    def __init__(self, tables: Dict[str, List[List[str]]], draw: Callable[[Set[str], int], List[str]]):
        """
        Args:
            tables: 寫入前一刻讀到的各工作表（含標題列；沒有的工作表不在裡面）
            draw: 抽新 ID 的函式，參數為不可用的 ID 與要抽幾個
        """
        self._tables = tables
        self._draw = draw
        self._next_row = {sheet: max(len(rows), 1) + 1 for sheet, rows in tables.items()}
        self._taken = {sheet: {row[0] for row in rows[1:] if row} for sheet, rows in tables.items()}
        self.cells: List[CellWrite] = []
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
        taken = self._taken.setdefault(sheet, set())
        record_id = self._draw(taken, 1)[0]
        taken.add(record_id)
        row = self._next_row.setdefault(sheet, 2)
        self._next_row[sheet] = row + 1
        values = [record_id if header == "id" else str(record.get(header, "")) for header in SHEET_HEADERS[sheet]]
        self.cells.append((sheet_range(sheet, f"A{row}"), [values]))
        return row

    def _touch(self, sheet: str) -> None:
        """第一次寫到這張工作表時補上缺的標題（工作表還沒建立，或分譜工作表還沒有新增的欄）"""
        if sheet in self.touched:
            return
        self.touched.add(sheet)
        headers = SHEET_HEADERS[sheet]
        existing = self._tables.get(sheet, [])
        present = existing[0] if existing else []
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
        self._durations = [
            (row, record) for row, record in _numbered_records(tables.get(SHEET_DURATIONS, []), SHEET_DURATIONS)
        ]
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
        return _voice_parts([
            (row, part) for row, part in self._parts
            if part.edition_id == edition_id and part.movement_id == movement_id
        ])

    def _score_row(self, edition_id: str, movement_id: str) -> Optional[Tuple[int, Part]]:
        """一處的總譜列與列號；有幾列時只認排在前面那一列"""
        return next((
            (row, part) for row, part in self._parts
            if part.edition_id == edition_id and part.movement_id == movement_id and _is_true(part.is_score)
        ), None)

    def _duration_row(self, edition_id: str, movement_id: str) -> Optional[Tuple[int, Dict[str, str]]]:
        """一處的時長列與列號；有幾列時只認第一列"""
        return next((
            (row, record) for row, record in self._durations
            if record["edition_id"] == edition_id and record["movement_id"] == movement_id
        ), None)


def _numbered_records(rows: List[List[str]], sheet: str) -> List[Tuple[int, Dict[str, str]]]:
    """把工作表讀到的列（含標題列）轉成欄位名稱對值，連同從 1 起算的列號；標題列與空列略過"""
    headers = SHEET_HEADERS[sheet]
    return [
        (index + 1, {header: (row[i] if i < len(row) else "") for i, header in enumerate(headers)})
        for index, row in enumerate(rows) if index > 0 and any(row)
    ]


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
        "new_value": new, "group": write.group_name,
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
    """要建立的譜庫試算表：建好後記下 ID，CatalogService.create_catalog 重送時沿用，不建第二份"""

    def __init__(self, name: str, folder_id: str):
        """
        Args:
            name: 試算表名稱
            folder_id: 譜庫資料夾的 Drive ID
        """
        self.name = name
        self.folder_id = folder_id
        self.spreadsheet_id = ""


class PendingPlace:
    """要在譜庫新建的一處：還沒有的曲目（連同作曲家）、版本（連同 Drive 資料夾）與樂章

    已有的部分給 ID，其餘依曲名、作曲家、樂章編號與名稱新建。CatalogService.create_place 建好一部分就記下來：
    之後中途失敗時以同一個物件重送，只補上缺的，不產生重複的列與資料夾。
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
    ) -> List[CatalogPlace]:
        """讀譜庫裡的幾處（開啟專案時各群組連到的那處），內容同 read_place；請求數同樣是兩次，不隨處數增加

        Args:
            places: 每一處的（版本 ID、樂章 ID；整首為空字串）
            orchestra_type: 專案的樂團類型（聲部組對照只取這一套）

        Returns:
            依 places 順序的各處

        Raises:
            CatalogError: 讀不到；任一處的版本或樂章已不在譜庫時為已經不在譜庫
        """
        catalog = self._place_tables(orchestra_type)
        return [self._place(catalog, edition_id, movement_id) for edition_id, movement_id in places]

    def edition_page(self, edition_id: str, movement_id: str, orchestra_type: OrchestraType) -> EditionPage:
        """讀譜庫視窗的版本頁：一處的編制、各聲部與總譜列登記的檔，以及版本資料夾裡沒登記的檔

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
        return EditionPage(
            place=place,
            registrations=catalog.registrations(edition_id, movement_id),
            score=catalog.score(edition_id, movement_id),
            unregistered=[pdf for pdf in self._pdfs_under(folder_id) if pdf.file.id not in registered]
            if folder_id else [],
        )

    def _place_tables(self, orchestra_type: OrchestraType) -> "_PlaceTables":
        """讀一處要的各工作表（列出工作表名稱、讀取各一次請求）；時長與聲部組對照還沒建立時當成空的"""
        sheets = _place_sheets(self._call(self._sheets.sheet_titles))
        return _PlaceTables(dict(zip(sheets, self._read_sheets(*sheets))), orchestra_type)

    def _pdfs_under(self, folder_id: str, prefix: str = "") -> List[FolderPdf]:
        """資料夾與它所有子資料夾裡的 PDF，連同在資料夾裡的路徑，依路徑排序（每個資料夾一次列出）"""
        contents = self.folder_contents(folder_id)
        pdfs = [FolderPdf(pdf, prefix + pdf.name) for pdf in contents.pdfs]
        for folder in contents.folders:
            pdfs += self._pdfs_under(folder.id, f"{prefix}{folder.name}/")
        return sorted(pdfs, key=lambda pdf: pdf.path)

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

    def section_map(self) -> Dict[OrchestraType, List[SectionMapEntry]]:
        """讀全社團的聲部組對照，每個樂團類型一套，連同譜庫裡用到、還沒歸類的樂器與各樂器用到幾首（兩次請求）

        Returns:
            樂團類型 → 這一套的樂器（見 _PlaceTables.section_entries）

        Raises:
            CatalogError: 讀不到
        """
        sheets = _place_sheets(self._call(self._sheets.sheet_titles))
        tables = dict(zip(sheets, self._read_sheets(*sheets)))
        return {orchestra: _PlaceTables(tables, orchestra).section_entries() for orchestra in OrchestraType}

    def sync_instrumentation(
        self, writes: Sequence[PlaceWrite], orchestra_type: OrchestraType, operator: str, source: WriteSource,
        decisions: Mapping[str, Mapping[Tuple[SyncField, object], Decision]], review_only: bool = False,
    ) -> SyncOutcome:
        """把群組的編制改動寫回譜庫：寫入前一刻重讀、逐格比對，沒有待選邊的衝突才以一個請求寫入

        每一格依譜庫現在的值處理（core/catalog_sync.py 的 review_items）：等於快照的原值就寫、等於要寫的新值就略過、
        兩者都不是就是衝突；有衝突（或選邊時看到的譜庫值之後又變了）時什麼都不寫，交回比對結果讓使用者選邊後再呼叫。
        要刪掉的聲部在譜庫已有 Drive 檔案時不寫（無法寫回），其餘照寫。

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
        titles = set(self._call(self._sheets.sheet_titles))
        sheets = _place_sheets(titles)
        ranges = [_whole_sheet(sheet) for sheet in sheets]
        has_log = SHEET_INSTRUMENTATION_LOG in titles
        if has_log:
            # 紀錄只會越來越長：只讀標題列與 ID 欄（找下一列、抽不撞號的 ID）
            ranges += [sheet_range(SHEET_INSTRUMENTATION_LOG, "1:1"), sheet_range(SHEET_INSTRUMENTATION_LOG, "A:A")]
        values = self._call(lambda: self._sheets.read_ranges(ranges))
        tables = dict(zip(sheets, values))
        if has_log:
            header, ids = values[-2:]
            tables[SHEET_INSTRUMENTATION_LOG] = header[:1] + ids[1:]
        catalogs = {orchestra: _PlaceTables(tables, orchestra) for orchestra in {o for _, o in writes}}
        places = []
        for write, orchestra in writes:
            catalog = catalogs[orchestra]
            current = catalog.current(write.edition_id, write.movement_id) if write.edition_id else catalog.club()
            reviews = review_items(write, current, dict(decisions.get(write.group_id, {})))
            places.append(PlaceReview(write, reviews, list(current.filed), catalog.other_pieces(write.edition_id)))
        outcome = SyncOutcome(places, written=False)
        if outcome.conflicts or review_only:
            return outcome
        batch = _Batch({sheet: rows for sheet, rows in tables.items()}, self._draw_ids)
        stamp = now().strftime("%Y-%m-%d %H:%M:%S")
        written_sections: Dict[OrchestraType, Set[str]] = {orchestra: set() for orchestra in catalogs}
        for place, (_, orchestra) in zip(places, writes):
            for review in catalogs[orchestra].write_place(batch, place, written_sections[orchestra]):
                batch.add(SHEET_INSTRUMENTATION_LOG, _log_record(stamp, operator, source, place.write, review))
        if batch.cells:
            for sheet in sorted(batch.touched - titles, key=list(SHEET_HEADERS).index):
                self._call(lambda: self._sheets.add_sheet(sheet))
            self._call(lambda: self._sheets.write_cells(batch.cells))
        outcome.written = True
        return outcome

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
            here = [
                (row, part) for row, part in parts
                if part.edition_id == pending.edition_id and part.movement_id == pending.movement_id
            ]
            voices = {voice_key(part.instrument_name): (row, part) for row, part in _voice_parts(here)}
            score = next(((row, part) for row, part in here if _is_true(part.is_score)), None)
            batch = _Batch({SHEET_PARTS: rows}, self._draw_ids)
            replaced = []
            targets = []
            for voice, file in pending.voices.items():
                if voice_key(voice) not in voices:
                    raise CatalogError(CatalogErrorKind.GONE, f"voice {voice} is no longer in the catalog")
                targets.append((voices[voice_key(voice)], file))
            if pending.score is not None and score is None:
                batch.add(SHEET_PARTS, {
                    "edition_id": pending.edition_id, "movement_id": pending.movement_id, "is_score": CATALOG_TRUE,
                    "drive_file_id": pending.score.id, "file_name": pending.score.name,
                })
            elif pending.score is not None:
                targets.append((score, pending.score))
            for (row, part), file in targets:
                if part.drive_file_id.strip() == file.id:
                    continue
                if part.drive_file_id.strip():
                    replaced.append(part.drive_file_id.strip())
                batch.set(SHEET_PARTS, row, "drive_file_id", file.id)
                batch.set(SHEET_PARTS, row, "file_name", file.name)
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
        """建立新的譜庫試算表（每張工作表有標題列並凍結）並搬進譜庫資料夾

        試算表建好就記在 pending：之後搬不進資料夾時，以同一個 pending 再呼叫一次只補搬，不再建第二份。

        Args:
            pending: 要建立的譜庫試算表；上一次失敗時傳入同一個

        Returns:
            新試算表的 ID

        Raises:
            CatalogError: 建不起來或搬不進資料夾；以同一個 pending 再呼叫一次即可補齊
        """
        if not pending.spreadsheet_id:
            pending.spreadsheet_id = self._call(
                lambda: self._sheets.create_spreadsheet(pending.name, SHEET_HEADERS),
            )
        self.move_file(self._call(lambda: self._drive.get_file(pending.spreadsheet_id)), pending.folder_id)
        return pending.spreadsheet_id

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
                pending.folder_id = self.create_folder(pending.title, pending.root_folder_id).id
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

    def create_folder(self, name: str, parent_id: str) -> DriveFile:
        """在 Drive 資料夾裡建立子資料夾

        Raises:
            CatalogError: 建不起來
        """
        return self._call(lambda: self._drive.create_folder(name, parent_id))

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
