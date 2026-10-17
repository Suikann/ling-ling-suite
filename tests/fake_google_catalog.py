# -*- coding: utf-8 -*-
"""
假 Google：記憶體裡的譜庫試算表加 Drive

實作試算表關口與 Drive 關口（services/catalog_ports.py），注入給譜庫服務。每次呼叫計為一次請求（requests），
可以設定在第 N 次請求時丟出某類錯誤（fail_at，可選擇在請求已經生效之後才丟出，扮演回應沒收到），
或在第 N 次請求之前讓「另一台電腦」改動譜庫（before_request）。

試算表照 Google 的行為：讀取時每列結尾的空格與結尾的空列不回傳；batchUpdate 全成或全不成；
範圍的工作表不存在時整個請求失敗。Drive 解析查詢字串裡的字串值（反斜線跳脫），沒跳脫好的查詢會失敗。

使用範例：
    from fake_google_catalog import FakeGoogle
    google = FakeGoogle()
    google.sheets.create_tab("曲目", [SHEET_HEADERS["曲目"], ["p1", "", "春江花月夜"]])
    folder = google.drive.add_folder("春江花月夜")
    google.fail_at(2, CatalogErrorKind.OFFLINE)
    catalog = CatalogService(google.sheets, google.drive, sleep=lambda seconds: None)
"""
import hashlib
import itertools
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from core.catalog_constants import DRIVE_FOLDER_MIME, DRIVE_PDF_MIME
from core.catalog_errors import CatalogError, CatalogErrorKind
from core.catalog_models import DriveFile, DriveListing

# 假 Drive 的根資料夾（My Drive）
ROOT = "root"


@dataclass
class _Failure:
    """排定在某次請求丟出的錯誤"""

    kind: CatalogErrorKind
    message: str
    applied: bool


class FakeGoogle:
    """記憶體裡的試算表加 Drive：請求計數、排定的錯誤與另一台電腦的改動"""

    def __init__(self):
        self.requests = 0
        self._failures: Dict[int, _Failure] = {}
        self._changes: Dict[int, List[Callable[[], None]]] = {}
        self.sheets = FakeSheets(self)
        self.drive = FakeDrive(self)

    def fail_at(self, number: int, kind: CatalogErrorKind, message: str = "", applied: bool = False) -> None:
        """第 number 次請求丟出 kind 類的錯誤

        Args:
            number: 第幾次請求（從 1 起算）
            kind: 錯誤類別
            message: 原始訊息；省略時為類別名稱
            applied: True 時請求照常生效之後才丟出（扮演 Google 做完了、回應卻沒收到）
        """
        self._failures[number] = _Failure(kind, message or f"fake {kind.value}", applied)

    def fail_next(self, kind: CatalogErrorKind, times: int = 1) -> None:
        """接下來的 times 次請求都丟出 kind 類的錯誤"""
        for number in range(self.requests + 1, self.requests + times + 1):
            self.fail_at(number, kind)

    def before_request(self, number: int, change: Callable[[], None]) -> None:
        """第 number 次請求之前，讓另一台電腦做 change（不算這邊的請求）"""
        self._changes.setdefault(number, []).append(change)

    def request(self, handle: Callable[[], object]) -> object:
        """執行一次請求：先套用另一台電腦的改動，再依排定的錯誤丟出或執行 handle"""
        self.requests += 1
        for change in self._changes.pop(self.requests, []):
            change()
        failure = self._failures.pop(self.requests, None)
        if failure is not None and not failure.applied:
            raise CatalogError(failure.kind, failure.message)
        result = handle()
        if failure is not None:
            raise CatalogError(failure.kind, failure.message)
        return result


def _column_index(letters: str) -> int:
    """欄位字母轉成從 0 起算的欄號（A → 0、AA → 26）"""
    index = 0
    for letter in letters:
        index = index * 26 + ord(letter) - ord("A") + 1
    return index - 1


_CELL = re.compile(r"^([A-Z]*)(\d*)$")


def _parse_range(text: str) -> Tuple[str, int, int, Optional[int], Optional[int]]:
    """解析「'工作表'!A1:C3」，回傳（工作表、起始列、起始欄、結束列、結束欄），列與欄從 0 起算；沒寫的結束為 None"""
    match = re.match(r"^'((?:[^']|'')*)'!(.+)$", text) or re.match(r"^([^!']+)!(.+)$", text)
    if not match:
        raise CatalogError(CatalogErrorKind.OTHER, f"Unable to parse range: {text}")
    sheet = match.group(1).replace("''", "'")
    parts = match.group(2).split(":")
    start = _CELL.match(parts[0])
    end = _CELL.match(parts[-1])
    if not start or not end or len(parts) > 2:
        raise CatalogError(CatalogErrorKind.OTHER, f"Unable to parse range: {text}")
    start_row = int(start.group(2)) - 1 if start.group(2) else 0
    start_col = _column_index(start.group(1)) if start.group(1) else 0
    end_row = int(end.group(2)) - 1 if end.group(2) else None
    end_col = _column_index(end.group(1)) if end.group(1) else None
    if len(parts) == 1:
        end_row, end_col = start_row, start_col
    return sheet, start_row, start_col, end_row, end_col


