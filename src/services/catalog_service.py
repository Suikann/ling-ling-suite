# -*- coding: utf-8 -*-
"""
譜庫服務

讀寫譜庫的唯一入口：UI 與其他服務只呼叫它，它對下只經過試算表與 Drive 兩個關口（services/catalog_ports.py）。
失敗時丟出帶錯誤類別的 CatalogError（core/catalog_errors.py），不以空值或空清單代表失敗。

所有讀寫共用的規則都在這裡：遇到 429（達到用量上限）以指數退避自動重試；以名稱搜尋 Drive 時先跳脫反斜線、
再跳脫單引號（drive_literal）；新增列時抽的 8 碼 ID 跟同一張工作表既有的撞號就重抽，同一批列重送前先以它們的 ID
檢查哪些已經寫進去，不產生重複列（new_rows、add_rows）。試算表的值一律以 RAW 寫入由通事負責。

網路請求會卡住呼叫的執行緒，UI 要在背景執行緒呼叫。

使用範例：
    from services.catalog_service import CatalogService
    catalog = CatalogService(sheets, drive)          # 兩個關口；真的 Google 見 services/google_catalog.py
    overview = catalog.overview(root_folder_id)      # 曲目、作曲家、根資料夾內容
    detail = catalog.piece_detail(piece_id)
    rows = catalog.new_rows(SHEET_PIECES, [{"title": "春江花月夜"}])
    ids = catalog.add_rows(rows)                     # 失敗時以同一個 rows 重送，不會重複新增
"""
import time
import uuid
from dataclasses import fields
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Set, Type, TypeVar

from core.catalog_constants import (
    DRIVE_FOLDER_MIME, DRIVE_PDF_MIME, SHEET_COMPOSERS, SHEET_EDITIONS, SHEET_HEADERS, SHEET_MOVEMENTS, SHEET_PARTS,
    SHEET_PERFORMANCES, SHEET_PIECES,
)
from core.catalog_errors import CatalogError, CatalogErrorKind
from core.catalog_models import (
    CatalogOverview, Composer, DriveFile, Edition, EditionFiles, FolderContents, Movement, Part, Performance, Piece,
    PieceDetail,
)
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
        """讀譜庫視窗開啟時要的全部：曲目、作曲家、哪個資料夾屬於哪首曲目，以及根資料夾的內容

        試算表只讀一次，請求數不隨曲目數增加。

        Args:
            root_folder_id: 譜庫根資料夾的 Drive ID

        Returns:
            譜庫總覽

        Raises:
            CatalogError: 讀不到；沒設定根資料夾時為找不到
        """
        if not root_folder_id:
            raise CatalogError(CatalogErrorKind.NOT_FOUND, "catalog_root_folder_id is empty")
        pieces_rows, composer_rows, edition_rows = self._read_sheets(SHEET_PIECES, SHEET_COMPOSERS, SHEET_EDITIONS)
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
        )

    def folder_contents(self, folder_id: str) -> FolderContents:
        """列出 Drive 資料夾裡的子資料夾與 PDF（各依名稱排序，不含垃圾桶裡的）

        Raises:
            CatalogError: 讀不到
        """
        children = self._list_files(f"{drive_literal(folder_id)} in parents and trashed = false")
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
        return self._list_files(
            f"{drive_literal(folder_id)} in parents and name = {drive_literal(name)} and trashed = false",
        )

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
            column = self._sheets.read_ranges([sheet_range(rows.sheet, "A:A")])[0]
            values = rows.to_send({line[0] for line in column[1:] if line}, self._draw_ids)
            if values:
                rows.mark_sent()
                self._sheets.append_rows(rows.sheet, values)
        self._call(send)
        return rows.ids

    def write_cells(self, data: Sequence[CellWrite]) -> None:
        """一次寫入多個範圍（一個請求，全成或全不成）

        Args:
            data: （A1 範圍，值）的清單；範圍以 services.catalog_ports.sheet_range 組出

        Raises:
            CatalogError: 寫不進去，沒有任何一格被寫入
        """
        self._call(lambda: self._sheets.write_cells(data))

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
