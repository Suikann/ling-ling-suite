# -*- coding: utf-8 -*-
"""
檔名清理

把使用者輸入或模板產生的字串整理成可安全寫入檔案系統的檔名。

使用範例：
    from core.filename import ensure_pdf_extension, sanitize_filename
    sanitize_filename('Flute: 1')            # 'Flute_ 1'
    sanitize_filename('   ', fallback='Part')  # 'Part'
    ensure_pdf_extension('Flute')            # 'Flute.pdf'
"""
from core.constants import FILENAME_ILLEGAL_CHARS, PDF_EXTENSION


def sanitize_filename(name: str, fallback: str = "") -> str:
    """把非法字元換成底線並去除頭尾空白

    Args:
        name: 原始名稱
        fallback: 清理後為空時改用的名稱，預設為空字串（不回退）

    Returns:
        清理後的檔名
    """
    for ch in FILENAME_ILLEGAL_CHARS:
        name = name.replace(ch, "_")
    return name.strip() or fallback


def ensure_pdf_extension(name: str) -> str:
    """檔名沒有 .pdf 副檔名（不分大小寫）時補上"""
    if name.lower().endswith(PDF_EXTENSION):
        return name
    return name + PDF_EXTENSION
