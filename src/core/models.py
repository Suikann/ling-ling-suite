# -*- coding: utf-8 -*-
"""
資料模型

定義專案、群組、檔案資訊等核心資料結構。
"""
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Dict, FrozenSet, Iterable, List, NamedTuple, Optional
from core.constants import (
    DEFAULT_MASTER_TEMPLATE, DEFAULT_PARTS_SUBFOLDER_NAME, DEFAULT_SUBFOLDER_TEMPLATE, LEGACY_PROJECT_FILE_VERSIONS,
    OperationKind, PartsOutputMode, WorkspaceStatus,
)
from core.paths import path_key
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
    """搬移對照：檔案從 original 搬到 renamed"""
    original: str
    renamed: str


@dataclass
class UndoRecord:
    """復原紀錄（復原與重做堆疊裡的一筆）

    Attributes:
        id: 紀錄的唯一識別，在復原與重做堆疊之間轉移時不變；舊版紀錄沒有，載入時以檔名代替
        timestamp: 建立時間（只供人閱讀，不作為鍵）
        description: 確認復原、重做時顯示的描述（建立時依當時的介面語言寫入）
        operation_type: 操作種類；殘留紀錄標示的是留下它的操作（重新命名、復原或重做）
        residual: 是否為殘留紀錄（操作中途失敗、記下搬不回原位的檔案）
        mappings: 整批搬移類的搬移對照（原位置到新位置）
        created_directories: 這次操作新建的目錄
        created_files: 這次操作產生的檔案（分割的分譜、旋轉另存的檔）
        backup_path: 旋轉覆蓋原檔前的備份
        original_path: 旋轉的來源檔；分割的合併譜
        workspace_meta: 來源位於工作區的項目，其子資料夾到 meta.json 內容的快照；
            復原時子資料夾已被清理掃描刪掉的話，用它把 meta 寫回
        replaced_files: 分割時被取代、已移到資源回收桶的檔案（復原不找回）
        placement: 專案怎麼安置分割的結果（復原時退回分割前的專案）；舊版紀錄沒有時為 None
    """
    id: str = ""
    timestamp: str = ""
    description: str = ""
    operation_type: OperationKind = OperationKind.RENAME
    residual: bool = False
    mappings: List[UndoMapping] = field(default_factory=list)
    created_directories: List[str] = field(default_factory=list)
    created_files: List[str] = field(default_factory=list)
    backup_path: str = ""
    original_path: str = ""
    workspace_meta: Dict[str, Dict] = field(default_factory=dict)
    replaced_files: List[str] = field(default_factory=list)
    placement: Optional["SplitPlacement"] = None

    def to_data(self) -> Dict[str, Any]:
        """紀錄檔中存的內容"""
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "description": self.description,
            "operation_type": self.operation_type.value,
            "residual": self.residual,
            "mappings": [{"original": m.original, "renamed": m.renamed} for m in self.mappings],
            "created_directories": list(self.created_directories),
            "created_files": list(self.created_files),
            "backup_path": self.backup_path,
            "original_path": self.original_path,
            "workspace_meta": dict(self.workspace_meta),
            "replaced_files": list(self.replaced_files),
            "placement": self.placement.to_data() if self.placement else None,
        }

    @classmethod
    def from_data(cls, data: Dict[str, Any], fallback_id: str) -> "UndoRecord":
        """由紀錄檔中的內容還原；舊版紀錄缺少的欄位補預設值

        Args:
            data: 紀錄檔中的內容
            fallback_id: 舊版紀錄沒有 id 時使用的識別

        Raises:
            ValueError: 操作種類不認得，或缺少必要欄位
        """
        try:
            record = cls(
                id=data.get("id") or fallback_id,
                timestamp=data.get("timestamp", ""),
                description=data.get("description", ""),
                operation_type=OperationKind(data.get("operation_type", OperationKind.RENAME.value)),
                residual=bool(data.get("residual", False)),
                mappings=[UndoMapping(m["original"], m["renamed"]) for m in data.get("mappings", [])],
                created_directories=data.get("created_directories", []),
                created_files=data.get("created_files", []),
                backup_path=data.get("backup_path", ""),
                original_path=data.get("original_path", ""),
                workspace_meta=data.get("workspace_meta", {}),
                replaced_files=data.get("replaced_files", []),
                placement=SplitPlacement.from_data(data["placement"]) if data.get("placement") else None,
            )
        except (KeyError, TypeError) as e:
            raise ValueError(str(e)) from e
        if (record.operation_type == OperationKind.ROTATE and record.original_path
                and not record.backup_path and not record.created_files):
            # 舊版旋轉另存的紀錄只把另存出的檔記在 original_path
            record.created_files = [record.original_path]
        return record


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
    """重新命名計畫項目

    Attributes:
        original_path: 來源
        new_path: 目標
        group_id: 所屬群組
        output_directory: 專案的輸出位置；空字串表示沒指定（放在來源檔所在的資料夾）
    """
    original_path: str
    new_path: str
    group_id: Optional[str] = None
    output_directory: str = ""

    def output_location(self) -> str:
        """這一項實際的輸出位置：目標必須在它之內；沒指定輸出位置時為來源檔所在的資料夾"""
        return self.output_directory or os.path.dirname(self.original_path)


