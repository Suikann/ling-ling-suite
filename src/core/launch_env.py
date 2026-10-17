# -*- coding: utf-8 -*-
"""
啟動與平台設定

依環境變數、平台與是否為打包版，判斷啟動時要補的環境變數、開啟外部程式時子行程的環境、
內附 Google 用戶端檔的位置，以及版號；只做判斷，不讀寫 os.environ、不碰檔案（版號檔由呼叫端傳入的函式讀）。

使用範例：
    from core.launch_env import qt_environment_defaults
    os.environ.update(qt_environment_defaults(os.environ, sys.platform))
"""
import os
from typing import Callable, Dict, Mapping, Optional

from core.catalog_constants import CLIENT_SECRETS_FILE
from core.constants import VERSION_FILE

# pip 裝的 Qt 沒附 fcitx 輸入法外掛：環境有 QT_IM_MODULE=fcitx 時 Qt 會找不到外掛、打不出中文。
# QT_IM_MODULES（Qt 6.7 起）優先於 QT_IM_MODULE，依序試 Wayland 原生輸入、fcitx、ibus（XWayland 下接 fcitx5 的 IBus 相容介面）
_IM_MODULES_VAR = "QT_IM_MODULES"
_LINUX_IM_MODULES = "wayland;fcitx;ibus"
# PyInstaller 打包版在 Linux 上把 LD_LIBRARY_PATH 指向打包的函式庫，原值存在 LD_LIBRARY_PATH_ORIG（原本沒設就沒有）
_LIBRARY_PATH_VAR = "LD_LIBRARY_PATH"
_ORIGINAL_LIBRARY_PATH_VAR = "LD_LIBRARY_PATH_ORIG"


def _is_linux(platform: str) -> bool:
    """平台代碼（sys.platform）是否為 Linux"""
    return platform.startswith("linux")


def qt_environment_defaults(environ: Mapping[str, str], platform: str) -> Dict[str, str]:
    """建立 QApplication 前要補設的環境變數

    Linux 上 QT_IM_MODULES 沒設時補成 wayland;fcitx;ibus；使用者自己設了就不動。不碰 QT_QPA_PLATFORM。

    Args:
        environ: 目前的環境變數
        platform: 平台代碼（sys.platform）

    Returns:
        要設定的環境變數；不需要補時為空
    """
    if _is_linux(platform) and _IM_MODULES_VAR not in environ:
        return {_IM_MODULES_VAR: _LINUX_IM_MODULES}
    return {}


def external_process_environment(environ: Mapping[str, str], platform: str, frozen: bool) -> Dict[str, str]:
    """開啟外部程式（瀏覽器、PDF 閱讀器、檔案管理員）時子行程的環境

    Linux 打包版把 LD_LIBRARY_PATH 還原成 LD_LIBRARY_PATH_ORIG，沒有就移除，
    外部程式才不會載到打包的 libstdc++、fontconfig。從原始碼執行與 Windows 照原樣沿用。

    Args:
        environ: 目前的環境變數（不會被修改）
        platform: 平台代碼（sys.platform）
        frozen: 是否為打包版（sys.frozen）

    Returns:
        子行程的環境變數
    """
    env = dict(environ)
    if not (_is_linux(platform) and frozen):
        return env
    original = env.get(_ORIGINAL_LIBRARY_PATH_VAR)
    if original is None:
        env.pop(_LIBRARY_PATH_VAR, None)
    else:
        env[_LIBRARY_PATH_VAR] = original
    return env


def bundled_client_secrets_path(frozen: bool, resource_dir: str, repo_root: str) -> str:
    """內附 Google 用戶端檔（client_secret.json）的位置

    打包版讀 PyInstaller 的資源目錄（sys._MEIPASS），從原始碼執行讀 repo 根目錄。
    不讀 exe 旁邊：AppImage 的程式資料夾是唯讀的，放在旁邊覆蓋的做法不保留。

    Args:
        frozen: 是否為打包版（sys.frozen）
        resource_dir: 打包版的資源目錄（sys._MEIPASS）；從原始碼執行時不使用
        repo_root: repo 根目錄（src/ 的上層）

    Returns:
        用戶端檔的路徑
    """
    return os.path.join(resource_dir if frozen else repo_root, CLIENT_SECRETS_FILE)


def app_version(frozen: bool, resource_dir: str, read_text: Callable[[str], str]) -> Optional[str]:
    """這次執行的版號；開發版為 None

    打包版讀資源目錄（sys._MEIPASS）裡 CD 寫入的版號檔；讀不到、內容是空的，或從原始碼執行時都是開發版。

    Args:
        frozen: 是否為打包版（sys.frozen）
        resource_dir: 打包版的資源目錄（sys._MEIPASS）；從原始碼執行時不使用
        read_text: 讀出文字檔的內容；檔案讀不到時拋出 OSError

    Returns:
        版號，例如 v1.2.0；開發版為 None
    """
    if not frozen:
        return None
    try:
        version = read_text(os.path.join(resource_dir, VERSION_FILE)).strip()
    except (OSError, UnicodeDecodeError):
        return None
    return version or None
