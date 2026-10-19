# -*- coding: utf-8 -*-
"""
收回

公演結束後，從譜庫的發譜紀錄挑曲目收回（spec #82）：刪掉曲目在 HuaXia 上的所有樂譜，包括別人放的（聲部長的弓法版
也是同一份譜的複本）。聲部、曲目、場次與排人都留著：應出席名單由場次掛的曲目加排人算出，刪了會改寫過去的出席率。
要收回哪些曲目只看譜庫的發譜紀錄（core/publish_log.py 的 published_pieces），所以專案丟了、換了譜務也能收回。

- 預覽（preview）：讀發譜紀錄裡發過、最後一次發譜之後還沒收回的曲目，再向 HuaXia 查它們現在的樂譜與掛的公演，
  分成已結束與還沒結束的（RecallPiece）。公演都已結束、沒有還要演的、也沒標過不收回的才建議收回（suggested）；
  依最近結束的那場公演分組（RecallPreview.groups）。
- 收回（recall）：重查幹部身分，先以孤兒檔清理清一次那首的（登記還在，只清真正的孤兒），再以使用者身分經 REST
  撤掉那首所有樂譜的登記，最後刪檔：只刪撤掉之後沒有任何樂譜登記還指著的（不論哪首曲目）。一首曲目一列「收回」
  （連同清孤兒檔的列）以新抽的發譜編號寫進發譜紀錄，也寫紀錄檔。中途停下時已撤的不會回來，沒刪成的檔記在那列，
  再收回一次時補刪（PublishedPiece.leftovers）。
- 不收回（keep）：一首曲目一列「不收回」寫進發譜紀錄，之後不再建議收回；不動 HuaXia。
網路請求會卡住呼叫的執行緒，要在背景執行緒呼叫。

使用範例：
    from services.recall_service import RecallService
    recaller = RecallService(huaxia_auth, log)
    preview = recaller.preview(catalog)
    result = recaller.recall([piece for piece in preview.pieces if piece.suggested], catalog)
"""
import datetime
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from core.catalog_errors import CatalogError
from core.constants import CONCERT_KIND
from core.locale import t
from core.log_entry import LogEntry
from core.publish_log import PublishAction, PublishRecord, published_pieces
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.huaxia_auth_service import HuaXiaAuthService, NotOfficer
from services.huaxia_client import HuaXiaError, HuaXiaEvent, HuaXiaScore
from services.orphan_cleanup import OrphanCleanup, clear_orphans


@dataclass(frozen=True)
class RecallPiece:
    """收回預覽裡的一首曲目：HuaXia 上現在的樂譜與掛的公演

    Attributes:
        piece_id: HuaXia 曲目編號
        piece_title: 曲名（發譜紀錄裡最後寫的）
        scores: 曲目上現在登記的樂譜，別人放的（不是泠靈路徑）在前
        part_names: 聲部編號對應 HuaXia 上的聲部名稱
        ended: 掛著、已經結束的公演，最近結束的在前
        upcoming: 掛著、還沒結束的公演，最早開始的在前
        kept: 最後一次發譜之後標過「不收回」
        leftovers: 上次收回中途停下、沒刪成的 Storage 檔（這次補刪）
    """
    piece_id: str
    piece_title: str
    scores: List[HuaXiaScore] = field(default_factory=list)
    part_names: Dict[str, str] = field(default_factory=dict)
    ended: List[HuaXiaEvent] = field(default_factory=list)
    upcoming: List[HuaXiaEvent] = field(default_factory=list)
    kept: bool = False
    leftovers: List[str] = field(default_factory=list)

    @property
    def others(self) -> List[HuaXiaScore]:
        """別人放的樂譜（網頁上傳的，不是泠靈路徑）"""
        return [score for score in self.scores if not score.lingling]

    @property
    def concert(self) -> Optional[HuaXiaEvent]:
        """分組用的公演：最近結束的那場；沒有已結束的公演時為 None"""
        return self.ended[0] if self.ended else None

    @property
    def suggested(self) -> bool:
        """建議收回（預設勾選）：掛的公演都已結束、沒有還要演的，也沒標過不收回"""
        return bool(self.ended) and not self.upcoming and not self.kept

    def voice(self, score: HuaXiaScore) -> str:
        """樂譜所在的聲部名稱；總譜（與舊的分組樂譜）為空字串"""
        return self.part_names.get(score.part_id or "", "")

    def events(self) -> List[Dict[str, str]]:
        """寫進發譜紀錄的公演：掛著的每一場（id、title、ends_at）"""
        return [
            {"id": event.id, "title": event.title, "ends_at": event.ends_at.isoformat()}
            for event in self.ended + self.upcoming
        ]


