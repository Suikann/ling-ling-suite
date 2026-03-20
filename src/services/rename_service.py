# -*- coding: utf-8 -*-
"""
重新命名服務

提供批次重新命名計畫生成、衝突偵測與執行。
"""
import os
from collections import defaultdict
from datetime import datetime
from typing import Dict, List
from core.locale import t
from core.models import Project, RenameEntry, UndoMapping, UndoRecord
from core.template_engine import build_variables_for_file, substitute_template
from services.file_service import FileService


_ILLEGAL_CHARS = '<>:"/\\|?*'


def _sanitize_name(name: str) -> str:
    """替換檔名中的非法字元"""
    for ch in _ILLEGAL_CHARS:
        name = name.replace(ch, "_")
    return name.strip()


class RenameService:
    """批次重新命名服務"""

    def __init__(self, file_service: FileService):
        self.file_service = file_service

    def generate_rename_plan(self, project: Project) -> List[RenameEntry]:
        """根據專案設定產生重新命名計畫

        Args:
            project: 專案資料

        Returns:
            重新命名項目清單
        """
        plan = []
        for group in project.groups:
            template = (
                group.small_template
                if group.use_small_template and group.small_template
                else project.master_template
            )
            if group.score_file:
                label = getattr(group, "score_label", "") or t("group.score_label")
                score_vars = {
                    "序號": "00", "Number": "00",
                    "樂器": label,
                    "Instrument": label,
                    "曲名": group.piece_name,
                    "PieceName": group.piece_name,
                    "樂章編號": group.movement_number,
                    "MovementNum": group.movement_number,
                    "樂章名稱": group.movement_name,
                    "MovementName": group.movement_name,
                    "作曲家": group.composer,
                    "Composer": group.composer,
                    "曲種": group.genre,
                    "Genre": group.genre,
                }
                score_name = _sanitize_name(substitute_template(template, score_vars))
                base_dir = (
                    project.output_directory
                    if project.output_directory
                    else os.path.dirname(group.score_file.original_path)
                )
                if project.use_subfolders and project.subfolder_template:
                    subfolder_name = _sanitize_name(substitute_template(
                        project.subfolder_template, score_vars,
                    ))
                    target_dir = os.path.join(base_dir, subfolder_name)
                else:
                    target_dir = base_dir
                plan.append(RenameEntry(
                    original_path=group.score_file.original_path,
                    new_path=os.path.join(target_dir, score_name),
                    group_id=group.id,
                ))
            if not group.files or not group.instruments:
                continue
            for i, file_info in enumerate(group.files):
                if i >= len(group.instruments):
                    break
                variables = build_variables_for_file(
                    i, group, project.instruments or None,
                )
                new_name = _sanitize_name(substitute_template(template, variables))
                base_dir = (
                    project.output_directory
                    if project.output_directory
                    else os.path.dirname(file_info.original_path)
                )
                if project.use_subfolders and project.subfolder_template:
                    subfolder_name = _sanitize_name(substitute_template(
                        project.subfolder_template, variables,
                    ))
                    target_dir = os.path.join(base_dir, subfolder_name)
                else:
                    target_dir = base_dir
                if project.use_parts_subfolder and project.parts_subfolder_name:
                    target_dir = os.path.join(
                        target_dir, _sanitize_name(project.parts_subfolder_name),
                    )
                new_path = os.path.join(target_dir, new_name)
                plan.append(RenameEntry(
                    original_path=file_info.original_path,
                    new_path=new_path,
                    group_id=group.id,
                ))
        return plan

    def detect_conflicts(self, plan: List[RenameEntry]) -> Dict[str, List[str]]:
        """偵測重新命名計畫中的檔名衝突

        使用大小寫不敏感比較（Windows 檔案系統）。

        Args:
            plan: 重新命名計畫

        Returns:
            衝突的新路徑（小寫）到原始路徑清單的對應
        """
        path_map = defaultdict(list)
        for entry in plan:
            key = entry.new_path.lower()
            path_map[key].append(entry.original_path)
        return {k: v for k, v in path_map.items() if len(v) > 1}

    def apply_auto_suffix(self, plan: List[RenameEntry]) -> List[RenameEntry]:
        """為衝突的檔名自動加上後綴

        Args:
            plan: 原始重新命名計畫

        Returns:
            處理後的重新命名計畫
        """
        seen = defaultdict(int)
        result = []
        for entry in plan:
            key = entry.new_path.lower()
            count = seen[key]
            seen[key] += 1
            if count > 0:
                base, ext = os.path.splitext(entry.new_path)
                new_path = f"{base} ({count}){ext}"
            else:
                new_path = entry.new_path
            result.append(RenameEntry(
                original_path=entry.original_path,
                new_path=new_path,
                group_id=entry.group_id,
            ))
        return result

    def execute_rename(
        self, plan: List[RenameEntry], project: Project,
    ) -> UndoRecord:
        """執行重新命名計畫

        Args:
            plan: 重新命名計畫
            project: 專案資料（用於判斷子資料夾設定）

        Returns:
            復原紀錄
        """
        record = UndoRecord(
            timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
            description=t("rename.undo_description", count=len(plan)),
        )
        created_dirs = set()
        for entry in plan:
            target_dir = os.path.dirname(entry.new_path)
            if target_dir and not os.path.isdir(target_dir):
                self.file_service.create_directory(target_dir)
                created_dirs.add(target_dir)
            self.file_service.rename_file(entry.original_path, entry.new_path)
            record.mappings.append(UndoMapping(
                original=entry.original_path,
                renamed=entry.new_path,
            ))
        record.created_directories = sorted(created_dirs)
        return record
