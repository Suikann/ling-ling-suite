# -*- coding: utf-8 -*-
"""
兩階段批次搬移引擎（搬移歷程的內部）

以兩階段搬移執行一批「來源 → 目標」，讓對調（A→B、B→A）與連鎖（A→B、B→C）
可以執行；中途失敗依反序回滾，搬不回去的檔案以 RenameRollbackError 回報。
搬第一個檔案前寫入進行中紀錄、每完成一步更新，程式被中途關掉也能據以還原。

只有 services.move_history.MoveHistory 執行搬移（重新命名、復原、重做、中斷還原）；
其他模組只用這裡的檢查規則判斷一批搬移能否執行。

使用範例：
    mover = MoveService(file_service, journal_path)
    created_dirs = mover.execute([(src, dst), ...], operation="rename", record_id=record.id)
"""
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Set, Tuple

from core.constants import MOVE_JOURNAL_FILE, RENAME_STAGING_SUFFIX
from core.locale import t
from core.models import UndoMapping
from core.paths import path_key, same_path
from services.file_service import FileService


Move = Tuple[str, str]


@dataclass
class MoveStep:
    """兩階段搬移中的一次實際檔案搬移

    Attributes:
        index: 所屬搬移項目在批次中的索引
        source: 搬移前的位置
        target: 搬移後的位置
    """
    index: int
    source: str
    target: str


@dataclass
class MoveJournal:
    """批次搬移的進行中紀錄

    steps 是目前仍生效的搬移（依執行順序），每個檔案目前的位置就是它最後一步的 target；
    pending 是正向搬移時「即將執行、可能已做也可能沒做」的那一步，由 load_pending 對照磁碟判定；
    complete 表示整批已搬完、只剩正式紀錄尚未確認寫入。

    Attributes:
        steps: 仍生效的搬移步驟（依執行順序）
        pending: 正向搬移中尚未確認完成的下一步
        complete: 整批是否已搬完
        created_directories: 本次執行新建的目錄
        operation: 執行這批搬移的操作種類；舊版紀錄沒有，為空字串
        record_id: 這批搬移所屬的復原紀錄 id；舊版紀錄沒有，為空字串
    """
    steps: List[MoveStep] = field(default_factory=list)
    pending: Optional[MoveStep] = None
    complete: bool = False
    created_directories: List[str] = field(default_factory=list)
    operation: str = ""
    record_id: str = ""

    def moved_indices(self) -> List[int]:
        """已被搬動過的項目索引（依首次搬動順序）"""
        seen = []
        for step in self.steps:
            if step.index not in seen:
                seen.append(step.index)
        return seen

    def origins(self) -> Dict[int, str]:
        """已被搬動過的項目索引到原始位置（第一步的 source）的對應"""
        origins: Dict[int, str] = {}
        for step in self.steps:
            origins.setdefault(step.index, step.source)
        return origins

    def locations(self) -> Dict[int, str]:
        """已被搬動過的項目索引到目前位置（最後一步的 target）的對應"""
        return {step.index: step.target for step in self.steps}


@dataclass
class MoveRecoveryResult:
    """中斷批次還原的結果；每一項都是原始位置到紀錄位置（或目前位置）的對應

    Attributes:
        restored: 已從紀錄位置搬回原位的檔案
        skipped: 已不在紀錄位置而略過的檔案
        residual: 搬不回去的檔案：原始位置到目前位置
    """
    restored: List[UndoMapping] = field(default_factory=list)
    skipped: List[UndoMapping] = field(default_factory=list)
    residual: List[UndoMapping] = field(default_factory=list)


