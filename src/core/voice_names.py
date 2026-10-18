# -*- coding: utf-8 -*-
"""
聲部名稱比對

全程式比對兩個聲部名稱只用這裡的定義，規則寫死、不查對照表：
- 格式：NFKC（全形轉半形、Ⅰ 轉 I）、不分大小寫、去掉空白與分隔符號（`-` `_` `.` `－` `、` 與括號）。
- 結尾編號互通：I 到 X、阿拉伯數字、一到十；只看名稱結尾。羅馬字母前面必須是中文字或分隔符號、
  而且單獨成段才算編號（「Di」的 i、「Horn in F」不算）。有編號和沒編號的永遠不同。
  先切出結尾的編號段再去分隔符號，「新笛Ⅰ、Ⅱ」才不會黏成「新笛III」。
- 不做樂器別名與簡繁轉換；多聲部與兼奏的名稱（含「、」「兼」或空白）整串是一個聲部。

只用於比對，各處保留自己的寫法。檔名與路徑的比對用 `core/paths.py`。

使用範例：
    from core.voice_names import same_instrument, same_voice, voice_key
    same_voice("二胡 I", "二胡1")  # True
    same_instrument("二胡 I", "二胡 II")  # True
    seen = {voice_key(name) for name in instruments}
"""
import re
import unicodedata
from typing import Iterable, List, Optional, Tuple

VoiceKey = Tuple[str, Optional[int]]

_SEPARATORS = "-_.、()[]{}【】〔〕「」『』《》〈〉"
_SEPARATOR_RUN = re.compile(f"[\\s{re.escape(_SEPARATORS)}]+")
_TRAILING_SEPARATORS = re.compile(f"{_SEPARATOR_RUN.pattern}$")
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}
_CHINESE = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_TRAILING_DIGITS = re.compile(r"[0-9]+$")
_TRAILING_LETTERS = re.compile(r"[a-z]+$")


def voice_key(name: str) -> VoiceKey:
    """聲部名稱的比對鍵：兩個名稱的鍵相同即為同一聲部

    Args:
        name: 聲部名稱，任何寫法

    Returns:
        （去掉結尾編號、正規化後的樂器部分，結尾編號；沒有編號時為 None）
    """
    text = unicodedata.normalize("NFKC", name).casefold()
    text = _TRAILING_SEPARATORS.sub("", text)
    stem, number = _split_number(text)
    return _SEPARATOR_RUN.sub("", stem), number


def same_voice(a: str, b: str) -> bool:
    """兩個名稱是否為同一聲部：「二胡 I」等於「二胡1」，不等於「二胡 II」或「二胡」"""
    return voice_key(a) == voice_key(b)


def instrument_key(name: str) -> str:
    """聲部名稱所屬樂器的比對鍵：去掉結尾編號，「二胡 I」「二胡 II」「二胡」的鍵相同"""
    return voice_key(name)[0]


def same_instrument(a: str, b: str) -> bool:
    """兩個聲部名稱是否為同一樂器（只用在聲部組對照的鍵）"""
    return instrument_key(a) == instrument_key(b)


def distinct_voices(names: Iterable[str]) -> List[str]:
    """去掉同一聲部的其他寫法，每個聲部保留第一次出現的寫法，順序不變"""
    seen = set()
    distinct = []
    for name in names:
        key = voice_key(name)
        if key not in seen:
            seen.add(key)
            distinct.append(name)
    return distinct


def _split_number(text: str) -> Tuple[str, Optional[int]]:
    """切出結尾的編號段；前面沒有其他內容時不算編號"""
    digits = _TRAILING_DIGITS.search(text)
    if digits and digits.start() > 0:
        return text[:digits.start()], int(digits.group())
    if text and text[-1] in _CHINESE and len(text) > 1 and text[-2] not in _CHINESE:
        return text[:-1], _CHINESE[text[-1]]
    letters = _TRAILING_LETTERS.search(text)
    if letters and letters.group() in _ROMAN and letters.start() > 0 and _ends_segment(text[letters.start() - 1]):
        return text[:letters.start()], _ROMAN[letters.group()]
    return text, None


def _ends_segment(char: str) -> bool:
    """羅馬字母編號前面的字元：中文字或分隔符號"""
    return char in _SEPARATORS or char.isspace() or unicodedata.category(char) == "Lo"
