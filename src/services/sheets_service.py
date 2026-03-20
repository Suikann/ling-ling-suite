# -*- coding: utf-8 -*-
"""
Google Sheets 譜庫服務

透過 Google Sheets API 提供譜庫目錄的 CRUD 操作。

使用範例：
    from services.sheets_service import SheetsService
    service = SheetsService(credentials)
    composers = service.list_composers()
"""
import uuid
from dataclasses import fields, asdict
from typing import Dict, List, Optional, Type, TypeVar
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials
from core.catalog_constants import (
    CATALOG_SPREADSHEET_NAME, SHEET_HEADERS,
    SHEET_COMPOSERS, SHEET_PIECES, SHEET_EDITIONS,
    SHEET_MOVEMENTS, SHEET_PARTS, SHEET_PERFORMANCES,
)
from core.catalog_models import (
    Composer, Edition, Movement, Part, Performance, Piece, PieceDetail,
)

T = TypeVar("T")


def _generate_id() -> str:
    """產生唯一識別碼"""
    return str(uuid.uuid4())[:8]


def _row_to_model(row: List[str], headers: List[str], model_class: Type[T]) -> T:
    """將試算表列轉換為資料模型

    Args:
        row: 試算表的一列資料
        headers: 欄位標題
        model_class: 目標資料模型類別

    Returns:
        資料模型實例
    """
    model_fields = {f.name for f in fields(model_class)}
    kwargs = {}
    for i, header in enumerate(headers):
        if header in model_fields and i < len(row):
            kwargs[header] = row[i]
        elif header in model_fields:
            kwargs[header] = ""
    return model_class(**kwargs)


def _model_to_row(model: object, headers: List[str]) -> List[str]:
    """將資料模型轉換為試算表列

    Args:
        model: 資料模型實例
        headers: 欄位標題

    Returns:
        試算表列資料
    """
    data = asdict(model) if hasattr(model, "__dataclass_fields__") else {}
    return [str(data.get(h, "")) for h in headers]


