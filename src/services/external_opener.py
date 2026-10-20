# -*- coding: utf-8 -*-
"""
外部開啟

以作業系統的預設程式開啟網址或本機路徑：瀏覽器、PDF 閱讀器、檔案管理員。
Linux 經 xdg-open 啟動，子行程的環境由 core.launch_env.external_process_environment 決定
（打包版還原 LD_LIBRARY_PATH）；Windows 經 os.startfile。

使用範例：
    from services.external_opener import ExternalOpener
    opened = ExternalOpener().open("https://example.com/")
"""
import os
import subprocess
import sys
from typing import Callable, Dict, List, Mapping, Optional

from core.launch_env import current_build, external_process_environment

Spawn = Callable[[List[str], Dict[str, str]], None]


def _spawn_detached(argv: List[str], env: Dict[str, str]) -> None:
    """在新的工作階段啟動外部程式，不等它結束、不接它的輸出"""
    subprocess.Popen(
        argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class ExternalOpener:
    """以作業系統的預設程式開啟網址或本機路徑"""

    def __init__(
        self, platform: str = sys.platform, environ: Optional[Mapping[str, str]] = None,
        frozen: Optional[bool] = None, spawn: Spawn = _spawn_detached,
        startfile: Optional[Callable[[str], None]] = None,
    ):
        """
        Args:
            platform: 平台代碼；省略時為 sys.platform
            environ: 子行程環境的來源；省略時為開啟當下的 os.environ
            frozen: 是否為打包版；省略時照 core.launch_env.current_build
            spawn: 啟動外部程式（指令、環境）；省略時在新的工作階段啟動、不等它結束
            startfile: Windows 上開啟網址或路徑；省略時為 os.startfile
        """
        self._platform = platform
        self._environ = environ
        self._frozen = current_build().frozen if frozen is None else frozen
        self._spawn = spawn
        self._startfile = startfile or getattr(os, "startfile", None)

    def open(self, target: str) -> bool:
        """以預設程式開啟網址（瀏覽器）或本機的檔案、資料夾

        只確認外部程式啟動得了；它啟動後開不開得了目標，這裡不知道。

        Args:
            target: 網址（例如 https://…）或本機路徑

        Returns:
            外部程式是否啟動；找不到程式或沒有對應的預設程式時為 False
        """
        try:
            if self._platform == "win32":
                self._startfile(target)
            else:
                environ = os.environ if self._environ is None else self._environ
                env = external_process_environment(environ, self._platform, self._frozen)
                self._spawn(["xdg-open", target], env)
        except OSError:
            return False
        return True
