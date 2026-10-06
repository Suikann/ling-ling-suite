# -*- coding: utf-8 -*-
"""
資料模型

定義專案、群組、檔案資訊等核心資料結構。
"""
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, NamedTuple, Optional
from core.constants import DEFAULT_MASTER_TEMPLATE, DEFAULT_SUBFOLDER_TEMPLATE, WorkspaceStatus
from core.paths import path_key


@dataclass
class FileInfo:
    """檔案資訊"""
    original_path: str
    display_name: str

    def to_data(self) -> Dict[str, str]:
        """專案檔中存的內容"""
        return {"original_path": self.original_path, "display_name": self.display_name}

    @classmethod
    def from_data(cls, data: Dict[str, str]) -> "FileInfo":
        """由專案檔中的內容還原"""
        return cls(original_path=data["original_path"], display_name=data["display_name"])


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

    def to_data(self) -> Dict[str, Any]:
        """專案檔中存的內容；串列一律複製，之後改動群組不會改到這份內容"""
        return {
            "id": self.id,
            "name": self.name,
            "files": [f.to_data() for f in self.files],
            "score_file": self.score_file.to_data() if self.score_file else None,
            "instruments": list(self.instruments),
            "score_label": self.score_label,
            "selected_instruments": list(self.selected_instruments),
            "piece_name": self.piece_name,
            "movement_number": self.movement_number,
            "movement_name": self.movement_name,
            "composer": self.composer,
            "genre": self.genre,
            "use_small_template": self.use_small_template,
            "small_template": self.small_template,
        }

    @classmethod
    def from_data(cls, data: Dict[str, Any]) -> "Group":
        """由專案檔中的內容還原（不做舊格式遷移，遷移由 Project.from_data 負責）"""
        score_data = data.get("score_file")
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            files=[FileInfo.from_data(f) for f in data.get("files", [])],
            score_file=FileInfo.from_data(score_data) if score_data else None,
            instruments=data.get("instruments", []),
            score_label=data.get("score_label", ""),
            selected_instruments=data.get("selected_instruments", []),
            piece_name=data.get("piece_name", ""),
            movement_number=data.get("movement_number", ""),
            movement_name=data.get("movement_name", ""),
            composer=data.get("composer", ""),
            genre=data.get("genre", ""),
            use_small_template=data.get("use_small_template", False),
            small_template=data.get("small_template", ""),
        )


@dataclass
class UndoMapping:
    """復原對照項目"""
    original: str
    renamed: str


@dataclass
class UndoRecord:
    """復原紀錄

    Attributes:
        workspace_meta: 來源位於工作區的項目，其子資料夾到 meta.json 內容的快照；
            復原時子資料夾已被清理掃描刪掉的話，用它把 meta 寫回
    """
    timestamp: str = ""
    description: str = ""
    operation_type: str = "rename"
    mappings: List[UndoMapping] = field(default_factory=list)
    created_directories: List[str] = field(default_factory=list)
    created_files: List[str] = field(default_factory=list)
    backup_path: str = ""
    original_path: str = ""
    workspace_meta: Dict[str, Dict] = field(default_factory=dict)


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
class WorkspaceOwner:
    """工作區子資料夾所屬的另一個專案（供重新分割提示）"""
    project_path: str
    exists: bool


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
class SplitEntry:
    """分割計畫項目：一個區段要輸出的頁面、顯示名稱與輸出路徑"""
    pages: List[int]
    display_name: str
    output_path: str


@dataclass
class DriveRenameEntry:
    """Drive 重新命名計畫項目"""
    file_id: str = ""
    original_name: str = ""
    new_name: str = ""
    folder_id: str = ""
    group_name: str = ""


class FileRef(NamedTuple):
    """專案引用到的一個檔案

    Attributes:
        group: 所屬群組；未分組檔案為 None
        file: 檔案資訊
    """
    group: Optional[Group]
    file: FileInfo


