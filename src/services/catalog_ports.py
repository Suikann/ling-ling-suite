# -*- coding: utf-8 -*-
"""
譜庫的兩個關口：試算表與 Drive

譜庫服務（services/catalog_service.py）只經過這兩個介面碰 Google；一次方法呼叫就是一次 Google 請求。
真正接 Google 的是通事（services/google_catalog.py），測試以記憶體裡的假 Google 實作同樣的介面。
失敗時一律丟出 CatalogError（core/catalog_errors.py），不以空值、空清單或 False 代表失敗。

範圍以 A1 表示法寫成「'工作表'!A1:C3」（見 sheet_range）；寫進試算表的值一律當成純文字（RAW），
以「=」開頭也不會變成公式。

使用範例：
    from services.catalog_ports import SheetsPort, DrivePort, sheet_range
    values = sheets.read_ranges([sheet_range("曲目", "A:ZZ")])[0]
    listing = drive.list_files("'root' in parents and trashed = false")
"""
from typing import Dict, List, Mapping, Protocol, Sequence, Tuple

from core.catalog_models import DriveFile, DriveListing, DriveRevision

# 一個範圍與要寫進去的值（由左上角往右下填）
CellWrite = Tuple[str, List[List[str]]]


def sheet_range(sheet: str, cells: str) -> str:
    """組出工作表裡的 A1 範圍，工作表名加單引號（名稱裡的單引號寫成兩個）

    Args:
        sheet: 工作表名稱
        cells: 工作表裡的範圍，例如 "A:ZZ"、"B5"、"A2:C3"

    Returns:
        例如 "'曲目'!A:ZZ"
    """
    quoted = sheet.replace("'", "''")
    return f"'{quoted}'!{cells}"


class SheetsPort(Protocol):
    """試算表關口：一份譜庫試算表的讀寫，一次呼叫一次請求"""

    def read_ranges(self, ranges: Sequence[str]) -> List[List[List[str]]]:
        """讀多個範圍（values.batchGet）

        Args:
            ranges: A1 範圍

        Returns:
            依 ranges 的順序，每個範圍的列；每列結尾的空格與結尾的空列不回傳

        Raises:
            CatalogError: 讀取失敗；任一範圍的工作表不存在時整個請求失敗
        """

    def write_cells(self, data: Sequence[CellWrite]) -> None:
        """一次寫入多個範圍（一個 values.batchUpdate，全成或全不成），值以 RAW 寫入

        Raises:
            CatalogError: 寫入失敗，沒有任何一格被寫入
        """

    def append_rows(self, sheet: str, rows: Sequence[List[str]]) -> None:
        """在工作表最後一列之後新增多列（values.append），值以 RAW 寫入

        Raises:
            CatalogError: 新增失敗
        """

    def add_sheet(self, title: str) -> None:
        """建立一張空的工作表

        Raises:
            CatalogError: 建立失敗，例如已有同名的工作表
        """

    def sheet_titles(self) -> List[str]:
        """列出試算表裡所有工作表的名稱（spreadsheets.get，只取名稱）

        讀不存在的工作表會讓整個讀取請求失敗，可以先以它確認工作表在不在。

        Returns:
            工作表名稱，依試算表裡的順序

        Raises:
            CatalogError: 讀取失敗
        """

    def sheet_ids(self) -> Dict[str, int]:
        """每張工作表的名稱與 sheetId（spreadsheets.get）

        Raises:
            CatalogError: 讀取失敗
        """

    def delete_rows(self, rows: Sequence[Tuple[int, int]]) -> None:
        """一次刪掉多列（一個 batchUpdate，全成或全不成）

        Args:
            rows: （sheetId，從 0 起算的列號）；列號都以這次刪除之前的為準

        Raises:
            CatalogError: 刪除失敗，沒有任何一列被刪掉
        """

    def create_spreadsheet(self, title: str, sheets: Mapping[str, Sequence[str]]) -> str:
        """建立一份新的試算表（spreadsheets.create，放在 Drive 的根資料夾），跟這個關口連著的那份無關

        Args:
            title: 試算表名稱
            sheets: 工作表名稱與標題列；每張的標題列都凍結

        Returns:
            新試算表的 ID（也是它在 Drive 上的檔案 ID）

        Raises:
            CatalogError: 建立失敗
        """


