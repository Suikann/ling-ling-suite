# -*- coding: utf-8 -*-
"""
重新命名服務

提供批次重新命名計畫生成、衝突偵測與執行。
"""
import os
from collections import defaultdict
from datetime import datetime
from typing import Callable, Collection, Dict, List, Optional
from core.constants import PartsOutputMode, detect_instrument_section
from core.locale import get_locale, t
from core.models import Group, Project, RenameEntry, UndoMapping, UndoRecord
from core.naming import name_group, named_voices, settings_for
from core.paths import path_key
from services.file_service import FileService
from services.move_service import Move, MoveService


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

    def generate_rename_plan(
        self, project: Project, group_ids: Optional[Collection[str]] = None,
    ) -> List[RenameEntry]:
        """根據專案設定產生重新命名計畫

        檔名與相對資料夾由命名模組決定，接在輸出位置之下；沒指定輸出位置時接在來源檔所在的資料夾。
        分譜依聲部組分放時，還沒有聲部組的聲部先依目前介面語言寫進專案（第一次用到時定下，之後不隨介面語言改變）。

        Args:
            project: 專案資料
            group_ids: 只為這些群組產生計畫；None 表示全部群組

        Returns:
            重新命名項目清單（各群組依序：總譜在前，接著依序的分譜；多於聲部數的分譜不在其中）

        Raises:
            UnsafeFolderNameError: 某一層資料夾名稱清理後是 . 或 ..
        """
        groups = [g for g in project.groups if group_ids is None or g.id in group_ids]
        if project.parts_output_mode == PartsOutputMode.SECTION:
            self._assign_default_sections(project, groups)
        plan = []
        for group in groups:
            for named in name_group(group, settings_for(project, group)).files:
                source = named.file.original_path
                base_dir = project.output_directory or os.path.dirname(source)
                plan.append(RenameEntry(
                    original_path=source,
                    new_path=os.path.join(base_dir, named.name.relative_path()),
                    group_id=group.id,
                ))
        return plan

    @staticmethod
    def _assign_default_sections(project: Project, groups: List[Group]) -> None:
        """這些群組會被命名的聲部中，還沒有聲部組（或留空）的，依目前介面語言寫入偵測到的聲部組"""
        english = get_locale() == "en"
        missing = {}
        for group in groups:
            for voice in named_voices(group):
                if not project.instrument_sections.get(voice, "").strip():
                    missing[voice] = detect_instrument_section(voice, english)
        if missing:
            project.update_ensemble(sections=missing)

    def detect_conflicts(self, plan: List[RenameEntry]) -> Dict[str, List[str]]:
        """偵測重新命名計畫中的檔名衝突（路徑以 core.paths 判定同一性）

        Args:
            plan: 重新命名計畫

        Returns:
            衝突的新路徑（首次出現的寫法）到原始路徑清單的對應
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
            key = path_key(entry.new_path)
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
        save_record: Optional[Callable[[UndoRecord], None]] = None,
    ) -> UndoRecord:
        """執行重新命名計畫

        先檢查新檔名不為空，再交給搬移引擎驗證並以兩階段搬移執行
        （對調與連鎖可執行；中途失敗回滾，搬不回去者以 RenameRollbackError 回報）。
        復原紀錄透過 save_record 在引擎刪除進行中紀錄之前寫入，兩者之間沒有空窗。

        Args:
            plan: 重新命名計畫
            project: 專案資料（用於判斷子資料夾設定）
            save_record: 整批搬完後用來寫入復原紀錄的函式

        Returns:
            復原紀錄（只記原始位置到最終位置，暫名不出現）

        Raises:
            ValueError: 產生的新檔名為空
        """
        empty = self.find_empty_names(plan)
        if empty:
            raise ValueError(t("rename.error.empty_name", files="\n".join(empty)))
        record = UndoRecord(
            timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
            description=t("rename.undo_description", count=len(plan)),
            mappings=[
                UndoMapping(original=entry.original_path, renamed=entry.new_path)
                for entry in plan
            ],
        )

        def complete(created_dirs: List[str]) -> None:
            record.created_directories = created_dirs
            if save_record:
                save_record(record)

        self._mover.execute(self._moves(plan), on_complete=complete)
        return record
