# -*- coding: utf-8 -*-
"""
命名格式套用

把群組中的一格（總譜，或第 N 份分譜）依命名設定轉成檔名與相對資料夾。
純函式：不碰磁碟、不讀介面語言；本機重新命名、預覽與 Drive 重新命名都經過這裡。
群組是否已重新命名（is_renamed，存入譜庫與發譜都只收已重新命名的群組）也以命名結果判定。

使用範例：
    from core.naming import NamingSettings, name_group
    naming = name_group(group, NamingSettings("{序號}-{樂器}.pdf"))
    for named in naming.files:
        print(named.file.original_path, named.name.relative_path())
"""
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, NamedTuple, Optional, Sequence, Tuple
from core.constants import (
    MIN_NUMBER_WIDTH, RELATIVE_DIR_NAMES, SCORE_NUMBER, TEMPLATE_SLOT_SOURCES, TEMPLATE_VARIABLES, OrchestraType,
    PartsOutputMode, TemplateVariable, VariableLevel,
)
from core.filename import ensure_pdf_extension, sanitize_filename
from core.paths import name_key, same_path
from core.models import FileInfo, Group, Project
from core.sections import section_for, stored_section
from core.voice_names import instrument_key

# 總譜所在的格；分譜依序為第 1、2…格
SCORE_SLOT = 0
# 命名格式中的 {變數}
_VARIABLE_PATTERN = re.compile(r"\{([^{}]+)\}")
# 中英文變數名稱到模板變數的對應
_VARIABLES_BY_NAME: Dict[str, TemplateVariable] = {
    name: var for var in TEMPLATE_VARIABLES for name in (var.name, var.name_en)
}


class UnsafeFolderNameError(ValueError):
    """資料夾名稱（子資料夾模板的結果、分譜資料夾或聲部組）清理後是 . 或 ..

    Attributes:
        name: 清理後的資料夾名稱
    """

    def __init__(self, name: str):
        super().__init__(f"資料夾名稱不能是「{name}」")
        self.name = name


@dataclass(frozen=True)
class NamingSettings:
    """命名設定

    Attributes:
        template: 命名格式（大模板或該群組的小模板）
        subfolder_template: 子資料夾模板，總譜與分譜都放進它的結果；空字串表示不放子資料夾
        parts_mode: 分譜存放模式；分譜資料夾與聲部組資料夾只放分譜，總譜不進去
        parts_folder: 分譜存放模式為分譜資料夾時的資料夾名稱；空字串表示不另放
        sections: 以樂器為鍵的聲部組對照（core/sections.py）；分譜存放模式為聲部組資料夾時，
            每個要命名的聲部的樂器都必須有
    """
    template: str
    subfolder_template: str = ""
    parts_mode: PartsOutputMode = PartsOutputMode.ROOT
    parts_folder: str = ""
    sections: Mapping[str, str] = field(default_factory=dict)


class SlotName(NamedTuple):
    """一格的命名結果

    Attributes:
        file_name: 檔名
        folders: 相對於輸出位置的資料夾層級（由外而內），空表示直接放在輸出位置
    """
    file_name: str
    folders: Tuple[str, ...] = ()

    def relative_path(self) -> str:
        """相對於輸出位置的路徑"""
        return os.path.join(*self.folders, self.file_name)


class NamedFile(NamedTuple):
    """群組中一份要改名的檔案與它的命名結果

    Attributes:
        file: 檔案資訊
        slot: 所在的格（0 為總譜，N 為第 N 份分譜）
        name: 命名結果
    """
    file: FileInfo
    slot: int
    name: SlotName


@dataclass(frozen=True)
class GroupNaming:
    """一個群組的命名結果

    Attributes:
        files: 要改名的檔案（總譜在前，接著依序的分譜）
        extra_files: 多於聲部數的分譜（依原順序），不改名
    """
    files: List[NamedFile] = field(default_factory=list)
    extra_files: List[FileInfo] = field(default_factory=list)


def settings_for(project: Project, group: Group) -> NamingSettings:
    """群組在專案中的命名設定：有小模板時用小模板，子資料夾模板只在開啟子資料夾輸出時套用"""
    template = group.small_template if group.use_small_template and group.small_template else project.master_template
    return NamingSettings(
        template=template,
        subfolder_template=project.subfolder_template if project.use_subfolders else "",
        parts_mode=project.parts_output_mode,
        parts_folder=project.parts_subfolder_name,
        sections=dict(project.instrument_sections),
    )


