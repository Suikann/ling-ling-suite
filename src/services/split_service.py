# -*- coding: utf-8 -*-
"""
分割

把合併譜依分段抽頁成分譜，分兩段：check 回報需要使用者確認的事（取代上次的分譜、
那些分譜屬於其他專案、指定資料夾已有同名檔案）與擋下的問題（分段同名、輸出會蓋掉合併譜）；
execute 依確認過的檢查結果一次執行，中途失敗整批撤回，成功時交回新分譜、被取代的路徑與分割紀錄。

分譜預設放進工作區：每份合併譜對應一個子資料夾，以來源路徑比對鍵的雜湊命名、跨專案共用
（ADR-0001）。子資料夾的定位、所屬專案的判定與改寫、取代上次分譜的順序都只在這個模組裡。

使用範例：
    splitter = SplitService(file_service, workspace_service)
    check = splitter.check(SplitRequest(source, [SplitSegment(0, 1, "Flute")], project_path=path))
    if not check.blocked:
        result = splitter.execute(check)
        placement = project.apply_split(result, score_label)
        history.record_split(result.record(placement))
"""
import hashlib
import os
import shutil
import uuid
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from core.constants import (
    SPLIT_FALLBACK_NAME, SPLIT_REPLACED_DIR_SUFFIX, SPLIT_STAGING_DIR_SUFFIX, WORKSPACE_FOLDER_HASH_LENGTH,
)
from core.filename import ensure_pdf_extension, sanitize_filename
from core.models import FileInfo, SplitEntry, SplitResult, WorkspaceOwner
from core.paths import path_key, same_path
from services.file_service import FileService
from services.pdf_service import extract_pages
from services.workspace_service import WorkspaceService

# 把合併譜的指定頁面（從 0 起算）寫成新檔：（合併譜、頁面、輸出路徑）
ExtractPages = Callable[[str, List[int], str], object]


@dataclass
class SplitSegment:
    """分割的一段：頁面範圍（從 0 起算、含頭尾）與使用者輸入的名稱"""
    first: int
    last: int
    name: str


@dataclass
class SplitRequest:
    """一次分割要做的事

    Attributes:
        source_path: 合併譜
        segments: 各分段，依畫面上的順序（分段編號從 1 起算）
        deleted_pages: 不輸出的頁面（從 0 起算）
        folder: 指定的輸出資料夾；None 表示放進工作區
        project_path: 目前專案檔，尚未存檔時為空字串；記為工作區子資料夾的所屬專案
    """
    source_path: str
    segments: List[SplitSegment]
    deleted_pages: Set[int] = field(default_factory=set)
    folder: Optional[str] = None
    project_path: str = ""


@dataclass
class DuplicateName:
    """會寫到同一個檔的分段（擋下）

    Attributes:
        file_name: 它們共用的檔名（第一個分段的寫法）
        segments: 分段編號（從 1 起算）
    """
    file_name: str
    segments: List[int]