class SheetsService:
    """Google Sheets 譜庫 CRUD 服務"""

    def __init__(self, credentials: Credentials, spreadsheet_id: str = ""):
        self._credentials = credentials
        self._spreadsheet_id = spreadsheet_id
        self._service = build("sheets", "v4", credentials=credentials)
        self._sheets = self._service.spreadsheets()

    @property
    def spreadsheet_id(self) -> str:
        return self._spreadsheet_id

    @spreadsheet_id.setter
    def spreadsheet_id(self, value: str):
        self._spreadsheet_id = value

    def create_catalog_spreadsheet(self) -> str:
        """建立新的譜庫試算表，包含所有工作表與標題列

        Returns:
            新建試算表的 ID
        """
        sheet_props = [
            {"properties": {"title": name}} for name in SHEET_HEADERS
        ]
        body = {
            "properties": {"title": CATALOG_SPREADSHEET_NAME},
            "sheets": sheet_props,
        }
        result = self._sheets.create(body=body).execute()
        self._spreadsheet_id = result["spreadsheetId"]
        batch_data = []
        for sheet_name, headers in SHEET_HEADERS.items():
            batch_data.append({
                "range": f"'{sheet_name}'!A1",
                "values": [headers],
            })
        self._sheets.values().batchUpdate(
            spreadsheetId=self._spreadsheet_id,
            body={"valueInputOption": "RAW", "data": batch_data},
        ).execute()
        self._freeze_header_rows()
        return self._spreadsheet_id

    def _freeze_header_rows(self):
        """凍結所有工作表的標題列"""
        spreadsheet = self._sheets.get(
            spreadsheetId=self._spreadsheet_id,
        ).execute()
        requests = []
        for sheet in spreadsheet.get("sheets", []):
            sheet_id = sheet["properties"]["sheetId"]
            requests.append({
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 1},
                    },
                    "fields": "gridProperties.frozenRowCount",
                },
            })
        if requests:
            self._sheets.batchUpdate(
                spreadsheetId=self._spreadsheet_id,
                body={"requests": requests},
            ).execute()

    # --- 通用讀寫 ---

    def _read_all_rows(self, sheet_name: str) -> List[List[str]]:
        """讀取工作表所有資料列（不含標題）"""
        result = self._sheets.values().get(
            spreadsheetId=self._spreadsheet_id,
            range=f"'{sheet_name}'!A:ZZ",
        ).execute()
        rows = result.get("values", [])
        return rows[1:] if len(rows) > 1 else []

    def _append_row(self, sheet_name: str, row: List[str]):
        """在工作表末尾新增一列"""
        self._sheets.values().append(
            spreadsheetId=self._spreadsheet_id,
            range=f"'{sheet_name}'!A:A",
            valueInputOption="RAW",
            body={"values": [row]},
        ).execute()

    def _update_row(self, sheet_name: str, row_index: int, row: List[str]):
        """更新工作表的指定列（row_index 從 0 開始，不含標題列）"""
        actual_row = row_index + 2
        self._sheets.values().update(
            spreadsheetId=self._spreadsheet_id,
            range=f"'{sheet_name}'!A{actual_row}",
            valueInputOption="RAW",
            body={"values": [row]},
        ).execute()

    def _delete_row(self, sheet_name: str, row_index: int):
        """刪除工作表的指定列（row_index 從 0 開始，不含標題列）"""
        spreadsheet = self._sheets.get(
            spreadsheetId=self._spreadsheet_id,
        ).execute()
        sheet_id = None
        for sheet in spreadsheet.get("sheets", []):
            if sheet["properties"]["title"] == sheet_name:
                sheet_id = sheet["properties"]["sheetId"]
                break
        if sheet_id is None:
            return
        actual_row = row_index + 1
        self._sheets.batchUpdate(
            spreadsheetId=self._spreadsheet_id,
            body={"requests": [{
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": actual_row,
                        "endIndex": actual_row + 1,
                    },
                },
            }]},
        ).execute()

    def _find_row_index(self, sheet_name: str, record_id: str) -> int:
        """依 ID 欄位找到資料列索引（不含標題列）

        Args:
            sheet_name: 工作表名稱
            record_id: 記錄 ID

        Returns:
            列索引（從 0 開始），找不到時回傳 -1
        """
        rows = self._read_all_rows(sheet_name)
        for i, row in enumerate(rows):
            if row and row[0] == record_id:
                return i
        return -1

    def _list_models(
        self, sheet_name: str, model_class: Type[T],
    ) -> List[T]:
        """通用：讀取工作表所有資料並轉為模型清單"""
        headers = SHEET_HEADERS[sheet_name]
        rows = self._read_all_rows(sheet_name)
        return [_row_to_model(row, headers, model_class) for row in rows]

    def _get_model(
        self, sheet_name: str, record_id: str, model_class: Type[T],
    ) -> Optional[T]:
        """通用：依 ID 取得單筆模型"""
        headers = SHEET_HEADERS[sheet_name]
        rows = self._read_all_rows(sheet_name)
        for row in rows:
            if row and row[0] == record_id:
                return _row_to_model(row, headers, model_class)
        return None

    def _create_model(
        self, sheet_name: str, model: object, headers: List[str],
    ) -> str:
        """通用：新增一筆記錄，自動產生 ID

        Returns:
            新記錄的 ID
        """
        record_id = _generate_id()
        data = asdict(model) if hasattr(model, "__dataclass_fields__") else {}
        data["id"] = record_id
        row = [str(data.get(h, "")) for h in headers]
        self._append_row(sheet_name, row)
        return record_id

    def _update_model(
        self, sheet_name: str, model: object, headers: List[str],
    ) -> bool:
        """通用：更新一筆記錄

        Returns:
            是否成功更新
        """
        data = asdict(model) if hasattr(model, "__dataclass_fields__") else {}
        record_id = data.get("id", "")
        if not record_id:
            return False
        row_index = self._find_row_index(sheet_name, record_id)
        if row_index < 0:
            return False
        row = [str(data.get(h, "")) for h in headers]
        self._update_row(sheet_name, row_index, row)
        return True

    def _delete_model(self, sheet_name: str, record_id: str) -> bool:
        """通用：刪除一筆記錄

        Returns:
            是否成功刪除
        """
        row_index = self._find_row_index(sheet_name, record_id)
        if row_index < 0:
            return False
        self._delete_row(sheet_name, row_index)
        return True

    # --- 作曲家 ---

    def list_composers(self) -> List[Composer]:
        """列出所有作曲家"""
        return self._list_models(SHEET_COMPOSERS, Composer)

    def get_composer(self, composer_id: str) -> Optional[Composer]:
        """依 ID 取得作曲家"""
        return self._get_model(SHEET_COMPOSERS, composer_id, Composer)

    def create_composer(self, composer: Composer) -> str:
        """新增作曲家

        Returns:
            新記錄的 ID
        """
        return self._create_model(
            SHEET_COMPOSERS, composer, SHEET_HEADERS[SHEET_COMPOSERS],
        )

    def update_composer(self, composer: Composer) -> bool:
        """更新作曲家"""
        return self._update_model(
            SHEET_COMPOSERS, composer, SHEET_HEADERS[SHEET_COMPOSERS],
        )

    def delete_composer(self, composer_id: str) -> bool:
        """刪除作曲家"""
        return self._delete_model(SHEET_COMPOSERS, composer_id)

    # --- 曲目 ---

    def list_pieces(self) -> List[Piece]:
        """列出所有曲目"""
        return self._list_models(SHEET_PIECES, Piece)

    def get_piece(self, piece_id: str) -> Optional[Piece]:
        """依 ID 取得曲目"""
        return self._get_model(SHEET_PIECES, piece_id, Piece)

    def create_piece(self, piece: Piece) -> str:
        """新增曲目"""
        return self._create_model(
            SHEET_PIECES, piece, SHEET_HEADERS[SHEET_PIECES],
        )

    def update_piece(self, piece: Piece) -> bool:
        """更新曲目"""
        return self._update_model(
            SHEET_PIECES, piece, SHEET_HEADERS[SHEET_PIECES],
        )

    def delete_piece(self, piece_id: str) -> bool:
        """刪除曲目"""
        return self._delete_model(SHEET_PIECES, piece_id)

    # --- 版本 ---

    def list_editions(self, piece_id: str = "") -> List[Edition]:
        """列出版本，可依曲目篩選"""
        all_editions = self._list_models(SHEET_EDITIONS, Edition)
        if piece_id:
            return [e for e in all_editions if e.piece_id == piece_id]
        return all_editions

    def get_edition(self, edition_id: str) -> Optional[Edition]:
        """依 ID 取得版本"""
        return self._get_model(SHEET_EDITIONS, edition_id, Edition)

    def create_edition(self, edition: Edition) -> str:
        """新增版本"""
        return self._create_model(
            SHEET_EDITIONS, edition, SHEET_HEADERS[SHEET_EDITIONS],
        )

    def update_edition(self, edition: Edition) -> bool:
        """更新版本"""
        return self._update_model(
            SHEET_EDITIONS, edition, SHEET_HEADERS[SHEET_EDITIONS],
        )

    def delete_edition(self, edition_id: str) -> bool:
        """刪除版本"""
        return self._delete_model(SHEET_EDITIONS, edition_id)

    # --- 樂章 ---

    def list_movements(self, piece_id: str = "") -> List[Movement]:
        """列出樂章，可依曲目篩選"""
        all_movements = self._list_models(SHEET_MOVEMENTS, Movement)
        if piece_id:
            return [m for m in all_movements if m.piece_id == piece_id]
        return sorted(all_movements, key=lambda m: m.sort_order)

    def get_movement(self, movement_id: str) -> Optional[Movement]:
        """依 ID 取得樂章"""
        return self._get_model(SHEET_MOVEMENTS, movement_id, Movement)

    def create_movement(self, movement: Movement) -> str:
        """新增樂章"""
        return self._create_model(
            SHEET_MOVEMENTS, movement, SHEET_HEADERS[SHEET_MOVEMENTS],
        )

    def update_movement(self, movement: Movement) -> bool:
        """更新樂章"""
        return self._update_model(
            SHEET_MOVEMENTS, movement, SHEET_HEADERS[SHEET_MOVEMENTS],
        )

    def delete_movement(self, movement_id: str) -> bool:
        """刪除樂章"""
        return self._delete_model(SHEET_MOVEMENTS, movement_id)

    # --- 分譜 ---

    def list_parts(
        self, edition_id: str = "", movement_id: str = "",
    ) -> List[Part]:
        """列出分譜，可依版本或樂章篩選"""
        all_parts = self._list_models(SHEET_PARTS, Part)
        result = all_parts
        if edition_id:
            result = [p for p in result if p.edition_id == edition_id]
        if movement_id:
            result = [p for p in result if p.movement_id == movement_id]
        return sorted(result, key=lambda p: p.sort_order)

    def get_part(self, part_id: str) -> Optional[Part]:
        """依 ID 取得分譜"""
        return self._get_model(SHEET_PARTS, part_id, Part)

    def create_part(self, part: Part) -> str:
        """新增分譜"""
        return self._create_model(
            SHEET_PARTS, part, SHEET_HEADERS[SHEET_PARTS],
        )

    def update_part(self, part: Part) -> bool:
        """更新分譜"""
        return self._update_model(
            SHEET_PARTS, part, SHEET_HEADERS[SHEET_PARTS],
        )

    def delete_part(self, part_id: str) -> bool:
        """刪除分譜"""
        return self._delete_model(SHEET_PARTS, part_id)

    # --- 演出紀錄 ---

    def list_performances(self, piece_id: str = "") -> List[Performance]:
        """列出演出紀錄，可依曲目篩選"""
        all_perfs = self._list_models(SHEET_PERFORMANCES, Performance)
        if piece_id:
            return [p for p in all_perfs if p.piece_id == piece_id]
        return sorted(all_perfs, key=lambda p: p.performance_date, reverse=True)

    def get_performance(self, performance_id: str) -> Optional[Performance]:
        """依 ID 取得演出紀錄"""
        return self._get_model(SHEET_PERFORMANCES, performance_id, Performance)

    def create_performance(self, performance: Performance) -> str:
        """新增演出紀錄"""
        return self._create_model(
            SHEET_PERFORMANCES, performance, SHEET_HEADERS[SHEET_PERFORMANCES],
        )

    def update_performance(self, performance: Performance) -> bool:
        """更新演出紀錄"""
        return self._update_model(
            SHEET_PERFORMANCES, performance, SHEET_HEADERS[SHEET_PERFORMANCES],
        )

    def delete_performance(self, performance_id: str) -> bool:
        """刪除演出紀錄"""
        return self._delete_model(SHEET_PERFORMANCES, performance_id)

    # --- 複合查詢 ---

    def get_piece_detail(self, piece_id: str) -> Optional[PieceDetail]:
        """取得曲目的完整資訊（含作曲家、版本、樂章、分譜、演出紀錄）

        Args:
            piece_id: 曲目 ID

        Returns:
            曲目完整資訊，找不到時回傳 None
        """
        piece = self.get_piece(piece_id)
        if not piece:
            return None
        composer = self.get_composer(piece.composer_id) if piece.composer_id else None
        editions = self.list_editions(piece_id)
        movements = self.list_movements(piece_id)
        edition_ids = {e.id for e in editions}
        all_parts = self._list_models(SHEET_PARTS, Part)
        parts = [p for p in all_parts if p.edition_id in edition_ids]
        performances = self.list_performances(piece_id)
        return PieceDetail(
            piece=piece,
            composer=composer,
            editions=editions,
            movements=movements,
            parts=parts,
            performances=performances,
        )

    def search_pieces(
        self, query: str = "", composer_id: str = "", genre: str = "",
    ) -> List[Piece]:
        """搜尋曲目

        Args:
            query: 關鍵字（比對標題、作品號、目錄號）
            composer_id: 篩選作曲家
            genre: 篩選曲種

        Returns:
            符合條件的曲目清單
        """
        pieces = self.list_pieces()
        results = pieces
        if composer_id:
            results = [p for p in results if p.composer_id == composer_id]
        if genre:
            results = [p for p in results if p.genre == genre]
        if query:
            q = query.lower()
            results = [
                p for p in results
                if q in p.title.lower()
                or q in p.opus.lower()
                or q in p.catalog_number.lower()
                or q in p.title_short.lower()
            ]
        return results

    def get_parts_availability(
        self, edition_id: str,
    ) -> Dict[str, Dict[str, str]]:
        """取得版本的分譜狀態矩陣

        Args:
            edition_id: 版本 ID

        Returns:
            巢狀字典 {movement_id: {instrument_name: status}}
        """
        parts = self.list_parts(edition_id=edition_id)
        matrix: Dict[str, Dict[str, str]] = {}
        for part in parts:
            if part.movement_id not in matrix:
                matrix[part.movement_id] = {}
            matrix[part.movement_id][part.instrument_name] = part.status
        return matrix
