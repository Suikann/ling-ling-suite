# -*- coding: utf-8 -*-
"""
專案存取

開啟與存檔的全部動作歸在這裡：讀寫專案檔、最近專案清單、工作區 meta 的所屬專案。
專案檔本身讀寫成功就算成功；附帶動作寫不進去只帶在結果裡，由呼叫端提示。

使用範例：
    from services.project_access import ProjectAccess
    access = ProjectAccess(file_service, preferences, workspace_service)
    result = access.open(path)
    if result.error is None:
        project = result.project
"""
import os
from dataclasses import dataclass, field
from typing import List, Optional
from core.constants import MAX_RECENT_PROJECTS, RECENT_PROJECTS_KEY
from core.models import Project, WorkspaceScan
from core.paths import path_key, same_path
from services.file_service import FileService
from services.preferences_service import PreferencesService
from services.project_service import ProjectService
from services.workspace_service import WorkspaceService


@dataclass
class AccessResult:
    """開啟或存檔的結果

    error 是專案檔本身讀寫失敗的原因，為 None 即算成功；
    recent_failed（最近清單寫不進去）與 owner_failed（meta 寫不進去的工作區子資料夾）是附帶動作的失敗，
    不影響成功與否；missing_files 只在開啟時填，是專案引用、但磁碟上找不到的檔案。
    """
    project: Optional[Project] = None
    error: Optional[Exception] = None
    recent_failed: bool = False
    owner_failed: List[str] = field(default_factory=list)
    missing_files: List[str] = field(default_factory=list)


class ProjectAccess:
    """專案檔的開啟與存檔，連同最近清單與工作區 meta 所屬專案"""

    def __init__(
        self, file_service: FileService, preferences: PreferencesService, workspace: WorkspaceService,
    ):
        self._file_service = file_service
        self._project_files = ProjectService(file_service)
        self._preferences = preferences
        self._workspace = workspace

    def open(self, path: str) -> AccessResult:
        """讀取專案檔；讀到後更新工作區 meta 的所屬專案並把它放到最近清單頂端

        Args:
            path: 專案檔路徑

        Returns:
            開啟結果；error 為 None 時 project 是讀到的專案（已判為已存檔），missing_files 是它引用、
            但磁碟上找不到的檔案（依群組順序）。專案檔已不存在時 error 為 FileNotFoundError，並已從最近清單移除
        """
        try:
            project = self._project_files.load_project(path)
        except FileNotFoundError as e:
            self._forget_recent([path])
            return AccessResult(error=e)
        except Exception as e:
            return AccessResult(error=e)
        missing = [p for p in project.all_file_paths() if not self._file_service.file_exists(p)]
        return self._record_location(AccessResult(project=project, missing_files=missing), path)

    def save(self, project: Project, path: str) -> AccessResult:
        """寫出專案檔；寫成後專案的快照即為寫出的內容，再更新工作區 meta 的所屬專案與最近清單

        Args:
            project: 要存的專案
            path: 專案檔路徑

        Returns:
            存檔結果；error 為 None 即算存檔成功（附帶動作的失敗另外帶在結果裡）。
            專案檔寫不成時不做任何附帶動作，專案仍判為未存檔
        """
        try:
            self._project_files.save_project(project, path)
        except Exception as e:
            return AccessResult(error=e)
        return self._record_location(AccessResult(project=project), path)

    def matches_file(self, project: Project, path: str) -> bool:
        """專案目前的內容是否與磁碟上的專案檔相同（關閉前的比對）；專案檔不在或讀不到都算不同"""
        return self._project_files.matches_file(project, path)

    def known_projects(self) -> List[str]:
        """已知專案：最近清單，加上各工作區子資料夾 meta 記錄的所屬專案（同一個檔案只留第一次出現的寫法）"""
        seen = set()
        known: List[str] = []
        for path in self.recent_projects() + self._workspace.recorded_owners():
            if path_key(path) not in seen:
                seen.add(path_key(path))
                known.append(path)
        return known

    def scan_workspace(self, current: Optional[Project]) -> WorkspaceScan:
        """「清理工作區」的掃描：以已知專案判定引用關係，最近清單中已不存在的專案檔順手移除

        掃描會直接刪除只剩 meta.json、且沒有被目前專案或已知專案引用的空資料夾（ADR-0001）。
        最近清單寫不進去不影響掃描結果，記憶體中的清單仍已移除，下次寫入偏好設定時一併寫出。

        Args:
            current: 目前開啟的專案，可為 None

        Returns:
            掃描結果
        """
        scan = self._workspace.scan(current, self.known_projects(), self._project_files.load_project)
        if scan.missing_projects:
            self._forget_recent(scan.missing_projects)
        return scan

    def forget_if_missing(self, path: str) -> bool:
        """專案檔已不存在時從最近清單移除（從最近清單開啟前先問，免得先問要不要儲存）

        Args:
            path: 專案檔路徑

        Returns:
            專案檔已不存在時為 True
        """
        if self._file_service.file_exists(path):
            return False
        self._forget_recent([path])
        return True

    def recent_projects(self) -> List[str]:
        """最近開啟或存檔的專案檔，最新的在前"""
        return list(self._preferences.get(RECENT_PROJECTS_KEY) or [])

    # --- 內部 ---

    def _record_location(self, result: AccessResult, path: str) -> AccessResult:
        """專案檔讀到或寫成後的附帶動作：meta 所屬專案與最近清單各自進行，一個失敗不影響另一個

        Args:
            result: 專案檔已讀到或寫成的結果
            path: 專案檔路徑

        Returns:
            填上附帶動作失敗的同一份結果
        """
        result.owner_failed = self._workspace.update_project_path(result.project, path)
        recent = [p for p in self.recent_projects() if not same_path(p, path)]
        recent.insert(0, os.path.normpath(path))
        result.recent_failed = not self._write_recent(recent[:MAX_RECENT_PROJECTS])
        return result

    def _forget_recent(self, paths: List[str]) -> None:
        """從最近清單移除指定的專案檔（任何寫法）；寫不進去時記憶體中的清單仍已移除"""
        keys = {path_key(p) for p in paths}
        self._write_recent([p for p in self.recent_projects() if path_key(p) not in keys])

    def _write_recent(self, recent: List[str]) -> bool:
        """更新最近清單並寫入偏好設定；回傳是否寫成（寫不成時記憶體中的清單仍已更新）"""
        self._preferences.set(RECENT_PROJECTS_KEY, recent)
        try:
            self._preferences.save()
        except OSError:
            return False
        return True
