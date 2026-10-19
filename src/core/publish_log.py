# -*- coding: utf-8 -*-
"""
發譜紀錄的一列

譜庫的「發譜紀錄」工作表（core/catalog_constants.py 的 SHEET_PUBLISH_LOG）只新增、不修改：每次發譜在第 1 段之前
先寫「開始發譜」（PublishAction.START），結束後補「發譜結果」（PublishAction.RESULT），兩者以同一個發譜編號串起來，
每個群組各一列。發譜開始前清掉的孤兒檔（services/orphan_cleanup.py）是「清孤兒檔」（PublishAction.CLEAR_ORPHANS），
一首曲目一列，與「發譜結果」一起寫入、同一個發譜編號。撤回（PublishAction.RETRACT）沿用被撤回那次的發譜編號，
每個勾選的群組一列。收回（PublishAction.RECALL）與不收回（PublishAction.KEEP）一首曲目一列，每次各抽一個發譜編號；
收回前清的孤兒檔同樣寫成「清孤兒檔」，與「收回」一起寫入。列表上的一項（publish_entries）是一次發譜，
或同一次寫入的撤回、收回、不收回；清孤兒檔不列成一項。發過哪些曲目、最後一次發譜之後收回了沒有或標了不收回，
由 published_pieces 整理；收回提醒記在偏好設定的摘要（reminder_summary）與要提醒的曲目（due_reminders）也由它算出，
收回畫面與收回提醒用同一條規則。

一列記這個群組對到哪首 HuaXia 曲目、新建的曲目與聲部、人數與聲部組的原值與新值、傳了／登記了／撤掉登記的／
刪了哪些檔（Storage 路徑）、掛了哪些場次與結束時間、沒發成的原因。清單與物件的欄以 JSON 寫進一格（PublishRecord.cells），
讀回時還原（PublishRecord.from_cells）；各欄的內容：

- 開始發譜：要做的。new_piece 是要新建的曲目欄位（title、composer、duration_min、ensemble）；parts_created 是要新建的聲部
  （name、needed、needed_min、section、sort）；parts_updated 是要覆寫的聲部（id、name，改的欄位各是［原值，新值］）；
  files_uploaded 是要傳的 Storage 路徑；scores_added 是要登記的（發譜函式 add_scores 的一項：title、part_id 或
  new_part、file_path；那次沒有結果時撤回依它的 file_path 認）；
  scores_removed 是要撤掉登記的（id、part_id、file_path）；files_deleted 是清舊檔要刪的；events 是要掛的場次（id、title、ends_at）。
- 發譜結果：做到的。new_piece 只在這次真的新建了曲目時有；parts_created、scores_added、scores_removed 是 HuaXia 發譜函式
  回報的（帶 HuaXia 的編號，撤回只動這些）；parts_updated 是切換成功時寫進去的；files_uploaded 是這次真的傳上去的；
  files_deleted 是真的刪掉的；events 是這次新掛上的。沒發成時 failure 寫出在哪一段（stage）與原因（code、detail），
  其他欄只有 files_uploaded（傳上去了、沒登記的檔）。
- 清孤兒檔：piece_id、piece_title 與 files_deleted（刪掉的孤兒檔）；沒清成時 failure 寫出原因（code、detail）。群組欄空白。
- 撤回：做到的。scores_removed 是撤掉的登記（id、part_id、file_path），parts_deleted 是刪掉的聲部（id、name），
  files_deleted 是刪掉的 Storage 檔；中途停下時 failure 寫出原因（code、detail），其他欄是停下之前做到的。
- 收回：做到的。piece_id、piece_title；scores_removed 是撤掉的登記（id、part_id、file_path，包括別人放的），
  files_deleted 是刪掉的 Storage 檔；events 是那一刻曲目掛著的公演（id、title、ends_at）；中途停下時 failure 寫出原因
  （code、detail）。群組欄空白。
- 不收回：piece_id、piece_title 與那一刻曲目掛著的公演（events）。群組欄空白。

使用範例：
    from core.publish_log import PublishAction, PublishRecord
    record = PublishRecord(PublishAction.START, group_id=group.id, group=group.name, piece_id=piece_id)
    cells = record.cells()
    entries = publish_entries(catalog.publish_log())   # 發譜紀錄的列表，新的在前
    pieces = published_pieces(catalog.publish_log())   # 發過的曲目，有沒有收回或標不收回
    titles = due_reminders(reminder_summary(records), now)   # 公演都結束、還沒收回的曲名
"""
import datetime
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, TypedDict