@dataclass
class SplitEntry:
    """分割計畫項目：一個區段要輸出的頁面、顯示名稱與輸出路徑"""
    pages: List[int]
    display_name: str
    output_path: str


@dataclass
class FilePosition:
    """檔案引用在專案裡的位置

    Attributes:
        group_id: 所在群組的 id；未分組為空字串
        score: 是否為該群組的總譜
        index: 在該群組分譜清單（未分組時為未分組清單）中的位置，從 0 起算；總譜為 0
    """
    group_id: str = ""
    score: bool = False
    index: int = 0

    def to_data(self) -> Dict[str, Any]:
        """紀錄檔中存的內容"""
        return {"group_id": self.group_id, "score": self.score, "index": self.index}

    @classmethod
    def from_data(cls, data: Dict[str, Any]) -> "FilePosition":
        """由紀錄檔中的內容還原"""
        return cls(group_id=data["group_id"], score=bool(data["score"]), index=int(data["index"]))


@dataclass
class SplitPlacement:
    """Project.apply_split 怎麼把分割結果放進專案；復原分割時據以退回分割前的專案

    Attributes:
        group_id: 接手新分譜的群組
        created_group: 接手的群組是否為這次分割另建的（復原時移除）
        instruments: 接手的群組在分割前的樂器表；分割沒有改變它時為 None
        piece_name: 接手的群組在分割前的曲名（分割可能自動偵測填入）；分割沒有改變它時為 None
        source: 合併譜在分割前的位置；專案沒有引用合併譜時為 None
    """
    group_id: str
    created_group: bool
    instruments: Optional[List[str]] = None
    piece_name: Optional[str] = None
    source: Optional[FilePosition] = None

    def to_data(self) -> Dict[str, Any]:
        """紀錄檔中存的內容"""
        return {
            "group_id": self.group_id,
            "created_group": self.created_group,
            "instruments": list(self.instruments) if self.instruments is not None else None,
            "piece_name": self.piece_name,
            "source": self.source.to_data() if self.source else None,
        }

    @classmethod
    def from_data(cls, data: Dict[str, Any]) -> "SplitPlacement":
        """由紀錄檔中的內容還原"""
        source = data.get("source")
        return cls(
            group_id=data["group_id"],
            created_group=bool(data["created_group"]),
            instruments=data.get("instruments"),
            piece_name=data.get("piece_name"),
            source=FilePosition.from_data(source) if source else None,
        )


@dataclass
class SplitRecord:
    """一次分割：交給搬移歷程記成復原紀錄，復原時再交回專案退回（Project.revert_split）

    Attributes:
        source_path: 合併譜；舊版紀錄沒有時為空字串
        created_files: 產生的分譜
        created_directories: 這次新建的目錄
        replaced_files: 被取代、已移到資源回收桶的檔案（上次的分譜，或指定資料夾內的同名檔案）；復原不找回
        placement: 專案怎麼安置這次分割的結果；舊版紀錄沒有時為 None
    """
    source_path: str
    created_files: List[str]
    created_directories: List[str]
    replaced_files: List[str]
    placement: Optional[SplitPlacement] = None


