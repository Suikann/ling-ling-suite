# -*- coding: utf-8 -*-
"""
搬移歷程

重新命名、復原、重做與中斷還原的唯一入口；重新命名前的預檢（check_rename）也在這裡，
回傳的判定（RenameVerdict）就是 rename 執行的計畫，預檢與執行前驗證共用搬移引擎的同一套規則。
擁有復原／重做堆疊、批次搬移的進行中紀錄
（透過內部的兩階段搬移引擎 services/move_service.py）與工作區 meta 的快照與寫回；
分割與旋轉的復原紀錄也在這裡組裝。

四個動作回傳同一種結果（MoveResult）：路徑變動、略過的檔、搬不回去的殘留、操作種類。
中斷還原依進行中紀錄記下的操作種類與所屬紀錄處理，可選「還原」（recover）或「保留結果」（keep_result）。
呼叫端只負責詢問與顯示，並把路徑變動交給 Project.replace_paths、復原的分割交給 Project.revert_split 套用。

使用範例：
    history = MoveHistory(file_service, workspace_service)
    verdict = history.check_rename(project)
    result = history.rename(verdict)
    project.replace_paths(result.changes + result.residual)
"""
import os
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable, Collection, Dict, Iterable, List, Optional, Tuple

from core.constants import (
    BACKUP_DIR, MOVE_JOURNAL_FILE, MOVE_OPERATIONS, REDO_DIR, RENAME_PROBLEM_RULES, UNDO_DIR,
    OperationKind, RenameProblem,
)
from core.locale import t
from core.models import Project, RenameEntry, SplitRecord, UndoMapping, UndoRecord
from core.naming import UnsafeFolderNameError
from core.paths import path_key
from services.file_service import FileService
from services.move_service import (
    BatchContext, Move, MoveJournal, MoveRecoveryResult, MoveService, RenameRollbackError,
)
from services.rename_service import RenamePlan, apply_auto_suffix, generate_rename_plan
from services.workspace_service import WorkspaceService


# 復原與重做堆疊裡紀錄檔的副檔名
_RECORD_EXTENSION = ".json"
# 殘留紀錄的描述：依留下它的操作種類
_RESIDUAL_DESCRIPTIONS = {
    OperationKind.RENAME: lambda count: t("history.residual.rename", count=count),
    OperationKind.UNDO: lambda count: t("history.residual.undo", count=count),
    OperationKind.REDO: lambda count: t("history.residual.redo", count=count),
}


@dataclass
class MoveResult:
    """搬移歷程一個動作的結果

    changes 與 residual 都是「動作前的位置 → 目前的位置」，交給 Project.replace_paths 一起套用；
    skipped 的檔案沒有搬動，專案裡的路徑維持原樣。

    Attributes:
        operation: 操作種類：重新命名為 RENAME；復原、重做為該筆紀錄的種類；
            中斷還原（還原或保留結果）為被中斷的那批搬移的種類（舊版進行中紀錄視為 RENAME）
        changes: 照計畫搬好的檔案
        skipped: 已不在預期位置而略過的檔案（預期的位置）
        residual: 中途失敗、搬不回原位的檔案；已另寫一筆殘留紀錄放上復原堆疊
        error: 動作失敗的原因；為 None 表示照計畫完成
        record_error: 檔案已搬好（或殘留已確定）、但復原或重做紀錄寫不進去的原因；
            中斷還原時發生則進行中紀錄保留，下次再處理
        split: 復原了一次分割時為該次分割，交給 Project.revert_split 退回分割前的專案；
            其中被取代的檔案（replaced_files）不找回，仍在資源回收桶
    """
    operation: OperationKind
    changes: List[UndoMapping] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    residual: List[UndoMapping] = field(default_factory=list)
    error: Optional[Exception] = None
    record_error: Optional[Exception] = None
    split: Optional[SplitRecord] = None


