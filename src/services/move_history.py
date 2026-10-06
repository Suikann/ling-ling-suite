# -*- coding: utf-8 -*-
"""
搬移歷程

重新命名、復原、重做與中斷還原的唯一入口。擁有復原／重做堆疊、批次搬移的進行中紀錄
（透過內部的兩階段搬移引擎 services/move_service.py）與工作區 meta 的快照與寫回；
分割與旋轉的復原紀錄也在這裡組裝。

四個動作回傳同一種結果（MoveResult）：路徑變動、略過的檔、搬不回去的殘留、操作種類。
呼叫端只負責詢問與顯示，並把路徑變動交給 Project.replace_paths 套用。

使用範例：
    history = MoveHistory(file_service, workspace_service)
    result = history.rename(plan)
    project.replace_paths(result.changes + result.residual)
"""
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Callable, Iterable, List, Optional, Tuple

from core.constants import (
    BACKUP_DIR, MOVE_JOURNAL_FILE, MOVE_OPERATIONS, REDO_DIR, UNDO_DIR, OperationKind,
)
from core.locale import t
from core.models import RenameEntry, UndoMapping, UndoRecord
from core.paths import path_key
from services.file_service import FileService
from services.move_service import Move, MoveService, RenameRollbackError
from services.workspace_service import WorkspaceService


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
            中斷還原為被中斷的那批搬移的種類（舊版進行中紀錄視為 RENAME）
        changes: 照計畫搬好的檔案
        skipped: 已不在預期位置而略過的檔案（預期的位置）
        residual: 中途失敗、搬不回原位的檔案；已另寫一筆殘留紀錄放上復原堆疊
        error: 動作失敗的原因；為 None 表示照計畫完成
        record_error: 檔案已搬好（或殘留已確定）、但復原或重做紀錄寫不進去的原因
    """
    operation: OperationKind
    changes: List[UndoMapping] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)
    residual: List[UndoMapping] = field(default_factory=list)
    error: Optional[Exception] = None
    record_error: Optional[Exception] = None


@dataclass
class PendingMove:
    """上次中途中斷、尚未處理的批次搬移摘要（供啟動時詢問）

    Attributes:
        operation: 被中斷的那批搬移的操作種類（舊版進行中紀錄視為 RENAME）
        moved: 已搬動的檔案數
        complete: 是否已整批搬完、只差正式紀錄沒確認寫入
    """
    operation: OperationKind
    moved: int
    complete: bool


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
        name = f"{max(sequences, default=0) + 1:08d}_{record.id}.json"
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

    def remove(self, record_id: str) -> None:
        """移除指定 id 的紀錄；不在這個堆疊裡時不做事"""
        for _, entry_id, path in self._entries():
            if entry_id == record_id:
                os.remove(path)

    def clear(self) -> None:
        """移除堆疊裡所有紀錄"""
        for _, _, path in self._entries():
            os.remove(path)

    def _entries(self) -> List[Tuple[Tuple[int, object], str, str]]:
        """堆疊裡的紀錄檔：（排序鍵、檔名推得的 id、路徑），排序鍵越大越靠近頂端"""
        if not os.path.isdir(self.directory):
            return []
        entries = []
        for name in os.listdir(self.directory):
            stem, ext = os.path.splitext(name)
            if ext != ".json":
                continue
            sequence, _, record_id = stem.partition("_")
            if sequence.isdigit() and record_id:
                entries.append(((1, int(sequence)), record_id, os.path.join(self.directory, name)))
            elif stem.startswith(self.legacy_prefix):
                entries.append(((0, stem), stem, os.path.join(self.directory, name)))
        return entries

    @staticmethod
    def _load(path: str, fallback_id: str) -> UndoRecord:
        with open(path, "r", encoding="utf-8") as f:
            return UndoRecord.from_data(json.load(f), fallback_id)


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

    # --- 四個動作 ---

    def rename(self, plan: List[RenameEntry]) -> MoveResult:
        """依計畫重新命名，整批搬完後寫入復原紀錄並清空重做堆疊

        Args:
            plan: 實際要執行的重新命名計畫

        Returns:
            結果；changes 為計畫的每一項（原路徑 → 新路徑）
        """
        record = self._new_record(
            OperationKind.RENAME, t("rename.undo_description", count=len(plan)),
            mappings=[UndoMapping(e.original_path, e.new_path) for e in plan],
        )

        def finish(created_dirs: List[str]) -> None:
            record.created_directories = created_dirs
            self._push_new(record)

        moves = [(m.original, m.renamed) for m in record.mappings]
        return self._execute(OperationKind.RENAME, record.id, moves, finish)

    def undo(self) -> Optional[MoveResult]:
        """復原最上面的紀錄；沒有可復原的紀錄時回傳 None

        整批搬移類的紀錄把檔案搬回原位：已不在新位置的檔略過，只有實際搬回的對照轉入重做堆疊。
        分割與旋轉的紀錄撤銷其檔案效果後移除，不進重做堆疊（無法重做）。
        """
        record = self._undo.top()
        if record is None:
            return None
        if record.operation_type in MOVE_OPERATIONS:
            return self._undo_moves(record)
        return self._undo_file_effects(record)

    def redo(self) -> Optional[MoveResult]:
        """重做最近一次被復原的紀錄；沒有可重做的紀錄時回傳 None

        已不在原位的檔略過，只有實際搬回新位置的對照轉回復原堆疊。
        """
        record = self.latest_redo()
        if record is None:
            return None
        present, skipped = self._partition(record.mappings, lambda m: m.original)
        self._refresh_workspace_meta(record)

        def finish(created_dirs: List[str]) -> None:
            record.created_directories = created_dirs
            self._transfer(record, present, self._redo, self._undo)

        moves = [(m.original, m.renamed) for m in present]
        result = self._execute(OperationKind.REDO, record.id, moves, finish)
        result.operation = record.operation_type
        result.skipped = [m.original for m in skipped]
        return result

    def pending(self) -> Optional[PendingMove]:
        """上次中途中斷、尚未處理的批次；沒有時為 None

        Raises:
            ValueError: 進行中紀錄損毀（呼叫端可用 discard_pending 捨棄）
            OSError: 進行中紀錄讀不到
        """
        journal = self._engine.load_pending()
        if journal is None:
            return None
        return PendingMove(self._operation_of(journal), len(journal.moved_indices()), journal.complete)

    def discard_pending(self) -> None:
        """捨棄進行中紀錄（無法讀取，或已搬完的批次決定保留結果）"""
        self._engine.discard_pending()

    def recover(self) -> MoveResult:
        """中斷還原：把上次中途中斷的批次已搬動的檔案依反序搬回原位，並清除進行中紀錄

        已不在紀錄位置的檔略過；搬不回去的留在原地，替它們寫一筆殘留紀錄。

        Returns:
            結果；changes 為搬回原位的檔（紀錄位置 → 原位），沒有未完成的批次時各項皆空
        """
        try:
            journal = self._engine.load_pending()
            if journal is None:
                return MoveResult(OperationKind.RENAME)
            outcome = self._engine.recover(journal)
        except (OSError, ValueError) as e:
            return MoveResult(OperationKind.RENAME, error=e)
        kind = self._operation_of(journal)
        result = MoveResult(
            kind,
            changes=[UndoMapping(m.renamed, m.original) for m in outcome.restored],
            skipped=[m.renamed for m in outcome.skipped],
            residual=outcome.residual,
        )
        if outcome.residual:
            result.record_error = self._save_residual(kind, outcome.residual)
        return result

    # --- 其他操作的紀錄 ---

    def record_split(self, created_files: List[str], created_directories: List[str]) -> None:
        """記下一次分割：復原時把產生的分譜移到資源回收桶、移除新建且已空的目錄

        Args:
            created_files: 分割產生的分譜
            created_directories: 分割新建的目錄

        Raises:
            OSError: 紀錄寫不進去
        """
        self._push_new(self._new_record(
            OperationKind.SPLIT, t("undo.split_description", count=len(created_files)),
            created_files=list(created_files), created_directories=list(created_directories),
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
        shutil.copy2(path, backup)
        return backup

    # --- 內部：復原 ---

    def _undo_moves(self, record: UndoRecord) -> MoveResult:
        """把整批搬移類紀錄的檔案搬回原位"""
        present, skipped = self._partition(record.mappings, lambda m: m.renamed)

        def finish(_created_dirs: List[str]) -> None:
            self._transfer(record, present, self._undo, self._redo)

        moves = [(m.renamed, m.original) for m in present]
        result = self._execute(OperationKind.UNDO, record.id, moves, finish)
        self._restore_workspace_meta(record)
        if result.error is None:
            for directory in sorted(record.created_directories, reverse=True):
                self.file_service.remove_empty_directory(directory)
        result.operation = record.operation_type
        result.skipped = [m.renamed for m in skipped]
        return result

    def _undo_file_effects(self, record: UndoRecord) -> MoveResult:
        """撤銷分割或旋轉：刪除產生的檔、以備份蓋回原檔，完成後移除紀錄"""
        result = MoveResult(record.operation_type)
        try:
            if record.backup_path and os.path.isfile(record.backup_path):
                shutil.copy2(record.backup_path, record.original_path)
                os.remove(record.backup_path)
            for path in record.created_files:
                if self.file_service.file_exists(path):
                    self.file_service.delete_file(path)
            for directory in sorted(record.created_directories, reverse=True):
                self.file_service.remove_empty_directory(directory)
            self._undo.remove(record.id)
        except OSError as e:
            result.error = e
        return result

    # --- 內部：執行與紀錄 ---

    def _execute(
        self, kind: OperationKind, record_id: str, moves: List[Move],
        finish: Callable[[List[str]], None],
    ) -> MoveResult:
        """交給引擎整批搬移，整批搬完後（刪除進行中紀錄前）呼叫 finish 寫紀錄

        Args:
            kind: 執行這批搬移的操作種類（記進進行中紀錄；失敗留下的殘留紀錄也標示它）
            record_id: 所屬復原紀錄的 id
            moves: 搬移項目
            finish: 整批搬完後寫紀錄的函式，參數為本次新建的目錄

        Returns:
            結果；operation 為 kind，呼叫端視需要改寫
        """
        result = MoveResult(kind)
        finished = []

        def complete(created_dirs: List[str]) -> None:
            finished.append(True)
            finish(created_dirs)

        try:
            self._engine.execute(moves, on_complete=complete, operation=kind.value, record_id=record_id)
        except RenameRollbackError as e:
            result.error = e
            result.residual = e.residual
            result.record_error = self._save_residual(kind, e.residual)
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
        if record.operation_type in MOVE_OPERATIONS:
            self._snapshot_workspace_meta(record)
        self._undo.push(record)
        self._redo.clear()

    def _transfer(
        self, record: UndoRecord, mappings: List[UndoMapping],
        source: _RecordStack, target: _RecordStack,
    ) -> None:
        """紀錄從一個堆疊轉到另一個，只帶實際搬動的對照；一個都沒有時只從原堆疊移除"""
        if mappings:
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

    @staticmethod
    def _operation_of(journal) -> OperationKind:
        """進行中紀錄所屬的操作種類；舊版紀錄沒有記（或記了不認得的值）時視為重新命名"""
        try:
            return OperationKind(journal.operation)
        except ValueError:
            return OperationKind.RENAME

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
