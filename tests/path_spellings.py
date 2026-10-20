# -*- coding: utf-8 -*-
"""
同一個檔案的不同寫法

路徑比對的測試共用：同一條路徑以大小寫不同、相對路徑、多餘分隔符表示時，
各模組都應判為同一條路徑。Windows 上路徑跟目前工作目錄在不同磁碟時寫不出相對路徑，只給另外兩種寫法。

使用範例：
    from path_spellings import spellings
    for label, spelled in spellings(path).items():
        ...
"""
import os
from typing import Dict


def spellings(path: str) -> Dict[str, str]:
    """同一個檔案的其他寫法

    Args:
        path: 絕對路徑（相對路徑以目前工作目錄為基準）

    Returns:
        寫法名稱到該寫法的對應；跟目前工作目錄在不同磁碟時沒有「相對路徑」
    """
    directory, name = os.path.split(path)
    written = {
        "大小寫不同": os.path.join(directory, name.swapcase()),
        "多餘分隔符": directory + os.sep + os.sep + os.curdir + os.sep + name,
    }
    try:
        written["相對路徑"] = os.path.relpath(path)
    except ValueError:  # Windows：不同磁碟之間沒有相對路徑
        pass
    return written