@dataclass(frozen=True)
class RecallPreview:
    """收回的預覽：發譜紀錄裡發過、還沒收回的曲目

    Attributes:
        pieces: 各曲目，依最後一次發譜由新到舊
        now: 判斷公演結束與否的時間（帶時區）
    """
    pieces: List[RecallPiece]
    now: datetime.datetime

    def groups(self) -> List[Tuple[Optional[HuaXiaEvent], List[RecallPiece]]]:
        """依公演分組：已結束的公演各一組（最近結束的在前），沒有已結束公演的曲目一組（公演為 None）放最後"""
        concerts: Dict[Optional[str], HuaXiaEvent] = {}
        grouped: Dict[Optional[str], List[RecallPiece]] = {}
        for piece in self.pieces:
            key = piece.concert.id if piece.concert is not None else None
            if piece.concert is not None:
                concerts[key] = piece.concert
            grouped.setdefault(key, []).append(piece)
        ended = sorted(concerts.values(), key=lambda event: event.ends_at, reverse=True)
        groups: List[Tuple[Optional[HuaXiaEvent], List[RecallPiece]]] = [
            (event, grouped[event.id]) for event in ended
        ]
        if None in grouped:
            groups.append((None, grouped[None]))
        return groups


@dataclass(frozen=True)
class RecallResult:
    """收回的結果

    Attributes:
        pieces: 要收回的曲目
        records: 寫進發譜紀錄的列（收回與清孤兒檔，做到的）
        cleanups: 收回前清孤兒檔的結果
        error: 中途停下的原因；做完了為 None
        log_error: 寫不進譜庫（HuaXia 已照 records 變動）
        not_officer: 不是幹部（什麼都沒做，已登出）
    """
    pieces: List[RecallPiece] = field(default_factory=list)
    records: List[PublishRecord] = field(default_factory=list)
    cleanups: List[OrphanCleanup] = field(default_factory=list)
    error: Optional[HuaXiaError] = None
    log_error: Optional[CatalogError] = None
    not_officer: Optional[NotOfficer] = None

    @property
    def recalled(self) -> List[str]:
        """做完收回的曲名"""
        return [
            record.piece_title for record in self.records
            if record.action is PublishAction.RECALL and record.failure is None
        ]

    @property
    def scores_removed(self) -> int:
        """撤掉登記的樂譜數"""
        return sum(len(record.scores_removed) for record in self.records if record.action is PublishAction.RECALL)


def _now() -> datetime.datetime:
    """現在的時間（UTC）"""
    return datetime.datetime.now(datetime.timezone.utc)


