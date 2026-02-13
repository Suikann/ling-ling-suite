# -*- coding: utf-8 -*-
"""
專案服務

提供專案檔案的儲存與載入功能。
"""
import json
from core.constants import APP_VERSION
from core.models import FileInfo, Group, Project


class ProjectService:
    """專案檔管理服務"""

    def save_project(self, project: Project, file_path: str) -> None:
        """將專案序列化為 JSON 並儲存

        Args:
            project: 專案資料
            file_path: 儲存路徑
        """
        data = {
            "version": APP_VERSION,
            "instruments": project.instruments,
            "master_template": project.master_template,
            "use_subfolders": project.use_subfolders,
            "subfolder_template": project.subfolder_template,
            "ungrouped_files": [
                {"original_path": f.original_path, "display_name": f.display_name}
                for f in project.ungrouped_files
            ],
            "groups": [self._serialize_group(g) for g in project.groups],
        }
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_project(self, file_path: str) -> Project:
        """從 JSON 檔案載入專案

        Args:
            file_path: 專案檔路徑

        Returns:
            還原的 Project 物件
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        project = Project(
            instruments=data.get("instruments", []),
            master_template=data.get("master_template", ""),
            use_subfolders=data.get("use_subfolders", False),
            subfolder_template=data.get("subfolder_template", ""),
        )
        project.ungrouped_files = [
            FileInfo(
                original_path=f["original_path"],
                display_name=f["display_name"],
            )
            for f in data.get("ungrouped_files", [])
        ]
        project.groups = [
            self._deserialize_group(g)
            for g in data.get("groups", [])
        ]
        return project

    def _serialize_group(self, group: Group) -> dict:
        """序列化單一群組

        Args:
            group: 群組資料

        Returns:
            可序列化的字典
        """
        return {
            "id": group.id,
            "name": group.name,
            "files": [
                {"original_path": f.original_path, "display_name": f.display_name}
                for f in group.files
            ],
            "selected_instruments": group.selected_instruments,
            "piece_name": group.piece_name,
            "movement_number": group.movement_number,
            "movement_name": group.movement_name,
            "use_small_template": group.use_small_template,
            "small_template": group.small_template,
        }

    def _deserialize_group(self, data: dict) -> Group:
        """反序列化單一群組

        Args:
            data: 群組字典

        Returns:
            Group 物件
        """
        group = Group(
            id=data.get("id", ""),
            name=data.get("name", ""),
            selected_instruments=data.get("selected_instruments", []),
            piece_name=data.get("piece_name", ""),
            movement_number=data.get("movement_number", ""),
            movement_name=data.get("movement_name", ""),
            use_small_template=data.get("use_small_template", False),
            small_template=data.get("small_template", ""),
        )
        group.files = [
            FileInfo(
                original_path=f["original_path"],
                display_name=f["display_name"],
            )
            for f in data.get("files", [])
        ]
        return group
