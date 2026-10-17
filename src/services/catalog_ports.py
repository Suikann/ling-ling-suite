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

from core.catalog_models import DriveFile, DriveListing

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
    """Drive 關口：一次呼叫一次請求"""

    def list_files(self, query: str, page_token: str = "") -> DriveListing:
        """以 Drive 的查詢字串列出檔案的一頁（files.list，依名稱排序）

        Args:
            query: Drive 的查詢字串；字串值裡的反斜線與單引號由呼叫端跳脫（見 services/catalog_service.py 的 drive_literal）
            page_token: 上一頁回傳的 next_page_token；第一頁為空字串

        Returns:
            這一頁的檔案（含 mimeType、parents 與 md5Checksum）與下一頁的 token

        Raises:
            CatalogError: 列出失敗
        """

    def get_file(self, file_id: str) -> DriveFile:
        """取一個檔案的中繼資料（files.get，含 parents 與 md5Checksum）

        Raises:
            CatalogError: 讀取失敗；沒有這個檔或存取被拒時為找不到
        """

    def create_folder(self, name: str, parent: str) -> DriveFile:
        """在 parent 裡建立資料夾（files.create）

        Raises:
            CatalogError: 建立失敗
        """

    def rename_or_move(self, file_id: str, name: str = "", add_parent: str = "", remove_parents: Sequence[str] = ()) -> None:
        """改名與搬移（files.update，一次請求）：name 不為空時改名，add_parent 不為空時加進那個資料夾，並離開 remove_parents

        Raises:
            CatalogError: 改不了，名稱與位置都沒變
        """

