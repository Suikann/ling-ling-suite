# -*- coding: utf-8 -*-
"""
路徑同一性

全程式比對兩條路徑是否指向同一個檔案只用這裡的定義：正規化成絕對路徑
（去掉多餘分隔符與 `.`、`..`）、不分大小寫。沒有本機路徑可比時（Drive 上同一資料夾內的檔名），
用同一套不分大小寫的規則比對名稱。只用於比對，讀寫檔案仍用原本的寫法。

使用範例：
    from core.paths import name_key, path_key, same_path
    same_path("Flute.pdf", os.path.abspath("FLUTE.pdf"))  # True
    in_use = {path_key(p) for p in paths}
    name_key("Flute.pdf") == name_key("FLUTE.pdf")  # True
    is_inside("out/Sym/01.pdf", "OUT")  # True
"""
import os


def name_key(name: str) -> str:
    """同一資料夾內名稱的比對鍵：兩個名稱的鍵相同即視為撞名（不分大小寫）"""
    return name.lower()


def path_key(path: str) -> str:
    """路徑的比對鍵：兩條路徑的鍵相同即視為同一條路徑

    Args:
        path: 絕對或相對路徑（相對路徑以目前工作目錄為基準）

    Returns:
        正規化後的絕對路徑，全部小寫
    """
    return name_key(os.path.normcase(os.path.abspath(path)))


def same_path(a: str, b: str) -> bool:
    """兩條路徑是否指向同一個檔案"""
    return path_key(a) == path_key(b)


def is_inside(path: str, folder: str) -> bool:
    """path 是否在 folder 之內（任意深度，不含 folder 本身）

    Args:
        path: 要判斷的路徑
        folder: 資料夾

    Returns:
        兩者正規化後，folder 是 path 的上層目錄時為 True；不同磁碟時為 False
    """
    path, folder = path_key(path), path_key(folder)
    try:
        return path != folder and os.path.commonpath([path, folder]) == folder
    except ValueError:
        return False