class PublishAction(Enum):
    """發譜紀錄一列的動作；值寫進工作表"""

    START = "start"                   # 開始發譜（第 1 段之前）
    RESULT = "result"                 # 發譜結果（結束後）
    CLEAR_ORPHANS = "clear_orphans"   # 清孤兒檔（發譜開始前清的，一首曲目一列，隨發譜結果寫入）
    RETRACT = "retract"               # 撤回（沿用被撤回那次的發譜編號）
    RECALL = "recall"                 # 收回（一首曲目一列，每次收回抽一個發譜編號）
    KEEP = "keep"                     # 不收回（一首曲目一列，每次標記抽一個發譜編號）


class ScoreEntry(TypedDict):
    """發譜紀錄裡的一份樂譜登記（撤掉登記的、發譜函式回報登記的）：HuaXia 的樂譜編號、聲部編號（總譜為 None）、
    Storage 路徑"""
    id: str
    part_id: Optional[str]
    file_path: str


class EventEntry(TypedDict):
    """發譜紀錄裡的一場公演：HuaXia 的場次編號、名稱與結束時間（ISO 8601，收回提醒用）"""
    id: str
    title: str
    ends_at: str


class FailureEntry(TypedDict, total=False):
    """發譜紀錄裡沒做完的原因：在哪一段（只有發譜結果有）、HuaXia 的錯誤代碼與說明"""
    stage: str
    code: str
    detail: str


def utc_now() -> datetime.datetime:
    """現在的時間（UTC，帶時區）：收回與收回提醒判斷公演結束與否的預設時間"""
    return datetime.datetime.now(datetime.timezone.utc)


def score_entry(score: Any) -> ScoreEntry:
    """一份樂譜（有 id、part_id、file_path，例如 services.huaxia_client.HuaXiaScore）寫成發譜紀錄的樣子"""
    return ScoreEntry(id=score.id, part_id=score.part_id, file_path=score.file_path)


def event_entry(event: Any) -> EventEntry:
    """一場公演（有 id、title 與帶時區的 ends_at，例如 services.huaxia_client.HuaXiaEvent）寫成發譜紀錄的樣子"""
    return EventEntry(id=event.id, title=event.title, ends_at=event.ends_at.isoformat())


def failure_entry(code: str, detail: str, stage: str = "") -> FailureEntry:
    """沒做完的原因寫成發譜紀錄的樣子；stage 為空字串時不寫"""
    entry = FailureEntry(code=code, detail=detail)
    if stage:
        entry["stage"] = stage
    return entry


# 以 JSON 寫進一格的欄
_JSON_FIELDS = (
    "new_piece", "parts_created", "parts_updated", "files_uploaded", "scores_added", "scores_removed",
    "files_deleted", "events", "failure", "parts_deleted",
)


