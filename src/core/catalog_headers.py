# -*- coding: utf-8 -*-
"""
譜庫標題列的語言

譜庫各工作表第一列的欄名可以是中文或英文（core/catalog_constants.py 的 SHEET_HEADER_NAMES_ZH；英文就是
SHEET_HEADERS 的欄位鍵）。程式照欄位位置讀寫、不看標題文字，所以語言只影響試算表上看到的欄名。
新建譜庫時跟著當下的介面語言；之後補欄或新建工作表沿用譜庫已有的語言，不混用；譜庫設定的欄位語言選單可整份改寫。
語言從標題列的第一欄認：是 id 為英文，其他為中文。社團設定兩種語言的標題列相同，不算進譜庫的語言。

使用範例：
    from core.catalog_headers import header_row, header_locale, catalog_header_locale
    row = header_row(SHEET_PARTS, LOCALE_ZH_TW)          # ["編號", "版本編號", ...]
    locale = header_locale(SHEET_PARTS, rows[0])          # 這張表現在的語言；沒有標題列時為 None
    catalog = catalog_header_locale({sheet: rows[0]})     # 各表一致時的語言；不一致或認不出時為 None
"""
from typing import List, Mapping, Optional, Sequence

from core.catalog_constants import SHEET_HEADER_NAMES_ZH, SHEET_HEADERS
from core.constants import LOCALE_EN, LOCALE_ZH_TW

# 英文標題列第一欄的欄名
_ENGLISH_FIRST_COLUMN = "id"


def localized_sheets() -> List[str]:
    """標題列分中英文的工作表，依 SHEET_HEADERS 的順序"""
    return [sheet for sheet in SHEET_HEADERS if sheet in SHEET_HEADER_NAMES_ZH]


def header_row(sheet: str, locale: str) -> List[str]:
    """一張工作表依語言寫成的完整標題列

    Args:
        sheet: 工作表名稱
        locale: 語言代碼（LOCALE_ZH_TW 或 LOCALE_EN）

    Returns:
        依 SHEET_HEADERS 欄位順序的欄名；不分語言的工作表兩種語言相同
    """
    keys = SHEET_HEADERS[sheet]
    names = SHEET_HEADER_NAMES_ZH.get(sheet)
    if locale != LOCALE_ZH_TW or names is None:
        return list(keys)
    return [names[key] for key in keys]


def header_locale(sheet: str, row: Sequence[str]) -> Optional[str]:
    """從工作表現有的標題列認出語言：第一欄是 id 為英文，其他為中文

    Args:
        sheet: 工作表名稱
        row: 工作表的第一列（可能是空的）

    Returns:
        語言代碼；第一欄空白、沒有標題列，或是不分語言的工作表時為 None
    """
    if sheet not in SHEET_HEADER_NAMES_ZH or not row or not row[0].strip():
        return None
    return LOCALE_EN if row[0].strip() == _ENGLISH_FIRST_COLUMN else LOCALE_ZH_TW


def catalog_header_locale(first_rows: Mapping[str, Sequence[str]]) -> Optional[str]:
    """整份譜庫的標題語言：認得出語言的工作表都一致時的那個語言

    Args:
        first_rows: 各工作表的第一列

    Returns:
        語言代碼；各表不一致或都認不出時為 None
    """
    found = {header_locale(sheet, row) for sheet, row in first_rows.items()} - {None}
    return found.pop() if len(found) == 1 else None
