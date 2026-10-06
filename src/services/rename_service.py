# -*- coding: utf-8 -*-
"""
重新命名計畫

依專案的命名設定產生重新命名計畫（還沒對照磁碟），以及重複目標的自動加後綴。
對照磁碟的預檢與執行都在搬移歷程（services/move_history.py）。
產生計畫不改專案；分譜依聲部組分放時，先以 assign_default_sections 把還沒有聲部組的聲部寫進專案。

使用範例：
    from services.rename_service import assign_default_sections, generate_rename_plan
    assign_default_sections(project, english=False)
    planned = generate_rename_plan(project)
    for entry in planned.entries:
        print(entry.original_path, entry.new_path)
"""
import os
from collections import defaultdict
from dataclasses import dataclass, field, replace
from typing import Collection, Iterable, List, Optional
from core.constants import PartsOutputMode, VariableLevel
from core.models import Group, Project, RenameEntry
from core.naming import default_sections, name_group, settings_for, unknown_variables, variable_level, variables_in
from core.paths import path_key


@dataclass
class RenamePlan:
    """依命名設定產生、還沒對照磁碟的重新命名計畫

    Attributes:
        entries: 要改名的項目（各群組依序：總譜在前，接著依序的分譜）
        extra_files: 多於聲部數、不改名的分譜（來源路徑，各群組依序）
        folder_variables: 子資料夾模板裡的逐檔變數（不重複，依出現順序）；子資料夾只依群組區分，用了就不能執行
        unknown_variables: 命名格式與子資料夾模板裡不是模板變數的名稱（不重複，依出現順序）；產出時已拿掉
    """
    entries: List[RenameEntry] = field(default_factory=list)
    extra_files: List[str] = field(default_factory=list)
    folder_variables: List[str] = field(default_factory=list)
    unknown_variables: List[str] = field(default_factory=list)


def assign_default_sections(
    project: Project, english: bool, group_ids: Optional[Collection[str]] = None,
) -> None:
    """分譜依聲部組分放時，把這些群組會被命名、還沒有聲部組（或留空）的聲部寫進專案的編制設定

    預設值依指定語言偵測；寫進專案後就定下，之後切換介面語言不會改變。其他分譜存放模式不寫。
    產生重新命名計畫（重新命名預檢）前呼叫。

    Args:
        project: 專案資料
        english: 預設值用英文名稱（通常依目前介面語言）
        group_ids: 只處理這些群組；None 表示全部群組
    """
    if project.parts_output_mode != PartsOutputMode.SECTION:
        return
    missing = default_sections(_chosen_groups(project, group_ids), project.instrument_sections, english)
    if missing:
        project.update_ensemble(sections=missing)


def generate_rename_plan(project: Project, group_ids: Optional[Collection[str]] = None) -> RenamePlan:
    """根據專案設定產生重新命名計畫（不改專案）

    檔名與相對資料夾由命名模組決定，接在輸出位置之下；沒指定輸出位置時接在來源檔所在的資料夾。

    Args:
        project: 專案資料；分譜依聲部組分放時，要命名的聲部都必須已有聲部組（見 assign_default_sections）
        group_ids: 只為這些群組產生計畫；None 表示全部群組

    Returns:
        重新命名計畫（多於聲部數的分譜不在 entries 中）

    Raises:
        UnsafeFolderNameError: 某一層資料夾名稱清理後是 . 或 ..
        KeyError: 分譜依聲部組分放時，有要命名的聲部沒有聲部組
    """
    plan = RenamePlan()
    for group in _chosen_groups(project, group_ids):
        settings = settings_for(project, group)
        naming = name_group(group, settings)
        for named in naming.files:
            entry = RenameEntry(named.file.original_path, "", group.id, project.output_directory)
            entry.new_path = os.path.join(entry.output_location(), named.name.relative_path())
            plan.entries.append(entry)
        plan.extra_files.extend(f.original_path for f in naming.extra_files)
        _add_new(plan.folder_variables, (
            name for name in variables_in(settings.subfolder_template) if variable_level(name) == VariableLevel.FILE
        ))
        for template in (settings.template, settings.subfolder_template):
            _add_new(plan.unknown_variables, unknown_variables(template))
    return plan


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


def _chosen_groups(project: Project, group_ids: Optional[Collection[str]]) -> List[Group]:
    """指定的群組（依專案中的順序）；group_ids 為 None 表示全部群組"""
    return [g for g in project.groups if group_ids is None or g.id in group_ids]


def _add_new(names: List[str], found: Iterable[str]) -> None:
    """把 found 中還不在 names 裡的名稱依序加到後面"""
    for name in found:
        if name not in names:
            names.append(name)
