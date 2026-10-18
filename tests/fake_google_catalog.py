# -*- coding: utf-8 -*-
"""
假 Google：記憶體裡的譜庫試算表加 Drive

實作試算表關口與 Drive 關口（services/catalog_ports.py），注入給譜庫服務。每次呼叫計為一次請求（requests），
可以設定在第 N 次請求時丟出某類錯誤（fail_at，可選擇在請求已經生效之後才丟出，扮演回應沒收到），
或在第 N 次請求之前讓「另一台電腦」改動譜庫（before_request）。每次寫入多格送出的內容依序記在 sheets.writes；
試算表與 Drive 的請求另外分開計數（sheets_requests、drive_requests）。

試算表照 Google 的行為：讀取時每列結尾的空格與結尾的空列不回傳；batchUpdate 全成或全不成；
範圍的工作表不存在時整個請求失敗。Drive 解析查詢字串裡的字串值（反斜線跳脫），沒跳脫好的查詢會失敗；檔案的每次內容是一個版本，
以預先要到的 ID 建檔重送時回 409（已經存在）。

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
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from core.catalog_constants import DRIVE_FOLDER_MIME, DRIVE_PDF_MIME, DRIVE_SPREADSHEET_MIME
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
        """從沒有請求、沒有排定錯誤的狀態開始，建出共用請求計數的試算表與 Drive"""
        self.requests = 0
        self._by_port = {"sheets": 0, "drive": 0}
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

    @property
    def sheets_requests(self) -> int:
        """試算表關口的請求數"""
        return self._by_port["sheets"]

    @property
    def drive_requests(self) -> int:
        """Drive 關口的請求數"""
        return self._by_port["drive"]

    def request(self, handle: Callable[[], object], port: str = "drive") -> object:
        """執行一次請求：先套用另一台電腦的改動，再依排定的錯誤丟出或執行 handle

        Args:
            handle: 請求的內容
            port: 哪個關口的請求（"sheets" 或 "drive"），分別計數
        """
        self.requests += 1
        self._by_port[port] += 1
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
        """空的試算表

        Args:
            google: 計請求與排定錯誤的假 Google
        """
        self._google = google
        self._tabs: Dict[str, List[List[str]]] = {}
        self._sheet_ids: Dict[str, int] = {}
        self._next_sheet_id = itertools.count(1)
        self._frozen: Dict[str, int] = {}
        # 每次 write_cells 送出的（範圍，值），依送出順序；失敗的請求不記
        self.writes: List[List[Tuple[str, List[List[str]]]]] = []

    # --- 測試直接操作（不算請求）---

    def create_tab(self, title: str, rows: Sequence[Sequence[str]] = ()) -> None:
        """建立一張工作表並放進 rows（第一列通常是標題列）；已有這張時換掉內容"""
        self._tabs[title] = [[str(value) for value in row] for row in rows]
        self._sheet_ids.setdefault(title, next(self._next_sheet_id))

    def delete_tab(self, title: str) -> None:
        """拿掉一張工作表（例如還沒有這張工作表的舊譜庫）"""
        self._tabs.pop(title)
        self._sheet_ids.pop(title)

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

    def frozen_rows(self, title: str) -> int:
        """工作表凍結了幾列"""
        return self._frozen.get(title, 0)

    # --- 試算表關口 ---

    def read_ranges(self, ranges: Sequence[str]) -> List[List[List[str]]]:
        """讀多個範圍；任一工作表不存在時整個請求失敗"""
        return self._google.request(lambda: [self._read(text) for text in ranges], "sheets")

    def write_cells(self, data: Sequence[Tuple[str, List[List[str]]]]) -> None:
        """一次寫入多個範圍，全成或全不成"""
        def handle():
            """寫入：先確認每張工作表都在，才逐格寫"""
            targets = [(_parse_range(text), values) for text, values in data]
            for (sheet, *_), _values in targets:
                self._tab(sheet)
            for (sheet, row, col, _, _), values in targets:
                for i, line in enumerate(values):
                    for j, value in enumerate(line):
                        self._put(self._tabs[sheet], row + i, col + j, str(value))
            self.writes.append([(text, [list(line) for line in values]) for text, values in data])
        self._google.request(handle, "sheets")

    def append_rows(self, sheet: str, rows: Sequence[List[str]]) -> None:
        """在最後一列有內容的列之後新增多列"""
        def handle():
            """接在最後一列有內容的列之後"""
            tab = self._tab(sheet)
            tab[:] = _trimmed(tab) + [[str(value) for value in row] for row in rows]
        self._google.request(handle, "sheets")

    def add_sheet(self, title: str) -> None:
        """建立一張空的工作表；已有同名的時失敗"""
        def handle():
            """建立工作表"""
            if title in self._tabs:
                raise CatalogError(CatalogErrorKind.OTHER, f"A sheet with the name \"{title}\" already exists.")
            self._tabs[title] = []
            self._sheet_ids[title] = next(self._next_sheet_id)
        self._google.request(handle, "sheets")

    def sheet_ids(self) -> Dict[str, int]:
        """每張工作表的 sheetId"""
        return self._google.request(lambda: dict(self._sheet_ids), "sheets")

    def delete_rows(self, rows: Sequence[Tuple[int, int]]) -> None:
        """刪掉多列（sheetId、從 0 起算的列號，都以刪除前為準），全成或全不成"""
        def handle():
            """確認 sheetId 都在才由下往上刪"""
            titles = {sheet_id: title for title, sheet_id in self._sheet_ids.items()}
            for sheet_id, _row in rows:
                if sheet_id not in titles:
                    raise CatalogError(CatalogErrorKind.OTHER, f"No grid with id: {sheet_id}")
            for sheet_id, row in sorted(set(rows), key=lambda target: -target[1]):
                tab = self._tabs[titles[sheet_id]]
                if row < len(tab):
                    del tab[row]
        self._google.request(handle, "sheets")

    def sheet_titles(self) -> List[str]:
        """列出所有工作表的名稱"""
        return self._google.request(lambda: list(self._tabs), "sheets")

    def create_spreadsheet(self, title: str, sheets: Mapping[str, Sequence[str]]) -> str:
        """建立新的試算表（放在 Drive 的根資料夾）：每張工作表有標題列並凍結；假 Google 只有一份試算表，以它為這份"""
        def handle():
            """換成新試算表的工作表，並在 Drive 根資料夾放一個試算表檔"""
            self._tabs = {name: [[str(h) for h in headers]] for name, headers in sheets.items()}
            self._sheet_ids = {name: next(self._next_sheet_id) for name in sheets}
            self._frozen = {name: 1 for name in sheets}
            return self._google.drive.add_file(title, ROOT, b"", DRIVE_SPREADSHEET_MIME)
        return self._google.request(handle, "sheets")

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
    """假 Drive 上的一個檔案或資料夾；檔案的每次內容是一個版本（revisions，最後一個是目前的內容）"""

    id: str
    name: str
    mime_type: str
    parents: List[str]
    revisions: List[Tuple[str, bytes]] = field(default_factory=list)
    trashed: bool = False

    @property
    def content(self) -> bytes:
        """目前的內容"""
        return self.revisions[-1][1] if self.revisions else b""

    def as_file(self) -> DriveFile:
        """以關口回傳的形狀呈現"""
        if self.mime_type.startswith("application/vnd.google-apps."):
            return DriveFile(self.id, self.name, self.mime_type, tuple(self.parents), trashed=self.trashed)
        return DriveFile(
            self.id, self.name, self.mime_type, tuple(self.parents), hashlib.md5(self.content).hexdigest(),
            self.revisions[-1][0] if self.revisions else "", self.trashed,
        )


class FakeDrive:
    """記憶體裡的 Drive：實作 Drive 關口"""

    def __init__(self, google: FakeGoogle):
        """空的 Drive

        Args:
            google: 計請求與排定錯誤的假 Google
        """
        self._google = google
        self._items: Dict[str, _DriveItem] = {}
        self._ids = (f"drive-{n}" for n in itertools.count(1))
        self._generated = (f"gen-{n}" for n in itertools.count(1))
        self._revision_ids = (f"rev-{n}" for n in itertools.count(1))
        # generate_ids 發出、還沒用掉的 ID（以指定 ID 建檔只收這些，照 Drive 的規定）
        self._issued: set = set()
        # 第幾次上傳要丟出的錯誤（fail_upload）與已經上傳了幾次
        self._upload_failures: Dict[int, Tuple[CatalogErrorKind, bool]] = {}
        self._uploads = 0
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

    def is_trashed(self, file_id: str) -> bool:
        """檔案是否在垃圾桶裡"""
        return self._items[file_id].trashed

    def file(self, file_id: str) -> DriveFile:
        """檔案目前的樣子"""
        return self._items[file_id].as_file()

    def children(self, folder_id: str) -> List[DriveFile]:
        """資料夾裡不在垃圾桶的檔案與資料夾"""
        return [item.as_file() for item in self._items.values() if folder_id in item.parents and not item.trashed]

    def content(self, file_id: str) -> bytes:
        """檔案目前的內容"""
        return self._items[file_id].content

    def revisions(self, file_id: str) -> List[str]:
        """檔案各次內容的版本 ID，由舊到新"""
        return [revision for revision, _ in self._items[file_id].revisions]

    def set_content(self, file_id: str, content: bytes) -> None:
        """換掉檔案的內容（例如另一台電腦在 Drive 網頁上傳了新版本），成為新的版本"""
        self._items[file_id].revisions.append((next(self._revision_ids), content))

    def fail_upload(self, number: int, kind: CatalogErrorKind, applied: bool = False) -> None:
        """第 number 次上傳（upload，從 1 起算）丟出 kind 類的錯誤；applied 時照常上傳之後才丟出（回應沒收到）"""
        self._upload_failures[number] = (kind, applied)

    def files_named(self, name: str) -> List[DriveFile]:
        """名稱是 name、不在垃圾桶的檔案（不分資料夾）"""
        return [item.as_file() for item in self._items.values() if item.name == name and not item.trashed]

    # --- Drive 關口 ---

    def list_files(self, query: str, page_token: str = "") -> DriveListing:
        """依查詢字串列出一頁檔案（依名稱排序）"""
        def handle():
            """篩選、依名稱排序後切出這一頁"""
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

    def get_file(self, file_id: str) -> DriveFile:
        """取檔案的中繼資料；沒有這個檔時找不到"""
        return self._google.request(lambda: self._item(file_id).as_file())

    def create_folder(self, name: str, parent: str, file_id: str = "") -> DriveFile:
        """在 parent 裡建立資料夾；給了 file_id 時以它為 ID（那個 ID 已有檔案時回 409）"""
        def handle():
            """確認上層資料夾存在後建立"""
            if parent != ROOT:
                self._item(parent)
            return self._items[self._add(name, DRIVE_FOLDER_MIME, parent, b"", self._claim(file_id))].as_file()
        return self._google.request(handle)

    def generate_ids(self, count: int) -> List[str]:
        """發出 count 個之後可以拿來建檔的 ID"""
        def handle():
            """發 ID 並記下"""
            ids = [next(self._generated) for _ in range(count)]
            self._issued.update(ids)
            return ids
        return self._google.request(handle)

    def upload(self, file_id: str, name: str, parent: str, content: bytes) -> DriveFile:
        """以預先要到的 ID 上傳一個 PDF；那個 ID 已有檔案時回 409"""
        self._uploads += 1
        if self._uploads in self._upload_failures:
            kind, applied = self._upload_failures.pop(self._uploads)
            self._google.fail_at(self._google.requests + 1, kind, applied=applied)

        def handle():
            """確認上層資料夾存在後建立"""
            if parent != ROOT:
                self._item(parent)
            return self._items[self._add(name, DRIVE_PDF_MIME, parent, content, self._claim(file_id))].as_file()
        return self._google.request(handle)

    def update_content(
        self, file_id: str, content: bytes, name: str = "", add_parent: str = "", remove_parents: Sequence[str] = (),
    ) -> DriveFile:
        """換掉內容（新的版本），同時改名與搬移"""
        def handle():
            """確認檔案與目標資料夾存在後更新"""
            item = self._item(file_id)
            if add_parent and add_parent != ROOT:
                self._item(add_parent)
            item.revisions.append((next(self._revision_ids), content))
            self._relocate(item, name, add_parent, remove_parents)
            return item.as_file()
        return self._google.request(handle)

    def rename_or_move(self, file_id: str, name: str = "", add_parent: str = "", remove_parents: Sequence[str] = ()) -> None:
        """改名、搬到 add_parent 並離開 remove_parents（一次請求）"""
        def handle():
            """確認檔案與目標資料夾存在後改名、搬移"""
            item = self._item(file_id)
            if add_parent and add_parent != ROOT:
                self._item(add_parent)
            self._relocate(item, name, add_parent, remove_parents)
        self._google.request(handle)

    def move_to_trash(self, file_id: str) -> None:
        """把檔案丟進垃圾桶；沒有這個檔時找不到"""
        def handle():
            """標成在垃圾桶裡"""
            self._item(file_id).trashed = True
        self._google.request(handle)

    @staticmethod
    def _relocate(item: _DriveItem, name: str, add_parent: str, remove_parents: Sequence[str]) -> None:
        """改名、加進 add_parent 並離開 remove_parents"""
        if name:
            item.name = name
        if add_parent or remove_parents:
            kept = [p for p in item.parents if p not in remove_parents]
            item.parents = kept + ([add_parent] if add_parent and add_parent not in kept else [])

    def _claim(self, file_id: str) -> str:
        """以指定 ID 建檔前的檢查：已有檔案時照 Drive 回 409，不是 generate_ids 發出的 ID 時回 400"""
        if not file_id:
            return ""
        if file_id in self._items:
            raise CatalogError(CatalogErrorKind.EXISTS, f"A file already exists with the provided ID: {file_id}.")
        if file_id not in self._issued:
            raise CatalogError(CatalogErrorKind.OTHER, f"The provided file ID is not usable: {file_id}.")
        self._issued.discard(file_id)
        return file_id

    def _item(self, file_id: str) -> _DriveItem:
        """取一個項目；沒有時照 Drive 回 404"""
        if file_id not in self._items:
            raise CatalogError(CatalogErrorKind.NOT_FOUND, f"File not found: {file_id}.")
        return self._items[file_id]

    def _add(self, name: str, mime_type: str, parent: str, content: bytes, item_id: str = "") -> str:
        """加一個項目（檔案有第一個版本），回傳 ID；沒給 ID 時自己編一個"""
        item_id = item_id or next(self._ids)
        revisions = [] if mime_type == DRIVE_FOLDER_MIME else [(next(self._revision_ids), content)]
        self._items[item_id] = _DriveItem(item_id, name, mime_type, [parent], revisions)
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
