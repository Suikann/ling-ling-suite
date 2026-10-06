# -*- coding: utf-8 -*-
"""
專案服務

提供專案檔案的儲存與載入功能。
"""
import json
import os
from typing import List, Optional
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

    @staticmethod
    def find_missing_files(project: Project) -> List[str]:
        """列出專案內指向不存在檔案的路徑

        Args:
            project: 專案資料

        Returns:
            找不到的檔案路徑清單（依群組順序）
        """
        return [p for p in project.all_file_paths() if not os.path.isfile(p)]