@dataclass(frozen=True)
class PublishRecord:
    """發譜紀錄的一列：一次發譜（或之後的撤回）裡的一個群組，或收回、不收回、清孤兒檔的一首曲目

    Attributes:
        action: 動作
        group_id: 群組 ID
        group: 群組名稱
        piece_id: 對到的 HuaXia 曲目編號
        piece_title: 曲名
        new_piece: 新建的曲目欄位；不新建時為 None
        parts_created: 新建的聲部
        parts_updated: 覆寫的聲部與改的欄位的［原值，新值］
        files_uploaded: 傳到 Storage 的路徑
        scores_added: 登記的樂譜
        scores_removed: 撤掉登記的樂譜
        files_deleted: 刪掉的 Storage 路徑
        events: 掛上的場次（含結束時間）
        failure: 沒發成時的段與原因（stage、code、detail）；發成了為 None
        parts_deleted: 撤回刪掉的聲部
        publish_id: 發譜編號（讀回時才有；寫入時由譜庫服務填）
        time: 寫入時間（讀回時才有）
        operator: 操作者的 Discord 名稱（讀回時才有）
    """

    action: PublishAction
    group_id: str = ""
    group: str = ""
    piece_id: str = ""
    piece_title: str = ""
    new_piece: Optional[Dict[str, Any]] = None
    parts_created: List[Dict[str, Any]] = field(default_factory=list)
    parts_updated: List[Dict[str, Any]] = field(default_factory=list)
    files_uploaded: List[str] = field(default_factory=list)
    scores_added: List[Dict[str, Any]] = field(default_factory=list)
    scores_removed: List[ScoreEntry] = field(default_factory=list)
    files_deleted: List[str] = field(default_factory=list)
    events: List[EventEntry] = field(default_factory=list)
    failure: Optional[FailureEntry] = None
    parts_deleted: List[Dict[str, Any]] = field(default_factory=list)
    publish_id: str = ""
    time: str = ""
    operator: str = ""

    def cells(self) -> Dict[str, str]:
        """寫進工作表的各欄（欄位名稱見 SHEET_HEADERS；不含 id、publish_id、time、operator）"""
        cells = {
            "action": self.action.value, "group_id": self.group_id, "group": self.group, "piece_id": self.piece_id,
            "piece_title": self.piece_title,
        }
        for name in _JSON_FIELDS:
            value = getattr(self, name)
            cells[name] = json.dumps(value, ensure_ascii=False) if value else ""
        return cells

    @classmethod
    def from_cells(cls, cells: Mapping[str, str]) -> Optional["PublishRecord"]:
        """由工作表的一列還原；動作不認得（較新版泠靈寫的）或內容壞掉時為 None"""
        try:
            action = PublishAction(cells.get("action", ""))
            decoded = {name: json.loads(cells[name]) for name in _JSON_FIELDS if cells.get(name, "").strip()}
        except (ValueError, TypeError):
            return None
        return cls(
            action, group_id=cells.get("group_id", ""), group=cells.get("group", ""),
            piece_id=cells.get("piece_id", ""), piece_title=cells.get("piece_title", ""),
            publish_id=cells.get("publish_id", ""), time=cells.get("time", ""), operator=cells.get("operator", ""),
            **decoded,
        )


@dataclass(frozen=True)
class PublishLogEntry:
    """發譜紀錄列表的一項：一次發譜（開始發譜與發譜結果），或同一次寫入的撤回、收回、不收回

    Attributes:
        publish_id: 發譜編號（撤回是被撤回那次的）
        action: 發譜為 START，其他是那個動作（RETRACT、RECALL、KEEP）
        time: 寫入時間（發譜是開始發譜那一刻）
        operator: 操作者的 Discord 名稱
        groups: 發譜是各群組的發譜結果（沒有結果時是開始發譜），撤回是各群組撤回的列，收回與不收回是各曲目的列
        finished: 發譜有發譜結果（沒有的是中途停下或結果寫不進譜庫）；其他動作一律為 True
    """
    publish_id: str
    action: PublishAction
    time: str
    operator: str
    groups: List[PublishRecord]
    finished: bool = True