@dataclass
class RenameVerdict:
    """重新命名預檢的判定：實際會執行的計畫，加上以來源為鍵、分類好的問題

    Attributes:
        plan: 實際會執行的計畫：來源已不在的項目已丟掉、重複的目標已加後綴
        problems: 來源路徑 → 該檔的問題種類（來源依計畫順序，多出的檔在最後）
        unsafe_folder: 清理後是 . 或 .. 的資料夾名稱，阻擋（此時計畫為空）；沒有時為空字串
        folder_variables: 子資料夾模板裡的逐檔變數，阻擋
        unknown_variables: 命名格式與子資料夾模板裡不是模板變數的名稱，只提醒（產出時已拿掉）
    """
    plan: List[RenameEntry] = field(default_factory=list)
    problems: Dict[str, List[RenameProblem]] = field(default_factory=dict)
    unsafe_folder: str = ""
    folder_variables: List[str] = field(default_factory=list)
    unknown_variables: List[str] = field(default_factory=list)

    def sources(self, kind: RenameProblem) -> List[str]:
        """有這種問題的來源（依計畫順序）"""
        return [source for source, kinds in self.problems.items() if kind in kinds]

    @property
    def blocked(self) -> bool:
        """有阻擋的問題：資料夾名稱不安全、子資料夾模板用了逐檔變數，或有 RENAME_PROBLEM_RULES 判為阻擋的種類"""
        return bool(self.unsafe_folder or self.folder_variables) or any(
            RENAME_PROBLEM_RULES[kind].blocking for kinds in self.problems.values() for kind in kinds
        )

    @property
    def runnable(self) -> bool:
        """可以執行：計畫不為空且沒有阻擋"""
        return bool(self.plan) and not self.blocked


@dataclass
class PendingMove:
    """上次中途中斷、尚未處理的批次搬移摘要（供啟動時詢問）

    Attributes:
        operation: 被中斷的那批搬移的操作種類（舊版進行中紀錄視為 RENAME）
        moved: 已搬動的檔案數
        complete: 是否已整批搬完、只差正式紀錄沒確認寫入
        project_path: 這批搬移所屬的專案檔；專案當時尚未存檔為空字串，沒有記（舊版進行中紀錄）為 None
    """
    operation: OperationKind
    moved: int
    complete: bool
    project_path: Optional[str] = None


@dataclass
class _Interrupted:
    """讀回的進行中紀錄與據以處理它所需的資訊

    Attributes:
        journal: 進行中紀錄
        record: 所屬復原紀錄在這批搬移開始時的內容；舊版紀錄沒有，為 None
    """
    journal: MoveJournal
    record: Optional[UndoRecord]

    @property
    def kind(self) -> OperationKind:
        """被中斷的操作種類（舊版紀錄視為 RENAME）"""
        return self.journal.context.operation


def _by_source(order: List[str], found: Dict[RenameProblem, List[str]]) -> Dict[str, List[RenameProblem]]:
    """把各種問題的來源清單轉成「來源 → 問題種類」：來源依 order 排列，種類依 RenameProblem 的順序"""
    hits = {kind: set(sources) for kind, sources in found.items()}
    problems: Dict[str, List[RenameProblem]] = {}
    for source in order:
        kinds = [kind for kind in RenameProblem if source in hits.get(kind, ())]
        if kinds:
            problems.setdefault(source, kinds)
    return problems


