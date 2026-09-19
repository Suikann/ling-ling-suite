# -*- coding: utf-8 -*-
"""
批次搬移服務

以兩階段搬移執行一批「來源 → 目標」，讓對調（A→B、B→A）與連鎖（A→B、B→C）
可以執行；中途失敗依反序回滾，搬不回去的檔案以 RenameRollbackError 回報。
重新命名、復原、重做都走這一個引擎。

使用範例：
    mover = MoveService(file_service)
    created_dirs = mover.execute([(src, dst), ...])
"""
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, List, Set, Tuple

from core.constants import RENAME_STAGING_SUFFIX
from core.locale import t
from core.models import UndoMapping
from services.file_service import FileService


Move = Tuple[str, str]


class RenameRollbackError(OSError):
    """搬移中途失敗且部分檔案無法搬回原位

    Attributes:
        cause: 觸發回滾的原始例外
        residual: 仍留在原位以外、需要記錄以便日後復原的對應（來源 → 目前位置）
    """

    def __init__(self, cause: Exception, residual: List[UndoMapping]):
        super().__init__(t("rename.error.rollback_failed",
                           error=cause, files="\n".join(m.renamed for m in residual)))
        self.cause = cause
        self.residual = residual


@dataclass
class _Step:
    """兩階段搬移中的一次實際檔案搬移

    Attributes:
        index: 所屬搬移項目在批次中的索引
        source: 搬移前的位置
        target: 搬移後的位置
    """
    index: int
    source: str
    target: str


def _group_duplicates(
    items: List[Move], key: Callable[[Move], str], value: Callable[[Move], str],
) -> Dict[str, List[str]]:
    """依 key 分組，回傳出現多次的鍵及其對應值清單"""
    grouped = defaultdict(list)
    for item in items:
        grouped[key(item)].append(value(item))
    return {k: v for k, v in grouped.items() if len(v) > 1}


def staging_path(path: str) -> str:
    """來源檔案在第一階段讓出位置時使用的暫名（同資料夾、純 rename）"""
    return path + RENAME_STAGING_SUFFIX