def _trimmed(rows: List[List[str]]) -> List[List[str]]:
    """去掉每列結尾的空格與結尾的空列（Google 讀取時不回傳它們）"""
    result = []
    for row in rows:
        cells = list(row)
        while cells and cells[-1] == "":
            cells.pop()
        result.append(cells)
    while result and not result[-1]:
        result.pop()
    return result


class FakeSheets:
    """記憶體裡的一份試算表：實作試算表關口"""

    def __init__(self, google: FakeGoogle):
        self._google = google
        self._tabs: Dict[str, List[List[str]]] = {}

    # --- 測試直接操作（不算請求）---

    def create_tab(self, title: str, rows: Sequence[Sequence[str]] = ()) -> None:
        """建立一張工作表並放進 rows（第一列通常是標題列）"""
        self._tabs[title] = [[str(value) for value in row] for row in rows]

    def rows(self, title: str) -> List[List[str]]:
        """工作表目前的內容（含標題列；去掉結尾的空格與空列）"""
        return _trimmed(self._tabs[title])

    def set_cell(self, title: str, cell: str, value: str) -> None:
        """改動一格（例如另一台電腦在網頁上改了它）"""
        _, row, col, _, _ = _parse_range(f"'{title}'!{cell}")
        self._put(self._tabs[title], row, col, value)

    def tabs(self) -> List[str]:
        """所有工作表的名稱"""
        return list(self._tabs)

    # --- 試算表關口 ---

    def read_ranges(self, ranges: Sequence[str]) -> List[List[List[str]]]:
        """讀多個範圍；任一工作表不存在時整個請求失敗"""
        return self._google.request(lambda: [self._read(text) for text in ranges])

    def write_cells(self, data: Sequence[Tuple[str, List[List[str]]]]) -> None:
        """一次寫入多個範圍，全成或全不成"""
        def handle():
            targets = [(_parse_range(text), values) for text, values in data]
            for (sheet, *_), _values in targets:
                self._tab(sheet)
            for (sheet, row, col, _, _), values in targets:
                for i, line in enumerate(values):
                    for j, value in enumerate(line):
                        self._put(self._tabs[sheet], row + i, col + j, str(value))
        self._google.request(handle)

    def append_rows(self, sheet: str, rows: Sequence[List[str]]) -> None:
        """在最後一列有內容的列之後新增多列"""
        def handle():
            tab = self._tab(sheet)
            tab[:] = _trimmed(tab) + [[str(value) for value in row] for row in rows]
        self._google.request(handle)

    def add_sheet(self, title: str) -> None:
        """建立一張空的工作表；已有同名的時失敗"""
        def handle():
            if title in self._tabs:
                raise CatalogError(CatalogErrorKind.OTHER, f"A sheet with the name \"{title}\" already exists.")
            self._tabs[title] = []
        self._google.request(handle)

    def sheet_titles(self) -> List[str]:
        """列出所有工作表的名稱"""
        return self._google.request(lambda: list(self._tabs))

    def _tab(self, title: str) -> List[List[str]]:
        """取工作表；不存在時照 Google 回 400"""
        if title not in self._tabs:
            raise CatalogError(CatalogErrorKind.OTHER, f"Unable to parse range: '{title}'")
        return self._tabs[title]

    def _read(self, text: str) -> List[List[str]]:
        """讀一個範圍"""
        sheet, start_row, start_col, end_row, end_col = _parse_range(text)
        rows = self._tab(sheet)
        last_row = len(rows) - 1 if end_row is None else end_row
        selected = []
        for row in rows[start_row:last_row + 1]:
            stop = len(row) if end_col is None else end_col + 1
            selected.append(list(row[start_col:stop]))
        return _trimmed(selected)

    @staticmethod
    def _put(tab: List[List[str]], row: int, col: int, value: str) -> None:
        """寫一格，不足的列與欄補空字串"""
        while len(tab) <= row:
            tab.append([])
        line = tab[row]
        while len(line) <= col:
            line.append("")
        line[col] = value