def publish_entries(records: Sequence[PublishRecord]) -> List[PublishLogEntry]:
    """把發譜紀錄的列整理成列表的項，新的在前

    同一個發譜編號的開始發譜與發譜結果是一次發譜；清孤兒檔略過（隨發譜結果寫入的整理，不是群組）；
    其他動作以（發譜編號，動作，時間）分成一項，同一次寫入的列在一起。

    Args:
        records: 發譜紀錄的全部列，依紀錄的順序（CatalogService.publish_log）

    Returns:
        列表的項，依第一列的順序由新到舊
    """
    order: List[Tuple[str, ...]] = []
    rows: Dict[Tuple[str, ...], List[PublishRecord]] = {}
    for record in records:
        if record.action is PublishAction.CLEAR_ORPHANS:
            continue
        if record.action in (PublishAction.START, PublishAction.RESULT):
            key: Tuple[str, ...] = (record.publish_id, PublishAction.START.value)
        else:
            key = (record.publish_id, record.action.value, record.time)
        if key not in rows:
            order.append(key)
            rows[key] = []
        rows[key].append(record)
    entries = []
    for key in reversed(order):
        group = rows[key]
        first = group[0]
        if key[1] == PublishAction.START.value:
            results = [record for record in group if record.action is PublishAction.RESULT]
            started = [record for record in group if record.action is PublishAction.START]
            entries.append(PublishLogEntry(
                first.publish_id, PublishAction.START, first.time, first.operator, results or started, bool(results),
            ))
        else:
            entries.append(PublishLogEntry(first.publish_id, first.action, first.time, first.operator, group))
    return entries


@dataclass(frozen=True)
class PublishedPiece:
    """發譜紀錄裡發過的一首 HuaXia 曲目，與它最後一次發譜之後有沒有收回、標了不收回

    Attributes:
        piece_id: HuaXia 曲目編號
        piece_title: 曲名（紀錄裡最後寫的）
        recalled: 最後一次發譜之後收回過，而且做完了
        kept: 最後一次發譜之後標過「不收回」
        leftovers: 上次收回中途停下、登記撤掉了但沒刪成的 Storage 檔（之後做完一次收回就清空；再收回時補刪）
        concerts: 紀錄裡看得到它掛著的公演（id、title、ends_at），依第一次出現的順序：發成的群組要掛的與新掛上的、
            中途停下那次要掛的，以及收回與不收回那一刻掛著的；幹部在網頁上另掛的看不到
    """
    piece_id: str
    piece_title: str
    recalled: bool = False
    kept: bool = False
    leftovers: List[str] = field(default_factory=list)
    concerts: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def outstanding(self) -> bool:
        """還沒收回，也沒標不收回"""
        return not self.recalled and not self.kept


