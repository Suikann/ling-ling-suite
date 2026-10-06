# -*- coding: utf-8 -*-
"""
資料模型

定義專案、群組、檔案資訊等核心資料結構。
"""
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Dict, FrozenSet, Iterable, List, NamedTuple, Optional
from core.constants import DEFAULT_MASTER_TEMPLATE, DEFAULT_SUBFOLDER_TEMPLATE, WorkspaceStatus
from core.paths import path_key, same_path
from core.template_engine import convert_template_language, detect_piece_name, detect_score_index


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
    EDITABLE_FIELDS: ClassVar[FrozenSet[str]] = frozenset({
        "name", "piece_name", "movement_number", "movement_name", "composer", "genre",
        "score_label", "use_small_template", "small_template",
    })

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
    OUTPUT_SETTINGS: ClassVar[FrozenSet[str]] = frozenset({
        "output_directory", "use_subfolders", "subfolder_template", "parts_output_mode", "parts_subfolder_name",
    })
    _saved: Optional[Dict[str, Any]] = field(default=None, init=False, repr=False, compare=False)
    _listeners: List[Callable[[], None]] = field(default_factory=list, init=False, repr=False, compare=False)

    def __post_init__(self):
        self._saved = self.to_data()

    # --- 已變更通知 ---

    def subscribe(self, listener: Callable[[], None]) -> None:
        """訂閱「已變更」通知：每次編輯操作或存檔、開啟後呼叫 listener（不帶參數）"""
        self._listeners.append(listener)

    def _changed(self) -> None:
        """通知訂閱者專案內容或已存檔狀態可能變了"""
        for listener in list(self._listeners):
            listener()

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
        self._changed()

    # --- 專案設定 ---

    def set_master_template(self, template: str) -> None:
        """設定通用大模板"""
        self.master_template = template
        self._changed()

    def set_output_settings(self, **settings: Any) -> None:
        """修改輸出設定（Project.OUTPUT_SETTINGS）；舊欄位 use_parts_subfolder 跟著分譜存放模式維持

        Raises:
            ValueError: 含有不屬於輸出設定的欄位
        """
        unknown = set(settings) - Project.OUTPUT_SETTINGS
        if unknown:
            raise ValueError(f"不是輸出設定的欄位：{sorted(unknown)}")
        for name, value in settings.items():
            setattr(self, name, value)
        self.use_parts_subfolder = self.parts_output_mode == "parts"
        self._changed()

    def update_ensemble(
        self, headcounts: Optional[Dict[str, int]] = None, sections: Optional[Dict[str, str]] = None,
    ) -> None:
        """寫入編制設定：逐聲部更新建議人數與聲部組，沒提到的聲部不變

        Args:
            headcounts: 聲部名稱到建議人數
            sections: 聲部名稱到聲部組名稱
        """
        self.instrument_headcounts.update(headcounts or {})
        self.instrument_sections.update(sections or {})
        self._changed()

    def convert_template_language(self, locale: str) -> None:
        """切換介面語言時，把大模板、子資料夾模板與各群組小模板的變數名稱換成該語言"""
        self.master_template = convert_template_language(self.master_template, locale)
        self.subfolder_template = convert_template_language(self.subfolder_template, locale)
        for group in self.groups:
            group.small_template = convert_template_language(group.small_template, locale)
        self._changed()

    # --- 群組與檔案 ---

    def add_group(self, name: str, score_label: str, files: Iterable[FileInfo] = ()) -> Group:
        """新增群組；帶入的檔案從原本所在處（通常是未分組）移入，並自動偵測總譜與曲名

        Args:
            name: 群組名稱
            score_label: 總譜標籤（依建立時的介面語言）
            files: 要放進群組的檔案

        Returns:
            新群組
        """
        group = Group(name=name, score_label=score_label)
        self.groups.append(group)
        self._move_into(group, files)
        self._changed()
        return group

    def add_groups(self, groups: Iterable[Group], score_label: str) -> None:
        """加入匯入資料夾建立的群組；總譜標籤留空的補上，並自動偵測總譜與曲名

        Args:
            groups: 匯入建立的群組（已帶有名稱與檔案）
            score_label: 總譜標籤（依建立時的介面語言）
        """
        for group in groups:
            if not group.score_label:
                group.score_label = score_label
            self.groups.append(group)
            self._detect_on_entry(group)
        self._changed()

    def add_files(self, files: Iterable[FileInfo], group: Optional[Group] = None) -> None:
        """加入新匯入的檔案；加進群組時自動偵測總譜與曲名

        Args:
            files: 新檔案
            group: 目標群組；None 表示加進未分組
        """
        if group is None:
            self.ungrouped_files.extend(files)
        else:
            group.files.extend(files)
            self._detect_on_entry(group)
        self._changed()

    def move_to_group(self, files: Iterable[FileInfo], group: Group) -> None:
        """把專案內的檔案（未分組或其他群組）搬進群組的分譜尾端，並自動偵測總譜與曲名"""
        self._move_into(group, files)
        self._changed()

    def add_split_result(
        self, files: Iterable[FileInfo], voices: Iterable[str], source_path: str,
        group: Optional[Group] = None, score_label: str = "",
    ) -> Group:
        """加入分割產生的分譜，並自動偵測總譜與曲名

        Args:
            files: 分割產生的分譜
            voices: 各分譜的聲部名稱，成為群組的樂器表
            source_path: 分割來源（合併譜）的路徑；從所在群組的分譜中移出
            group: 合併譜所在的群組；None 表示另建一個以合併譜檔名命名的群組
            score_label: 另建群組時的總譜標籤（依建立時的介面語言）

        Returns:
            接收分譜的群組
        """
        if group is None:
            group = Group(name=os.path.splitext(os.path.basename(source_path))[0], score_label=score_label)
            self.groups.append(group)
        else:
            group.files = [f for f in group.files if not same_path(f.original_path, source_path)]
        group.files.extend(files)
        group.instruments = list(voices)
        group.selected_instruments = list(range(len(group.instruments)))
        self._detect_on_entry(group)
        self._changed()
        return group

    def update_group(self, group: Group, **fields: Any) -> None:
        """修改群組的文字與小模板欄位（Group.EDITABLE_FIELDS）

        Args:
            group: 要修改的群組
            fields: 欄位名稱與新值

        Raises:
            ValueError: 含有不能以此方式修改的欄位（檔案、總譜、樂器表各有專用操作）
        """
        unknown = set(fields) - Group.EDITABLE_FIELDS
        if unknown:
            raise ValueError(f"不能以 update_group 修改的欄位：{sorted(unknown)}")
        for name, value in fields.items():
            setattr(group, name, value)
        self._changed()

    def delete_group(self, group: Group) -> None:
        """刪除群組，總譜與分譜移回未分組（總譜在前）"""
        self.groups = [g for g in self.groups if g is not group]
        if group.score_file:
            self.ungrouped_files.append(group.score_file)
        self.ungrouped_files.extend(group.files)
        self._changed()

    def move_to_ungrouped(self, files: Iterable[FileInfo]) -> None:
        """把群組裡的檔案移回未分組的尾端"""
        files = list(files)
        self._detach_files(files)
        self.ungrouped_files.extend(files)
        self._changed()

    def set_score(self, group: Group, file: FileInfo) -> None:
        """把群組的一份分譜設為總譜，原本的總譜放回分譜尾端"""
        if group.score_file:
            group.files.append(group.score_file)
        group.files = [f for f in group.files if f is not file]
        group.score_file = file
        self._changed()

    def clear_score(self, group: Group) -> None:
        """清除群組的總譜，該檔放回分譜的最前面"""
        if group.score_file:
            group.files.insert(0, group.score_file)
            group.score_file = None
            self._changed()

    def reorder_files(self, group: Group, files: List[FileInfo]) -> None:
        """依新順序排列群組的分譜

        Raises:
            ValueError: 新順序與群組現有的分譜不是同一批檔案
        """
        if sorted(map(id, files)) != sorted(map(id, group.files)):
            raise ValueError("新順序必須是群組現有的同一批分譜")
        group.files = list(files)
        self._changed()

    def set_instruments(self, group: Group, instruments: Iterable[str]) -> None:
        """設定群組的樂器表（聲部清單）；舊欄位 selected_instruments 跟著維持全選"""
        group.instruments = list(instruments)
        group.selected_instruments = list(range(len(group.instruments)))
        self._changed()

    def link_movements(self, groups: Iterable[Group], piece_name: str) -> None:
        """把群組連成同一首曲目的各樂章：曲名一致、樂章編號依序，沒有樂章名稱的以群組名稱補上"""
        for number, group in enumerate(groups, start=1):
            group.piece_name = piece_name
            group.movement_number = str(number)
            if not group.movement_name:
                group.movement_name = group.name
        self._changed()

    def guess_piece_name(self, group: Group) -> str:
        """使用者要求重猜曲名：由分譜檔名的共同部分偵測，猜得到就取代目前的曲名

        Returns:
            偵測到的曲名；猜不到時為空字串，曲名不變
        """
        detected = detect_piece_name([f.display_name for f in group.files])
        if detected:
            group.piece_name = detected
            self._changed()
        return detected

    def _move_into(self, group: Group, files: Iterable[FileInfo]) -> None:
        """把檔案從專案中原本的位置拿掉、放進群組分譜尾端，並自動偵測"""
        files = list(files)
        if not files:
            return
        self._detach_files(files)
        group.files.extend(files)
        self._detect_on_entry(group)

    def _detach_files(self, files: Iterable[FileInfo]) -> None:
        """把指定的檔案資訊（依物件本身判定）從所在的群組或未分組拿掉"""
        targets = {id(f) for f in files}
        for ref in self.file_refs():
            if id(ref.file) in targets:
                self._detach(ref)

    @staticmethod
    def _detect_on_entry(group: Group) -> None:
        """檔案進入群組後的自動偵測：沒有總譜才猜總譜、沒有曲名才猜曲名（由分譜檔名）"""
        if group.score_file is None:
            index = detect_score_index([f.display_name for f in group.files])
            if index is not None:
                group.score_file = group.files.pop(index)
        if not group.piece_name.strip():
            group.piece_name = detect_piece_name([f.display_name for f in group.files])

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
        self._changed()

    def remove_paths(self, paths: Iterable[str]) -> None:
        """移除指向指定路徑的檔案引用（分譜、總譜、未分組），路徑以 core.paths 判定同一性

        Args:
            paths: 要移除的檔案路徑
        """
        doomed = {path_key(p) for p in paths}
        for ref in self.file_refs():
            if path_key(ref.file.original_path) in doomed:
                self._detach(ref)
        self._changed()

    def _detach(self, ref: FileRef) -> None:
        """把一個檔案引用從所在的群組或未分組清單拿掉"""
        if ref.group is None:
            self.ungrouped_files = [f for f in self.ungrouped_files if f is not ref.file]
        elif ref.group.score_file is ref.file:
            ref.group.score_file = None
        else:
            ref.group.files = [f for f in ref.group.files if f is not ref.file]
