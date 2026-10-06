# -*- coding: utf-8 -*-
"""
模板引擎

提供曲名、總譜、樂器偵測與模板變數的雙語轉換；命名格式的套用見 core.naming。
"""
import os
import re
from typing import List, Optional
from core.constants import LOCALE_EN, SCORE_KEYWORDS, TEMPLATE_VARIABLES


def detect_piece_name(filenames: List[str]) -> str:
    """從檔名清單偵測共同的曲名

    依序嘗試兩種策略：
    1. 共同前綴法：取所有檔名的 commonprefix，清除尾端分隔符號
    2. 共同詞彙法：將檔名拆為詞彙，取所有檔案共有的詞彙，按原始順序組合

    Args:
        filenames: 檔案名稱清單（不含路徑）

    Returns:
        偵測到的曲名，若無法偵測則回傳空字串
    """
    if not filenames:
        return ""
    basenames = [os.path.splitext(f)[0] for f in filenames]
    if len(basenames) == 1:
        return basenames[0].strip()
    result = _detect_by_common_prefix(basenames)
    if result:
        return result
    return _detect_by_common_tokens(basenames)


def detect_score_index(filenames: List[str]) -> Optional[int]:
    """從檔名清單找出總譜

    Args:
        filenames: 檔案名稱清單（不含路徑）

    Returns:
        第一個檔名（不含副檔名、不分大小寫）含總譜關鍵字的索引，找不到時為 None
    """
    for i, name in enumerate(filenames):
        stem = os.path.splitext(name)[0].lower()
        if any(keyword in stem for keyword in SCORE_KEYWORDS):
            return i
    return None


def _detect_by_common_prefix(basenames: List[str]) -> str:
    """使用共同前綴法偵測曲名

    Args:
        basenames: 去除副檔名後的檔名清單

    Returns:
        偵測到的曲名
    """
    prefix = os.path.commonprefix(basenames)
    prefix = re.sub(r'[\s\-_.,;:]+$', '', prefix)
    prefix = re.sub(r'[\s\-_.,;:]\d+$', '', prefix)
    result = prefix.strip()
    if result.isdigit():
        return ""
    return result


def _detect_by_common_tokens(basenames: List[str]) -> str:
    """使用共同詞彙法偵測曲名

    將每個檔名拆為詞彙，找出所有檔案共有的非數字詞彙，
    按照第一個檔名中的出現順序組合。

    Args:
        basenames: 去除副檔名後的檔名清單

    Returns:
        偵測到的曲名
    """
    tokenized = [re.split(r'[\s\-_.,;:]+', name) for name in basenames]
    if not tokenized:
        return ""
    token_sets = [set(tokens) for tokens in tokenized]
    common = token_sets[0]
    for s in token_sets[1:]:
        common = common & s
    common = {t for t in common if t and not t.isdigit()}
    if not common:
        return ""
    ordered = [t for t in tokenized[0] if t in common]
    return " ".join(ordered).strip()


def convert_template_language(template: str, to_locale: str) -> str:
    """將模板中的變數名稱轉換為目標語言

    Args:
        template: 模板字串
        to_locale: 目標語言代碼（LOCALE_ZH_TW 或 LOCALE_EN）

    Returns:
        轉換後的模板字串
    """
    if to_locale == LOCALE_EN:
        for tv in TEMPLATE_VARIABLES:
            template = template.replace(f"{{{tv.name}}}", f"{{{tv.name_en}}}")
    else:
        for tv in TEMPLATE_VARIABLES:
            template = template.replace(f"{{{tv.name_en}}}", f"{{{tv.name}}}")
    return template


def extract_instruments_from_filenames(filenames: List[str]) -> List[str]:
    """從檔名清單中提取樂器名稱

    策略：找出共同前綴與共同後綴（以分隔符為邊界），
    取出中間不同的部分作為樂器名稱。

    Args:
        filenames: 檔案名稱清單（不含路徑）

    Returns:
        提取出的樂器名稱清單（保持原始順序）
    """
    if not filenames:
        return []
    basenames = [os.path.splitext(f)[0] for f in filenames]
    if len(basenames) == 1:
        name = re.sub(r'^\d+[\s.\-_]*', '', basenames[0]).strip()
        return [name] if name else basenames
    prefix = os.path.commonprefix(basenames)
    prefix = re.sub(r'[^\s.\-_,;:]+$', '', prefix)
    reversed_names = [n[::-1] for n in basenames]
    suffix_rev = os.path.commonprefix(reversed_names)
    suffix_rev = re.sub(r'[^\s.\-_,;:]+$', '', suffix_rev)
    suffix = suffix_rev[::-1]
    instruments = []
    for name in basenames:
        start = len(prefix)
        end = len(name) - len(suffix) if suffix else len(name)
        middle = name[start:end] if end > start else name
        middle = re.sub(r'^[\s.\-_,;:]+', '', middle)
        middle = re.sub(r'[\s.\-_,;:]+$', '', middle)
        middle = re.sub(r'^\d+[\s.\-_]*', '', middle)
        if middle:
            instruments.append(middle)
        else:
            instruments.append(name.strip())
    return instruments
