# -*- coding: utf-8 -*-
"""
從譜庫下載的計畫

從譜庫下載有兩個入口，共用同一套下載與 MD5 核對（services/download_service.py）：
- 下載回原位：群組裡只在譜庫的檔（Group.has_catalog_copy 加上本機找不到）放回清除前的路徑與檔名。
  要下載的是群組連到的那處現在登記給這個聲部（總譜為總譜列）的 Drive 檔，所以清除後又重新存入過時下載的是最新的；
  那處已經沒登記、或登記的檔已不在 Drive、在垃圾桶裡的，列為譜庫裡沒有（restore_items）。
- 一般下載：譜庫視窗版本頁勾選的檔或整個版本，下載到自選的資料夾。整個版本保留子資料夾結構，
  只勾幾份時都放在那個資料夾裡（folder_items）。Drive 上的資料夾名稱與檔名照檔名清理的規則換掉非法字元，
  「.」「..」換成底線，放的位置不會跑出自選的資料夾。

這裡是純邏輯：每份要下載的 Drive 檔與要放的本機路徑。讀寫檔案與碰 Google 在下載服務與譜庫服務。

使用範例：
    from core.catalog_download import folder_items, restore_items
    items, unavailable = restore_items(groups, places)
    items = folder_items(page.pdfs, "/home/me/譜", keep_folders=True, piece_title="春江花月夜")
"""
import os
from dataclasses import dataclass
from typing import List, Mapping, Sequence, Tuple

from core.catalog_models import DriveFile, FolderPdf
from core.clear_local import ClearSources
from core.deposit import PlaceFiles
from core.filename import sanitize_filename

# 清理後是空的、或是「.」「..」的資料夾名稱與檔名改用它
_SAFE_FALLBACK = "_"


@dataclass(frozen=True)
class DownloadItem:
    """要下載的一份檔

    Attributes:
        file: Drive 上的檔（以它的 md5 核對下載到的內容）
        target: 要放的本機路徑
        piece_title: 哪首曲目的，記進紀錄檔
    """
    file: DriveFile
    target: str
    piece_title: str = ""


def restore_items(
    groups: Sequence[ClearSources], places: Mapping[Tuple[str, str], PlaceFiles],
) -> Tuple[List[DownloadItem], List[str]]:
    """下載回原位要下載的檔：群組裡每份只在譜庫的檔對到那處現在登記給它的聲部的 Drive 檔

    Args:
        groups: 各群組只在譜庫的檔（聲部、原本的路徑）
        places: 各群組連到的那處現在的登記：（版本 ID，樂章 ID）→ 那處

    Returns:
        （要下載的檔，放回原位的路徑；譜庫裡已經沒有的檔的本機路徑）
    """
    items: List[DownloadItem] = []
    unavailable: List[str] = []
    for group in groups:
        link = group.link
        place = places.get((link.edition_id, link.movement_id)) if link is not None else None
        for source in group.sources:
            file = place.registered_file(source.voice) if place is not None and source.voice is not None else None
            if file is None:
                unavailable.append(source.path)
            else:
                items.append(DownloadItem(file, source.path, link.piece_title))
    return items, unavailable


def folder_items(
    pdfs: Sequence[FolderPdf], destination: str, keep_folders: bool, piece_title: str = "",
) -> List[DownloadItem]:
    """一般下載要下載的檔：放進自選的資料夾，整個版本時保留版本資料夾底下的子資料夾

    Args:
        pdfs: 版本資料夾裡要下載的 PDF（連同在版本資料夾裡的路徑）
        destination: 自選的資料夾
        keep_folders: 是否保留子資料夾結構（整個版本時）
        piece_title: 哪首曲目的，記進紀錄檔

    Returns:
        依 pdfs 的順序
    """
    items = []
    for pdf in pdfs:
        parts = pdf.path.split("/") if keep_folders else [pdf.file.name]
        items.append(DownloadItem(pdf.file, os.path.join(destination, *map(_safe_name, parts)), piece_title))
    return items


def _safe_name(name: str) -> str:
    """Drive 上的資料夾名稱或檔名換成本機能用、不會往上跳的名稱"""
    cleaned = sanitize_filename(name, fallback=_SAFE_FALLBACK)
    return _SAFE_FALLBACK if cleaned in (os.curdir, os.pardir) else cleaned