def name_slot(
    group: Group, slot: int, settings: NamingSettings, voices: Optional[Sequence[str]] = None,
) -> SlotName:
    """為群組中的一格命名

    Args:
        group: 群組
        slot: 0（SCORE_SLOT）為總譜，N 為第 N 份分譜
        settings: 命名設定
        voices: 明確指定的樂器表（每個聲部都有分譜），優先於群組的樂器表；未提供或為空時用群組的樂器表，
            本專案沒有、沒有分譜的聲部跳過

    Returns:
        這一格的檔名與相對資料夾

    Raises:
        ValueError: slot 超出聲部數
        KeyError: 聲部組資料夾模式下，這一格的聲部的樂器沒有聲部組
        UnsafeFolderNameError: 某一層資料夾名稱清理後是 . 或 ..
    """
    slots, count = _voice_slots(group, voices)
    if slot == SCORE_SLOT:
        number, voice = SCORE_NUMBER, group.score_label
    elif 1 <= slot <= len(slots):
        position, voice = slots[slot - 1]
        number = str(position).zfill(max(MIN_NUMBER_WIDTH, len(str(count))))
    else:
        raise ValueError(f"第 {slot} 格超出聲部數 {len(slots)}")
    variables = _variables(group, number, voice)
    folders = []
    if settings.subfolder_template:
        folders.append(_substitute(settings.subfolder_template, variables))
    if slot != SCORE_SLOT:
        if settings.parts_mode == PartsOutputMode.PARTS and settings.parts_folder:
            folders.append(settings.parts_folder)
        elif settings.parts_mode == PartsOutputMode.SECTION:
            folders.append(_section_folder(voice, settings.sections))
    file_name = ensure_pdf_extension(sanitize_filename(_substitute(settings.template, variables)))
    return SlotName(file_name, tuple(name for name in map(_folder_name, folders) if name))


def name_group(group: Group, settings: NamingSettings, voices: Optional[Sequence[str]] = None) -> GroupNaming:
    """為群組的總譜與分譜命名；每份分譜用它配到的聲部（Group.pairing），多於聲部數的分譜不改名

    Args:
        group: 群組
        settings: 命名設定
        voices: 明確指定的樂器表，優先於群組的樂器表；未提供或為空時用群組的樂器表

    Returns:
        群組的命名結果

    Raises:
        KeyError: 聲部組資料夾模式下，有聲部沒有聲部組
        UnsafeFolderNameError: 某一層資料夾名稱清理後是 . 或 ..
    """
    paired = len(_voice_slots(group, voices)[0])
    slots = [(SCORE_SLOT, group.score_file)] if group.score_file else []
    slots += list(enumerate(group.files[:paired], start=1))
    return GroupNaming(
        files=[NamedFile(file, slot, name_slot(group, slot, settings, voices)) for slot, file in slots],
        extra_files=group.files[paired:],
    )


def is_named_at(path: str, name: SlotName, output_directory: str) -> bool:
    """本機的檔是不是在命名結果的位置：有輸出位置時比整條路徑，沒有（就地改名）時比檔名與上層資料夾（不分大小寫）"""
    if output_directory:
        return same_path(path, os.path.join(output_directory, name.relative_path()))
    wanted = [name_key(part) for part in (*name.folders, name.file_name)]
    parts = [name_key(part) for part in os.path.normpath(os.path.abspath(path)).split(os.sep)]
    return parts[-len(wanted):] == wanted


def is_renamed(project: Project, group: Group) -> bool:
    """群組是否已重新命名：總譜與每份配到聲部的分譜都在命名結果的位置（is_named_at）

    沒有要命名的檔、或命名不出來（聲部組資料夾缺聲部組、資料夾名稱不安全）時為否；多於聲部數的分譜不算。
    """
    try:
        naming = name_group(group, settings_for(project, group))
    except (KeyError, UnsafeFolderNameError):
        return False
    return bool(naming.files) and all(
        is_named_at(named.file.original_path, named.name, project.output_directory) for named in naming.files
    )