@dataclass
class SplitCheck:
    """執行前的檢查結果：要產生的分譜、需要使用者確認的事與擋下的問題

    Attributes:
        request: 檢查的請求
        folder: 分譜會寫到的資料夾
        plan: 要產生的分譜（略過頁面全被刪掉的分段）
        duplicates: 檔名相同（含清理非法字元後相同、空名回退成同一個名字、只差大小寫）的分段（擋下）
        source_conflicts: 輸出路徑就是合併譜本身的分譜檔名（擋下）
        previous_outputs: 工作區子資料夾裡上次分割的分譜；執行後移到資源回收桶（需確認）
        owner: 上次的分譜所屬的另一個專案（確認時點名）；就是目前專案、未記錄或沒有上次的分譜時為 None
        overwritten: 指定資料夾裡已有的同名檔案；執行後移到資源回收桶、由新分譜取代（需確認）
    """
    request: SplitRequest
    folder: str
    plan: List[SplitEntry]
    duplicates: List[DuplicateName] = field(default_factory=list)
    source_conflicts: List[str] = field(default_factory=list)
    previous_outputs: List[str] = field(default_factory=list)
    owner: Optional[WorkspaceOwner] = None
    overwritten: List[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        """是否不能執行：沒有可輸出的分段，或有擋下的問題"""
        return not self.plan or bool(self.duplicates) or bool(self.source_conflicts)

    @property
    def replaced(self) -> List[str]:
        """執行後會被取代、移到資源回收桶的檔案"""
        return self.previous_outputs + self.overwritten


class SplitService:
    """分割：先檢查、確認後一次執行，失敗整批撤回"""

    def __init__(
        self, file_service: FileService, workspace: WorkspaceService,
        extract: ExtractPages = extract_pages,
    ):
        """
        Args:
            file_service: 檔案服務
            workspace: 工作區服務（子資料夾的 meta）
            extract: 抽頁寫檔的函式
        """
        self.file_service = file_service
        self.workspace = workspace
        self._extract = extract

    def output_folder(self, source_path: str, folder: Optional[str] = None) -> str:
        """分譜會寫到的資料夾

        Args:
            source_path: 合併譜
            folder: 指定的輸出資料夾；None 表示放進工作區

        Returns:
            指定的資料夾，或合併譜在工作區的子資料夾：以來源路徑比對鍵的雜湊命名；
            還沒有這個子資料夾、但有 meta 記錄同一份來源的子資料夾（升級前以舊雜湊建立）時沿用後者
        """
        if folder is not None:
            return folder
        digest = hashlib.sha1(path_key(source_path).encode("utf-8")).hexdigest()
        hashed = os.path.join(self.workspace.workspace_dir, digest[:WORKSPACE_FOLDER_HASH_LENGTH])
        if self.file_service.directory_exists(hashed):
            return hashed
        return self.workspace.find_folder(source_path) or hashed

    def check(self, request: SplitRequest) -> SplitCheck:
        """檢查一次分割：要產生哪些分譜、要使用者確認什麼、有沒有擋下的問題（不寫任何檔）

        Args:
            request: 分割的內容

        Returns:
            檢查結果；交給 execute 執行
        """
        folder = self.output_folder(request.source_path, request.folder)
        numbered = self._plan(request, folder)
        plan = [entry for _, entry in numbered]
        check = SplitCheck(
            request, folder, plan,
            duplicates=self._duplicates(numbered),
            source_conflicts=[
                os.path.basename(e.output_path) for e in plan if same_path(e.output_path, request.source_path)
            ],
        )
        if request.folder is not None:
            check.overwritten = [e.output_path for e in plan if self.file_service.file_exists(e.output_path)]
        elif self.file_service.directory_exists(folder):
            check.previous_outputs = self.file_service.list_pdf_files(folder)
            if check.previous_outputs:
                check.owner = self._other_owner(folder, request.project_path)
        return check

    def execute(self, check: SplitCheck) -> SplitResult:
        """依使用者確認過的檢查結果執行分割

        新分譜先全部寫進暫用子資料夾；寫好後才挪開位置會被新分譜佔用的被取代檔案、把新分譜放到定位、
        改寫工作區子資料夾的所屬專案，最後把被取代的檔移到資源回收桶（沒被挪開的從原處移，
        從資源回收桶還原時回到原處）。任何一步失敗都撤回已做的步驟後拋出：
        被取代的檔案留在原處、內容不變，不留下新分譜。

        Args:
            check: check 的結果

        Returns:
            新分譜、被取代的路徑、它們移到資源回收桶時所在的位置與新建的目錄

        Raises:
            ValueError: 檢查結果有擋下的問題
            OSError: 寫檔或搬移失敗（已撤回）
        """
        if check.blocked:
            raise ValueError("分割檢查有擋下的問題，不能執行")
        request = check.request
        token = uuid.uuid4().hex[:8]
        staging = os.path.join(check.folder, token + SPLIT_STAGING_DIR_SUFFIX)
        set_aside = os.path.join(check.folder, token + SPLIT_REPLACED_DIR_SUFFIX)
        created = [] if self.file_service.directory_exists(check.folder) else [check.folder]
        taken = {path_key(e.output_path) for e in check.plan}
        moved_aside: List[Tuple[str, str]] = []
        placed: List[str] = []
        try:
            self.file_service.create_directory(staging)
            staged = [os.path.join(staging, os.path.basename(e.output_path)) for e in check.plan]
            for entry, temp in zip(check.plan, staged):
                self._extract(request.source_path, entry.pages, temp)
            for path in (p for p in check.replaced if path_key(p) in taken):
                self.file_service.create_directory(set_aside)
                kept = os.path.join(set_aside, os.path.basename(path))
                self.file_service.rename_file(path, kept)
                moved_aside.append((path, kept))
            for entry, temp in zip(check.plan, staged):
                self.file_service.rename_file(temp, entry.output_path)
                placed.append(entry.output_path)
            if request.folder is None:
                self.workspace.write_meta(check.folder, request.source_path, request.project_path)
        except Exception:
            self._roll_back(placed, moved_aside, staging, set_aside, created)
            raise
        trashed = self._discard(check.replaced, dict(moved_aside), [staging, set_aside])
        return SplitResult(
            source_path=request.source_path,
            parts=[FileInfo(e.output_path, os.path.basename(e.output_path)) for e in check.plan],
            voices=[e.display_name for e in check.plan],
            replaced=list(check.replaced),
            trashed=trashed,
            created_directories=created,
        )

    # --- 內部 ---

    @staticmethod
    def _plan(request: SplitRequest, folder: str) -> List[Tuple[int, SplitEntry]]:
        """各分段要產生的分譜與其分段編號（從 1 起算）

        顯示名稱為去頭尾空白的輸入；檔名清理非法字元、空名回退為 Part 並補上 .pdf。
        頁面全被刪掉的分段略過。
        """
        plan = []
        for number, segment in enumerate(request.segments, start=1):
            pages = [p for p in range(segment.first, segment.last + 1) if p not in request.deleted_pages]
            if not pages:
                continue
            name = segment.name.strip()
            file_name = ensure_pdf_extension(sanitize_filename(name, fallback=SPLIT_FALLBACK_NAME))
            plan.append((number, SplitEntry(pages, name, os.path.join(folder, file_name))))
        return plan

    def _other_owner(self, folder: str, project_path: str) -> Optional[WorkspaceOwner]:
        """子資料夾記錄的所屬專案若不是目前專案，回傳該專案與其檔案是否仍存在

        尚未存檔的目前專案（路徑為空）不是任何已記錄的專案。
        """
        owner = self.workspace.owner_of(folder)
        if not owner or (project_path and same_path(owner, project_path)):
            return None
        return WorkspaceOwner(project_path=owner, exists=self.file_service.file_exists(owner))

    @staticmethod
    def _duplicates(numbered: List[Tuple[int, SplitEntry]]) -> List[DuplicateName]:
        """輸出路徑相同（core.paths 判定）的分段，依第一次出現的順序"""
        by_path: Dict[str, List[Tuple[int, SplitEntry]]] = {}
        for number, entry in numbered:
            by_path.setdefault(path_key(entry.output_path), []).append((number, entry))
        return [
            DuplicateName(os.path.basename(same[0][1].output_path), [number for number, _ in same])
            for same in by_path.values() if len(same) > 1
        ]

    def _roll_back(
        self, placed: List[str], moved_aside: List[Tuple[str, str]],
        staging: str, set_aside: str, created: List[str],
    ) -> None:
        """撤回執行到一半的分割（盡力而為，不蓋過原始例外）

        移除已就位的新分譜，把挪開的檔案放回原處，刪掉放新分譜的暫用子資料夾（裡面只有寫好或寫到一半的
        新分譜）；放挪開檔案的暫用子資料夾與這次新建的目錄只在已空時移除。
        """
        for path in reversed(placed):
            _remove_quietly(path)
        for original, kept in reversed(moved_aside):
            try:
                self.file_service.rename_file(kept, original)
            except OSError:
                continue
        shutil.rmtree(staging, ignore_errors=True)
        self.file_service.remove_empty_directory(set_aside)
        for directory in reversed(created):
            self.file_service.remove_empty_directory(directory)

    def _discard(self, replaced: List[str], kept_at: Dict[str, str], scratch: List[str]) -> List[str]:
        """分割完成後把被取代的檔案移到資源回收桶並移除暫用子資料夾

        被挪開的檔案從暫用子資料夾裡移（保留原檔名），其餘從原處移。
        移不進資源回收桶的檔案留在當時的位置，不影響已完成的分割。

        Args:
            replaced: 被取代的檔案（原本的路徑）
            kept_at: 被挪開的檔案：原本的路徑到暫用子資料夾裡的路徑
            scratch: 暫用子資料夾，已空時移除

        Returns:
            移到資源回收桶的檔案當時所在的位置（依 replaced 的順序）
        """
        trashed = []
        for path in replaced:
            location = kept_at.get(path, path)
            try:
                self.file_service.delete_file(location)
            except OSError:
                continue
            trashed.append(location)
        for directory in scratch:
            self.file_service.remove_empty_directory(directory)
        return trashed

def _remove_quietly(path: str) -> None:
    """移除檔案；不存在或移除失敗都不拋出"""
    try:
        os.remove(path)
    except OSError:
        pass
