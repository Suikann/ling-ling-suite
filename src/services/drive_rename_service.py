# -*- coding: utf-8 -*-
"""
Drive 重新命名服務

把譜庫元資料與 Drive 資料夾結構轉成群組，計畫與撞名偵測經過命名模組（與本機重新命名同一套檔名），
再透過 Drive 服務（adapter）在雲端重新命名。計畫與撞名偵測不需要 Google 用戶端程式庫。

使用範例：
    from services.drive_rename_service import generate_drive_rename_plan
    plan = generate_drive_rename_plan(groups, "{序號}. {樂器}.pdf", ["Flute", "Oboe"])
    if not plan.conflicts:
        execute_drive_rename(drive, plan.entries)
"""
from collections import defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional, Sequence, Tuple
from core.models import DriveRenameEntry, FileInfo, Group
from core.catalog_models import Composer, PieceDetail
from core.naming import NamingSettings, name_group
from core.paths import name_key

if TYPE_CHECKING:
    from services.drive_service import DriveService


@dataclass(frozen=True)
class DriveRenamePlan:
    """Drive 重新命名計畫

    Attributes:
        entries: 要重新命名的檔案（各群組依序：總譜在前，接著依序的分譜；多於聲部數的分譜不在其中）
        conflicts: 撞名的檔案，每組是重新命名後在同一群組（同一個 Drive 資料夾）內同名的檔案 ID；有撞名時不能執行
    """
    entries: List[DriveRenameEntry] = field(default_factory=list)
    conflicts: List[List[str]] = field(default_factory=list)


def build_groups_from_drive(
    drive: "DriveService",
    folder_id: str,
    detail: PieceDetail,
    composer: Composer = None,
) -> List[Group]:
    """從 Drive 資料夾結構建立 Group 清單

    有子資料夾時，每個子資料夾建立一個 Group。
    無子資料夾時，所有 PDF 建立一個 Group。

    Args:
        drive: Drive 服務
        folder_id: 版本的 Drive 資料夾 ID
        detail: 曲目完整資訊
        composer: 作曲家（可選）

    Returns:
        Group 清單，每個 Group 的 files 中 original_path 存放 Drive file ID
    """
    piece = detail.piece
    composer_name = ""
    if composer:
        composer_name = composer.name_short or composer.name
    subfolders = drive.list_subfolders(folder_id)
    groups = []
    if subfolders:
        for i, sf in enumerate(subfolders):
            pdfs = drive.list_pdfs_in_folder(sf["id"])
            files = [
                FileInfo(original_path=p["id"], display_name=p["name"])
                for p in pdfs
            ]
            group = Group(
                name=sf["name"],
                files=files,
                piece_name=piece.title,
                composer=composer_name,
                genre=piece.genre,
                movement_number=str(i + 1),
                movement_name=sf["name"],
            )
            groups.append(group)
        root_pdfs = drive.list_pdfs_in_folder(folder_id)
        if root_pdfs:
            files = [
                FileInfo(original_path=p["id"], display_name=p["name"])
                for p in root_pdfs
            ]
            group = Group(
                name=piece.title,
                files=files,
                piece_name=piece.title,
                composer=composer_name,
                genre=piece.genre,
            )
            groups.append(group)
    else:
        pdfs = drive.list_pdfs_in_folder(folder_id)
        files = [
            FileInfo(original_path=p["id"], display_name=p["name"])
            for p in pdfs
        ]
        group = Group(
            name=piece.title,
            files=files,
            piece_name=piece.title,
            composer=composer_name,
            genre=piece.genre,
        )
        groups.append(group)
    return groups


def generate_drive_rename_plan(
    groups: List[Group],
    template: str,
    voices: Optional[Sequence[str]] = None,
) -> DriveRenamePlan:
    """根據 Group 清單與命名格式產生 Drive 重新命名計畫；檔名由命名模組決定，不改寫群組

    Args:
        groups: Group 清單
        template: 命名格式
        voices: 明確輸入的樂器表，優先於各群組的樂器表；未提供或為空時用各群組的樂器表

    Returns:
        重新命名計畫
    """
    settings = NamingSettings(template)
    entries = []
    conflicts = []
    for group in groups:
        naming = name_group(group, settings, voices)
        group_entries = [
            DriveRenameEntry(
                file_id=named.file.original_path,
                original_name=named.file.display_name,
                new_name=named.name.file_name,
                group_name=group.name,
            )
            for named in naming.files
        ]
        entries += group_entries
        conflicts += _conflicts_in_folder(group_entries, naming.extra_files)
    return DriveRenamePlan(entries=entries, conflicts=conflicts)


def _conflicts_in_folder(entries: List[DriveRenameEntry], unchanged: List[FileInfo]) -> List[List[str]]:
    """一個群組（同一個 Drive 資料夾）重新命名後同名的檔案

    計畫內的檔案用新檔名，不改名的檔案維持原名，名稱以 name_key 比對（不分大小寫，與本機一致）；
    只有不改名的檔案彼此同名時不算（Drive 上本來就這樣，不是這次造成的）。

    Args:
        entries: 這個群組要重新命名的檔案
        unchanged: 這個群組不改名的檔案（多於聲部數的分譜）

    Returns:
        每組同名的檔案 ID（計畫內的在前），每組至少兩個
    """
    by_name = defaultdict(list)
    for entry in entries:
        by_name[name_key(entry.new_name)].append(entry.file_id)
    for file in unchanged:
        ids = by_name.get(name_key(file.display_name))
        if ids is not None:
            ids.append(file.original_path)
    return [ids for ids in by_name.values() if len(ids) > 1]


def execute_drive_rename(
    drive: "DriveService",
    entries: List[DriveRenameEntry],
) -> Tuple[int, List[str]]:
    """執行 Drive 重新命名

    Args:
        drive: Drive 服務
        entries: 重新命名計畫中要重新命名的檔案

    Returns:
        (成功數, 失敗的檔名清單)
    """
    success = 0
    errors = []
    for entry in entries:
        if entry.original_name == entry.new_name:
            success += 1
            continue
        ok = drive.rename_file(entry.file_id, entry.new_name)
        if ok:
            success += 1
        else:
            errors.append(entry.original_name)
    return success, errors