def named_voices(group: Group, voices: Optional[Sequence[str]] = None) -> List[str]:
    """有分譜對應、會被命名的聲部（依序）；參數同 name_group"""
    return [voice for _, voice in _voice_slots(group, voices)[0][:len(group.files)]]


def default_sections(
    groups: Iterable[Group], sections: Mapping[str, str], orchestra_type: OrchestraType,
) -> Dict[str, str]:
    """這些群組會被命名的聲部中，樂器還沒有聲部組（或留空）的，各樂器第一個聲部到它的預設聲部組

    Args:
        groups: 群組
        sections: 以樂器為鍵的聲部組對照
        orchestra_type: 樂團類型，預設值依它偵測

    Returns:
        聲部名稱（同一樂器只列第一次出現的寫法）到偵測的聲部組；都已設定時為空
    """
    missing: Dict[str, str] = {}
    seen = set()
    for group in groups:
        for voice in named_voices(group):
            key = instrument_key(voice)
            if key not in seen and not stored_section(voice, sections):
                missing[voice] = section_for(voice, sections, orchestra_type)
            seen.add(key)
    return missing


def _section_folder(voice: str, sections: Mapping[str, str]) -> str:
    """聲部組資料夾模式下這個聲部的資料夾：它的樂器存的聲部組

    Raises:
        KeyError: 它的樂器沒有聲部組（或留空）
    """
    section = stored_section(voice, sections)
    if not section:
        raise KeyError(voice)
    return section


def _voice_slots(group: Group, voices: Optional[Sequence[str]]) -> Tuple[List[Tuple[int, str]], int]:
    """依序配給分譜的聲部與它們的序號，以及樂器表的列數（決定序號位數）

    明確指定且不為空的樂器表每個聲部都配；否則用群組的樂器表，只配接分譜的聲部（Group.part_voices：
    本專案有、沒標沒有分譜的），序號是聲部在樂器表中的位置（從 1 起算）。
    """
    if voices:
        return list(enumerate(voices, start=1)), len(voices)
    return [(index + 1, voice) for index, voice in group.part_voices()], len(group.instruments)


def variable_level(name: str) -> Optional[VariableLevel]:
    """變數（中文或英文名稱）的層級；不是模板變數時為 None"""
    var = _VARIABLES_BY_NAME.get(name)
    return var.level if var else None


def variables_in(template: str) -> List[str]:
    """命名格式裡 {…} 的名稱（不重複，依出現順序），含不是模板變數的"""
    return list(dict.fromkeys(_VARIABLE_PATTERN.findall(template)))


def unknown_variables(template: str) -> List[str]:
    """命名格式裡不是模板變數的 {…}（不重複，依出現順序）；產出時會被拿掉"""
    return [name for name in variables_in(template) if name not in _VARIABLES_BY_NAME]


def _folder_name(name: str) -> str:
    """清理一層資料夾名稱；清理後為空表示這一層不存在

    Raises:
        UnsafeFolderNameError: 清理後是 . 或 ..
    """
    name = sanitize_filename(name)
    if name in RELATIVE_DIR_NAMES:
        raise UnsafeFolderNameError(name)
    return name


def _variables(group: Group, number: str, voice: str) -> Dict[str, str]:
    """一格的變數表（中英文名稱都有），由模板變數常數推得：逐檔變數取這一格的值，群組層級變數取群組欄位

    Args:
        group: 群組
        number: 這一格的序號（總譜為 SCORE_NUMBER）
        voice: 這一格的聲部（總譜為總譜標籤）

    Returns:
        變數名稱（中文與英文）到值
    """
    slot_values = dict(zip(TEMPLATE_SLOT_SOURCES, (number, voice)))
    table = {}
    for var in TEMPLATE_VARIABLES:
        value = slot_values[var.source] if var.level == VariableLevel.FILE else getattr(group, var.source)
        table[var.name] = table[var.name_en] = value
    return table


def _substitute(template: str, variables: Dict[str, str]) -> str:
    """把命名格式中的 {變數} 換成值、不是模板變數的 {…} 拿掉；只掃描命名格式一次，代入的值不再被替換"""
    return _VARIABLE_PATTERN.sub(lambda m: variables.get(m.group(1), ""), template)