class _RecordStack:
    """一個紀錄堆疊（復原或重做）：目錄裡每筆紀錄一個 JSON 檔

    新紀錄的檔名是「推入序號_紀錄 id.json」，序號越大越靠近頂端；
    舊版以時間戳命名的紀錄（undo_YYYYMMDD_HHMMSS.json）一律在新紀錄之下，彼此依時間排列，
    沒有 id 的以檔名（不含副檔名）當 id。
    """

    def __init__(self, file_service: FileService, directory: str, legacy_prefix: str):
        self.file_service = file_service
        self.directory = directory
        self.legacy_prefix = legacy_prefix

    def push(self, record: UndoRecord) -> None:
        """把紀錄放到頂端"""
        sequences = [key[1] for key, _, _ in self._entries() if key[0] == 1]
        name = f"{max(sequences, default=0) + 1:08d}_{record.id}{_RECORD_EXTENSION}"
        self.file_service.write_json_atomic(os.path.join(self.directory, name), record.to_data())

    def top(self, kinds: Optional[Iterable[OperationKind]] = None) -> Optional[UndoRecord]:
        """最上面的紀錄；指定 kinds 時只看這些種類，其他種類的紀錄略過

        Raises:
            ValueError: 紀錄內容損毀
        """
        kinds = set(kinds) if kinds is not None else None
        for _, record_id, path in sorted(self._entries(), reverse=True):
            record = self._load(path, record_id)
            if kinds is None or record.operation_type in kinds:
                return record
        return None

    def contains(self, record_id: str) -> bool:
        """堆疊裡是否有指定 id 的紀錄"""
        return any(entry_id == record_id for _, entry_id, _ in self._entries())

    def remove(self, record_id: str) -> None:
        """移除指定 id 的紀錄；不在這個堆疊裡時不做事"""
        for _, entry_id, path in self._entries():
            if entry_id == record_id:
                self.file_service.remove_file(path)

    def clear(self) -> None:
        """移除堆疊裡所有紀錄"""
        for _, _, path in self._entries():
            self.file_service.remove_file(path)

    def _entries(self) -> List[Tuple[Tuple[int, object], str, str]]:
        """堆疊裡的紀錄檔：（排序鍵、檔名推得的 id、路徑），排序鍵越大越靠近頂端"""
        if not self.file_service.directory_exists(self.directory):
            return []
        entries = []
        for path in self.file_service.list_files(self.directory, _RECORD_EXTENSION):
            stem = os.path.splitext(os.path.basename(path))[0]
            sequence, _, record_id = stem.partition("_")
            if sequence.isdigit() and record_id:
                entries.append(((1, int(sequence)), record_id, path))
            elif stem.startswith(self.legacy_prefix):
                entries.append(((0, stem), stem, path))
        return entries

    def _load(self, path: str, fallback_id: str) -> UndoRecord:
        """讀取一筆紀錄；舊版紀錄沒有 id 時以 fallback_id 代替

        Raises:
            ValueError: 紀錄內容損毀
        """
        return UndoRecord.from_data(self.file_service.read_json(path), fallback_id)


