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
from services.move_service import Move, MoveService


_ILLEGAL_CHARS = '<>:"/\\|?*'


def _sanitize_name(name: str) -> str:
    """替換檔名中的非法字元"""
    for ch in _ILLEGAL_CHARS:
        name = name.replace(ch, "_")
    return name.strip()


def _name_stem(path: str) -> str:
    """取路徑最後一段去掉副檔名（最後一個點之後）的部分"""
    name = os.path.basename(path)
    return name.rpartition(".")[0] if "." in name else name


class RenameService:
    """批次重新命名服務"""

    def __init__(self, file_service: FileService):
        self.file_service = file_service
        self._mover = MoveService(file_service)

    @staticmethod
    def _moves(plan: List[RenameEntry]) -> List[Move]:
        """把計畫轉成搬移引擎的「來源 → 目標」清單"""
        return [(e.original_path, e.new_path) for e in plan]

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
                if project.parts_output_mode == "parts" and project.parts_subfolder_name:
                    target_dir = os.path.join(
                        target_dir, _sanitize_name(project.parts_subfolder_name),
                    )
                elif project.parts_output_mode == "section":
                    instrument = group.instruments[i] if i < len(group.instruments) else ""
                    section = project.instrument_sections.get(instrument)
                    if not section:
                        from core.constants import detect_instrument_section
                        section = detect_instrument_section(instrument)
                    target_dir = os.path.join(target_dir, _sanitize_name(section))
                elif project.use_parts_subfolder and project.parts_subfolder_name:
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
        return self._mover.detect_duplicate_targets(self._moves(plan))

    def detect_duplicate_sources(self, plan: List[RenameEntry]) -> Dict[str, List[str]]:
        """偵測同一來源檔案被多個項目引用的情況

        同一個檔案被兩個群組同時引用時，第一次搬移後第二次必定失敗，
        且無法用自動加後綴解決，需由使用者修正群組內容。

        Args:
            plan: 重新命名計畫

        Returns:
            被重複引用的原始路徑到新路徑清單的對應
        """
        return self._mover.detect_duplicate_sources(self._moves(plan))

    def find_missing_sources(self, plan: List[RenameEntry]) -> List[str]:
        """列出計畫中來源檔案已不存在的原始路徑"""
        return self._mover.find_missing_sources(self._moves(plan))

    def find_empty_names(self, plan: List[RenameEntry]) -> List[str]:
        """列出新檔名去掉副檔名後為空的項目

        副檔名取最後一個點之後的部分，因此「.pdf」這種只剩副檔名的名字視為空。

        Args:
            plan: 重新命名計畫

        Returns:
            新檔名為空的項目原始路徑清單（依計畫順序）
        """
        return [e.original_path for e in plan if not _name_stem(e.new_path)]

    def find_occupied_targets(self, plan: List[RenameEntry]) -> List[str]:
        """列出被計畫外檔案佔用的目標路徑（計畫內來源不算佔用），依計畫順序"""
        return self._mover.find_occupied_targets(self._moves(plan))

    def find_taken_staging_names(self, plan: List[RenameEntry]) -> List[str]:
        """列出讓位用暫名已被佔用的項目（暫名路徑），依計畫順序"""
        return self._mover.find_taken_staging_names(self._moves(plan))

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

        先檢查新檔名不為空，再交給搬移引擎驗證並以兩階段搬移執行
        （對調與連鎖可執行；中途失敗回滾，搬不回去者以 RenameRollbackError 回報）。

        Args:
            plan: 重新命名計畫
            project: 專案資料（用於判斷子資料夾設定）

        Returns:
            復原紀錄（只記原始位置到最終位置，暫名不出現）

        Raises:
            ValueError: 產生的新檔名為空
        """
        empty = self.find_empty_names(plan)
        if empty:
            raise ValueError(t("rename.error.empty_name", files="\n".join(empty)))
        created_dirs = self._mover.execute(self._moves(plan))
        return UndoRecord(
            timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
            description=t("rename.undo_description", count=len(plan)),
            mappings=[
                UndoMapping(original=entry.original_path, renamed=entry.new_path)
                for entry in plan
            ],
            created_directories=created_dirs,
        )
