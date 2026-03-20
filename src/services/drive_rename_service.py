# -*- coding: utf-8 -*-
"""
Drive 重新命名服務

將譜庫元資料與 Drive 檔案結構橋接至現有模板引擎，
透過 Drive API 直接在雲端重新命名檔案。
"""
from collections import defaultdict
from typing import Dict, List, Tuple
from core.models import DriveRenameEntry, FileInfo, Group
from core.catalog_models import Composer, PieceDetail
from core.template_engine import build_variables_for_file, substitute_template
from services.drive_service import DriveService


def build_groups_from_drive(
    drive: DriveService,
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
    instruments: List[str] = None,
) -> List[DriveRenameEntry]:
    """根據 Group 清單與模板產生 Drive 重新命名計畫

    Args:
        groups: Group 清單
        template: 命名模板
        instruments: 全域樂器表（未提供時使用各 Group 的樂器表）

    Returns:
        DriveRenameEntry 清單
    """
    plan = []
    for group in groups:
        effective_instruments = instruments or group.instruments or []
        if effective_instruments:
            group.instruments = list(effective_instruments)
        for i, file_info in enumerate(group.files):
            variables = build_variables_for_file(i, group, instruments)
            new_name = substitute_template(template, variables)
            if not new_name.lower().endswith(".pdf"):
                new_name += ".pdf"
            plan.append(DriveRenameEntry(
                file_id=file_info.original_path,
                original_name=file_info.display_name,
                new_name=new_name,
                group_name=group.name,
            ))
    return plan


def detect_conflicts(plan: List[DriveRenameEntry]) -> Dict[str, List[str]]:
    """偵測重新命名計畫中的檔名衝突

    Args:
        plan: 重新命名計畫

    Returns:
        衝突的新檔名（小寫）到原始檔名清單的對應
    """
    name_map = defaultdict(list)
    for entry in plan:
        key = (entry.group_name, entry.new_name.lower())
        name_map[key].append(entry.original_name)
    return {
        f"{k[0]}/{k[1]}": v for k, v in name_map.items() if len(v) > 1
    }


def execute_drive_rename(
    drive: DriveService,
    plan: List[DriveRenameEntry],
) -> Tuple[int, List[str]]:
    """執行 Drive 重新命名

    Args:
        drive: Drive 服務
        plan: 重新命名計畫

    Returns:
        (成功數, 失敗的檔名清單)
    """
    success = 0
    errors = []
    for entry in plan:
        if entry.original_name == entry.new_name:
            success += 1
            continue
        ok = drive.rename_file(entry.file_id, entry.new_name)
        if ok:
            success += 1
        else:
            errors.append(entry.original_name)
    return success, errors