@dataclass
class Project:
    """專案資料"""
    instruments: List[str] = field(default_factory=list)
    master_template: str = DEFAULT_MASTER_TEMPLATE
    groups: List[Group] = field(default_factory=list)
    ungrouped_files: List[FileInfo] = field(default_factory=list)
    use_subfolders: bool = False
    subfolder_template: str = DEFAULT_SUBFOLDER_TEMPLATE
    use_parts_subfolder: bool = False
    parts_subfolder_name: str = "Parts"
    parts_output_mode: str = "root"
    output_directory: str = ""
    instrument_headcounts: Dict[str, int] = field(default_factory=dict)
    instrument_sections: Dict[str, str] = field(default_factory=dict)
    _saved: Optional[Dict[str, Any]] = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self):
        self._saved = self.to_data()

    # --- 內容與未存檔 ---

    def to_data(self) -> Dict[str, Any]:
        """專案檔中存的內容（不含存檔格式的版本號）；串列與字典一律複製

        存檔寫出的就是這份內容，未存檔判定也拿它和快照比對，兩者不會分歧。
        """
        return {
            "instruments": list(self.instruments),
            "master_template": self.master_template,
            "use_subfolders": self.use_subfolders,
            "subfolder_template": self.subfolder_template,
            "use_parts_subfolder": self.use_parts_subfolder,
            "parts_subfolder_name": self.parts_subfolder_name,
            "parts_output_mode": self.parts_output_mode,
            "output_directory": self.output_directory,
            "instrument_headcounts": dict(self.instrument_headcounts),
            "instrument_sections": dict(self.instrument_sections),
            "ungrouped_files": [f.to_data() for f in self.ungrouped_files],
            "groups": [g.to_data() for g in self.groups],
        }

    @classmethod
    def from_data(cls, data: Dict[str, Any], score_label: str) -> "Project":
        """由專案檔中的內容還原，完成舊格式遷移後以還原結果作為已存檔的快照

        Args:
            data: 專案檔中的內容
            score_label: 總譜標籤留空的舊群組要補上的標籤（依目前介面語言）

        Returns:
            判為已存檔的專案
        """
        project = cls(
            instruments=data.get("instruments", []),
            master_template=data.get("master_template", ""),
            use_subfolders=data.get("use_subfolders", False),
            subfolder_template=data.get("subfolder_template", ""),
            use_parts_subfolder=data.get("use_parts_subfolder", False),
            parts_subfolder_name=data.get("parts_subfolder_name", "Parts"),
            parts_output_mode=data.get("parts_output_mode", "root"),
            output_directory=data.get("output_directory", ""),
            instrument_headcounts=data.get("instrument_headcounts", {}),
            instrument_sections=data.get("instrument_sections", {}),
            ungrouped_files=[FileInfo.from_data(f) for f in data.get("ungrouped_files", [])],
            groups=[Group.from_data(g) for g in data.get("groups", [])],
        )
        if "parts_output_mode" not in data and project.use_parts_subfolder:
            project.parts_output_mode = "parts"
        for group in project.groups:
            project._migrate_group(group, score_label)
        project.mark_saved()
        return project

    def _migrate_group(self, group: Group, score_label: str) -> None:
        """舊格式群組的遷移：補上總譜標籤、承接專案層級的樂器表、把勾選的子集收成群組自己的樂器表"""
        if not group.score_label:
            group.score_label = score_label
        if not group.instruments and self.instruments:
            group.instruments = list(self.instruments)
        selected = group.selected_instruments
        if group.instruments and selected and selected != list(range(len(group.instruments))):
            group.instruments = [group.instruments[i] for i in selected if i < len(group.instruments)]
        group.selected_instruments = list(range(len(group.instruments)))

    def is_modified(self) -> bool:
        """目前內容是否與上次存檔或開啟時的快照不同"""
        return self.to_data() != self._saved

    def mark_saved(self) -> None:
        """以目前內容作為已存檔的快照（存檔或開啟後呼叫）"""
        self._saved = self.to_data()

    # --- 檔案引用 ---

    def file_refs(self) -> List[FileRef]:
        """專案引用到的所有檔案（分譜、總譜、未分組的唯一走訪）

        Returns:
            依群組順序列出各群組的總譜與分譜（總譜在前），最後是未分組檔案
        """
        refs: List[FileRef] = []
        for group in self.groups:
            if group.score_file:
                refs.append(FileRef(group, group.score_file))
            refs.extend(FileRef(group, f) for f in group.files)
        refs.extend(FileRef(None, f) for f in self.ungrouped_files)
        return refs

    def all_file_paths(self) -> List[str]:
        """專案引用到的所有檔案路徑，順序同 file_refs()"""
        return [ref.file.original_path for ref in self.file_refs() if ref.file.original_path]

    def replace_paths(self, mappings: Iterable[UndoMapping]) -> None:
        """依搬移結果（原路徑 → 新路徑）更新檔案引用的路徑與顯示名稱，路徑以 core.paths 判定同一性

        Args:
            mappings: 搬移對照
        """
        moved = {path_key(m.original): m.renamed for m in mappings}
        for ref in self.file_refs():
            new_path = moved.get(path_key(ref.file.original_path))
            if new_path is not None:
                ref.file.original_path = new_path
                ref.file.display_name = os.path.basename(new_path)

    def remove_paths(self, paths: Iterable[str]) -> None:
        """移除指向指定路徑的檔案引用（分譜、總譜、未分組），路徑以 core.paths 判定同一性

        Args:
            paths: 要移除的檔案路徑
        """
        doomed = {path_key(p) for p in paths}
        for ref in self.file_refs():
            if path_key(ref.file.original_path) in doomed:
                self._detach(ref)

    def _detach(self, ref: FileRef) -> None:
        """把一個檔案引用從所在的群組或未分組清單拿掉"""
        if ref.group is None:
            self.ungrouped_files = [f for f in self.ungrouped_files if f is not ref.file]
        elif ref.group.score_file is ref.file:
            ref.group.score_file = None
        else:
            ref.group.files = [f for f in ref.group.files if f is not ref.file]