class DrivePort(Protocol):
    """Drive 關口：一次呼叫一次請求（resumable 上傳的大檔除外：內容分段送出）"""

    def list_files(self, query: str, page_token: str = "") -> DriveListing:
        """以 Drive 的查詢字串列出檔案的一頁（files.list，依名稱排序）

        Args:
            query: Drive 的查詢字串；字串值裡的反斜線與單引號由呼叫端跳脫（見 services/catalog_service.py 的 drive_literal）
            page_token: 上一頁回傳的 next_page_token；第一頁為空字串

        Returns:
            這一頁的檔案（含 mimeType、parents、md5Checksum、headRevisionId 與 trashed）與下一頁的 token

        Raises:
            CatalogError: 列出失敗
        """

    def get_file(self, file_id: str) -> DriveFile:
        """取一個檔案的中繼資料（files.get，含 parents、md5Checksum、headRevisionId 與 trashed；在垃圾桶裡的也取得到）

        Raises:
            CatalogError: 讀取失敗；沒有這個檔或存取被拒時為找不到
        """

    def create_folder(self, name: str, parent: str, file_id: str = "") -> DriveFile:
        """在 parent 裡建立資料夾（files.create）

        Args:
            name: 資料夾名稱
            parent: 上層資料夾的 ID
            file_id: 預先以 generate_ids 要到的 ID；空字串時由 Drive 給

        Raises:
            CatalogError: 建立失敗；file_id 已經有檔案時（重送）為已經存在
        """

    def generate_ids(self, count: int) -> List[str]:
        """預先向 Drive 要 count 個檔案 ID（files.generateIds），之後以它們建檔，重送時不會建出第二份

        Raises:
            CatalogError: 要不到
        """

    def upload(self, file_id: str, name: str, parent: str, content: bytes) -> DriveFile:
        """以預先要到的 ID 在 parent 裡上傳一個 PDF（files.create，大檔用 resumable 上傳）

        Returns:
            上傳後的檔案（含 md5Checksum 與 headRevisionId）

        Raises:
            CatalogError: 上傳失敗；file_id 已經有檔案時（重送）為已經存在
        """

    def update_content(
        self, file_id: str, content: bytes, name: str = "", add_parent: str = "", remove_parents: Sequence[str] = (),
    ) -> DriveFile:
        """換掉檔案的內容（files.update，檔案 ID 不變，舊內容成為 Drive 的版本），同一個請求裡改名與搬移

        Args:
            file_id: 檔案 ID
            content: 新內容
            name: 不為空時改成這個名稱
            add_parent: 不為空時加進這個資料夾
            remove_parents: 離開這些資料夾

        Returns:
            更新後的檔案（含新的 md5Checksum 與 headRevisionId）

        Raises:
            CatalogError: 更新失敗，內容、名稱與位置都沒變
        """

    def rename_or_move(self, file_id: str, name: str = "", add_parent: str = "", remove_parents: Sequence[str] = ()) -> None:
        """改名與搬移（files.update，一次請求）：name 不為空時改名，add_parent 不為空時加進那個資料夾，並離開 remove_parents

        Raises:
            CatalogError: 改不了，名稱與位置都沒變
        """

    def move_to_trash(self, file_id: str) -> None:
        """把檔案丟進 Drive 垃圾桶（files.update，trashed 設為 true）；約 30 天內可從垃圾桶救回

        Raises:
            CatalogError: 丟不進去，檔案還在原處
        """

    def download(self, file_id: str) -> bytes:
        """下載一個檔案目前的內容（files.get，alt=media）

        Returns:
            檔案內容的原始位元組

        Raises:
            CatalogError: 下載失敗；沒有這個檔時為找不到
        """

    def list_revisions(self, file_id: str) -> List[DriveRevision]:
        """列出檔案 Drive 還留著的版本（revisions.list，一頁最多 1000 個；Drive 最多留最近 100 個版本，一頁就列完）

        Returns:
            由舊到新，最後一個是目前的內容

        Raises:
            CatalogError: 列出失敗；沒有這個檔時為找不到
        """

    def keep_revision(self, file_id: str, revision_id: str, keep: bool) -> None:
        """設定一個版本要不要永久保留（revisions.update 的 keepForever）；沒設的舊版本約 30 天後會被 Drive 清掉

        Raises:
            CatalogError: 設定失敗；版本已被清掉時為找不到
        """

    def download_revision(self, file_id: str, revision_id: str) -> bytes:
        """下載檔案的一個版本的內容（revisions.get，alt=media）

        Returns:
            那個版本內容的原始位元組

        Raises:
            CatalogError: 下載失敗；版本已被清掉時為找不到
        """
