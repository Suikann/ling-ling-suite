# -*- coding: utf-8 -*-
"""
Storage 檔的刪除與孤兒檔清理

全程式判斷「HuaXia Storage 的這個檔能不能刪」只用這裡的 delete_unregistered：沒有任何樂譜登記指著它（不論哪首曲目，
以 HuaXiaClient.registered_paths 向 HuaXia 查那一刻的登記）才刪。發譜的清舊檔（publish_runner）、撤回
（retract_service）、收回（recall_service）與孤兒檔清理都經過它；各自只決定要考慮哪些檔。

孤兒檔是 HuaXia Storage 裡泠靈路徑格式（huaxia_client.is_lingling_path）、但沒有任何樂譜登記指著的檔：
發譜第 1 段傳了、那個群組卻沒切換的新檔，第 3 段沒刪成的舊檔，以及撤回與收回中斷時剩下的泠靈的檔。團員看不到它們。
發譜開始前（publish_runner）與收回前各清一次涉及的曲目：列出那首在泠靈資料夾（lingling/<曲目編號>/）的檔，
扣掉這次自己要用的（keep，例如這次要傳、還沒登記的檔），剩下的交給 delete_unregistered。
不是泠靈路徑格式的檔（HuaXia 網頁上傳的，路徑是時間戳）一律不碰，所以別人正在網頁上傳、還沒登記的檔不受影響。

清理是順手的整理：列不出、讀不到或刪不掉時只寫出原因，不擋發譜或收回。每首清掉的檔寫進紀錄檔；
寫進譜庫的發譜紀錄是呼叫端的事（OrphanCleanup.record 交出那一列）。網路請求會卡住呼叫的執行緒，要在背景執行緒呼叫。

使用範例：
    from services.orphan_cleanup import clear_orphans, delete_unregistered
    cleanups = clear_orphans(huaxia_auth, {piece_id: piece_title}, keep=paths_this_run_sends, log=log)
    records = [cleanup.record() for cleanup in cleanups if cleanup.deleted or cleanup.detail]
    deletion = delete_unregistered(huaxia_auth, replaced_paths)   # deletion.deleted 是真的刪掉的
"""
from dataclasses import dataclass, field
from typing import Collection, Iterable, List, Mapping, NamedTuple, Optional, Set

from core.locale import t
from core.log_entry import LogEntry
from core.publish_log import PublishAction, PublishRecord
from services.activity_log import ActivityLog
from services.huaxia_auth_service import HuaXiaAuthService
from services.huaxia_client import HuaXiaError, is_lingling_path, lingling_storage_folder


class StorageDeletion(NamedTuple):
    """刪 Storage 檔的結果（delete_unregistered）

    Attributes:
        deleted: 真的刪掉的
        in_use: 還有樂譜登記指著、不刪的
        not_deleted: 沒有登記指著、Storage 卻沒刪掉、還在的（例如 RLS 擋下；回應照樣成功，以回應的清單判斷）。
            本來就已經不在 Storage 的不在任何一項裡
    """
    deleted: List[str]
    in_use: List[str]
    not_deleted: List[str]


def delete_unregistered(auth: HuaXiaAuthService, paths: Iterable[str]) -> StorageDeletion:
    """刪掉這些 Storage 檔裡沒有任何樂譜登記指著的（不論哪首曲目的登記）

    Args:
        auth: HuaXia 登入服務（全程式一份）；請求經它的用戶端送出
        paths: 要考慮的 Storage 路徑（重複的與空字串略過）

    Returns:
        刪掉的、還在用的與沒刪掉的，各依 paths 的順序；有沒刪掉的時另列出它們所在的資料夾，分出本來就已經不在的

    Raises:
        HuaXiaError: 連不上、讀不到登記或刪除失敗
    """
    client = auth.client
    paths = list(dict.fromkeys(path for path in paths if path))
    if not paths:
        return StorageDeletion([], [], [])
    registered = client.registered_paths(auth.access_token(), paths)
    doomed = [path for path in paths if path not in registered]
    deleted = set(client.delete_files(auth.access_token(), doomed)) if doomed else set()
    left = [path for path in doomed if path not in deleted]
    present: Set[str] = set()
    for folder in dict.fromkeys(path.rsplit("/", 1)[0] for path in left):
        present |= set(client.stored_files(auth.access_token(), folder))
    return StorageDeletion(
        [path for path in doomed if path in deleted], [path for path in paths if path in registered],
        [path for path in left if path in present],
    )


