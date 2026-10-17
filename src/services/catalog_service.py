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
    overview = catalog.overview(root_folder_id)      # 曲目、作曲家、根資料夾內容
    detail = catalog.piece_detail(piece_id)
    platform = catalog.publishing_platform()         # 社團設定啟用的發譜平台，沒有時為 None
    rows = catalog.new_rows(SHEET_PIECES, [{"title": "春江花月夜"}])
    ids = catalog.add_rows(rows)                     # 失敗時以同一個 rows 重送，不會重複新增
"""
import time
import uuid
from dataclasses import fields
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Set, Type, TypeVar

from core.catalog_constants import (
    CLUB_SETTING_PUBLISHING_PLATFORM, DRIVE_FOLDER_MIME, DRIVE_PDF_MIME, DRIVE_SPREADSHEET_MIME,
    PUBLISHING_PLATFORM_NAMES, SHEET_CLUB_SETTINGS, SHEET_COMPOSERS, SHEET_EDITIONS, SHEET_HEADERS, SHEET_MOVEMENTS,
    SHEET_PARTS, SHEET_PERFORMANCES, SHEET_PIECES, PublishingPlatform,
)
from core.catalog_errors import CatalogError, CatalogErrorKind
from core.constants import CATALOG_ROOT_FOLDER_ID_KEY
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


def _records(rows: List[List[str]], sheet: str) -> List[Dict[str, str]]:
    """把工作表讀到的列（含標題列）轉成欄位名稱對值；標題列與空列略過"""
    headers = SHEET_HEADERS[sheet]
    return [
        {header: (row[i] if i < len(row) else "") for i, header in enumerate(headers)}
        for row in rows[1:] if any(row)
    ]


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
            raise CatalogError(CatalogErrorKind.NOT_FOUND, f"{CATALOG_ROOT_FOLDER_ID_KEY} is empty")
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

    def new_movement(self, piece_id: str) -> PendingRows:
        """準備一首曲目的新樂章：寫入前一刻才讀這首曲目已有的樂章，編號與排序接在它們之後（一次請求）

        Args:
            piece_id: 曲目 ID

        Returns:
            待新增的一列；交給 add_rows 送出，失敗時以同一個物件重送

        Raises:
            CatalogError: 讀不到
        """
        rows = self._read_sheets(SHEET_MOVEMENTS)[0]
        count = sum(1 for movement in _models(rows, SHEET_MOVEMENTS, Movement) if movement.piece_id == piece_id)
        number = str(count + 1)
        return self.new_rows(SHEET_MOVEMENTS, [{"piece_id": piece_id, "movement_number": number, "sort_order": number}])

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
