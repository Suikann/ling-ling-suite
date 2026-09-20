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
from typing import Callable, Dict, List, Optional, Set, Tuple

from core.constants import RENAME_STAGING_SUFFIX
from core.locale import t
from core.models import MoveJournal, MoveRecoveryResult, MoveStep, UndoMapping
from services.file_service import FileService
from services.move_journal import MoveJournalStore


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


class PendingMoveError(OSError):
    """上次中斷的批次尚未還原，拒絕執行新的批次（否則會蓋掉它唯一的紀錄）"""

    def __init__(self):
        super().__init__(t("rename.error.pending_move"))


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

    def __init__(self, file_service: FileService, journal_store: Optional[MoveJournalStore] = None):
        self.file_service = file_service
        self._journal_store = journal_store or MoveJournalStore(file_service)

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
        搬第一個檔案前先寫入進行中紀錄，每完成一步更新（下一步先記為 pending），
        成功或回滾結束即刪除；上次中斷的批次尚未還原時拒絕執行。

        Args:
            moves: 搬移項目清單

        Returns:
            本次新建的目錄（排序後）

        Raises:
            PendingMoveError: 上次中斷的批次尚未還原
            RenameRollbackError: 中途失敗且回滾時有檔案搬不回原位
            OSError: 中途失敗且已全部回滾
        """
        if self._journal_store.exists():
            raise PendingMoveError()
        self.validate(moves)
        steps = self._build_steps(moves)
        journal = MoveJournal(pending=steps[0] if steps else None)
        try:
            self._journal_store.save(journal)
            for k, step in enumerate(steps):
                target_dir = os.path.dirname(step.target)
                if target_dir and not self.file_service.directory_exists(target_dir):
                    self.file_service.create_directory(target_dir)
                    journal.created_directories.append(target_dir)
                    self._journal_store.save(journal)
                self.file_service.rename_file(step.source, step.target)
                journal.steps.append(step)
                journal.pending = steps[k + 1] if k + 1 < len(steps) else None
                self._journal_store.save(journal)
        except Exception as e:
            journal.pending = None
            residual = self._rollback(moves, journal)
            if residual:
                raise RenameRollbackError(e, residual) from e
            raise
        self._journal_store.clear()
        return sorted(journal.created_directories)

    def load_pending(self) -> Optional[MoveJournal]:
        """讀取上次中途中斷的批次；沒有時回傳 None

        紀錄在每步搬移完成後才更新，搬完、來不及記就當機的那一步只記為 pending；
        這裡對照磁碟判定：來源已不在、目標已出現即視為已完成，補進生效清單。

        Raises:
            ValueError: 紀錄內容損毀
        """
        journal = self._journal_store.load()
        if journal and journal.pending:
            step = journal.pending
            if (self.file_service.file_exists(step.target)
                    and not self.file_service.file_exists(step.source)):
                journal.steps.append(step)
            journal.pending = None
        return journal

    def discard_pending(self) -> None:
        """捨棄進行中紀錄（無法讀取時使用）"""
        self._journal_store.clear()

    def recover(self, journal: MoveJournal) -> MoveRecoveryResult:
        """把中途中斷的批次已搬動的檔案依反序搬回原位，並清除進行中紀錄

        走訪與回滾共用，每逆轉一步就更新紀錄，還原途中再被中斷也能接續。
        檔案在紀錄位置就搬回；已不在紀錄位置、也不在原位的略過；
        搬回途中失敗的留在目前位置回報，由呼叫端寫入復原紀錄。

        Args:
            journal: load_pending() 讀回的進行中紀錄

        Returns:
            還原結果
        """
        moved = journal.moved_indices()
        origins = journal.origins()
        locations = journal.locations()
        stuck = self._reverse_all(journal)
        result = MoveRecoveryResult(
            residual=[UndoMapping(origins[i], location) for i, location in sorted(stuck.items())],
        )
        for i in moved:
            if i in stuck:
                continue
            if self.file_service.file_exists(origins[i]):
                result.restored.append(origins[i])
            else:
                result.skipped.append(UndoMapping(origins[i], locations[i]))
        return result

    @staticmethod
    def _staged_indices(moves: List[Move]) -> Set[int]:
        """找出來源同時是其他項目目標、需要先讓出位置的項目索引"""
        targets = {
            os.path.normcase(dst) for src, dst in moves
            if os.path.normcase(dst) != os.path.normcase(src)
        }
        return {i for i, (src, _) in enumerate(moves) if os.path.normcase(src) in targets}

    def _build_steps(self, moves: List[Move]) -> List[MoveStep]:
        """展開兩階段搬移順序：先把需讓位的項目搬到暫名，再全部就位"""
        staged = self._staged_indices(moves)
        steps = [MoveStep(i, moves[i][0], staging_path(moves[i][0])) for i in sorted(staged)]
        for i, (src, dst) in enumerate(moves):
            steps.append(MoveStep(i, staging_path(src) if i in staged else src, dst))
        return steps

    def _rollback(self, moves: List[Move], journal: MoveJournal) -> List[UndoMapping]:
        """依搬移的反序把檔案搬回原位

        Args:
            moves: 搬移項目清單
            journal: 本次執行的進行中紀錄

        Returns:
            搬不回去的項目：來源到目前停留位置（可能是暫名）的對應，依批次順序
        """
        stuck = self._reverse_all(journal)
        return [
            UndoMapping(original=moves[i][0], renamed=location)
            for i, location in sorted(stuck.items())
        ]

    def _reverse_all(self, journal: MoveJournal) -> Dict[int, str]:
        """依反序逆轉紀錄中仍生效的搬移，結束後移除新建且仍為空的目錄並刪除紀錄

        每逆轉一步就從紀錄移除並存檔，紀錄隨時反映實際位置。
        檔案已不在該步目標的步驟略過（檔案不見了，或上次逆轉完來不及記）；
        某個項目一旦搬不回去，該項目更早的搬移也不再逆轉（檔案已不在那裡）。

        Args:
            journal: 進行中紀錄（pending 已清空）

        Returns:
            搬不回去的項目索引到目前停留位置的對應
        """
        self._journal_store.save(journal)
        stuck: Dict[int, str] = {}
        for k in range(len(journal.steps) - 1, -1, -1):
            step = journal.steps[k]
            if step.index in stuck or not self.file_service.file_exists(step.target):
                continue
            try:
                self.file_service.rename_file(step.target, step.source)
            except OSError:
                stuck[step.index] = step.target
                continue
            del journal.steps[k]
            self._journal_store.save(journal)
        for directory in sorted(journal.created_directories, key=len, reverse=True):
            self.file_service.remove_empty_directory(directory)
        self._journal_store.clear()
        return stuck
