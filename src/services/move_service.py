# -*- coding: utf-8 -*-
"""
兩階段批次搬移引擎（搬移歷程的內部）

以兩階段搬移執行一批「來源 → 目標」，讓對調（A→B、B→A）與連鎖（A→B、B→C）
可以執行；中途失敗依反序回滾，搬不回去的檔案以 RenameRollbackError 回報。
搬第一個檔案前寫入進行中紀錄、每完成一步更新，程式被中途關掉也能據以還原。

只有 services.move_history.MoveHistory 使用這裡：執行搬移（重新命名、復原、重做、中斷還原），
以及重新命名預檢以執行前驗證的同一套規則（find_problems）判定計畫。

使用範例：
    mover = MoveService(file_service, journal_path)
    created_dirs = mover.execute([(src, dst), ...], context=BatchContext(OperationKind.RENAME, record.id))
"""
import json
import os
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from core.constants import (
    MOVE_JOURNAL_FILE, RENAME_PROBLEM_RULES, RENAME_STAGING_SUFFIX, OperationKind, ProblemPath, RenameProblem,
)
from core.locale import t
from core.models import UndoMapping
from core.paths import is_inside, path_key, same_path
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
class BatchContext:
    """這批搬移的來歷：記進進行中紀錄，中斷還原時交回搬移歷程據以處理（引擎只負責保存）

    Attributes:
        operation: 執行這批搬移的操作種類；舊版紀錄沒有記（或記了不認得的值）時為重新命名
        record_id: 所屬復原紀錄的 id；舊版紀錄沒有，為空字串
        record: 所屬復原紀錄在這批搬移開始時的內容（引擎不解讀）；舊版紀錄沒有，為 None
        project_path: 這批搬移所屬的專案檔；專案當時尚未存檔為空字串，沒有記（舊版紀錄）為 None
    """
    operation: OperationKind = OperationKind.RENAME
    record_id: str = ""
    record: Optional[Dict[str, Any]] = None
    project_path: Optional[str] = None


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
        context: 這批搬移的來歷
    """
    steps: List[MoveStep] = field(default_factory=list)
    pending: Optional[MoveStep] = None
    complete: bool = False
    created_directories: List[str] = field(default_factory=list)
    context: BatchContext = field(default_factory=BatchContext)

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
            "operation": journal.context.operation.value,
            "record_id": journal.context.record_id,
            "record": journal.context.record,
            "project_path": journal.context.project_path,
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
                context=BatchContext(
                    operation=self._operation(data.get("operation")),
                    record_id=data.get("record_id") or "",
                    record=data.get("record"),
                    project_path=data.get("project_path"),
                ),
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
    def _operation(value: Any) -> OperationKind:
        """紀錄裡的操作種類；舊版紀錄沒有記（或記了不認得的值）時視為重新命名"""
        try:
            return OperationKind(value)
        except ValueError:
            return OperationKind.RENAME

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


def _repeated(moves: List[Move], side: int) -> Set[int]:
    """路徑（side 為 0 看來源、1 看目標）與其他項目相同的項目索引"""
    counts = Counter(path_key(move[side]) for move in moves)
    return {i for i, move in enumerate(moves) if counts[path_key(move[side])] > 1}


def _name_stem(path: str) -> str:
    """取路徑最後一段去掉副檔名（最後一個點之後）的部分"""
    name = os.path.basename(path)
    return name.rpartition(".")[0] if "." in name else name


def staging_path(path: str) -> str:
    """來源檔案在第一階段讓出位置時使用的暫名（同資料夾、純 rename）"""
    return path + RENAME_STAGING_SUFFIX


# 拒絕訊息列出的路徑：該項的來源、目標或暫名
_LISTED_PATH: Dict[ProblemPath, Callable[[Move], str]] = {
    ProblemPath.SOURCE: lambda m: m[0],
    ProblemPath.TARGET: lambda m: m[1],
    ProblemPath.STAGING: lambda m: staging_path(m[0]),
}


class MoveService:
    """兩階段批次搬移引擎：只有搬移歷程執行搬移；執行前驗證的規則也供重新命名預檢使用"""

    def __init__(self, file_service: FileService, journal_path: str = MOVE_JOURNAL_FILE):
        self.file_service = file_service
        self._journal = _JournalFile(file_service, journal_path)

    def find_missing_sources(self, moves: List[Move]) -> List[str]:
        """列出來源檔案已不存在的來源路徑（依批次順序）"""
        return [src for src, _ in moves if not self.file_service.file_exists(src)]

    def find_problems(
        self, moves: List[Move], bounds: Optional[List[str]] = None,
    ) -> Dict[str, List[RenameProblem]]:
        """執行前驗證的規則：批次中每個來源的問題

        目標落在邊界外、目標檔名去掉副檔名後為空（「.pdf」也算）、來源不存在、
        同一來源被多個項目引用、多個項目要用同一目標、目標被批次外的檔案佔用、讓位用的暫名被佔用。
        「佔用」指磁碟上已存在、且不是本批次任何一筆的來源；批次內來源（對調、連鎖、原地不動）
        會在搬移時讓出位置，不算佔用。暫名已存在於磁碟（檔案或目錄），或與批次內任一來源、目標相同，都算被佔用。

        Args:
            moves: 搬移項目清單
            bounds: 各項目標必須在其中的資料夾（與 moves 一一對應）；None 表示不限制

        Returns:
            來源路徑（該項的寫法）→ 問題種類（依 RenameProblem 的順序）；只列有問題的來源，依批次順序
        """
        missing = set(self.find_missing_sources(moves))
        hits = {
            RenameProblem.OUTSIDE_OUTPUT: {
                i for i, (_, dst) in enumerate(moves) if bounds is not None and not is_inside(dst, bounds[i])
            },
            RenameProblem.EMPTY_NAME: {i for i, (_, dst) in enumerate(moves) if not _name_stem(dst)},
            RenameProblem.MISSING_SOURCE: {i for i, (src, _) in enumerate(moves) if src in missing},
            RenameProblem.DUPLICATE_SOURCE: _repeated(moves, 0),
            RenameProblem.DUPLICATE_TARGET: _repeated(moves, 1),
            RenameProblem.TARGET_OCCUPIED: self._occupied_targets(moves),
            RenameProblem.STAGING_TAKEN: self._taken_staging_names(moves),
        }
        problems: Dict[str, List[RenameProblem]] = {}
        for i, (src, _) in enumerate(moves):
            for kind in RenameProblem:
                if i in hits.get(kind, ()) and kind not in problems.setdefault(src, []):
                    problems[src].append(kind)
        return {src: kinds for src, kinds in problems.items() if kinds}

    def validate(self, moves: List[Move], bounds: Optional[List[str]] = None) -> None:
        """執行前檢查批次是否可安全執行（規則見 find_problems）；任一項不符即拋出例外，不會搬動任何檔案

        依 RENAME_PROBLEM_RULES 的順序，第一種有問題的種類決定拋出的例外與訊息。

        Args:
            moves: 搬移項目清單
            bounds: 各項目標必須在其中的資料夾（與 moves 一一對應）；None 表示不限制

        Raises:
            ValueError: 目標落在邊界外，或目標檔名為空
            FileNotFoundError: 來源檔案不存在
            FileExistsError: 目標或暫名已有檔案，或同一來源、同一目標被多個項目引用
        """
        problems = self.find_problems(moves, bounds)
        for kind, rule in RENAME_PROBLEM_RULES.items():
            if not rule.refusal_key:
                continue
            listed = _LISTED_PATH[rule.refusal_lists]
            paths = [listed(move) for move in moves if kind in problems.get(move[0], ())]
            if paths:
                raise rule.refusal_error(t(rule.refusal_key, files="\n".join(dict.fromkeys(paths))))

    def _occupied_targets(self, moves: List[Move]) -> Set[int]:
        """目標被批次外檔案佔用的項目索引"""
        sources = {path_key(src) for src, _ in moves}
        return {
            i for i, (_, dst) in enumerate(moves)
            if path_key(dst) not in sources and self.file_service.file_exists(dst)
        }

    def _taken_staging_names(self, moves: List[Move]) -> Set[int]:
        """需要讓位、但暫名已被佔用的項目索引"""
        reserved = {path_key(p) for move in moves for p in move}
        taken = set()
        for i in self._staged_indices(moves):
            staging = staging_path(moves[i][0])
            if (path_key(staging) in reserved
                    or self.file_service.file_exists(staging)
                    or self.file_service.directory_exists(staging)):
                taken.add(i)
        return taken

    def execute(
        self, moves: List[Move], on_complete: Optional[Callable[[List[str]], None]] = None,
        on_rollback: Optional[Callable[[List[UndoMapping]], None]] = None,
        context: Optional[BatchContext] = None, bounds: Optional[List[str]] = None,
    ) -> List[str]:
        """驗證後以兩階段搬移執行整批

        來源同時是其他項目目標的檔案，第一階段先改成暫名讓出位置，
        第二階段所有檔案一併就位；目標的父目錄不存在時建立。
        搬第一個檔案前先寫入進行中紀錄，每完成一步更新（下一步先記為 pending）；
        整批搬完先標記 complete、呼叫 on_complete 寫正式紀錄，寫完才刪除進行中紀錄，
        所以任何時點被關掉都留得下紀錄。回滾結束先呼叫 on_rollback，之後也刪除。
        上次的紀錄尚未處理時拒絕執行。

        Args:
            moves: 搬移項目清單
            on_complete: 整批搬完後、刪除進行中紀錄前要做的事（通常是寫正式復原紀錄），
                參數為本次新建的目錄；它拋出的例外原樣傳出、不回滾，進行中紀錄保留
            on_rollback: 回滾結束後、刪除進行中紀錄前要做的事（替搬不回去的檔案寫紀錄、寫回 meta），
                參數為搬不回去的項目（同 RenameRollbackError.residual，可能為空）；
                它拋出的例外原樣傳出，進行中紀錄保留（其中只剩搬不回去的步驟）
            context: 記進進行中紀錄的這批搬移的來歷（中斷後據以整理堆疊、寫回工作區 meta）；省略時視為重新命名
            bounds: 各項目標必須在其中的資料夾（與 moves 一一對應）；None 表示不限制

        Returns:
            本次新建的目錄（排序後）

        Raises:
            PendingMoveError: 上次的進行中紀錄尚未處理
            RenameRollbackError: 中途失敗且回滾時有檔案搬不回原位
            OSError: 中途失敗且已全部回滾
        """
        if self._journal.exists():
            raise PendingMoveError()
        self.validate(moves, bounds)
        steps = self._build_steps(moves)
        journal = MoveJournal(
            pending=steps[0] if steps else None, complete=not steps, context=context or BatchContext(),
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
            if on_rollback:
                on_rollback(residual)
            self._journal.clear()
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

    def recover(
        self, journal: MoveJournal,
        on_restored: Optional[Callable[[MoveRecoveryResult], None]] = None,
    ) -> MoveRecoveryResult:
        """把中途中斷的批次已搬動的檔案依反序搬回原位，並清除進行中紀錄

        走訪與回滾共用，每逆轉一步就更新紀錄，還原途中再被中斷也能接續。
        檔案在紀錄位置就搬回；已不在紀錄位置、也不在原位的略過；
        搬回途中失敗的留在目前位置回報，由呼叫端寫入復原紀錄。

        Args:
            journal: load_pending() 讀回的進行中紀錄
            on_restored: 檔案搬回後、刪除進行中紀錄前要做的事（整理堆疊、寫回 meta），參數為還原結果；
                它拋出的例外原樣傳出，進行中紀錄保留（其中只剩搬不回去的步驟）

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
        if on_restored:
            on_restored(result)
        self._journal.clear()
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
        """依反序逆轉紀錄中仍生效的搬移，結束後移除新建且仍為空的目錄

        每逆轉一步就從紀錄移除並存檔，紀錄隨時反映實際位置；紀錄由呼叫端在寫完其他紀錄後刪除。
        開始逆轉就不再算整批搬完（complete），中途被打斷時不會被當成可以保留的結果。
        檔案已不在該步目標的步驟略過（檔案不見了，或上次逆轉完來不及記）；
        某個項目一旦搬不回去，該項目更早的搬移也不再逆轉（檔案已不在那裡）。

        Args:
            journal: 進行中紀錄（pending 已清空）

        Returns:
            搬不回去的項目索引到目前停留位置的對應
        """
        journal.complete = False
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
        return stuck
