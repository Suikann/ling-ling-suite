# -*- coding: utf-8 -*-
"""
聲部名稱自動完成的候選

輸入聲部名稱的地方（編制表的新增與改名、分割的分段名稱）以這裡的候選提示：
- 已讀過譜庫時，候選是譜庫裡所有樂器表用過的聲部名稱，依使用次數排序（讀譜庫總覽時一起取得，不另外發請求）。
- 還沒讀過譜庫時，候選是專案其他群組的聲部名稱（同樣依使用次數），再接上內建的預設樂器表。
- 輸入的名稱與候選是同一聲部（`core/voice_names.py` 的規則）但寫法不同時，那個候選標出來。

只提示、不改寫輸入，也不參與比對；選到的名稱照樣受同一份樂器表不重名的規則擋下。不另外維護標準名稱表。

使用範例：
    from core.voice_suggestions import VoiceCandidates, suggest
    candidates = VoiceCandidates()
    candidates.remember_catalog(overview.voice_names)   # 讀到譜庫總覽時
    names = candidates.for_group(project, group)
    suggest("二胡1", names)  # [VoiceSuggestion("二胡 I", same_voice=True), ...]
"""
import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, List, Optional, TYPE_CHECKING

from core.constants import INSTRUMENT_PRESETS
from core.voice_names import same_voice

if TYPE_CHECKING:
    from core.models import Group, Project


@dataclass(frozen=True)
class VoiceSuggestion:
    """一個候選：名稱，以及它是否與輸入的名稱同一聲部但寫法不同"""
    name: str
    same_voice: bool = False


def ranked_by_usage(names: Iterable[str]) -> List[str]:
    """依使用次數排序的不重複名稱：次數多的在前，次數相同時依第一次出現的順序；空白的略過

    寫法不同就是不同的名稱（「二胡 I」與「二胡1」各算各的），社團的寫法才看得出哪個最常用。
    """
    counts = Counter(name.strip() for name in names if name.strip())
    return [name for name, _count in counts.most_common()]


def suggest(typed: str, candidates: Iterable[str]) -> List[VoiceSuggestion]:
    """輸入 typed 時要列出的候選，依 candidates 的順序

    列出包含輸入文字（不分大小寫與全半形）的候選，以及與輸入同一聲部的候選（標 same_voice）；
    與輸入寫法完全相同的不列。沒有輸入時沒有候選。
    """
    text = typed.strip()
    if not text:
        return []
    needle = _folded(text)
    result = []
    for name in candidates:
        if name == text:
            continue
        if same_voice(text, name):
            result.append(VoiceSuggestion(name, same_voice=True))
        elif needle in _folded(name):
            result.append(VoiceSuggestion(name))
    return result


def offline_candidates(project: Optional["Project"], group: Optional["Group"]) -> List[str]:
    """沒讀過譜庫時的候選：專案裡 group 以外各群組的聲部名稱（依使用次數），再接上預設樂器表的名稱"""
    used = [
        voice for other in (project.groups if project is not None else []) if other is not group
        for voice in other.instruments
    ]
    ranked = ranked_by_usage(used)
    seen = set(ranked)
    for preset in INSTRUMENT_PRESETS:
        for voice in preset.instruments:
            if voice not in seen:
                seen.add(voice)
                ranked.append(voice)
    return ranked


class VoiceCandidates:
    """全程式共用一份的候選來源：記住讀到的譜庫聲部名稱，沒讀過時改用專案與預設樂器表"""

    def __init__(self):
        self._catalog_names: Optional[List[str]] = None

    def remember_catalog(self, names: List[str]) -> None:
        """記下讀譜庫總覽時取得的聲部名稱（已依使用次數排序）"""
        self._catalog_names = list(names)

    def for_group(self, project: Optional["Project"], group: Optional["Group"]) -> List[str]:
        """在 group 輸入聲部名稱時的候選：讀過譜庫就是譜庫的名稱，否則是 offline_candidates"""
        if self._catalog_names is not None:
            return list(self._catalog_names)
        return offline_candidates(project, group)


def _folded(text: str) -> str:
    """比對包含與否用的寫法：全形轉半形、不分大小寫"""
    return unicodedata.normalize("NFKC", text).casefold()
