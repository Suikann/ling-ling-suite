# -*- coding: utf-8 -*-
"""
工作區服務

管理程式產生、尚待重新命名的檔案（目前只有分割輸出）。每個來源合併譜對應
工作區內一個以路徑雜湊命名的子資料夾，資料夾內的 meta.json 記錄來源與所屬專案。

使用範例：
    from services.workspace_service import WorkspaceService
    workspace = WorkspaceService(file_service)
    folder = workspace.prepare_folder(source_pdf, project_path)
"""
import hashlib
import json
import os
import time
from typing import Callable, Dict, Iterable, List, Optional, Set

from core.constants import (
    WORKSPACE_DIR, WORKSPACE_FOLDER_HASH_LENGTH, WORKSPACE_META_FILE, WorkspaceStatus,
)
from core.models import Project, WorkspaceEntry, WorkspaceOwner, WorkspaceScan
from services.file_service import FileService


def _normalize(path: str) -> str:
    """路徑正規化，供比對與雜湊使用"""
    return os.path.normcase(os.path.abspath(path))


class WorkspaceService:
    """工作區管理服務"""

    def __init__(self, file_service: FileService, workspace_dir: str = WORKSPACE_DIR):
        self.file_service = file_service
        self.workspace_dir = workspace_dir

    # --- 路徑 ---

    def folder_for_source(self, source_path: str) -> str:
        """取得來源檔案對應的工作區子資料夾路徑（不建立）

        Args:
            source_path: 來源合併譜路徑

        Returns:
            子資料夾的絕對路徑，名稱為來源絕對路徑雜湊的前幾碼
        """
        digest = hashlib.sha1(_normalize(source_path).encode("utf-8")).hexdigest()
        return os.path.join(self.workspace_dir, digest[:WORKSPACE_FOLDER_HASH_LENGTH])

    def is_in_workspace(self, path: str) -> bool:
        """檢查路徑是否位於工作區內"""
        try:
            return os.path.commonpath([_normalize(path), _normalize(self.workspace_dir)]) == _normalize(self.workspace_dir)
        except ValueError:
            return False

    def folder_of(self, path: str) -> Optional[str]:
        """取得工作區內檔案所屬的子資料夾；不在工作區內則回傳 None"""
        if not self.is_in_workspace(path):
            return None
        folder = os.path.dirname(os.path.abspath(path))
        if _normalize(folder) == _normalize(self.workspace_dir):
            return None
        return folder

    # --- 子資料夾生命週期 ---

    def prepare_folder(self, source_path: str, project_path: str = "") -> str:
        """建立（或沿用）來源對應的子資料夾並寫入 meta.json

        所屬專案一律改成目前專案：輸出被這次分割取代後就不再屬於先前的專案，
        尚未存檔時記為未知（空字串），存檔時再由 update_project_path 補上。

        Args:
            source_path: 來源合併譜路徑
            project_path: 目前專案檔路徑，尚未存檔時為空字串

        Returns:
            子資料夾路徑
        """
        folder = self.folder_for_source(source_path)
        self.file_service.create_directory(folder)
        meta = self.read_meta(folder) or {}
        meta.update({
            "source_path": os.path.abspath(source_path),
            "source_name": os.path.basename(source_path),
            "project_path": project_path,
            "created_at": meta.get("created_at") or time.time(),
        })
        self._write_meta(folder, meta)
        return folder

    def list_outputs(self, folder: str) -> List[str]:
        """列出子資料夾內的分割輸出（PDF）"""
        if not os.path.isdir(folder):
            return []
        return self.file_service.list_pdf_files(folder)

    def clear_outputs(self, folder: str) -> None:
        """將子資料夾內上一次的分割輸出移至資源回收桶，保留 meta.json"""
        for path in self.list_outputs(folder):
            self.file_service.delete_file(path)

    def remove_folder(self, folder: str) -> None:
        """移除只剩 meta.json 的空子資料夾；仍有輸出檔時不動

        連同 meta.json 原子寫入的殘留一起清掉，否則資料夾清不空、下次掃描會變成來源不明。
        """
        if not os.path.isdir(folder) or self.list_outputs(folder):
            return
        meta_path = os.path.join(folder, WORKSPACE_META_FILE)
        if os.path.isfile(meta_path):
            os.remove(meta_path)
        self.file_service.remove_atomic_residue(meta_path)
        self.file_service.remove_empty_directory(folder)

    def purge_empty_folders(self, in_use: Optional[Set[str]] = None) -> int:
        """移除所有已搬空（只剩 meta.json）且未被使用的子資料夾

        搬空是暫態：復原重新命名會把分譜搬回來，需要 meta.json 才能辨識來源，
        所以搬空當下不刪，留到掃描時再一併清除。

        Args:
            in_use: 目前專案引用的子資料夾（正規化路徑），這些不移除

        Returns:
            移除的資料夾數
        """
        in_use = in_use or set()
        count = 0
        for folder in self._list_folders():
            if _normalize(folder) in in_use or self.list_outputs(folder):
                continue
            self.remove_folder(folder)
            count += 1
        return count

    # --- meta.json ---

    @staticmethod
    def _owner_path(meta: Optional[Dict]) -> str:
        """meta 記錄的所屬專案檔路徑；沒有 meta 或未記錄時為空字串"""
        return (meta or {}).get("project_path") or ""

    def read_meta(self, folder: str) -> Optional[Dict]:
        """讀取子資料夾的 meta.json；不存在或損毀時回傳 None"""
        meta_path = os.path.join(folder, WORKSPACE_META_FILE)
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _write_meta(self, folder: str, meta: Dict) -> None:
        """寫入子資料夾的 meta.json"""
        self.file_service.write_json_atomic(os.path.join(folder, WORKSPACE_META_FILE), meta)

    def restore_meta(self, folder: str, meta: Dict) -> None:
        """復原重新命名把分譜搬回後，子資料夾若已沒有可讀的 meta.json，用快照寫回

        子資料夾不存在（分譜沒搬回來）或已有 meta（例如另一專案在這期間重新分割過）時不寫。

        Args:
            folder: 子資料夾路徑
            meta: 先前快照的 meta.json 內容
        """
        if not os.path.isdir(folder) or self.read_meta(folder) is not None:
            return
        self._write_meta(folder, meta)

    def update_project_path(self, project: Project, project_path: str) -> List[str]:
        """專案開啟或存檔時，更新其引用到的所有工作區子資料夾的所屬專案

        專案檔被搬到別處後從新位置開啟，meta 也要跟著記到新位置。
        單一子資料夾寫入失敗不中斷其餘更新，由呼叫端決定如何提示。

        Args:
            project: 專案資料
            project_path: 專案檔路徑

        Returns:
            meta 寫入失敗的子資料夾清單
        """
        failed: List[str] = []
        for folder in sorted(self._referenced_folders(project)):
            meta = self.read_meta(folder)
            if meta is None:
                continue
            meta["project_path"] = os.path.abspath(project_path)
            try:
                self._write_meta(folder, meta)
            except OSError:
                failed.append(folder)
        return failed

    def other_owner(self, folder: str, current_project_path: str) -> Optional[WorkspaceOwner]:
        """子資料夾的所屬專案若不是目前專案，回傳該專案

        子資料夾以來源為鍵、跨專案共用，重新分割會取代另一個專案尚未重新命名的分譜，
        提示時要點名。尚未存檔的專案（路徑為空）視為「不是」任何已記錄的所屬專案。

        Args:
            folder: 子資料夾路徑
            current_project_path: 目前專案檔路徑，尚未存檔時為空字串

        Returns:
            所屬專案與其檔案是否仍存在；所屬專案為空或就是目前專案時回傳 None
        """
        owner_path = self._owner_path(self.read_meta(folder))
        if not owner_path:
            return None
        if current_project_path and _normalize(owner_path) == _normalize(current_project_path):
            return None
        return WorkspaceOwner(project_path=owner_path, exists=self.file_service.file_exists(owner_path))

    # --- 清理 ---

    def scan(
        self,
        current_project: Optional[Project],
        recent_projects: List[str],
        load_project: Callable[[str], Project],
    ) -> WorkspaceScan:
        """掃描工作區並判定各子資料夾的引用狀態

        Args:
            current_project: 目前開啟的專案，可為 None
            recent_projects: 最近專案檔路徑清單
            load_project: 載入專案檔的函式

        Returns:
            掃描結果，含各子資料夾摘要與無法處理的專案檔清單
        """
        scan = WorkspaceScan()
        in_use = self._referenced_folders(current_project) if current_project else set()
        owned: Dict[str, str] = {}
        unreadable: Set[str] = set()
        for path in self._candidate_projects(recent_projects):
            if not os.path.isfile(path):
                scan.missing_projects.append(path)
                continue
            try:
                project = load_project(path)
            except Exception:
                scan.unreadable_projects.append(path)
                unreadable.add(_normalize(path))
                continue
            for folder in self._referenced_folders(project):
                owned.setdefault(folder, path)
        self.purge_empty_folders(in_use | set(owned))
        for folder in self._list_folders():
            key = _normalize(folder)
            meta = self.read_meta(folder)
            owner_path = self._owner_path(meta)
            if key in in_use:
                status = WorkspaceStatus.IN_USE
            elif key in owned:
                status = WorkspaceStatus.OWNED_BY_OTHER
            elif meta is None:
                status = WorkspaceStatus.UNKNOWN_SOURCE
            elif owner_path and _normalize(owner_path) in unreadable:
                status = WorkspaceStatus.OWNER_UNREADABLE
            else:
                status = WorkspaceStatus.ORPHAN
            scan.entries.append(self._build_entry(folder, meta, status, owned.get(key, "")))
        scan.missing_projects = [p for p in scan.missing_projects if p in recent_projects]
        scan.entries.sort(key=lambda e: e.modified_at, reverse=True)
        return scan

    def cleanup(self, entries: Iterable[WorkspaceEntry]) -> int:
        """將指定的子資料夾整個移至資源回收桶

        Args:
            entries: 要清理的項目

        Returns:
            實際清理的資料夾數
        """
        count = 0
        for entry in entries:
            if os.path.isdir(entry.folder):
                self.file_service.delete_directory(entry.folder)
                count += 1
        return count

    # --- 內部 ---

    def _list_folders(self) -> List[str]:
        """列出工作區內所有子資料夾；工作區尚未建立時回傳空清單"""
        if not os.path.isdir(self.workspace_dir):
            return []
        return self.file_service.list_subdirectories(self.workspace_dir)

    def _candidate_projects(self, recent_projects: List[str]) -> List[str]:
        """需要載入以判定引用關係的專案檔：最近清單，加上各子資料夾 meta 指向的專案"""
        seen: Set[str] = set()
        candidates: List[str] = []
        owners = [self._owner_path(self.read_meta(f)) for f in self._list_folders()]
        for path in list(recent_projects) + owners:
            if path and _normalize(path) not in seen:
                seen.add(_normalize(path))
                candidates.append(path)
        return candidates

    def _referenced_folders(self, project: Project) -> Set[str]:
        """專案引用到的工作區子資料夾（正規化後的路徑集合）"""
        folders: Set[str] = set()
        for path in project.all_file_paths():
            folder = self.folder_of(path)
            if folder:
                folders.add(_normalize(folder))
        return folders

    def _build_entry(
        self, folder: str, meta: Optional[Dict], status: WorkspaceStatus, owner: str,
    ) -> WorkspaceEntry:
        """組出子資料夾的摘要

        Args:
            folder: 子資料夾路徑
            meta: 已讀取的 meta.json 內容，可為 None
            status: 判定後的引用狀態
            owner: 引用此資料夾的專案檔路徑，無則為空字串

        Returns:
            供清理對話框顯示的摘要
        """
        outputs = self.list_outputs(folder)
        total = sum(os.path.getsize(p) for p in outputs)
        modified = max((os.path.getmtime(p) for p in outputs), default=os.path.getmtime(folder))
        meta = meta or {}
        return WorkspaceEntry(
            folder=folder,
            source_name=meta.get("source_name", ""),
            source_path=meta.get("source_path", ""),
            project_path=owner or meta.get("project_path", ""),
            file_count=len(outputs),
            total_bytes=total,
            modified_at=modified,
            status=status,
        )