class MoveHistory:
    """搬移歷程：重新命名、復原、重做、中斷還原，以及分割與旋轉的復原紀錄"""

    def __init__(
        self, file_service: FileService, workspace_service: WorkspaceService,
        undo_dir: str = UNDO_DIR, redo_dir: str = REDO_DIR,
        journal_path: str = MOVE_JOURNAL_FILE, backup_dir: str = BACKUP_DIR,
    ):
        """
        Args:
            file_service: 檔案服務
            workspace_service: 工作區服務（meta 的快照與寫回）
            undo_dir: 復原堆疊的目錄
            redo_dir: 重做堆疊的目錄
            journal_path: 批次搬移進行中紀錄的檔案
            backup_dir: 旋轉覆蓋原檔前的備份目錄
        """
        self.file_service = file_service
        self.workspace_service = workspace_service
        self.backup_dir = backup_dir
        self._engine = MoveService(file_service, journal_path)
        self._undo = _RecordStack(file_service, undo_dir, "undo_")
        self._redo = _RecordStack(file_service, redo_dir, "redo_")

    # --- 詢問用 ---

    def latest_undo(self) -> Optional[UndoRecord]:
        """下一次復原會處理的紀錄；沒有時為 None"""
        return self._undo.top()

    def latest_redo(self) -> Optional[UndoRecord]:
        """下一次重做會處理的紀錄；沒有時為 None（舊版留在重做堆疊、無法重做的分割與旋轉紀錄不算）"""
        return self._redo.top(MOVE_OPERATIONS)

    # --- 重新命名預檢 ---

    def check_rename(self, project: Project, group_ids: Optional[Collection[str]] = None) -> RenameVerdict:
        """重新命名預檢：依專案的命名設定產生計畫並判定（不改專案）

        Args:
            project: 專案；分譜依聲部組分放時，先以 rename_service.assign_default_sections 補上聲部組
            group_ids: 只檢查這些群組；None 表示全部群組

        Returns:
            判定；rename 執行的就是它的計畫

        Raises:
            KeyError: 分譜依聲部組分放時，有要命名的聲部沒有聲部組
        """
        try:
            planned = generate_rename_plan(project, group_ids)
        except UnsafeFolderNameError as e:
            return RenameVerdict(unsafe_folder=e.name)
        return self.check_plan(planned)

    def check_plan(self, planned: RenamePlan) -> RenameVerdict:
        """重新命名預檢：判定一份依命名設定產生的計畫

        來源已不在的項目從計畫丟掉，重複的目標自動加後綴（只加一次），
        再以執行前驗證的規則檢查加後綴後的計畫；遺失來源、加後綴、多出的檔以外的問題一律阻擋。

        Args:
            planned: 還沒對照磁碟的計畫

        Returns:
            判定；rename 執行的就是它的計畫
        """
        missing = self._engine.find_missing_sources(self._moves(planned.entries))
        missing_keys = {path_key(source) for source in missing}
        present = [e for e in planned.entries if path_key(e.original_path) not in missing_keys]
        plan = apply_auto_suffix(present)
        found: Dict[RenameProblem, List[str]] = {
            RenameProblem.MISSING_SOURCE: missing,
            RenameProblem.SUFFIXED: [e.original_path for e, s in zip(present, plan) if e.new_path != s.new_path],
            RenameProblem.EXTRA_FILE: list(planned.extra_files),
        }
        for source, kinds in self._engine.find_problems(self._moves(plan), self._bounds(plan)).items():
            for kind in kinds:
                found.setdefault(kind, []).append(source)
        order = [e.original_path for e in planned.entries] + planned.extra_files
        return RenameVerdict(
            plan=plan, problems=_by_source(order, found),
            folder_variables=list(planned.folder_variables), unknown_variables=list(planned.unknown_variables),
        )

    # --- 四個動作 ---

    def rename(self, verdict: RenameVerdict, project_path: Optional[str] = None) -> MoveResult:
        """依預檢的判定重新命名，整批搬完後寫入復原紀錄並清空重做堆疊

        Args:
            verdict: check_rename 的判定
            project_path: 目前專案的專案檔（尚未存檔為空字串）；記進進行中紀錄，中斷後保留結果時據以更新該專案

        Returns:
            結果；changes 為計畫的每一項（原路徑 → 新路徑）；判定有阻擋時不搬動任何檔案、結果帶 error
        """
        if verdict.blocked:
            return MoveResult(OperationKind.RENAME, error=ValueError(t("rename.error.blocked")))
        plan = verdict.plan
        record = self._new_record(
            OperationKind.RENAME, t("rename.undo_description", count=len(plan)),
            mappings=[UndoMapping(e.original_path, e.new_path) for e in plan],
        )
        self._snapshot_workspace_meta(record)

        def finish(created_dirs: List[str]) -> None:
            record.created_directories = created_dirs
            self._push_new(record)

        moves = [(m.original, m.renamed) for m in record.mappings]
        return self._execute(
            OperationKind.RENAME, record, moves, finish, bounds=self._bounds(plan), project_path=project_path,
        )

    def undo(self, project_path: Optional[str] = None) -> Optional[MoveResult]:
        """復原最上面的紀錄；沒有可復原的紀錄時回傳 None

        整批搬移類的紀錄把檔案搬回原位：已不在新位置的檔略過，只有實際搬回的對照轉入重做堆疊。
        分割與旋轉的紀錄撤銷其檔案效果後移除，不進重做堆疊（無法重做）。

        Args:
            project_path: 目前專案的專案檔（尚未存檔為空字串）；記進進行中紀錄，同 rename
        """
        record = self._undo.top()
        if record is None:
            return None
        if record.operation_type in MOVE_OPERATIONS:
            return self._undo_moves(record, project_path)
        return self._undo_file_effects(record)

    def redo(self, project_path: Optional[str] = None) -> Optional[MoveResult]:
        """重做最近一次被復原的紀錄；沒有可重做的紀錄時回傳 None

        已不在原位的檔略過，只有實際搬回新位置的對照轉回復原堆疊。

        Args:
            project_path: 目前專案的專案檔（尚未存檔為空字串）；記進進行中紀錄，同 rename
        """
        record = self.latest_redo()
        if record is None:
            return None
        present, skipped = self._partition(record.mappings, lambda m: m.original)
        self._refresh_workspace_meta(record)

        def finish(created_dirs: List[str]) -> None:
            self._finish_redo(record, present, created_dirs)

        moves = [(m.original, m.renamed) for m in present]
        result = self._execute(OperationKind.REDO, record, moves, finish, project_path=project_path)
        result.operation = record.operation_type
        result.skipped = [m.original for m in skipped]
        return result

    def pending(self) -> Optional[PendingMove]:
        """上次中途中斷、尚未處理的批次；沒有時為 None

        Raises:
            ValueError: 進行中紀錄損毀（呼叫端可用 discard_pending 捨棄）
            OSError: 進行中紀錄讀不到
        """
        interrupted = self._load_pending()
        if interrupted is None:
            return None
        journal = interrupted.journal
        return PendingMove(
            interrupted.kind, len(journal.moved_indices()), journal.complete, journal.context.project_path,
        )

    def discard_pending(self) -> None:
        """捨棄無法讀取的進行中紀錄"""
        self._engine.discard_pending()

    def recover(self) -> MoveResult:
        """中斷還原：把上次中途中斷的批次已搬動的檔案依反序搬回原位，並清除進行中紀錄

        已不在紀錄位置的檔略過；搬不回去的留在原地，替它們寫一筆殘留紀錄。
        堆疊回到這批搬移開始前的樣子：被中斷的復原，其紀錄留在（或回到）復原堆疊；
        被中斷的重做，其紀錄留在（或回到）重做堆疊；被中斷的重新命名，其紀錄不留。
        整理完堆疊才刪除進行中紀錄；途中寫不進去時進行中紀錄保留，下次再處理。

        Returns:
            結果；changes 為搬回原位的檔（紀錄位置 → 原位），沒有未完成的批次時各項皆空
        """
        try:
            interrupted = self._load_pending()
        except (OSError, ValueError) as e:
            return MoveResult(OperationKind.RENAME, error=e)
        if interrupted is None:
            return MoveResult(OperationKind.RENAME)
        result = MoveResult(interrupted.kind)
        settled = []

        def settle(outcome: MoveRecoveryResult) -> None:
            settled.append(True)
            result.changes = [UndoMapping(m.renamed, m.original) for m in outcome.restored]
            result.skipped = [m.renamed for m in outcome.skipped]
            result.residual = outcome.residual
            if interrupted.record is not None:
                self._restore_workspace_meta(interrupted.record)
            self._put_back(interrupted)
            if outcome.residual:
                error = self._save_residual(interrupted.kind, outcome.residual)
                if error is not None:
                    raise error

        try:
            self._engine.recover(interrupted.journal, settle)
        except OSError as e:
            if settled:
                result.record_error = e
            else:
                result.error = e
        return result

    def keep_result(self) -> MoveResult:
        """保留結果：上次已整批搬完、只差紀錄沒確認寫入的批次，補完紀錄後清除進行中紀錄

        只在 pending() 回報已整批搬完（complete）時使用。
        被中斷的復原，其紀錄轉入重做堆疊；被中斷的重做，其紀錄轉回復原堆疊；
        被中斷的重新命名不補寫復原紀錄（之後無法復原），重做堆疊照新操作清空。
        補完紀錄才刪除進行中紀錄；途中寫不進去時進行中紀錄保留，下次再處理。

        Returns:
            結果；changes 為搬好的檔（原位置 → 目前位置），呼叫端據以更新專案路徑
        """
        try:
            interrupted = self._load_pending()
        except (OSError, ValueError) as e:
            return MoveResult(OperationKind.RENAME, error=e)
        if interrupted is None:
            return MoveResult(OperationKind.RENAME)
        journal = interrupted.journal
        origins, locations = journal.origins(), journal.locations()
        result = MoveResult(
            interrupted.kind,
            changes=[UndoMapping(origins[i], locations[i]) for i in journal.moved_indices()],
        )
        try:
            self._finish_kept(interrupted)
            self._engine.discard_pending()
        except OSError as e:
            result.record_error = e
        return result

    # --- 其他操作的紀錄 ---

    def record_split(self, record: SplitRecord) -> None:
        """記下一次分割：復原時把產生的分譜移到資源回收桶、移除新建且已空的目錄，並交回分割紀錄供專案退回

        Args:
            record: 分割紀錄（SplitResult.record(placement)，帶 Project.apply_split 回報的安置方式）

        Raises:
            OSError: 紀錄寫不進去
        """
        self._push_new(self._new_record(
            OperationKind.SPLIT, t("undo.split_description", count=len(record.created_files)),
            original_path=record.source_path,
            created_files=list(record.created_files), created_directories=list(record.created_directories),
            replaced_files=list(record.replaced_files), placement=record.placement,
        ))

    def record_rotate(self, source_path: str, output_path: str, backup_path: str = "") -> None:
        """記下一次旋轉：覆蓋原檔時復原以備份蓋回，另存時復原把另存出的檔移到資源回收桶

        Args:
            source_path: 旋轉的來源檔
            output_path: 旋轉結果寫到的檔（覆蓋原檔時與來源相同）
            backup_path: 覆蓋原檔前的備份（create_backup 的結果）；另存時為空字串

        Raises:
            OSError: 紀錄寫不進去
        """
        overwritten = bool(backup_path)
        self._push_new(self._new_record(
            OperationKind.ROTATE, t("undo.rotate_description"),
            original_path=source_path, backup_path=backup_path,
            created_files=[] if overwritten else [output_path],
        ))

    def create_backup(self, path: str) -> str:
        """覆蓋原檔前先備份，供復原旋轉時蓋回

        Args:
            path: 要備份的檔案

        Returns:
            備份檔的路徑
        """
        self.file_service.create_directory(self.backup_dir)
        backup = os.path.join(self.backup_dir, f"{uuid.uuid4().hex}_{os.path.basename(path)}")
        self.file_service.copy_file(path, backup)
        return backup

    # --- 內部：復原 ---

    def _undo_moves(self, record: UndoRecord, project_path: Optional[str]) -> MoveResult:
        """把整批搬移類紀錄的檔案搬回原位；project_path 同 undo"""
        present, skipped = self._partition(record.mappings, lambda m: m.renamed)

        def finish(_created_dirs: List[str]) -> None:
            self._finish_undo(record, present)

        moves = [(m.renamed, m.original) for m in present]
        result = self._execute(
            OperationKind.UNDO, record, moves, finish,
            on_rollback=lambda: self._restore_workspace_meta(record), project_path=project_path,
        )
        result.operation = record.operation_type
        result.skipped = [m.renamed for m in skipped]
        return result

    def _undo_file_effects(self, record: UndoRecord) -> MoveResult:
        """撤銷分割或旋轉：刪除產生的檔、以備份蓋回原檔，完成後移除紀錄

        分割的結果帶回該次分割（split），呼叫端交給專案退回分割前的樣子。
        """
        result = MoveResult(record.operation_type)
        try:
            if record.backup_path and self.file_service.file_exists(record.backup_path):
                self.file_service.copy_file(record.backup_path, record.original_path)
                self.file_service.remove_file(record.backup_path)
            for path in record.created_files:
                if self.file_service.file_exists(path):
                    self.file_service.delete_file(path)
            for directory in sorted(record.created_directories, reverse=True):
                self.file_service.remove_empty_directory(directory)
            self._undo.remove(record.id)
        except OSError as e:
            result.error = e
            return result
        if record.operation_type == OperationKind.SPLIT:
            result.split = SplitRecord(
                record.original_path, list(record.created_files), list(record.created_directories),
                list(record.replaced_files), record.placement,
            )
        return result

    # --- 內部：執行與紀錄 ---

    def _execute(
        self, kind: OperationKind, record: UndoRecord, moves: List[Move],
        finish: Callable[[List[str]], None], on_rollback: Optional[Callable[[], None]] = None,
        bounds: Optional[List[str]] = None, project_path: Optional[str] = None,
    ) -> MoveResult:
        """交給引擎整批搬移，整批搬完後（刪除進行中紀錄前）呼叫 finish 寫紀錄

        中途失敗回滾時，先呼叫 on_rollback，再把搬不回去的檔案寫成殘留紀錄，都在刪除進行中紀錄之前。

        Args:
            kind: 執行這批搬移的操作種類（記進進行中紀錄；失敗留下的殘留紀錄也標示它）
            record: 所屬復原紀錄（以搬移前的內容記進進行中紀錄，中斷還原時據以整理堆疊）
            moves: 搬移項目
            finish: 整批搬完後寫紀錄的函式，參數為本次新建的目錄
            on_rollback: 回滾結束、刪除進行中紀錄前另外要做的事（不得拋出 OSError）
            bounds: 各項目標必須在其中的資料夾（與 moves 一一對應）；None 表示不限制
            project_path: 這批搬移所屬的專案檔（記進進行中紀錄）；None 表示沒有提供

        Returns:
            結果；operation 為 kind，呼叫端視需要改寫
        """
        result = MoveResult(kind)
        finished = []

        def complete(created_dirs: List[str]) -> None:
            finished.append(True)
            finish(created_dirs)

        def rolled_back(residual: List[UndoMapping]) -> None:
            if on_rollback:
                on_rollback()
            if residual:
                result.residual = residual
                result.record_error = self._save_residual(kind, residual)

        try:
            self._engine.execute(
                moves, on_complete=complete, on_rollback=rolled_back,
                context=BatchContext(kind, record.id, record.to_data(), project_path), bounds=bounds,
            )
        except RenameRollbackError as e:
            result.error = e
            return result
        except (OSError, ValueError) as e:
            if finished:
                result.changes = [UndoMapping(src, dst) for src, dst in moves]
                result.record_error = e
            else:
                result.error = e
            return result
        result.changes = [UndoMapping(src, dst) for src, dst in moves]
        return result

    def _save_residual(self, kind: OperationKind, residual: List[UndoMapping]) -> Optional[OSError]:
        """替搬不回原位的檔案寫一筆殘留紀錄放上復原堆疊（不清空重做堆疊）；回傳寫入失敗的原因"""
        record = self._new_record(kind, "", residual=True, mappings=list(residual))
        record.description = self._describe(record, len(residual))
        try:
            self._snapshot_workspace_meta(record)
            self._undo.push(record)
        except OSError as e:
            return e
        return None

    def _push_new(self, record: UndoRecord) -> None:
        """新的操作：紀錄放上復原堆疊、清空重做堆疊"""
        self._undo.push(record)
        self._redo.clear()

    def _finish_undo(self, record: UndoRecord, present: List[UndoMapping]) -> None:
        """復原整批搬完：寫回工作區 meta、紀錄轉入重做堆疊、移除重新命名新建且已空的目錄"""
        self._restore_workspace_meta(record)
        self._transfer(record, present, self._undo, self._redo)
        for directory in sorted(record.created_directories, reverse=True):
            self.file_service.remove_empty_directory(directory)

    def _finish_redo(self, record: UndoRecord, present: List[UndoMapping], created_dirs: List[str]) -> None:
        """重做整批搬完：記下重做新建的目錄，紀錄轉回復原堆疊"""
        record.created_directories = created_dirs
        self._transfer(record, present, self._redo, self._undo)

    def _transfer(
        self, record: UndoRecord, mappings: List[UndoMapping],
        source: _RecordStack, target: _RecordStack,
    ) -> None:
        """紀錄從一個堆疊轉到另一個，只帶實際搬動的對照；一個都沒有時只從原堆疊移除

        已在目標堆疊（上次轉到一半被中斷）就不再放一次；先放入目標再從原堆疊移除。
        """
        if mappings and not target.contains(record.id):
            moved = replace(
                record, mappings=list(mappings),
                description=self._describe(record, len(mappings)),
            )
            target.push(moved)
        source.remove(record.id)

    def _partition(
        self, mappings: List[UndoMapping], location: Callable[[UndoMapping], str],
    ) -> Tuple[List[UndoMapping], List[UndoMapping]]:
        """依檔案是否還在 location 所指的位置，分成（在、不在）兩份"""
        present = [m for m in mappings if self.file_service.file_exists(location(m))]
        missing = [m for m in mappings if not self.file_service.file_exists(location(m))]
        return present, missing

    # --- 內部：中斷還原 ---

    def _load_pending(self) -> Optional[_Interrupted]:
        """讀取上次中途中斷的批次；沒有時為 None

        Raises:
            ValueError: 進行中紀錄或其中的復原紀錄損毀
            OSError: 進行中紀錄讀不到
        """
        journal = self._engine.load_pending()
        if journal is None:
            return None
        context = journal.context
        record = UndoRecord.from_data(context.record, context.record_id) if context.record else None
        return _Interrupted(journal, record)

    def _finish_kept(self, interrupted: _Interrupted) -> None:
        """保留結果：補做被中斷的那批搬移整批搬完後該寫的紀錄（已寫過的不重寫）"""
        record, journal = interrupted.record, interrupted.journal
        if interrupted.kind == OperationKind.RENAME:
            self._redo.clear()
            return
        if record is None:
            return
        moved = {path_key(path) for path in journal.origins().values()}
        if interrupted.kind == OperationKind.UNDO:
            self._finish_undo(record, [m for m in record.mappings if path_key(m.renamed) in moved])
        else:
            present = [m for m in record.mappings if path_key(m.original) in moved]
            self._finish_redo(record, present, sorted(journal.created_directories))

    def _put_back(self, interrupted: _Interrupted) -> None:
        """還原後讓堆疊回到這批搬移開始前的樣子：所屬紀錄回到原本的堆疊、不在另一個堆疊

        先放回再移除，任何時點中斷都不會兩邊都沒有。
        """
        record_id = interrupted.journal.context.record_id
        source, target = self._stacks_of(interrupted.kind)
        if source is not None and interrupted.record is not None and not source.contains(record_id):
            source.push(interrupted.record)
        target.remove(record_id)

    def _stacks_of(self, kind: OperationKind) -> Tuple[Optional[_RecordStack], _RecordStack]:
        """操作種類的紀錄從哪個堆疊轉到哪個堆疊；重新命名的紀錄是新的，沒有來源堆疊"""
        if kind == OperationKind.UNDO:
            return self._undo, self._redo
        if kind == OperationKind.REDO:
            return self._redo, self._undo
        return None, self._undo

    @staticmethod
    def _moves(plan: List[RenameEntry]) -> List[Move]:
        """重新命名計畫的「來源 → 目標」"""
        return [(e.original_path, e.new_path) for e in plan]

    @staticmethod
    def _bounds(plan: List[RenameEntry]) -> List[str]:
        """重新命名計畫各項目標必須在其中的資料夾：該項的輸出位置"""
        return [e.output_location() for e in plan]

    @staticmethod
    def _new_record(kind: OperationKind, description: str, **fields) -> UndoRecord:
        """新紀錄：唯一 id 與建立時間"""
        return UndoRecord(
            id=uuid.uuid4().hex, timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
            description=description, operation_type=kind, **fields,
        )

    @staticmethod
    def _describe(record: UndoRecord, count: int) -> str:
        """整批搬移類紀錄搬動 count 個檔案時的描述；其他種類維持原描述"""
        if record.residual and record.operation_type in _RESIDUAL_DESCRIPTIONS:
            return _RESIDUAL_DESCRIPTIONS[record.operation_type](count)
        if record.operation_type == OperationKind.RENAME:
            return t("rename.undo_description", count=count)
        return record.description

    # --- 內部：工作區 meta ---

    def _snapshot_workspace_meta(self, record: UndoRecord) -> None:
        """把來源位於工作區的項目其子資料夾的 meta.json 快照進紀錄

        搬空的子資料夾會被清理掃描刪掉，復原時要靠快照把來源資訊寫回。
        """
        folders = (self.workspace_service.folder_of(m.original) for m in record.mappings)
        self._capture_workspace_meta(record, [f for f in folders if f])

    def _refresh_workspace_meta(self, record: UndoRecord) -> None:
        """重做前以子資料夾目前的 meta 更新快照：復原與重做之間專案可能另存到新位置"""
        self._capture_workspace_meta(record, list(record.workspace_meta))

    def _capture_workspace_meta(self, record: UndoRecord, folders: List[str]) -> None:
        """讀取各子資料夾目前的 meta 存進快照（同一資料夾的不同寫法算同一個）；沒有可讀的 meta 則保留原值"""
        known = {path_key(folder): folder for folder in record.workspace_meta}
        for folder in folders:
            folder = known.setdefault(path_key(folder), folder)
            meta = self.workspace_service.read_meta(folder)
            if meta is not None:
                record.workspace_meta[folder] = meta

    def _restore_workspace_meta(self, record: UndoRecord) -> None:
        """檔案搬回後，用快照補回被清掉的 meta.json；寫回失敗不影響已完成的檔案復原"""
        for folder, meta in record.workspace_meta.items():
            try:
                self.workspace_service.restore_meta(folder, meta)
            except OSError:
                continue
