# -*- coding: utf-8 -*-
"""
發譜紀錄的一列

譜庫的「發譜紀錄」工作表（core/catalog_constants.py 的 SHEET_PUBLISH_LOG）只新增、不修改：每次發譜在第 1 段之前
先寫「開始發譜」（PublishAction.START），結束後補「發譜結果」（PublishAction.RESULT），兩者以同一個發譜編號串起來，
每個群組各一列。撤回、收回與不收回之後也寫在這裡（各自的動作值）。

一列記這個群組對到哪首 HuaXia 曲目、新建的曲目與聲部、人數與聲部組的原值與新值、傳了／登記了／撤掉登記的／
刪了哪些檔（Storage 路徑）、掛了哪些場次與結束時間、沒發成的原因。清單與物件的欄以 JSON 寫進一格（PublishRecord.cells），
讀回時還原（PublishRecord.from_cells）；各欄的內容：

- 開始發譜：要做的。new_piece 是要新建的曲目欄位（title、composer、duration_min、ensemble）；parts_created 是要新建的聲部
  （name、needed、needed_min、section、sort）；parts_updated 是要覆寫的聲部（id、name，改的欄位各是［原值，新值］）；
  files_uploaded 是要傳的 Storage 路徑；scores_added 是要登記的（voice、part_id 或 new_part、file_path）；
  scores_removed 是要撤掉登記的（id、part_id、file_path）；files_deleted 是清舊檔要刪的；events 是要掛的場次（id、title、ends_at）。
- 發譜結果：做到的。new_piece 只在這次真的新建了曲目時有；parts_created、scores_added、scores_removed 是 HuaXia 發譜函式
  回報的（帶 HuaXia 的編號，撤回只動這些）；parts_updated 是切換成功時寫進去的；files_uploaded 是這次真的傳上去的；
  files_deleted 是真的刪掉的；events 是這次新掛上的。沒發成時 failure 寫出在哪一段（stage）與原因（code、detail），
  其他欄只有 files_uploaded（傳上去了、沒登記的檔）。

使用範例：
    from core.publish_log import PublishAction, PublishRecord
    record = PublishRecord(PublishAction.START, group_id=group.id, group=group.name, piece_id=piece_id)
    cells = record.cells()
"""
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional


class PublishAction(Enum):
    """發譜紀錄一列的動作；值寫進工作表"""

    START = "start"    # 開始發譜（第 1 段之前）
    RESULT = "result"  # 發譜結果（結束後）


# 以 JSON 寫進一格的欄
_JSON_FIELDS = (
    "new_piece", "parts_created", "parts_updated", "files_uploaded", "scores_added", "scores_removed",
    "files_deleted", "events", "failure",
)


@dataclass(frozen=True)
class PublishRecord:
    """發譜紀錄的一列：一次發譜（或之後的撤回、收回）裡的一個群組

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
    scores_removed: List[Dict[str, Any]] = field(default_factory=list)
    files_deleted: List[str] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    failure: Optional[Dict[str, Any]] = None
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
