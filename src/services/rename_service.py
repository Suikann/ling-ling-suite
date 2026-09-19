# -*- coding: utf-8 -*-
"""
重新命名服務

提供批次重新命名計畫生成、衝突偵測與執行。
"""
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Set
from core.constants import RENAME_STAGING_SUFFIX
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


class RenameRollbackError(OSError):
    """重新命名中途失敗且部分檔案無法搬回原位

    Attributes:
        cause: 觸發回滾的原始例外
        residual: 仍留在新位置、需要記錄以便日後復原的對應
    """

    def __init__(self, cause: Exception, residual: List[UndoMapping]):
        super().__init__(t("rename.error.rollback_failed",
                           error=cause, files="\n".join(m.renamed for m in residual)))
        self.cause = cause
        self.residual = residual


def _group_duplicates(plan: List[RenameEntry], key, value) -> Dict[str, List[str]]:
    """依 key 分組，回傳出現多次的鍵及其對應值清單"""
    grouped = defaultdict(list)
    for entry in plan:
        grouped[key(entry)].append(value(entry))
    return {k: v for k, v in grouped.items() if len(v) > 1}


def _name_stem(path: str) -> str:
    """取路徑最後一段去掉副檔名（最後一個點之後）的部分"""
    name = os.path.basename(path)
    return name.rpartition(".")[0] if "." in name else name


def _staging_path(path: str) -> str:
    """來源檔案在第一階段讓出位置時使用的暫名（同資料夾、純 rename）"""
    return path + RENAME_STAGING_SUFFIX


