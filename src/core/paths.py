# -*- coding: utf-8 -*-
"""
路徑同一性

全程式比對兩條路徑是否指向同一個檔案只用這裡的定義：正規化成絕對路徑
（去掉多餘分隔符與 `.`、`..`）、不分大小寫。只用於比對，讀寫檔案仍用原本的寫法。

使用範例：
    from core.paths import path_key, same_path
    same_path("Flute.pdf", os.path.abspath("FLUTE.pdf"))  # True
    in_use = {path_key(p) for p in paths}
"""
import os


def path_key(path: str) -> str:
    """路徑的比對鍵：兩條路徑的鍵相同即視為同一條路徑

    Args:
        path: 絕對或相對路徑（相對路徑以目前工作目錄為基準）

    Returns:
        正規化後的絕對路徑，全部小寫
    """
    return os.path.normcase(os.path.abspath(path)).lower()


def same_path(a: str, b: str) -> bool:
    """兩條路徑是否指向同一個檔案"""
    return path_key(a) == path_key(b)
