# -*- coding: utf-8 -*-
"""
命名格式套用

把群組中的一格（總譜，或第 N 份分譜）依命名設定轉成檔名與相對資料夾。
純函式：不碰磁碟、不讀介面語言；本機重新命名、預覽與 Drive 重新命名都經過這裡。

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
    MIN_NUMBER_WIDTH, RELATIVE_DIR_NAMES, SCORE_NUMBER, TEMPLATE_SLOT_SOURCES, TEMPLATE_VARIABLES, PartsOutputMode,
    TemplateVariable, VariableLevel, detect_instrument_section,
)
from core.filename import ensure_pdf_extension, sanitize_filename
from core.models import FileInfo, Group, Project

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
        sections: 聲部名稱到聲部組名稱；分譜存放模式為聲部組資料夾時，每個要命名的聲部都必須有
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
        voices: 明確指定的樂器表，優先於群組的樂器表；未提供或為空時用群組的樂器表

    Returns:
        這一格的檔名與相對資料夾

    Raises:
        ValueError: slot 超出聲部數
        KeyError: 聲部組資料夾模式下，這一格的聲部沒有聲部組
        UnsafeFolderNameError: 某一層資料夾名稱清理後是 . 或 ..
    """
    voices = _effective_voices(group, voices)
    if slot == SCORE_SLOT:
        number, voice = SCORE_NUMBER, group.score_label
    elif 1 <= slot <= len(voices):
        number = str(slot).zfill(max(MIN_NUMBER_WIDTH, len(str(len(voices)))))
        voice = voices[slot - 1]
    else:
        raise ValueError(f"第 {slot} 格超出聲部數 {len(voices)}")
    variables = _variables(group, number, voice)
    folders = []
    if settings.subfolder_template:
        folders.append(_substitute(settings.subfolder_template, variables))
    if slot != SCORE_SLOT:
        if settings.parts_mode == PartsOutputMode.PARTS and settings.parts_folder:
            folders.append(settings.parts_folder)
        elif settings.parts_mode == PartsOutputMode.SECTION:
            folders.append(settings.sections[voice])
    file_name = ensure_pdf_extension(sanitize_filename(_substitute(settings.template, variables)))
    return SlotName(file_name, tuple(name for name in map(_folder_name, folders) if name))


def name_group(group: Group, settings: NamingSettings, voices: Optional[Sequence[str]] = None) -> GroupNaming:
    """為群組的總譜與分譜命名；第 N 份分譜對應第 N 個聲部，多於聲部數的分譜不改名

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
    voices = _effective_voices(group, voices)
    slots = [(SCORE_SLOT, group.score_file)] if group.score_file else []
    slots += list(enumerate(group.files[:len(voices)], start=1))
    return GroupNaming(
        files=[NamedFile(file, slot, name_slot(group, slot, settings, voices)) for slot, file in slots],
        extra_files=group.files[len(voices):],
    )


def named_voices(group: Group, voices: Optional[Sequence[str]] = None) -> List[str]:
    """有分譜對應、會被命名的聲部（依序）；參數同 name_group"""
    return _effective_voices(group, voices)[:len(group.files)]


def section_for(voice: str, sections: Mapping[str, str], english: bool) -> str:
    """聲部的聲部組：專案已設定（不是空白）就用設定的，否則是依指定語言偵測的預設值

    Args:
        voice: 聲部名稱
        sections: 專案的聲部名稱到聲部組名稱
        english: 預設值用英文名稱

    Returns:
        聲部組名稱
    """
    stored = sections.get(voice, "")
    return stored if stored.strip() else detect_instrument_section(voice, english)


def default_sections(groups: Iterable[Group], sections: Mapping[str, str], english: bool) -> Dict[str, str]:
    """這些群組會被命名的聲部中，還沒有聲部組（或留空）的聲部到它的預設聲部組

    Args:
        groups: 群組
        sections: 專案的聲部名稱到聲部組名稱
        english: 預設值用英文名稱

    Returns:
        聲部名稱到依指定語言偵測的聲部組；都已設定時為空
    """
    return {
        voice: section_for(voice, sections, english)
        for group in groups for voice in named_voices(group) if not sections.get(voice, "").strip()
    }


def _effective_voices(group: Group, voices: Optional[Sequence[str]]) -> List[str]:
    """命名用的樂器表：明確指定且不為空時用它，否則用群組的樂器表"""
    return list(voices) if voices else list(group.instruments)


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
    """一格的變數表（中英文名稱都有），由模板變數常數推得：逐檔變數取這一格的值，群組層級變數取群組欄位"""
    slot_values = dict(zip(TEMPLATE_SLOT_SOURCES, (number, voice)))
    table = {}
    for var in TEMPLATE_VARIABLES:
        value = slot_values[var.source] if var.level == VariableLevel.FILE else getattr(group, var.source)
        table[var.name] = table[var.name_en] = value
    return table


def _substitute(template: str, variables: Dict[str, str]) -> str:
    """把命名格式中的 {變數} 換成值、不是模板變數的 {…} 拿掉；只掃描命名格式一次，代入的值不再被替換"""
    return _VARIABLE_PATTERN.sub(lambda m: variables.get(m.group(1), ""), template)
