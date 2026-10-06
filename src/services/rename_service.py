# -*- coding: utf-8 -*-
"""
重新命名計畫

依專案的命名設定產生重新命名計畫（還沒對照磁碟），以及重複目標的自動加後綴。
對照磁碟的預檢與執行都在搬移歷程（services/move_history.py）。

使用範例：
    from services.rename_service import generate_rename_plan
    planned = generate_rename_plan(project)
    for entry in planned.entries:
        print(entry.original_path, entry.new_path)
"""
import os
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Collection, List, Optional
from core.constants import PartsOutputMode, detect_instrument_section
from core.locale import get_locale
from core.models import Group, Project, RenameEntry
from core.naming import name_group, named_voices, settings_for
from core.paths import path_key


@dataclass(frozen=True)
class RenamePlan:
    """依命名設定產生、還沒對照磁碟的重新命名計畫

    Attributes:
        entries: 要改名的項目（各群組依序：總譜在前，接著依序的分譜）
    """
    entries: List[RenameEntry] = field(default_factory=list)


def generate_rename_plan(project: Project, group_ids: Optional[Collection[str]] = None) -> RenamePlan:
    """根據專案設定產生重新命名計畫

    檔名與相對資料夾由命名模組決定，接在輸出位置之下；沒指定輸出位置時接在來源檔所在的資料夾。
    分譜依聲部組分放時，還沒有聲部組的聲部先依目前介面語言寫進專案（第一次用到時定下，之後不隨介面語言改變）。

    Args:
        project: 專案資料
        group_ids: 只為這些群組產生計畫；None 表示全部群組

    Returns:
        重新命名計畫（多於聲部數的分譜不在 entries 中）

    Raises:
        UnsafeFolderNameError: 某一層資料夾名稱清理後是 . 或 ..
    """
    groups = [g for g in project.groups if group_ids is None or g.id in group_ids]
    if project.parts_output_mode == PartsOutputMode.SECTION:
        _assign_default_sections(project, groups)
    entries = []
    for group in groups:
        for named in name_group(group, settings_for(project, group)).files:
            entry = RenameEntry(named.file.original_path, "", group.id, project.output_directory)
            entry.new_path = os.path.join(entry.output_location(), named.name.relative_path())
            entries.append(entry)
    return RenamePlan(entries=entries)


def apply_auto_suffix(entries: List[RenameEntry]) -> List[RenameEntry]:
    """重複的目標（路徑以 core.paths 判定同一性）從第二次出現起依序加上「 (1)」「 (2)」…後綴，只加一次

    Args:
        entries: 重新命名項目

    Returns:
        加後綴後的項目（順序不變）
    """
    seen = defaultdict(int)
    result = []
    for entry in entries:
        key = path_key(entry.new_path)
        count = seen[key]
        seen[key] += 1
        if count > 0:
            base, ext = os.path.splitext(entry.new_path)
            entry = replace(entry, new_path=f"{base} ({count}){ext}")
        result.append(entry)
    return result


def _assign_default_sections(project: Project, groups: List[Group]) -> None:
    """這些群組會被命名的聲部中，還沒有聲部組（或留空）的，依目前介面語言寫入偵測到的聲部組"""
    english = get_locale() == "en"
    missing = {}
    for group in groups:
        for voice in named_voices(group):
            if not project.instrument_sections.get(voice, "").strip():
                missing[voice] = detect_instrument_section(voice, english)
    if missing:
        project.update_ensemble(sections=missing)