class MoveService:
    """兩階段批次搬移服務"""

    def __init__(self, file_service: FileService):
        self.file_service = file_service

    def find_missing_sources(self, moves: List[Move]) -> List[str]:
        """列出來源檔案已不存在的來源路徑"""
        return [src for src, _ in moves if not self.file_service.file_exists(src)]

    def detect_duplicate_sources(self, moves: List[Move]) -> Dict[str, List[str]]:
        """偵測同一來源被多個項目引用：來源路徑（normcase）到目標清單的對應"""
        return _group_duplicates(moves, lambda m: os.path.normcase(m[0]), lambda m: m[1])

    def detect_duplicate_targets(self, moves: List[Move]) -> Dict[str, List[str]]:
        """偵測多個項目要用同一目標（大小寫不敏感）：目標路徑（小寫）到來源清單的對應"""
        return _group_duplicates(moves, lambda m: m[1].lower(), lambda m: m[0])

    def find_occupied_targets(self, moves: List[Move]) -> List[str]:
        """列出被批次外檔案佔用的目標路徑

        「佔用」指磁碟上已存在、且不是本批次任何一筆的來源；
        批次內來源（對調、連鎖、原地不動）會在搬移時讓出位置，不算佔用。

        Args:
            moves: 搬移項目清單

        Returns:
            被佔用的目標路徑清單（依批次順序）
        """
        sources = {os.path.normcase(src) for src, _ in moves}
        return [
            dst for _, dst in moves
            if os.path.normcase(dst) not in sources and self.file_service.file_exists(dst)
        ]

    def find_taken_staging_names(self, moves: List[Move]) -> List[str]:
        """列出無法使用的讓位用暫名

        暫名已存在於磁碟（檔案或目錄），或與批次內任一來源、目標相同，都算被佔用。

        Args:
            moves: 搬移項目清單

        Returns:
            被佔用的暫名清單（依批次順序）
        """
        reserved = {os.path.normcase(p) for move in moves for p in move}
        taken = []
        for i in sorted(self._staged_indices(moves)):
            staging = staging_path(moves[i][0])
            if (os.path.normcase(staging) in reserved
                    or self.file_service.file_exists(staging)
                    or self.file_service.directory_exists(staging)):
                taken.append(staging)
        return taken

    def validate(self, moves: List[Move]) -> None:
        """執行前檢查批次是否可安全執行

        檢查來源存在、來源未被重複引用、目標未重複、目標未被批次外檔案佔用、
        讓位用的暫名未被佔用。任一項不符即拋出例外，不會搬動任何檔案。

        Args:
            moves: 搬移項目清單

        Raises:
            FileNotFoundError: 來源檔案不存在
            FileExistsError: 目標或暫名已有檔案，或同一來源被多個項目引用
        """
        missing = self.find_missing_sources(moves)
        if missing:
            raise FileNotFoundError(t("rename.error.source_missing", files="\n".join(missing)))
        duplicates = self.detect_duplicate_sources(moves)
        if duplicates:
            raise FileExistsError(t("rename.error.duplicate_source", files="\n".join(duplicates)))
        conflicts = self.detect_duplicate_targets(moves)
        if conflicts:
            raise FileExistsError(t("rename.error.duplicate_target", files="\n".join(conflicts)))
        occupied = self.find_occupied_targets(moves)
        if occupied:
            raise FileExistsError(t("rename.error.target_exists", files="\n".join(occupied)))
        staging_taken = self.find_taken_staging_names(moves)
        if staging_taken:
            raise FileExistsError(t("rename.error.staging_exists", files="\n".join(staging_taken)))

    def execute(self, moves: List[Move]) -> List[str]:
        """驗證後以兩階段搬移執行整批

        來源同時是其他項目目標的檔案，第一階段先改成暫名讓出位置，
        第二階段所有檔案一併就位；目標的父目錄不存在時建立。

        Args:
            moves: 搬移項目清單

        Returns:
            本次新建的目錄（排序後）

        Raises:
            RenameRollbackError: 中途失敗且回滾時有檔案搬不回原位
            OSError: 中途失敗且已全部回滾
        """
        self.validate(moves)
        created_dirs: Set[str] = set()
        done: List[_Step] = []
        try:
            for step in self._build_steps(moves):
                target_dir = os.path.dirname(step.target)
                if target_dir and not self.file_service.directory_exists(target_dir):
                    self.file_service.create_directory(target_dir)
                    created_dirs.add(target_dir)
                self.file_service.rename_file(step.source, step.target)
                done.append(step)
        except Exception as e:
            residual = self._rollback(moves, done, created_dirs)
            if residual:
                raise RenameRollbackError(e, residual) from e
            raise
        return sorted(created_dirs)

    @staticmethod
    def _staged_indices(moves: List[Move]) -> Set[int]:
        """找出來源同時是其他項目目標、需要先讓出位置的項目索引"""
        targets = {
            os.path.normcase(dst) for src, dst in moves
            if os.path.normcase(dst) != os.path.normcase(src)
        }
        return {i for i, (src, _) in enumerate(moves) if os.path.normcase(src) in targets}

    def _build_steps(self, moves: List[Move]) -> List[_Step]:
        """展開兩階段搬移順序：先把需讓位的項目搬到暫名，再全部就位"""
        staged = self._staged_indices(moves)
        steps = [_Step(i, moves[i][0], staging_path(moves[i][0])) for i in sorted(staged)]
        for i, (src, dst) in enumerate(moves):
            steps.append(_Step(i, staging_path(src) if i in staged else src, dst))
        return steps

    def _rollback(
        self, moves: List[Move], done: List[_Step], created_dirs: Set[str],
    ) -> List[UndoMapping]:
        """依搬移的反序把檔案搬回原位，並移除本次新建且仍為空的目錄

        某個項目一旦搬不回去，該項目更早的搬移也不再逆轉（檔案已不在那裡）。

        Args:
            moves: 搬移項目清單
            done: 已完成的搬移（依執行順序）
            created_dirs: 本次執行新建的目錄

        Returns:
            搬不回去的項目：來源到目前停留位置（可能是暫名）的對應，依批次順序
        """
        stuck: Dict[int, str] = {}
        for step in reversed(done):
            if step.index in stuck:
                continue
            try:
                self.file_service.rename_file(step.target, step.source)
            except OSError:
                stuck[step.index] = step.target
        for directory in sorted(created_dirs, key=len, reverse=True):
            self.file_service.remove_empty_directory(directory)
        return [
            UndoMapping(original=moves[i][0], renamed=location)
            for i, location in sorted(stuck.items())
        ]