@dataclass
class _DriveItem:
    """假 Drive 上的一個檔案或資料夾"""

    id: str
    name: str
    mime_type: str
    parents: List[str]
    content: bytes = b""
    trashed: bool = False

    def as_file(self) -> DriveFile:
        """以關口回傳的形狀呈現"""
        md5 = "" if self.mime_type == DRIVE_FOLDER_MIME else hashlib.md5(self.content).hexdigest()
        return DriveFile(self.id, self.name, self.mime_type, tuple(self.parents), md5)


class FakeDrive:
    """記憶體裡的 Drive：實作 Drive 關口"""

    def __init__(self, google: FakeGoogle):
        self._google = google
        self._items: Dict[str, _DriveItem] = {}
        self._ids = (f"drive-{n}" for n in itertools.count(1))
        self.page_size = 100

    # --- 測試直接操作（不算請求）---

    def add_folder(self, name: str, parent: str = ROOT) -> str:
        """建立資料夾，回傳 ID"""
        return self._add(name, DRIVE_FOLDER_MIME, parent, b"")

    def add_file(self, name: str, parent: str = ROOT, content: bytes = b"%PDF", mime_type: str = DRIVE_PDF_MIME) -> str:
        """建立檔案（預設是 PDF），回傳 ID"""
        return self._add(name, mime_type, parent, content)

    def trash(self, file_id: str) -> None:
        """把檔案丟進垃圾桶"""
        self._items[file_id].trashed = True

    # --- Drive 關口 ---

    def list_files(self, query: str, page_token: str = "") -> DriveListing:
        """依查詢字串列出一頁檔案（依名稱排序）"""
        def handle():
            matches = _parse_query(query)
            found = sorted(
                (item for item in self._items.values() if matches(item)), key=lambda item: item.name,
            )
            start = int(page_token or 0)
            page = found[start:start + self.page_size]
            following = start + self.page_size
            next_token = str(following) if following < len(found) else ""
            return DriveListing([item.as_file() for item in page], next_token)
        return self._google.request(handle)

    def _add(self, name: str, mime_type: str, parent: str, content: bytes) -> str:
        """加一個項目，回傳 ID"""
        item_id = next(self._ids)
        self._items[item_id] = _DriveItem(item_id, name, mime_type, [parent], content)
        return item_id


def _invalid_query(query: str) -> CatalogError:
    """查詢字串解析不了：Drive 回 400 Invalid Value"""
    return CatalogError(CatalogErrorKind.OTHER, f"Invalid Value: {query}")


def _tokens(query: str) -> List[Tuple[str, str]]:
    """把查詢字串切成（種類，內容）：字串值（"str"，已解開跳脫）或字詞（"word"）"""
    tokens = []
    i = 0
    while i < len(query):
        char = query[i]
        if char == " ":
            i += 1
        elif char == "'":
            value = []
            i += 1
            while True:
                if i >= len(query):
                    raise _invalid_query(query)
                if query[i] == "\\":
                    if i + 1 >= len(query) or query[i + 1] not in "\\'":
                        raise _invalid_query(query)
                    value.append(query[i + 1])
                    i += 2
                elif query[i] == "'":
                    i += 1
                    break
                else:
                    value.append(query[i])
                    i += 1
            tokens.append(("str", "".join(value)))
        else:
            end = i
            while end < len(query) and query[end] not in " '":
                end += 1
            tokens.append(("word", query[i:end]))
            i = end
    return tokens


def _parse_query(query: str) -> Callable[[_DriveItem], bool]:
    """解析以 and 連接的條件：'<id>' in parents、name = '…'、mimeType = / != '…'、trashed = true / false"""
    tokens = _tokens(query)
    conditions = []
    i = 0
    while i < len(tokens):
        clause = tokens[i:i + 3]
        kinds = [kind for kind, _ in clause]
        values = [value for _, value in clause]
        if kinds == ["str", "word", "word"] and values[1:] == ["in", "parents"]:
            parent = values[0]
            conditions.append(lambda item, parent=parent: parent in item.parents)
        elif kinds == ["word", "word", "str"] and values[0] in ("name", "mimeType") and values[1] in ("=", "!="):
            attribute = "name" if values[0] == "name" else "mime_type"
            expected, equal = values[2], values[1] == "="
            conditions.append(
                lambda item, a=attribute, e=expected, eq=equal: (getattr(item, a) == e) == eq,
            )
        elif kinds == ["word", "word", "word"] and values[:2] == ["trashed", "="] and values[2] in ("true", "false"):
            wanted = values[2] == "true"
            conditions.append(lambda item, wanted=wanted: item.trashed == wanted)
        else:
            raise _invalid_query(query)
        i += 3
        if i < len(tokens):
            if tokens[i] != ("word", "and"):
                raise _invalid_query(query)
            i += 1
            if i >= len(tokens):
                raise _invalid_query(query)
    return lambda item: all(condition(item) for condition in conditions)
