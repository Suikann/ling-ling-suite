# -*- coding: utf-8 -*-
"""
專案服務

提供專案檔案的儲存與載入功能。
"""
import json
from typing import Optional
from core.constants import APP_VERSION
from core.locale import t
from core.models import Project
from services.file_service import FileService


class ProjectService:
    """專案檔管理服務"""

    def __init__(self, file_service: Optional[FileService] = None):
        self.file_service = file_service or FileService()

    def save_project(self, project: Project, file_path: str) -> None:
        """將專案序列化為 JSON 並儲存；寫入成功後以存出的內容作為專案的已存檔快照

        Args:
            project: 專案資料
            file_path: 儲存路徑
        """
        data = {"version": APP_VERSION, **project.to_data()}
        self.file_service.write_json_atomic(file_path, data)
        project.mark_saved()

    def load_project(self, file_path: str) -> Project:
        """從 JSON 檔案載入專案

        Args:
            file_path: 專案檔路徑

        Returns:
            還原的 Project 物件，已完成舊格式遷移並判為已存檔
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return Project.from_data(data, score_label=t("group.score_label"))

    def matches_file(self, project: Project, file_path: str) -> bool:
        """專案目前的內容是否與專案檔的內容相同（專案檔照開啟時的方式還原、含舊格式遷移）

        Args:
            project: 專案資料
            file_path: 專案檔路徑

        Returns:
            相同時為 True；專案檔不存在、讀不到或格式不對時一律視為不同
        """
        try:
            on_disk = self.load_project(file_path)
        except Exception:
            return False
        return on_disk.to_data() == project.to_data()