class _JournalFile:
    """進行中紀錄檔的讀寫：執行前寫入、每完成一步原子覆寫、結束後刪除"""

    def __init__(self, file_service: FileService, path: str):
        self.file_service = file_service
        self.path = path

    def save(self, journal: MoveJournal) -> None:
        """原子寫入紀錄（覆蓋既有）"""
        self.file_service.write_json_atomic(self.path, {
            "steps": [self._step_to_json(s) for s in journal.steps],
            "pending": self._step_to_json(journal.pending) if journal.pending else None,
            "complete": journal.complete,
            "created_directories": journal.created_directories,
            "operation": journal.operation,
            "record_id": journal.record_id,
        })

    def exists(self) -> bool:
        """是否有進行中紀錄（不論內容能否讀取）"""
        return os.path.isfile(self.path)

    def load(self) -> Optional[MoveJournal]:
        """讀取紀錄；不存在時回傳 None

        Raises:
            ValueError: 紀錄內容不是合法的 JSON 或缺少必要欄位
        """
        if not os.path.isfile(self.path):
            return None
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        try:
            return MoveJournal(
                steps=[self._step_from_json(s) for s in data["steps"]],
                pending=self._step_from_json(data["pending"]) if data.get("pending") else None,
                complete=bool(data.get("complete", False)),
                created_directories=data.get("created_directories", []),
                operation=data.get("operation") or "",
                record_id=data.get("record_id") or "",
            )
        except (KeyError, TypeError) as e:
            raise ValueError(str(e)) from e

    def clear(self) -> None:
        """刪除紀錄；不存在時不拋出"""
        try:
            os.remove(self.path)
        except FileNotFoundError:
            pass

    @staticmethod
    def _step_to_json(step: MoveStep) -> dict:
        return {"index": step.index, "source": step.source, "target": step.target}

    @staticmethod
    def _step_from_json(data: dict) -> MoveStep:
        return MoveStep(index=data["index"], source=data["source"], target=data["target"])


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
    items: List[Move], path_of: Callable[[Move], str], value: Callable[[Move], str],
) -> Dict[str, List[str]]:
    """依路徑同一性分組，回傳出現多次的路徑（首次出現的寫法）及其對應值清單"""
    spelled: Dict[str, str] = {}
    grouped = defaultdict(list)
    for item in items:
        key = path_key(path_of(item))
        spelled.setdefault(key, path_of(item))
        grouped[key].append(value(item))
    return {spelled[k]: v for k, v in grouped.items() if len(v) > 1}


def _name_stem(path: str) -> str:
    """取路徑最後一段去掉副檔名（最後一個點之後）的部分"""
    name = os.path.basename(path)
    return name.rpartition(".")[0] if "." in name else name


def staging_path(path: str) -> str:
    """來源檔案在第一階段讓出位置時使用的暫名（同資料夾、純 rename）"""
    return path + RENAME_STAGING_SUFFIX


