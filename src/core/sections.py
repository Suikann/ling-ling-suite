# -*- coding: utf-8 -*-
"""
聲部組

聲部組依樂團類型各用一套（`ORCHESTRA_SECTIONS`），兩套都另有「其他」。
專案層級的聲部組對照以樂器為鍵：同一樂器（`core/voice_names.py` 的 `instrument_key`）的各個聲部共用一個值，
對照的鍵保留第一次寫入的聲部寫法。沒有存值的聲部依樂團類型以關鍵字偵測（`SECTION_KEYWORDS`）。
值一律是中文名稱；介面為英文時另以 `SECTION_NAMES_EN` 顯示。

格式 3 以前的專案檔（聲部組以聲部名稱為鍵、可能存英文名稱、沒有樂團類型）開啟時，
以 `guess_orchestra_type` 判定樂團類型、`migrate_sections` 轉成以樂器為鍵的對照。

使用範例：
    from core.sections import section_for
    section_for("二胡 II", {"二胡 I": "拉弦"}, OrchestraType.CHINESE)  # 拉弦
"""
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple
from core.constants import ORCHESTRA_SECTIONS, OTHER_SECTION, SECTION_KEYWORDS, SECTION_NAMES_EN, OrchestraType
from core.locale import is_english
from core.voice_names import instrument_key

# 舊版存的英文聲部組名稱對回中文
_FROM_ENGLISH = {english: chinese for chinese, english in SECTION_NAMES_EN.items()}
# 舊專案出現這些聲部組（存的或偵測的）就判為國樂團
_CHINESE_ORCHESTRA_MARKS = ("吹管", "彈撥")


def section_options(orchestra_type: OrchestraType) -> Tuple[str, ...]:
    """樂團類型的聲部組選項，依選單順序，「其他」在最後"""
    return ORCHESTRA_SECTIONS[orchestra_type] + (OTHER_SECTION,)


def section_label(section: str) -> str:
    """聲部組在介面上的名稱：介面為英文時換成英文名稱，自訂的值原樣顯示"""
    return SECTION_NAMES_EN.get(section, section) if is_english() else section


def detect_section(voice: str, orchestra_type: OrchestraType) -> str:
    """依樂團類型的關鍵字偵測聲部的聲部組

    Args:
        voice: 聲部名稱
        orchestra_type: 樂團類型

    Returns:
        聲部組名稱（中文）；偵測不到時為「其他」
    """
    name = voice.lower()
    for section, keywords in SECTION_KEYWORDS[orchestra_type]:
        if any(keyword in name for keyword in keywords):
            return section
    return OTHER_SECTION


def stored_key(voice: str, sections: Mapping[str, str]) -> Optional[str]:
    """對照裡與聲部同一樂器的那個鍵（第一次寫入的寫法）；沒有時為 None"""
    key = instrument_key(voice)
    return next((name for name in sections if instrument_key(name) == key), None)


def stored_section(voice: str, sections: Mapping[str, str]) -> str:
    """對照裡聲部所屬樂器存的聲部組；沒有存或存的是空白時為空字串（空白一律當成沒存）"""
    name = stored_key(voice, sections)
    value = sections[name] if name is not None else ""
    return value if value.strip() else ""


def section_for(voice: str, sections: Mapping[str, str], orchestra_type: OrchestraType) -> str:
    """聲部的聲部組：對照裡它的樂器有存值（不是空白）就用存的，否則依樂團類型偵測

    Args:
        voice: 聲部名稱
        sections: 以樂器為鍵的聲部組對照
        orchestra_type: 樂團類型

    Returns:
        聲部組名稱
    """
    return stored_section(voice, sections) or detect_section(voice, orchestra_type)


def put_section(sections: Dict[str, str], voice: str, section: str) -> None:
    """把聲部所屬樂器的聲部組寫進對照：同一樂器已有鍵就改它的值（鍵的寫法不變），否則以這個寫法新增"""
    name = stored_key(voice, sections)
    sections[voice if name is None else name] = section


def guess_orchestra_type(voices: Iterable[str], stored: Mapping[str, str]) -> OrchestraType:
    """舊專案的樂團類型：已存的聲部組優先，沒存的聲部以國樂團的關鍵字偵測；出現吹管或彈撥就是國樂團

    Args:
        voices: 各群組樂器表裡的聲部
        stored: 舊格式以聲部名稱為鍵的聲部組（可能是英文名稱）

    Returns:
        判定的樂團類型；沒有吹管、彈撥時為管弦樂團
    """
    found = [_from_english(value) for value in stored.values() if value.strip()]
    found += [detect_section(voice, OrchestraType.CHINESE) for voice in voices if not stored.get(voice, "").strip()]
    is_chinese = any(section in _CHINESE_ORCHESTRA_MARKS for section in found)
    return OrchestraType.CHINESE if is_chinese else OrchestraType.WESTERN


def migrate_sections(
    voices: Sequence[str], stored: Mapping[str, str], orchestra_type: OrchestraType,
) -> Dict[str, str]:
    """舊格式以聲部名稱為鍵的聲部組，轉成以樂器為鍵的對照

    英文名稱先對回中文；國樂團中不在五組內的值（舊的弦樂、木管、自訂文字）依國樂團重新偵測，
    偵測不到的歸「其他」；管弦樂團的值不動。同一樂器有多個值時，依樂器表順序取第一個出現的值，
    鍵是那個聲部的寫法；樂器表裡已沒有的鍵排在後面，同樣一個樂器只留一個。留空的值丟掉（沿用偵測）。

    Args:
        voices: 各群組樂器表裡的聲部，依群組與樂器表順序
        stored: 舊格式以聲部名稱為鍵的聲部組
        orchestra_type: 這個專案的樂團類型

    Returns:
        以樂器為鍵的聲部組對照
    """
    chinese_sections = ORCHESTRA_SECTIONS[OrchestraType.CHINESE]
    sections: Dict[str, str] = {}
    for voice in list(voices) + list(stored):
        value = _from_english(stored.get(voice, ""))
        if not value or stored_key(voice, sections) is not None:
            continue
        if orchestra_type == OrchestraType.CHINESE and value not in chinese_sections:
            value = detect_section(voice, OrchestraType.CHINESE)
        sections[voice] = value
    return sections


def _from_english(value: str) -> str:
    """舊版存的聲部組：去掉前後空白，英文名稱換回中文"""
    value = value.strip()
    return _FROM_ENGLISH.get(value, value)