def published_pieces(records: Sequence[PublishRecord]) -> List[PublishedPiece]:
    """發譜紀錄裡發過的曲目，新發的在前

    發過是指有一列發成了的發譜結果，或有一次發譜只有開始發譜、沒有任何結果（中途停下，HuaXia 上可能已經有那次的譜）。
    沒發成的群組不算；一次發譜裡對到那首、發成了的群組（沒有結果的那次是開始發譜的群組）都撤回了（撤回做完），
    那次也不算：HuaXia 上已經沒有那次的譜。
    收回與不收回只看那首最後一次發譜之後的列；中途停下的收回不算收回過。

    Args:
        records: 發譜紀錄的全部列，依紀錄的順序（CatalogService.publish_log）

    Returns:
        每首一個，依最後一次發譜由新到舊
    """
    finished = {record.publish_id for record in records if record.action is PublishAction.RESULT}
    retracted = {
        (record.publish_id, record.group_id) for record in records
        if record.action is PublishAction.RETRACT and record.failure is None
    }
    planned = {
        (record.publish_id, record.group_id): record.events for record in records
        if record.action is PublishAction.START
    }
    remaining: Dict[Tuple[str, str], bool] = {}
    for record in records:
        if record.action is PublishAction.RESULT and record.failure is None:
            key = (record.publish_id, record.piece_id)
            remaining[key] = remaining.get(key, False) or (record.publish_id, record.group_id) not in retracted
    last: Dict[str, int] = {}
    titles: Dict[str, str] = {}
    concerts: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for index, record in enumerate(records):
        succeeded = record.action is PublishAction.RESULT and record.failure is None
        unfinished = (
            record.action is PublishAction.START and record.publish_id not in finished
            and (record.publish_id, record.group_id) not in retracted
        )
        if succeeded and remaining[(record.publish_id, record.piece_id)] or unfinished:
            if record.piece_id:
                last[record.piece_id] = index
        if record.piece_id and record.piece_title:
            titles[record.piece_id] = record.piece_title
        seen = []
        if succeeded:
            seen = planned.get((record.publish_id, record.group_id), []) + record.events
        elif unfinished or record.action in (PublishAction.RECALL, PublishAction.KEEP):
            seen = record.events
        for event in seen:
            if record.piece_id and event.get("id"):
                concerts.setdefault(record.piece_id, {})[event["id"]] = event
    recalled: Dict[str, bool] = {}
    kept: Dict[str, bool] = {}
    pending: Dict[str, Dict[str, None]] = {}
    for index, record in enumerate(records):
        if record.piece_id not in last:
            continue
        after = index > last[record.piece_id]
        if record.action is PublishAction.RECALL and record.failure is None:
            pending.pop(record.piece_id, None)
            if after:
                recalled[record.piece_id] = True
        elif record.action is PublishAction.RECALL:
            left = pending.setdefault(record.piece_id, {})
            for path in (score.get("file_path") for score in record.scores_removed):
                if path and path not in record.files_deleted:
                    left[path] = None
        elif record.action is PublishAction.KEEP and after:
            kept[record.piece_id] = True
    order = sorted(last, key=lambda piece_id: last[piece_id], reverse=True)
    return [
        PublishedPiece(
            piece_id, titles.get(piece_id, ""), recalled.get(piece_id, False), kept.get(piece_id, False),
            list(pending.get(piece_id, {})), list(concerts.get(piece_id, {}).values()),
        )
        for piece_id in order
    ]


def reminder_summary(records: Sequence[PublishRecord]) -> List[Dict[str, Any]]:
    """收回提醒記在偏好設定的摘要：已發譜、還沒收回也沒標不收回的曲目，與它們掛的公演結束時間

    Args:
        records: 發譜紀錄的全部列，依紀錄的順序

    Returns:
        每首一個 {piece_id, piece_title, ends_at（ISO 8601 字串的清單）}，依最後一次發譜由新到舊
    """
    return [
        {
            "piece_id": piece.piece_id, "piece_title": piece.piece_title,
            "ends_at": [concert["ends_at"] for concert in piece.concerts if concert.get("ends_at")],
        }
        for piece in published_pieces(records) if piece.outstanding
    ]


def due_reminders(summary: Any, now: datetime.datetime) -> List[str]:
    """依摘要，現在要提醒收回的曲名：有掛公演、掛的公演都已結束的那幾首

    摘要不是 reminder_summary 寫的樣子（例如偏好設定被手改壞）時，壞掉的那首不提醒。

    Args:
        summary: reminder_summary 寫進偏好設定、讀回來的值
        now: 現在的時間（帶時區）

    Returns:
        曲名，依摘要的順序
    """
    due = []
    for piece in summary if isinstance(summary, list) else []:
        if not isinstance(piece, dict) or not piece.get("piece_title") or not isinstance(piece.get("ends_at"), list):
            continue
        ends = [_moment(text) for text in piece["ends_at"]]
        if ends and all(end is not None and end <= now for end in ends):
            due.append(piece["piece_title"])
    return due


def _moment(text: Any) -> Optional[datetime.datetime]:
    """ISO 8601 的時間（帶時區）；不是這個樣子時為 None"""
    try:
        moment = datetime.datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else None
