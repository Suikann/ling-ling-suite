# -*- coding: utf-8 -*-
"""
聲部組

聲部組依樂團類型各用一套（`ORCHESTRA_SECTIONS`），兩套都另有「其他」。
專案層級的聲部組對照以樂器為鍵：同一樂器（`core/voice_names.py` 的 `instrument_key`）的各個聲部共用一個值，
對照的鍵保留第一次寫入的聲部寫法。沒有存值的聲部依樂團類型以關鍵字偵測（`SECTION_KEYWORDS`）。
值一律是中文名稱；介面為英文時另以 `SECTION_NAMES_EN` 顯示。

使用範例：
    from core.sections import section_for
    section_for("二胡 II", {"二胡 I": "拉弦"}, OrchestraType.CHINESE)  # 拉弦
"""
from typing import Dict, Mapping, Optional, Tuple
from core.constants import ORCHESTRA_SECTIONS, OTHER_SECTION, SECTION_KEYWORDS, OrchestraType
from core.voice_names import instrument_key


def section_options(orchestra_type: OrchestraType) -> Tuple[str, ...]:
    """樂團類型的聲部組選項，依選單順序，「其他」在最後"""
    return ORCHESTRA_SECTIONS[OrchestraType(orchestra_type)] + (OTHER_SECTION,)


def detect_section(voice: str, orchestra_type: OrchestraType) -> str:
    """依樂團類型的關鍵字偵測聲部的聲部組

    Args:
        voice: 聲部名稱
        orchestra_type: 樂團類型

    Returns:
        聲部組名稱（中文）；偵測不到時為「其他」
    """
    name = voice.lower()
    for section, keywords in SECTION_KEYWORDS[OrchestraType(orchestra_type)]:
        if any(keyword in name for keyword in keywords):
            return section
    return OTHER_SECTION


def stored_key(voice: str, sections: Mapping[str, str]) -> Optional[str]:
    """對照裡與聲部同一樂器的那個鍵（第一次寫入的寫法）；沒有時為 None"""
    key = instrument_key(voice)
    return next((name for name in sections if instrument_key(name) == key), None)


def stored_section(voice: str, sections: Mapping[str, str]) -> str:
    """對照裡聲部所屬樂器存的聲部組；沒有存時為空字串"""
    name = stored_key(voice, sections)
    return sections[name] if name is not None else ""


def section_for(voice: str, sections: Mapping[str, str], orchestra_type: OrchestraType) -> str:
    """聲部的聲部組：對照裡它的樂器有存值（不是空白）就用存的，否則依樂團類型偵測

    Args:
        voice: 聲部名稱
        sections: 以樂器為鍵的聲部組對照
        orchestra_type: 樂團類型

    Returns:
        聲部組名稱
    """
    stored = stored_section(voice, sections)
    return stored if stored.strip() else detect_section(voice, orchestra_type)


def put_section(sections: Dict[str, str], voice: str, section: str) -> None:
    """把聲部所屬樂器的聲部組寫進對照：同一樂器已有鍵就改它的值（鍵的寫法不變），否則以這個寫法新增"""
    name = stored_key(voice, sections)
    sections[voice if name is None else name] = section
