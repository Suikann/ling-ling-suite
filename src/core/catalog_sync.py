# -*- coding: utf-8 -*-
"""
編制寫回譜庫的格子與比對

存檔時，連到譜庫的群組相對於快照的改動（Project.catalog_changes）拆成一格一格（SyncItem）：
聲部的增刪各一格、順序一格、每個聲部的人數、時長、每種樂器的聲部組各一格。
寫入前重讀譜庫，每一格依譜庫現在的值分成：等於這次要寫的新值為已完成、等於快照的原值為寫入、
兩者都不是為衝突（review_item）。衝突要由使用者選邊（Decision）才寫：選「用我的」照寫，
選「用譜庫的」不寫，快照與群組跟著譜庫。要刪掉的聲部在譜庫已有 Drive 檔案時無法寫回（BLOCKED）。

這裡只有純邏輯；讀寫譜庫在 services/catalog_service.py 的 CatalogService.sync_instrumentation，
寫完交回專案在 Project.mark_catalog_synced。

使用範例：
    from core.catalog_sync import WriteSource, place_writes
    writes = place_writes(project)
    outcome = catalog.sync_instrumentation(writes, project.orchestra_type, email, WriteSource.SAVE, {})
    if outcome.conflicts: ...   # 衝突畫面選邊後帶著 decisions 再呼叫一次
    if outcome.written:
        project.mark_catalog_synced(outcome)
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, NamedTuple, Optional, Sequence, Set, Tuple

from core.catalog_models import SectionMapEntry
from core.models import CatalogChangeKind, CatalogRevert, Headcount, Project, format_duration
from core.sections import stored_section
from core.voice_names import VoiceKey, instrument_key, voice_key


class WriteSource(str, Enum):
    """編制寫入紀錄的來源；值是寫進試算表的寫法"""
    SAVE = "save"                      # 專案存檔
    CATALOG_WINDOW = "catalog_window"  # 譜庫視窗按編制表的儲存
    UNDO = "undo"                      # 撤銷存入、改連時撤掉編制


class SyncField(str, Enum):
    """一格改動的欄位；值是編制寫入紀錄裡「欄位」的寫法"""
    VOICE = "voice"          # 聲部的增刪
    ORDER = "order"          # 聲部的順序
    HEADCOUNT = "headcount"  # 一個聲部的人數
    DURATION = "duration"    # 時長
    SECTION = "section"      # 一種樂器在社團對照裡的聲部組


class Verdict(Enum):
    """一格在寫入前比對的結果"""
    WRITE = "write"        # 譜庫等於快照的原值（或使用者選「用我的」）：寫入
    DONE = "done"          # 譜庫已經等於要寫的新值：不寫，不算衝突
    CONFLICT = "conflict"  # 兩者都不是，還沒選邊：整次不寫
    CATALOG = "catalog"    # 使用者選「用譜庫的」：不寫，快照與群組跟著譜庫
    BLOCKED = "blocked"    # 要刪掉的聲部在譜庫已有 Drive 檔案：無法寫回，要到譜庫視窗處理
    KEPT = "kept"          # 撤掉編制時譜庫已不是那次寫的值（之後又被改過）：不動，不算衝突


class Choice(Enum):
    """衝突的一格選哪一邊"""
    MINE = "mine"
    CATALOG = "catalog"


class SyncItem(NamedTuple):
    """一格改動

    Attributes:
        field: 欄位
        voice: 聲部（增刪、人數）或樂器的一個聲部寫法（聲部組）；順序與時長為空字串
        old: 快照裡的值：增刪為有沒有這個聲部（bool），順序為聲部名稱的 tuple（只含快照與群組都有的聲部），
            人數為 Headcount（快照沒有這個聲部時為未填），時長為秒數或 None，聲部組為名稱（社團對照沒有時為空字串）
        new: 群組目前的值，形式同 old
    """
    field: SyncField
    voice: str
    old: Any
    new: Any

    @property
    def key(self) -> Tuple[SyncField, Any]:
        """認這一格的鍵：聲部以同一聲部、聲部組以同一樂器的規則比對，名稱的其他寫法也是同一格"""
        if self.field is SyncField.SECTION:
            return self.field, instrument_key(self.voice)
        if self.field in (SyncField.VOICE, SyncField.HEADCOUNT):
            return self.field, voice_key(self.voice)
        return self.field, ""


class Decision(NamedTuple):
    """使用者在衝突畫面對一格的選擇，連同當時看到的譜庫值（譜庫之後又變了就要再問一次）"""
    choice: Choice
    theirs: Any


class ItemReview(NamedTuple):
    """一格比對的結果

    Attributes:
        item: 這一格
        theirs: 譜庫現在的值，形式同 SyncItem.old；人數在譜庫已沒有這個聲部時為 None
        verdict: 比對結果
    """
    item: SyncItem
    theirs: Any
    verdict: Verdict


@dataclass
class PlaceWrite:
    """一個群組要寫回它連到的那處的改動

    Attributes:
        group_id: 群組 ID
        group_name: 群組名稱（寫進編制寫入紀錄）
        edition_id: 版本 ID
        movement_id: 樂章 ID；整首為空字串
        voices: 群組目前的聲部清單（新增的聲部依它排序）；撤掉時為空清單
        items: 一格一格的改動
        confirm: 寫入前先在衝突畫面確認（開啟專案時選了「用專案的（存檔時寫回）」，CatalogLink.confirm_voices）
        revert: 改連後撤掉這個群組寫進原處的編制（CatalogRevert）：原值是那次寫的值、新值是撤掉後的值；
            譜庫已不是那次寫的值時不動（Verdict.KEPT），編制寫入紀錄的來源記撤銷，不改群組與快照
    """
    group_id: str
    group_name: str
    edition_id: str
    movement_id: str
    voices: List[str]
    items: List[SyncItem]
    confirm: bool = False
    revert: bool = False


@dataclass
class PlaceReview:
    """一處寫入前的比對結果

    Attributes:
        write: 要寫的改動
        items: 每一格的比對結果，依 write.items 的順序
        filed: 譜庫裡這處已有 Drive 檔案的聲部（寫入前重讀到的）
        other_pieces: 群組各樂器在譜庫裡被其他曲目用到幾首（樂器的一個聲部寫法 → 曲目數）
    """
    write: PlaceWrite
    items: List[ItemReview]
    filed: List[str] = field(default_factory=list)
    other_pieces: Dict[str, int] = field(default_factory=dict)


@dataclass
class SyncOutcome:
    """一次寫回的結果

    Attributes:
        places: 每一處的比對結果
        written: 是否已寫入；還有沒選邊的衝突時為 False，譜庫沒有任何變動
    """
    places: List[PlaceReview]
    written: bool

    @property
    def conflicts(self) -> List[Tuple[PlaceReview, ItemReview]]:
        """還沒選邊的衝突"""
        return self._with(Verdict.CONFLICT)

    @property
    def blocked(self) -> List[Tuple[PlaceReview, ItemReview]]:
        """無法寫回的格子（要刪掉的聲部在譜庫已有 Drive 檔案）"""
        return self._with(Verdict.BLOCKED)

    def _with(self, verdict: Verdict) -> List[Tuple[PlaceReview, ItemReview]]:
        """比對結果是 verdict 的格子，連同所在的那處"""
        return [(place, review) for place in self.places for review in place.items if review.verdict is verdict]


def place_writes(project: Project) -> List[PlaceWrite]:
    """專案裡連到譜庫、有待寫入改動的群組各自要寫回的改動（依群組順序），每個群組之後接著它還沒寫的撤掉

    聲部清單的改動拆成每個新增或刪掉的聲部各一格，加上快照與群組都有的聲部順序變了時一格。
    撤掉是要刪的聲部各一格（有→沒有）、要改回的人數各一格（那次寫的→原值）。
    """
    writes = []
    for group in project.groups:
        link = group.catalog_link
        if link is None:
            continue
        items = _split_changes(project.catalog_changes(group))
        if items:
            writes.append(PlaceWrite(
                group.id, group.name, link.edition_id, link.movement_id, list(group.instruments), items,
                confirm=link.confirm_voices,
            ))
        writes += [revert_write(group.id, group.name, revert) for revert in link.reverts]
    return writes


def revert_write(group_id: str, group_name: str, revert: CatalogRevert) -> PlaceWrite:
    """撤掉一個群組寫進一處的編制要寫的格子：要刪的聲部各一格（有→沒有）、要改回的人數各一格（那次寫的→原值）

    改連後存檔時（place_writes）與撤銷存入的「一起撤掉編制」共用。

    Args:
        group_id: 群組 ID（衝突選擇的鍵；撤掉沒有衝突）
        group_name: 群組名稱（寫進編制寫入紀錄）
        revert: 要撤掉的（core/catalog_revert.py 的 pending_revert）
    """
    items = [SyncItem(SyncField.VOICE, voice, True, False) for voice in revert.remove]
    items += [SyncItem(SyncField.HEADCOUNT, voice, written, original) for voice, written, original in revert.restore]
    return PlaceWrite(group_id, group_name, revert.edition_id, revert.movement_id, [], items, revert=True)


def section_map_items(entries: Sequence[SectionMapEntry], chosen: Mapping[str, str]) -> List[SyncItem]:
    """譜庫視窗編輯聲部組對照時改過的樂器，一種樂器一格（依 entries 的順序）

    Args:
        entries: 讀到的這一套對照（CatalogService.section_map）
        chosen: 編輯後的聲部組：樂器（entries 的寫法）→ 聲部組，還沒歸類為空字串；沒列出的樂器不變

    Returns:
        聲部組的格子：原值為讀到的聲部組，新值為編輯後的
    """
    return [
        SyncItem(SyncField.SECTION, entry.instrument, entry.section, chosen[entry.instrument])
        for entry in entries if chosen.get(entry.instrument, entry.section) != entry.section
    ]


def _split_changes(changes: Iterable) -> List[SyncItem]:
    """把 Project.catalog_changes 的改動拆成一格一格"""
    items = []
    for change in changes:
        if change.kind is CatalogChangeKind.VOICES:
            items.extend(_voice_items(list(change.old), list(change.new)))
        elif change.kind is CatalogChangeKind.HEADCOUNT:
            old = change.old if change.old is not None else Headcount()
            items.append(SyncItem(SyncField.HEADCOUNT, change.voice, old, change.new))
        elif change.kind is CatalogChangeKind.DURATION:
            items.append(SyncItem(SyncField.DURATION, "", change.old, change.new))
        elif change.kind is CatalogChangeKind.SECTION:
            items.append(SyncItem(SyncField.SECTION, change.voice, change.old, change.new))
    return items


def _voice_items(old: List[str], new: List[str]) -> List[SyncItem]:
    """聲部清單的改動：刪掉的、新增的聲部各一格，兩邊都有的聲部順序變了時再一格"""
    old_keys = {voice_key(name) for name in old}
    new_keys = {voice_key(name) for name in new}
    items = [SyncItem(SyncField.VOICE, name, True, False) for name in old if voice_key(name) not in new_keys]
    items += [SyncItem(SyncField.VOICE, name, False, True) for name in new if voice_key(name) not in old_keys]
    kept_old = tuple(name for name in old if voice_key(name) in new_keys)
    kept_new = tuple(name for name in new if voice_key(name) in old_keys)
    if [voice_key(n) for n in kept_old] != [voice_key(n) for n in kept_new]:
        items.append(SyncItem(SyncField.ORDER, "", kept_old, kept_new))
    return items


class CurrentPlace(NamedTuple):
    """寫入前重讀到的一處（只有比對要用的）

    Attributes:
        voices: 聲部名稱與人數，依譜庫的順序（不含總譜列）
        duration_seconds: 時長；未填為 None
        sections: 社團對照裡專案樂團類型的部分（樂器 → 聲部組）
        filed: 已有 Drive 檔案的聲部
    """
    voices: List[Tuple[str, Headcount]]
    duration_seconds: Optional[int]
    sections: Dict[str, str]
    filed: List[str]


def review_items(
    write: PlaceWrite, current: CurrentPlace, decisions: Dict[Tuple[SyncField, Any], Decision],
) -> List[ItemReview]:
    """比對一處的每一格：譜庫現在等於新值為已完成、等於原值為寫入、都不是為衝突（有選邊時照選的）

    撤掉（PlaceWrite.revert）沒有衝突：都不是時不動（Verdict.KEPT），不看選邊。

    Args:
        write: 要寫的改動
        current: 寫入前重讀到的那處
        decisions: 這處各格的選擇（以 SyncItem.key 為鍵）；選的時候看到的譜庫值與現在不同時不算數，再列為衝突

    Returns:
        依 write.items 順序的比對結果
    """
    adding = {voice_key(i.voice) for i in write.items if i.field is SyncField.VOICE and i.new}
    if write.revert:
        reviews = [_review(item, current, adding, None) for item in write.items]
        return [r._replace(verdict=Verdict.KEPT) if r.verdict is Verdict.CONFLICT else r for r in reviews]
    return [_review(item, current, adding, decisions.get(item.key)) for item in write.items]


def _review(item: SyncItem, current: CurrentPlace, adding: Set[VoiceKey], decision: Optional[Decision]) -> ItemReview:
    """比對一格"""
    voices = {voice_key(name): headcount for name, headcount in current.voices}
    if item.field is SyncField.VOICE:
        theirs = voice_key(item.voice) in voices
        if theirs == item.new:
            return ItemReview(item, theirs, Verdict.DONE)
        if not item.new and voice_key(item.voice) in {voice_key(name) for name in current.filed}:
            return ItemReview(item, theirs, Verdict.BLOCKED)
        return ItemReview(item, theirs, Verdict.WRITE)
    if item.field is SyncField.ORDER:
        keys = {voice_key(name) for name in item.new} & set(voices)
        theirs = tuple(name for name, _ in current.voices if voice_key(name) in keys)
        mine = [voice_key(name) for name in item.new if voice_key(name) in keys]
        original = [voice_key(name) for name in item.old if voice_key(name) in keys]
        now = [voice_key(name) for name in theirs]
        if now == mine:
            return ItemReview(item, theirs, Verdict.DONE)
        if now == original:
            return ItemReview(item, theirs, Verdict.WRITE)
        same = decision is not None and [voice_key(name) for name in decision.theirs] == now
        return ItemReview(item, theirs, _decided(decision) if same else Verdict.CONFLICT)
    if item.field is SyncField.HEADCOUNT:
        key = voice_key(item.voice)
        if key not in voices and key in adding:
            return ItemReview(item, None, Verdict.WRITE)
        theirs = voices.get(key)
    elif item.field is SyncField.DURATION:
        theirs = current.duration_seconds
    else:
        theirs = stored_section(item.voice, current.sections)
    if theirs == item.new:
        return ItemReview(item, theirs, Verdict.DONE)
    if theirs == item.old:
        return ItemReview(item, theirs, Verdict.WRITE)
    same = decision is not None and decision.theirs == theirs
    return ItemReview(item, theirs, _decided(decision) if same else Verdict.CONFLICT)


def _decided(decision: Decision) -> Verdict:
    """選了邊的衝突：用我的就寫入，用譜庫的就不寫"""
    return Verdict.WRITE if decision.choice is Choice.MINE else Verdict.CATALOG


def value_text(sync_field: SyncField, value: Any) -> str:
    """一格的值寫成文字（編制寫入紀錄與衝突畫面）：人數用照存的寫法（stored_text），時長為秒數，順序以「、」連接"""
    if sync_field is SyncField.VOICE:
        return "" if not value else str(value)
    if sync_field is SyncField.ORDER:
        return "、".join(value or ())
    if sync_field is SyncField.HEADCOUNT:
        return value.stored_text() if value is not None else ""
    if sync_field is SyncField.DURATION:
        return str(value) if value else ""
    return value or ""


def display_text(sync_field: SyncField, value: Any) -> str:
    """一格的值在畫面上的寫法：時長寫成「分:秒」，其餘同 value_text"""
    if sync_field is SyncField.DURATION:
        return format_duration(value)
    return value_text(sync_field, value)


def final_voices(write: PlaceWrite, reviews: Sequence[ItemReview]) -> List[str]:
    """寫完後群組的聲部清單：群組目前的清單，再照選「用譜庫的」的格子調整

    順序選「用譜庫的」時，兩邊都有的聲部照譜庫的相對順序；譜庫已刪掉的聲部，人數選「用譜庫的」時拿掉。
    """
    dropped = {
        voice_key(r.item.voice) for r in reviews
        if r.item.field is SyncField.HEADCOUNT and r.verdict is Verdict.CATALOG and r.theirs is None
    }
    voices = [name for name in write.voices if voice_key(name) not in dropped]
    order = next((r for r in reviews if r.item.field is SyncField.ORDER), None)
    if order is not None and order.verdict is Verdict.CATALOG:
        voices = reorder_like(voices, order.theirs)
    return voices


def reorder_like(names: Sequence[str], reference: Sequence[str]) -> List[str]:
    """names 裡與 reference 同一聲部的那些，依 reference 的相對順序填回它們原本佔的位置，其餘不動"""
    keys = [voice_key(name) for name in reference]
    slots = [i for i, name in enumerate(names) if voice_key(name) in keys]
    ordered = sorted((names[i] for i in slots), key=lambda name: keys.index(voice_key(name)))
    result = list(names)
    for slot, name in zip(slots, ordered):
        result[slot] = name
    return result