@dataclass
class SplitResult:
    """一次分割的結果，交給專案套用（Project.apply_split）

    Attributes:
        source_path: 合併譜
        parts: 產生的分譜，依分段順序
        voices: 各分譜的聲部名稱（使用者輸入的分段名稱）
        replaced: 被取代的檔案；專案裡指向它們的引用要移除
        created_directories: 這次新建的目錄
    """
    source_path: str
    parts: List[FileInfo]
    voices: List[str]
    replaced: List[str]
    created_directories: List[str] = field(default_factory=list)

    def record(self, placement: Optional[SplitPlacement] = None) -> SplitRecord:
        """交給搬移歷程的分割紀錄

        Args:
            placement: Project.apply_split 回報的安置方式

        Returns:
            分割紀錄
        """
        return SplitRecord(
            source_path=self.source_path,
            created_files=[p.original_path for p in self.parts],
            created_directories=list(self.created_directories),
            replaced_files=list(self.replaced),
            placement=placement,
        )


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
    parts_subfolder_name: str = DEFAULT_PARTS_SUBFOLDER_NAME
    parts_output_mode: str = PartsOutputMode.ROOT
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

        群組的遷移只在舊版程式寫的專案檔（version 在 LEGACY_PROJECT_FILE_VERSIONS）做，
        新版存的檔裡留空的總譜標籤與樂器表是使用者清掉的，不補回。

        Args:
            data: 專案檔中的內容（含 version）
            score_label: 舊版專案檔裡總譜標籤留空的群組要補上的標籤（依目前介面語言）

        Returns:
            判為已存檔的專案
        """
        project = cls(
            instruments=data.get("instruments", []),
            master_template=data.get("master_template", ""),
            use_subfolders=data.get("use_subfolders", False),
            subfolder_template=data.get("subfolder_template", ""),
            use_parts_subfolder=data.get("use_parts_subfolder", False),
            parts_subfolder_name=data.get("parts_subfolder_name", DEFAULT_PARTS_SUBFOLDER_NAME),
            parts_output_mode=data.get("parts_output_mode", PartsOutputMode.ROOT),
            output_directory=data.get("output_directory", ""),
            instrument_headcounts=data.get("instrument_headcounts", {}),
            instrument_sections=data.get("instrument_sections", {}),
            ungrouped_files=[FileInfo.from_data(f) for f in data.get("ungrouped_files", [])],
            groups=[Group.from_data(g) for g in data.get("groups", [])],
        )
        if "parts_output_mode" not in data and project.use_parts_subfolder:
            project.parts_output_mode = PartsOutputMode.PARTS
        if data.get("version") in LEGACY_PROJECT_FILE_VERSIONS:
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
        self.use_parts_subfolder = self.parts_output_mode == PartsOutputMode.PARTS
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

    def apply_split(self, result: SplitResult, score_label: str) -> SplitPlacement:
        """套用分割結果：移除被取代的引用、把合併譜移出所在的群組，新分譜交給接手的群組

        接手的群組依序為：引用被取代檔案的群組（重新分割，不另建群組、不留下空群組）、
        引用合併譜的群組（不論它是分譜還是總譜）；都沒有時另建一個以合併譜檔名命名的群組。
        未分組裡的合併譜留在未分組。新分譜的聲部名稱成為接手群組的樂器表，並自動偵測總譜與曲名。

        Args:
            result: 分割模組的執行結果
            score_label: 另建群組時的總譜標籤（依建立時的介面語言）

        Returns:
            安置方式（接手的群組、合併譜原本的位置、被改掉的群組欄位）；連同分割紀錄交給搬移歷程，復原時退回
        """
        group = self._group_referencing(result.replaced) or self._group_referencing([result.source_path])
        self._detach_paths(result.replaced)
        source_position = self._position_of(result.source_path)
        source = path_key(result.source_path)
        for ref in self.file_refs():
            if ref.group is not None and path_key(ref.file.original_path) == source:
                self._detach(ref)
        created = group is None
        if created:
            name = os.path.splitext(os.path.basename(result.source_path))[0]
            group = Group(name=name, score_label=score_label)
            self.groups.append(group)
        instruments, piece_name = list(group.instruments), group.piece_name
        group.files.extend(result.parts)
        group.instruments = list(result.voices)
        group.selected_instruments = list(range(len(group.instruments)))
        self._detect_on_entry(group)
        self._changed()
        return SplitPlacement(
            group_id=group.id, created_group=created,
            instruments=instruments if instruments != group.instruments else None,
            piece_name=piece_name if piece_name != group.piece_name else None,
            source=source_position,
        )

    def revert_split(self, record: SplitRecord) -> None:
        """退回一次分割（套用復原分割的結果）

        移除新分譜的引用；分割時另建的群組移除（之後才放進去的檔案移回未分組），
        接手的既有群組被分割改掉的樂器表與曲名改回；合併譜已不在專案裡時放回原本的位置
        （同一個群組、同樣是分譜或總譜，分譜放回原本的順序；原本的群組已刪除就放回未分組）。
        被取代的檔案不找回。舊版紀錄沒有安置資訊，只移除新分譜的引用。

        Args:
            record: 搬移歷程復原的分割紀錄
        """
        self._detach_paths(record.created_files)
        placement = record.placement
        if placement is not None:
            group = self._group_by_id(placement.group_id)
            if group is not None and placement.created_group:
                self._remove_group(group)
            elif group is not None:
                if placement.instruments is not None:
                    group.instruments = list(placement.instruments)
                    group.selected_instruments = list(range(len(group.instruments)))
                if placement.piece_name is not None:
                    group.piece_name = placement.piece_name
            if placement.source is not None and not self._references(record.source_path):
                self._put_back(FileInfo(record.source_path, os.path.basename(record.source_path)), placement.source)
        self._changed()

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
        self._remove_group(group)
        self._changed()

    def _remove_group(self, group: Group) -> None:
        """移除群組，總譜與分譜移回未分組尾端（總譜在前；不發通知）"""
        self.groups = [g for g in self.groups if g is not group]
        if group.score_file:
            self.ungrouped_files.append(group.score_file)
        self.ungrouped_files.extend(group.files)

    def move_to_ungrouped(self, files: Iterable[FileInfo]) -> None:
        """把群組裡的檔案移回未分組的尾端"""
        files = list(files)
        self._detach_files(files)
        self.ungrouped_files.extend(files)
        self._changed()

    def set_score(self, group: Group, file: FileInfo) -> None:
        """把群組的一份分譜設為總譜，原本的總譜放回分譜尾端"""
        self._assign_score(group, file)
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

    @staticmethod
    def _assign_score(group: Group, file: FileInfo) -> None:
        """把檔案設為群組的總譜（原本在分譜清單裡就拿出來），原本的總譜放回分譜尾端"""
        if group.score_file:
            group.files.append(group.score_file)
        group.files = [f for f in group.files if f is not file]
        group.score_file = file

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

    def references_any(self, paths: Iterable[str]) -> bool:
        """專案是否引用其中任一路徑（分譜、總譜或未分組），路徑以 core.paths 判定同一性"""
        return bool(self._refs_to(paths))

    def _refs_to(self, paths: Iterable[str]) -> List[FileRef]:
        """指向其中任一路徑的檔案引用，順序同 file_refs()"""
        keys = {path_key(p) for p in paths}
        return [ref for ref in self.file_refs() if path_key(ref.file.original_path) in keys]

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
        self._detach_paths(paths)
        self._changed()

    def _detach_paths(self, paths: Iterable[str]) -> None:
        """拿掉指向指定路徑的檔案引用（不發通知）"""
        doomed = {path_key(p) for p in paths}
        for ref in self.file_refs():
            if path_key(ref.file.original_path) in doomed:
                self._detach(ref)

    def _group_referencing(self, paths: Iterable[str]) -> Optional[Group]:
        """第一個（依 file_refs 順序）引用到其中任一路徑的群組；沒有時為 None"""
        keys = {path_key(p) for p in paths}
        for ref in self.file_refs():
            if ref.group is not None and path_key(ref.file.original_path) in keys:
                return ref.group
        return None

    def _references(self, path: str) -> bool:
        """專案是否引用指定路徑（分譜、總譜或未分組）"""
        key = path_key(path)
        return any(path_key(ref.file.original_path) == key for ref in self.file_refs())

    def _group_by_id(self, group_id: str) -> Optional[Group]:
        """指定 id 的群組；已不在專案裡時為 None"""
        return next((g for g in self.groups if g.id == group_id), None)

    def _position_of(self, path: str) -> Optional[FilePosition]:
        """第一個（依 file_refs 順序）指向指定路徑的引用所在的位置；專案沒有引用它時為 None"""
        key = path_key(path)
        ref = next((r for r in self.file_refs() if path_key(r.file.original_path) == key), None)
        if ref is None:
            return None
        if ref.group is None:
            return FilePosition(index=_index_of(self.ungrouped_files, ref.file))
        if ref.group.score_file is ref.file:
            return FilePosition(group_id=ref.group.id, score=True)
        return FilePosition(group_id=ref.group.id, index=_index_of(ref.group.files, ref.file))

    def _put_back(self, file: FileInfo, position: FilePosition) -> None:
        """把檔案引用放回記下的位置；原本的群組已不在專案裡時放回未分組尾端"""
        group = self._group_by_id(position.group_id) if position.group_id else None
        if group is None:
            index = position.index if not position.group_id else len(self.ungrouped_files)
            self.ungrouped_files.insert(index, file)
        elif position.score:
            self._assign_score(group, file)
        else:
            group.files.insert(position.index, file)

    def _detach(self, ref: FileRef) -> None:
        """把一個檔案引用從所在的群組或未分組清單拿掉"""
        if ref.group is None:
            self.ungrouped_files = [f for f in self.ungrouped_files if f is not ref.file]
        elif ref.group.score_file is ref.file:
            ref.group.score_file = None
        else:
            ref.group.files = [f for f in ref.group.files if f is not ref.file]


def _index_of(files: List[FileInfo], file: FileInfo) -> int:
    """檔案資訊（依物件本身判定）在清單中的位置"""
    return next(i for i, f in enumerate(files) if f is file)