class MoveService:
    """兩階段批次搬移引擎：只有搬移歷程執行搬移；檢查規則另供重新命名預覽判斷"""

    def __init__(self, file_service: FileService, journal_path: str = MOVE_JOURNAL_FILE):
        self.file_service = file_service
        self._journal = _JournalFile(file_service, journal_path)

    def find_empty_names(self, moves: List[Move]) -> List[str]:
        """列出目標檔名去掉副檔名後為空的來源路徑（依批次順序）

        副檔名取最後一個點之後的部分，因此「.pdf」這種只剩副檔名的名字視為空。
        """
        return [src for src, dst in moves if not _name_stem(dst)]

    def find_missing_sources(self, moves: List[Move]) -> List[str]:
        """列出來源檔案已不存在的來源路徑"""
        return [src for src, _ in moves if not self.file_service.file_exists(src)]

    def detect_duplicate_sources(self, moves: List[Move]) -> Dict[str, List[str]]:
        """偵測同一來源被多個項目引用：來源路徑（首次出現的寫法）到目標清單的對應"""
        return _group_duplicates(moves, lambda m: m[0], lambda m: m[1])

    def detect_duplicate_targets(self, moves: List[Move]) -> Dict[str, List[str]]:
        """偵測多個項目要用同一目標：目標路徑（首次出現的寫法）到來源清單的對應"""
        return _group_duplicates(moves, lambda m: m[1], lambda m: m[0])

    def find_occupied_targets(self, moves: List[Move]) -> List[str]:
        """列出被批次外檔案佔用的目標路徑

        「佔用」指磁碟上已存在、且不是本批次任何一筆的來源；
        批次內來源（對調、連鎖、原地不動）會在搬移時讓出位置，不算佔用。

        Args:
            moves: 搬移項目清單

        Returns:
            被佔用的目標路徑清單（依批次順序）
        """
        sources = {path_key(src) for src, _ in moves}
        return [
            dst for _, dst in moves
            if path_key(dst) not in sources and self.file_service.file_exists(dst)
        ]

    def find_taken_staging_names(self, moves: List[Move]) -> List[str]:
        """列出無法使用的讓位用暫名

        暫名已存在於磁碟（檔案或目錄），或與批次內任一來源、目標相同，都算被佔用。

        Args:
            moves: 搬移項目清單

        Returns:
            被佔用的暫名清單（依批次順序）
        """
        reserved = {path_key(p) for move in moves for p in move}
        taken = []
        for i in sorted(self._staged_indices(moves)):
            staging = staging_path(moves[i][0])
            if (path_key(staging) in reserved
                    or self.file_service.file_exists(staging)
                    or self.file_service.directory_exists(staging)):
                taken.append(staging)
        return taken

    def validate(self, moves: List[Move]) -> None:
        """執行前檢查批次是否可安全執行

        檢查目標檔名不為空、來源存在、來源未被重複引用、目標未重複、目標未被批次外檔案佔用、
        讓位用的暫名未被佔用。任一項不符即拋出例外，不會搬動任何檔案。

        Args:
            moves: 搬移項目清單

        Raises:
            ValueError: 目標檔名為空
            FileNotFoundError: 來源檔案不存在
            FileExistsError: 目標或暫名已有檔案，或同一來源被多個項目引用
        """
        empty = self.find_empty_names(moves)
        if empty:
            raise ValueError(t("rename.error.empty_name", files="\n".join(empty)))
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

    def execute(
        self, moves: List[Move], on_complete: Optional[Callable[[List[str]], None]] = None,
        operation: str = "", record_id: str = "",
    ) -> List[str]:
        """驗證後以兩階段搬移執行整批

        來源同時是其他項目目標的檔案，第一階段先改成暫名讓出位置，
        第二階段所有檔案一併就位；目標的父目錄不存在時建立。
        搬第一個檔案前先寫入進行中紀錄，每完成一步更新（下一步先記為 pending）；
        整批搬完先標記 complete、呼叫 on_complete 寫正式紀錄，寫完才刪除進行中紀錄，
        所以任何時點被關掉都留得下紀錄。回滾結束也刪除。上次的紀錄尚未處理時拒絕執行。

        Args:
            moves: 搬移項目清單
            on_complete: 整批搬完後、刪除進行中紀錄前要做的事（通常是寫正式復原紀錄），
                參數為本次新建的目錄；它拋出的例外原樣傳出、不回滾，進行中紀錄保留
            operation: 記進進行中紀錄的操作種類
            record_id: 記進進行中紀錄的所屬復原紀錄 id

        Returns:
            本次新建的目錄（排序後）

        Raises:
            PendingMoveError: 上次的進行中紀錄尚未處理
            RenameRollbackError: 中途失敗且回滾時有檔案搬不回原位
            OSError: 中途失敗且已全部回滾
        """
        if self._journal.exists():
            raise PendingMoveError()
        self.validate(moves)
        steps = self._build_steps(moves)
        journal = MoveJournal(
            pending=steps[0] if steps else None, complete=not steps,
            operation=operation, record_id=record_id,
        )
        try:
            self._journal.save(journal)
            for k, step in enumerate(steps):
                target_dir = os.path.dirname(step.target)
                if target_dir and not self.file_service.directory_exists(target_dir):
                    self.file_service.create_directory(target_dir)
                    journal.created_directories.append(target_dir)
                    self._journal.save(journal)
                self.file_service.rename_file(step.source, step.target)
                journal.steps.append(step)
                journal.pending = steps[k + 1] if k + 1 < len(steps) else None
                journal.complete = journal.pending is None
                self._journal.save(journal)
        except Exception as e:
            journal.pending = None
            residual = self._rollback(moves, journal)
            if residual:
                raise RenameRollbackError(e, residual) from e
            raise
        created_dirs = sorted(journal.created_directories)
        if on_complete:
            on_complete(created_dirs)
        self._journal.clear()
        return created_dirs

    def load_pending(self) -> Optional[MoveJournal]:
        """讀取上次中途中斷的批次；沒有時回傳 None

        紀錄在每步搬移完成後才更新，搬完、來不及記就當機的那一步只記為 pending；
        這裡對照磁碟判定：來源已不在、目標已出現即視為已完成，補進生效清單。
        比對用檔名大小寫完全相同的檢查，只改大小寫的那一步在不分大小寫的檔案系統上才判得出。

        Raises:
            ValueError: 紀錄內容損毀
        """
        journal = self._journal.load()
        if journal and journal.pending:
            step = journal.pending
            if (self.file_service.file_exists_exact(step.target)
                    and not self.file_service.file_exists_exact(step.source)):
                journal.steps.append(step)
            journal.pending = None
        return journal

    def discard_pending(self) -> None:
        """捨棄進行中紀錄（無法讀取，或已搬完的批次決定保留結果、不再需要它）"""
        self._journal.clear()

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
                result.restored.append(UndoMapping(origins[i], locations[i]))
            else:
                result.skipped.append(UndoMapping(origins[i], locations[i]))
        return result

    @staticmethod
    def _staged_indices(moves: List[Move]) -> Set[int]:
        """找出來源同時是其他項目目標、需要先讓出位置的項目索引"""
        targets = {path_key(dst) for src, dst in moves if not same_path(dst, src)}
        return {i for i, (src, _) in enumerate(moves) if path_key(src) in targets}

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
        self._journal.save(journal)
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
            self._journal.save(journal)
        for directory in sorted(journal.created_directories, key=len, reverse=True):
            self.file_service.remove_empty_directory(directory)
        self._journal.clear()
        return stuck