class RecallService:
    """收回公演已結束的曲目：預覽（preview）、收回（recall）與不收回（keep）"""

    def __init__(
        self, auth: HuaXiaAuthService, log: Optional[ActivityLog] = None,
        now: Callable[[], datetime.datetime] = _now,
    ):
        """
        Args:
            auth: HuaXia 登入服務（全程式一份）；請求經它的用戶端送出
            log: 紀錄檔；省略時不寫
            now: 現在的時間（帶時區），判斷公演結束與否；測試傳入固定的時間
        """
        self._auth = auth
        self._client = auth.client
        self._log = log
        self._now = now

    def preview(self, catalog: CatalogService) -> RecallPreview:
        """讀發譜紀錄裡發過、還沒收回的曲目，再向 HuaXia 查它們現在的樂譜與掛的公演

        Args:
            catalog: 譜庫服務（讀發譜紀錄）

        Returns:
            預覽；發譜紀錄裡沒有要收回的曲目時沒有曲目

        Raises:
            CatalogError: 讀不到發譜紀錄
            HuaXiaError: 沒有登入、登入已失效或連不上 HuaXia
        """
        now = self._now()
        published = [piece for piece in published_pieces(catalog.publish_log()) if not piece.recalled]
        if not published:
            return RecallPreview([], now)
        token = self._auth.access_token()
        piece_ids = [piece.piece_id for piece in published]
        scores = self._client.scores(token, piece_ids)
        names = {part.id: part.name for part in self._client.piece_parts(token, piece_ids)}
        links = self._client.piece_events(token, piece_ids)
        found = self._client.events_by_id(token, list(dict.fromkeys(event_id for event_id, _ in links)))
        concerts = {event.id: event for event in found if CONCERT_KIND in event.kinds}
        pieces = []
        for piece in published:
            attached = [concerts[event_id] for event_id, piece_id in links if piece_id == piece.piece_id and event_id in concerts]
            pieces.append(RecallPiece(
                piece.piece_id, piece.piece_title,
                scores=sorted((score for score in scores if score.piece_id == piece.piece_id), key=lambda s: s.lingling),
                part_names=names,
                ended=sorted((e for e in attached if e.ends_at <= now), key=lambda e: e.ends_at, reverse=True),
                upcoming=sorted((e for e in attached if e.ends_at > now), key=lambda e: e.starts_at),
                kept=piece.kept, leftovers=list(piece.leftovers),
            ))
        return RecallPreview(pieces, now)

    def recall(self, pieces: Sequence[RecallPiece], catalog: CatalogService) -> RecallResult:
        """收回這幾首：重查幹部身分，清孤兒檔，撤掉所有樂譜的登記、刪掉沒有登記還指著的檔，寫一列列「收回」

        Args:
            pieces: 勾選的曲目（預覽裡的）
            catalog: 譜庫服務（寫發譜紀錄）

        Returns:
            收回的結果；HuaXia 寫入中途失敗與發譜紀錄寫不進去不丟出，記在結果裡

        Raises:
            HuaXiaError: 沒有登入、登入已失效，或重查幹部身分時連不上 HuaXia（什麼都沒做）
        """
        not_officer = self._auth.recheck_officer()
        if not_officer is not None:
            return RecallResult(not_officer=not_officer)
        pieces = list(pieces)
        cleanups = clear_orphans(self._auth, {piece.piece_id: piece.piece_title for piece in pieces}, log=self._log)
        records: List[PublishRecord] = []
        error: Optional[HuaXiaError] = None
        for piece in pieces:
            removed: List[HuaXiaScore] = []
            deleted: List[str] = []
            try:
                removed, deleted = self._recall_piece(piece, removed)
            except HuaXiaError as e:
                error = e
            if error is None or removed or deleted:
                records.append(_recall_record(piece, removed, deleted, error))
            self._record_in_log(piece, error)
            if error is not None:
                break
        records += [cleanup.record() for cleanup in cleanups if cleanup.deleted or cleanup.detail]
        log_error = None
        if records:
            try:
                catalog.begin_publish(records, self._auth.status().discord_name)
            except CatalogError as e:
                log_error = e
        return RecallResult(pieces, records, cleanups, error, log_error)

    def keep(self, pieces: Sequence[RecallPiece], catalog: CatalogService) -> None:
        """把這幾首標成「不收回」：一首一列寫進發譜紀錄（新抽的發譜編號），之後不再建議收回；不動 HuaXia

        Args:
            pieces: 勾選的曲目（預覽裡的）
            catalog: 譜庫服務（寫發譜紀錄）

        Raises:
            CatalogError: 寫不進譜庫
        """
        records = [
            PublishRecord(
                PublishAction.KEEP, piece_id=piece.piece_id, piece_title=piece.piece_title, events=piece.events(),
            )
            for piece in pieces
        ]
        if records:
            catalog.begin_publish(records, self._auth.status().discord_name)

    def _recall_piece(self, piece: RecallPiece, removed: List[HuaXiaScore]) -> Tuple[List[HuaXiaScore], List[str]]:
        """收回一首：重讀登記、撤掉全部，再刪沒有登記還指著的檔（含上次沒刪成的）

        Args:
            piece: 要收回的曲目
            removed: 撤掉登記的樂譜（中途失敗時呼叫端仍拿得到已撤的）

        Returns:
            （撤掉登記的樂譜，刪掉的 Storage 路徑）

        Raises:
            HuaXiaError: 連不上或寫入失敗（removed 是失敗前撤掉的）
        """
        token = self._auth.access_token()
        current = self._client.scores(token, [piece.piece_id])
        gone = set(self._client.delete_scores(token, [score.id for score in current]))
        removed.extend(score for score in current if score.id in gone)
        paths = list(dict.fromkeys([score.file_path for score in removed if score.file_path] + piece.leftovers))
        still = self._client.registered_paths(token, paths) if paths else set()
        doomed = [path for path in paths if path not in still]
        if doomed:
            self._client.delete_files(token, doomed)
        return removed, doomed

    def _record_in_log(self, piece: RecallPiece, error: Optional[HuaXiaError]) -> None:
        """寫一筆紀錄檔：收回了，或中途停下的原因（不記 HuaXia 傳回的團員資料）"""
        if self._log is None:
            return
        if error is None:
            entry = LogEntry(t("recall.log.recalled"), piece_title=piece.piece_title)
        else:
            entry = LogEntry(
                t("recall.log.stopped"), piece_title=piece.piece_title,
                message=" ".join(part for part in (error.code, error.detail or str(error)) if part),
            )
        self._log.record(entry)


def _recall_record(
    piece: RecallPiece, removed: List[HuaXiaScore], deleted: List[str], error: Optional[HuaXiaError],
) -> PublishRecord:
    """發譜紀錄裡這首的「收回」：撤掉的登記、刪掉的檔、那一刻掛著的公演，中途停下的原因"""
    return PublishRecord(
        PublishAction.RECALL, piece_id=piece.piece_id, piece_title=piece.piece_title,
        scores_removed=[{"id": s.id, "part_id": s.part_id, "file_path": s.file_path} for s in removed],
        files_deleted=list(deleted), events=piece.events(),
        failure=None if error is None else {"code": error.code, "detail": error.detail or str(error)},
    )
