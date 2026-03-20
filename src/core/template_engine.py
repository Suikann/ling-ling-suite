# -*- coding: utf-8 -*-
"""
模板引擎

提供模板解析、變數替換與曲名偵測功能。
"""
import os
import re
from typing import Dict, List
from core.constants import ALL_VARIABLE_NAMES, TEMPLATE_VARIABLES
from core.models import Group


def substitute_template(template: str, variables: Dict[str, str]) -> str:
    """將模板中的 {變數} 替換為對應值

    Args:
        template: 模板字串，例如 "{序號}. {樂器}.pdf"
        variables: 變數名稱到值的對應字典

    Returns:
        替換後的字串
    """
    result = template
    for name, value in variables.items():
        result = result.replace(f"{{{name}}}", value)
    return result


def build_variables_for_file(
    file_index: int,
    group: Group,
    instruments: List[str] = None,
) -> Dict[str, str]:
    """為單一檔案組合所有模板變數（同時產生中英文鍵名）

    Args:
        file_index: 檔案在群組中的索引（從 0 開始）
        group: 所屬群組
        instruments: 樂器表（未提供時使用 group.instruments）

    Returns:
        變數名稱到值的對應字典（包含中英文鍵名）
    """
    group_instruments = getattr(group, "instruments", []) or []
    if group_instruments:
        total = len(group_instruments)
        pad_width = len(str(total)) if total > 0 else 1
        sequence_number = str(file_index + 1).zfill(pad_width)
        instrument_name = (
            group_instruments[file_index]
            if file_index < len(group_instruments)
            else ""
        )
    elif instruments:
        total = len(instruments)
        pad_width = len(str(total)) if total > 0 else 1
        sequence_number = str(file_index + 1).zfill(pad_width)
        instrument_name = (
            instruments[file_index] if file_index < len(instruments) else ""
        )
    else:
        pad_width = len(str(len(group.files))) if group.files else 1
        sequence_number = str(file_index + 1).zfill(pad_width)
        instrument_name = ""
    values = {
        "序號": sequence_number,
        "樂器": instrument_name,
        "曲名": group.piece_name,
        "樂章編號": group.movement_number,
        "樂章名稱": group.movement_name,
        "作曲家": group.composer,
        "曲種": group.genre,
    }
    en_mapping = {tv.name: tv.name_en for tv in TEMPLATE_VARIABLES}
    for zh_name, val in list(values.items()):
        en_name = en_mapping.get(zh_name)
        if en_name:
            values[en_name] = val
    return values


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
        to_locale: 目標語言代碼，"zh_TW" 或 "en"

    Returns:
        轉換後的模板字串
    """
    if to_locale == "en":
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


def validate_template(template: str) -> List[str]:
    """驗證模板中的變數是否合法（中英文變數名皆可辨識）

    Args:
        template: 模板字串

    Returns:
        未知變數名稱清單，空清單表示所有變數皆合法
    """
    found = re.findall(r'\{([^}]+)\}', template)
    unknown = [name for name in found if name not in ALL_VARIABLE_NAMES]
    return unknown
