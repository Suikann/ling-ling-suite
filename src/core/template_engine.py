# -*- coding: utf-8 -*-
"""
模板引擎

提供模板解析、變數替換與曲名偵測功能。
"""
import os
import re
from typing import Dict, List
from core.constants import VARIABLE_NAMES
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
    instruments: List[str],
) -> Dict[str, str]:
    """為單一檔案組合所有模板變數

    Args:
        file_index: 檔案在群組中的索引（從 0 開始）
        group: 所屬群組
        instruments: 完整樂器表

    Returns:
        變數名稱到值的對應字典
    """
    selected = group.selected_instruments
    total = len(selected)
    pad_width = len(str(total)) if total > 0 else 1
    instrument_index = selected[file_index] if file_index < len(selected) else 0
    sequence_number = str(instrument_index + 1).zfill(pad_width)
    instrument_name = (
        instruments[instrument_index]
        if instrument_index < len(instruments)
        else ""
    )
    return {
        "序號": sequence_number,
        "樂器": instrument_name,
        "曲名": group.piece_name,
        "樂章編號": group.movement_number,
        "樂章名稱": group.movement_name,
    }


def detect_piece_name(filenames: List[str]) -> str:
    """從檔名清單偵測共同的曲名

    使用共同前綴法，去除副檔名後取共同前綴，再移除尾端的分隔符號。

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
    prefix = os.path.commonprefix(basenames)
    prefix = re.sub(r'[\s\-_.,;:]+$', '', prefix)
    prefix = re.sub(r'[\s\-_.,;:]\d+$', '', prefix)
    return prefix.strip()


def validate_template(template: str) -> List[str]:
    """驗證模板中的變數是否合法

    Args:
        template: 模板字串

    Returns:
        未知變數名稱清單，空清單表示所有變數皆合法
    """
    found = re.findall(r'\{([^}]+)\}', template)
    unknown = [name for name in found if name not in VARIABLE_NAMES]
    return unknown