@dataclass
class _Move:
    """兩階段搬移中的一次實際檔案搬移

    Attributes:
        index: 所屬計畫項目在計畫中的索引
        source: 搬移前的位置
        target: 搬移後的位置
    """
    index: int
    source: str
    target: str


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
        return _group_duplicates(plan, lambda e: e.new_path.lower(), lambda e: e.original_path)

    def detect_duplicate_sources(self, plan: List[RenameEntry]) -> Dict[str, List[str]]:
        """偵測同一來源檔案被多個項目引用的情況

        同一個檔案被兩個群組同時引用時，第一次搬移後第二次必定失敗，
        且無法用自動加後綴解決，需由使用者修正群組內容。

        Args:
            plan: 重新命名計畫

        Returns:
            被重複引用的原始路徑到新路徑清單的對應
        """
        return _group_duplicates(plan, lambda e: os.path.normcase(e.original_path), lambda e: e.new_path)

    def find_missing_sources(self, plan: List[RenameEntry]) -> List[str]:
        """列出計畫中來源檔案已不存在的原始路徑"""
        return [e.original_path for e in plan if not self.file_service.file_exists(e.original_path)]

    def find_empty_names(self, plan: List[RenameEntry]) -> List[str]:
        """列出新檔名去掉副檔名後為空的項目原始路徑

        副檔名取最後一個點之後的部分，因此「.pdf」這種只剩副檔名的名字視為空。
        """
        return [e.original_path for e in plan if not _name_stem(e.new_path)]

    def find_occupied_targets(self, plan: List[RenameEntry]) -> List[str]:
        """列出被計畫外檔案佔用的目標路徑

        「佔用」指磁碟上已存在、且不是本次計畫任何一筆的來源；
        計畫內來源（對調、連鎖、原地不動）會在搬移時讓出位置，不算佔用。

        Args:
            plan: 重新命名計畫

        Returns:
            被佔用的新路徑清單（依計畫順序）
        """
        sources = {os.path.normcase(e.original_path) for e in plan}
        return [
            e.new_path for e in plan
            if os.path.normcase(e.new_path) not in sources
            and self.file_service.file_exists(e.new_path)
        ]

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

        以兩階段搬移處理對調與連鎖：來源同時是其他項目目標的檔案，
        第一階段先改成暫名讓出位置，第二階段所有檔案一併就位。

        Args:
            plan: 重新命名計畫
            project: 專案資料（用於判斷子資料夾設定）

        Returns:
            復原紀錄（只記原始位置到最終位置，暫名不出現）
        """
        self._validate_plan(plan)
        record = UndoRecord(
            timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
            description=t("rename.undo_description", count=len(plan)),
        )
        created_dirs = set()
        done: List[_Move] = []
        try:
            for move in self._build_moves(plan):
                target_dir = os.path.dirname(move.target)
                if target_dir and not os.path.isdir(target_dir):
                    self.file_service.create_directory(target_dir)
                    created_dirs.add(target_dir)
                self.file_service.rename_file(move.source, move.target)
                done.append(move)
        except Exception as e:
            residual = self._rollback(plan, done, created_dirs)
            if residual:
                raise RenameRollbackError(e, residual) from e
            raise
        record.mappings = [
            UndoMapping(original=entry.original_path, renamed=entry.new_path)
            for entry in plan
        ]
        record.created_directories = sorted(created_dirs)
        return record

    @staticmethod
    def _staged_indices(plan: List[RenameEntry]) -> Set[int]:
        """找出來源同時是其他項目目標、需要先讓出位置的項目索引"""
        targets = {
            os.path.normcase(e.new_path) for e in plan
            if os.path.normcase(e.new_path) != os.path.normcase(e.original_path)
        }
        return {
            i for i, e in enumerate(plan)
            if os.path.normcase(e.original_path) in targets
        }

    def _build_moves(self, plan: List[RenameEntry]) -> List[_Move]:
        """展開兩階段搬移順序：先把需讓位的項目搬到暫名，再全部就位"""
        staged = self._staged_indices(plan)
        moves = [
            _Move(i, plan[i].original_path, _staging_path(plan[i].original_path))
            for i in sorted(staged)
        ]
        for i, entry in enumerate(plan):
            source = _staging_path(entry.original_path) if i in staged else entry.original_path
            moves.append(_Move(i, source, entry.new_path))
        return moves

    def _validate_plan(self, plan: List[RenameEntry]) -> None:
        """執行前檢查計畫是否可安全執行

        檢查來源檔案存在、來源未被重複引用、新檔名不為空、目標路徑未被
        計畫外的檔案佔用、讓位用的暫名未被佔用。任一項不符即拋出例外，不會搬動任何檔案。

        Args:
            plan: 重新命名計畫

        Raises:
            FileNotFoundError: 來源檔案不存在
            ValueError: 產生的新檔名為空
            FileExistsError: 目標或暫名已有檔案，或同一來源被多個項目引用
        """
        missing = self.find_missing_sources(plan)
        if missing:
            raise FileNotFoundError(t("rename.error.source_missing", files="\n".join(missing)))
        duplicates = self.detect_duplicate_sources(plan)
        if duplicates:
            raise FileExistsError(t("rename.error.duplicate_source", files="\n".join(duplicates)))
        conflicts = self.detect_conflicts(plan)
        if conflicts:
            raise FileExistsError(t("rename.error.duplicate_target", files="\n".join(conflicts)))
        empty = self.find_empty_names(plan)
        if empty:
            raise ValueError(t("rename.error.empty_name", files="\n".join(empty)))
        occupied = self.find_occupied_targets(plan)
        if occupied:
            raise FileExistsError(t("rename.error.target_exists", files="\n".join(occupied)))
        staging_taken = self._find_occupied_staging(plan)
        if staging_taken:
            raise FileExistsError(t("rename.error.staging_exists", files="\n".join(staging_taken)))

    def _find_occupied_staging(self, plan: List[RenameEntry]) -> List[str]:
        """列出讓位用暫名已存在於磁碟、或與計畫內其他路徑相撞的項目"""
        reserved = {os.path.normcase(e.new_path) for e in plan}
        reserved |= {os.path.normcase(e.original_path) for e in plan}
        taken = []
        for i in sorted(self._staged_indices(plan)):
            staging = _staging_path(plan[i].original_path)
            if (os.path.normcase(staging) in reserved
                    or self.file_service.file_exists(staging)
                    or self.file_service.directory_exists(staging)):
                taken.append(staging)
        return taken

    def _rollback(
        self, plan: List[RenameEntry], done: List[_Move], created_dirs: set,
    ) -> List[UndoMapping]:
        """依搬移的反序把檔案搬回原位，並移除本次新建且仍為空的目錄

        某個項目一旦搬不回去，該項目更早的搬移也不再逆轉（檔案已不在那裡）。

        Args:
            plan: 重新命名計畫
            done: 已完成的搬移（依執行順序）
            created_dirs: 本次執行新建的目錄

        Returns:
            搬不回去的項目：原始位置到目前停留位置（可能是暫名）的對應，依計畫順序
        """
        stuck: Dict[int, str] = {}
        for move in reversed(done):
            if move.index in stuck:
                continue
            try:
                self.file_service.rename_file(move.target, move.source)
            except OSError:
                stuck[move.index] = move.target
        for directory in sorted(created_dirs, key=len, reverse=True):
            self.file_service.remove_empty_directory(directory)
        return [
            UndoMapping(original=plan[i].original_path, renamed=location)
            for i, location in sorted(stuck.items())
        ]