@dataclass(frozen=True)
class OrphanCleanup:
    """一首曲目清孤兒檔的結果

    Attributes:
        piece_id: HuaXia 曲目編號
        piece_title: 曲名
        deleted: 刪掉的孤兒檔（Storage 路徑）
        code: 沒清成時 HuaXia 回的錯誤代碼；沒有時為空字串
        detail: 沒清成的原因；清成了（含沒有孤兒檔）為空字串
    """
    piece_id: str
    piece_title: str
    deleted: List[str] = field(default_factory=list)
    code: str = ""
    detail: str = ""

    def record(self) -> PublishRecord:
        """發譜紀錄裡這首的「清孤兒檔」：刪掉的檔，沒清成時的原因"""
        return PublishRecord(
            PublishAction.CLEAR_ORPHANS, piece_id=self.piece_id, piece_title=self.piece_title,
            files_deleted=list(self.deleted),
            failure={"code": self.code, "detail": self.detail} if self.detail else None,
        )


def clear_orphans(
    auth: HuaXiaAuthService, pieces: Mapping[str, str], keep: Collection[str] = (), log: Optional[ActivityLog] = None,
) -> List[OrphanCleanup]:
    """清掉這幾首曲目底下泠靈路徑格式、但沒有任何登記指著的檔；不碰 keep 裡的與不是泠靈路徑格式的

    Args:
        auth: HuaXia 登入服務（全程式一份）；請求經它的用戶端送出
        pieces: 曲目編號對應曲名，依清理的順序
        keep: 這次自己要用、還沒登記的 Storage 路徑（例如這次發譜要傳的），不算孤兒檔
        log: 紀錄檔；省略時不寫

    Returns:
        每首曲目的結果，依 pieces 的順序；失敗不丟出，記在結果裡
    """
    keep = set(keep)
    cleanups = []
    for piece_id, piece_title in pieces.items():
        try:
            deleted = _clear_piece(auth, piece_id, keep)
        except HuaXiaError as e:
            cleanup = OrphanCleanup(piece_id, piece_title, code=e.code, detail=e.detail or str(e))
        else:
            cleanup = OrphanCleanup(piece_id, piece_title, deleted)
        _record_in_log(cleanup, log)
        cleanups.append(cleanup)
    return cleanups


def _clear_piece(auth: HuaXiaAuthService, piece_id: str, keep: Collection[str]) -> List[str]:
    """清一首曲目的孤兒檔，回傳刪掉的路徑

    Raises:
        HuaXiaError: 列不出、讀不到登記或刪不掉
    """
    stored = auth.client.stored_files(auth.access_token(), lingling_storage_folder(piece_id))
    candidates = sorted(path for path in stored if is_lingling_path(path) and path not in keep)
    return delete_unregistered(auth, candidates).deleted


def _record_in_log(cleanup: OrphanCleanup, log: Optional[ActivityLog]) -> None:
    """清掉的每個檔各寫一筆紀錄檔；沒清成時寫一筆原因"""
    if log is None:
        return
    for path in cleanup.deleted:
        log.record(LogEntry(t("publish.log.orphan_cleared"), piece_title=cleanup.piece_title, path=path))
    if cleanup.detail:
        log.record(LogEntry(
            t("publish.log.orphan_clear_failed"), piece_title=cleanup.piece_title,
            message=" ".join(part for part in (cleanup.code, cleanup.detail) if part),
        ))
