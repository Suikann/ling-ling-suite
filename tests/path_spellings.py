# -*- coding: utf-8 -*-
"""
同一個檔案的不同寫法

路徑比對的測試共用：同一條路徑以大小寫不同、相對路徑、多餘分隔符表示時，
各模組都應判為同一條路徑。

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
        path: 目前工作目錄之下的絕對路徑（相對路徑以目前工作目錄為基準）

    Returns:
        寫法名稱到該寫法的對應
    """
    directory, name = os.path.split(path)
    return {
        "大小寫不同": os.path.join(directory, name.swapcase()),
        "相對路徑": os.path.relpath(path),
        "多餘分隔符": directory + os.sep + os.sep + os.curdir + os.sep + name,
    }
