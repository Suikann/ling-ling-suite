# -*- coding: utf-8 -*-
"""
資料模型

定義專案、群組、檔案資訊等核心資料結構。
"""
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from core.constants import DEFAULT_MASTER_TEMPLATE, DEFAULT_SUBFOLDER_TEMPLATE, WorkspaceStatus


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
    instruments: List[str] = field(default_factory=list)
    selected_instruments: List[int] = field(default_factory=list)
    piece_name: str = ""
    movement_number: str = ""
    movement_name: str = ""
    composer: str = ""
    genre: str = ""
    score_file: Optional["FileInfo"] = None
    score_label: str = ""
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
    operation_type: str = "rename"
    mappings: List[UndoMapping] = field(default_factory=list)
    created_directories: List[str] = field(default_factory=list)
    created_files: List[str] = field(default_factory=list)
    backup_path: str = ""
    original_path: str = ""


@dataclass
class MoveStep:
    """兩階段搬移中的一次實際檔案搬移

    Attributes:
        index: 所屬搬移項目在批次中的索引
        source: 搬移前的位置
        target: 搬移後的位置
    """
    index: int
    source: str
    target: str


@dataclass
class MoveJournal:
    """批次搬移的進行中紀錄

    steps 是目前仍生效的搬移（依執行順序），每個檔案目前的位置就是它最後一步的 target；
    pending 是正向搬移時「即將執行、可能已做也可能沒做」的那一步，由 load_pending 對照磁碟判定；
    complete 表示整批已搬完、只剩正式紀錄尚未確認寫入。

    Attributes:
        steps: 仍生效的搬移步驟（依執行順序）
        pending: 正向搬移中尚未確認完成的下一步
        complete: 整批是否已搬完
        created_directories: 本次執行新建的目錄
    """
    steps: List[MoveStep] = field(default_factory=list)
    pending: Optional[MoveStep] = None
    complete: bool = False
    created_directories: List[str] = field(default_factory=list)

    def moved_indices(self) -> List[int]:
        """已被搬動過的項目索引（依首次搬動順序）"""
        seen = []
        for step in self.steps:
            if step.index not in seen:
                seen.append(step.index)
        return seen

    def origins(self) -> Dict[int, str]:
        """已被搬動過的項目索引到原始位置（第一步的 source）的對應"""
        origins: Dict[int, str] = {}
        for step in self.steps:
            origins.setdefault(step.index, step.source)
        return origins

    def locations(self) -> Dict[int, str]:
        """已被搬動過的項目索引到目前位置（最後一步的 target）的對應"""
        return {step.index: step.target for step in self.steps}


@dataclass
class MoveRecoveryResult:
    """中斷批次還原的結果

    Attributes:
        restored: 已回到原位的檔案（原始路徑）
        skipped: 已不在紀錄位置而略過的檔案：原始路徑到紀錄位置的對應
        residual: 搬不回去的檔案：原始路徑到目前位置的對應
    """
    restored: List[str] = field(default_factory=list)
    skipped: List[UndoMapping] = field(default_factory=list)
    residual: List[UndoMapping] = field(default_factory=list)


@dataclass
class WorkspaceEntry:
    """工作區子資料夾的摘要，供清理對話框顯示"""
    folder: str
    source_name: str
    source_path: str
    project_path: str
    file_count: int
    total_bytes: int
    modified_at: float
    status: WorkspaceStatus


@dataclass
class WorkspaceScan:
    """清理前的掃描結果"""
    entries: List[WorkspaceEntry] = field(default_factory=list)
    missing_projects: List[str] = field(default_factory=list)
    unreadable_projects: List[str] = field(default_factory=list)


@dataclass
class RenameEntry:
    """重新命名計畫項目"""
    original_path: str
    new_path: str
    group_id: Optional[str] = None


@dataclass
class DriveRenameEntry:
    """Drive 重新命名計畫項目"""
    file_id: str = ""
    original_name: str = ""
    new_name: str = ""
    folder_id: str = ""
    group_name: str = ""


@dataclass
class Project:
    """專案資料"""
    instruments: List[str] = field(default_factory=list)
    master_template: str = DEFAULT_MASTER_TEMPLATE
    groups: List[Group] = field(default_factory=list)
    ungrouped_files: List[FileInfo] = field(default_factory=list)

    def all_file_paths(self) -> List[str]:
        """專案引用到的所有檔案路徑：各群組的分譜與總譜，以及未分組檔案"""
        paths = []
        for group in self.groups:
            paths.extend(f.original_path for f in group.files)
            if group.score_file:
                paths.append(group.score_file.original_path)
        paths.extend(f.original_path for f in self.ungrouped_files)
        return [p for p in paths if p]
    use_subfolders: bool = False
    subfolder_template: str = DEFAULT_SUBFOLDER_TEMPLATE
    use_parts_subfolder: bool = False
    parts_subfolder_name: str = "Parts"
    parts_output_mode: str = "root"
    output_directory: str = ""
    instrument_headcounts: Dict[str, int] = field(default_factory=dict)
    instrument_sections: Dict[str, str] = field(default_factory=dict)
