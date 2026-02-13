# -*- coding: utf-8 -*-
"""
資料模型

定義專案、群組、檔案資訊等核心資料結構。
"""
import uuid
from dataclasses import dataclass, field
from typing import List, Optional
from core.constants import DEFAULT_MASTER_TEMPLATE, DEFAULT_SUBFOLDER_TEMPLATE


@dataclass
class FileInfo:
    """檔案資訊"""
    original_path: str
    display_name: str


@dataclass
class Group:
    """群組：代表一首曲目的一個樂章"""
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    files: List[FileInfo] = field(default_factory=list)
    selected_instruments: List[int] = field(default_factory=list)
    piece_name: str = ""
    movement_number: str = ""
    movement_name: str = ""
    use_small_template: bool = False
    small_template: str = ""


@dataclass
class UndoMapping:
    """復原對照項目"""
    original: str
    renamed: str


@dataclass
class UndoRecord:
    """復原紀錄"""
    timestamp: str = ""
    description: str = ""
    mappings: List[UndoMapping] = field(default_factory=list)
    created_directories: List[str] = field(default_factory=list)


@dataclass
class RenameEntry:
    """重新命名計畫項目"""
    original_path: str
    new_path: str
    group_id: Optional[str] = None


@dataclass
class Project:
    """專案資料"""
    instruments: List[str] = field(default_factory=list)
    master_template: str = DEFAULT_MASTER_TEMPLATE
    groups: List[Group] = field(default_factory=list)
    ungrouped_files: List[FileInfo] = field(default_factory=list)
    use_subfolders: bool = False
    subfolder_template: str = DEFAULT_SUBFOLDER_TEMPLATE
