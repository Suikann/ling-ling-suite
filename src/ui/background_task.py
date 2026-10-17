# -*- coding: utf-8 -*-
"""
背景工作

在背景執行緒執行一件可取消的長工作（例如壓縮 PDF），進度、結果與錯誤都回到主執行緒通知；
主視窗據此鎖住會動到檔案的操作、顯示進度與取消鈕，關閉時詢問要不要取消。

使用範例：
    task = BackgroundTask(lambda should_cancel, report: service.compress(path, should_cancel, report))
    task.progressed.connect(show_progress)
    task.finished.connect(show_result)
    task.start()
"""
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

# 工作函式：接收「是否已要求取消」與「回報進度」兩個函式，回傳結果
Work = Callable[[Callable[[], bool], Callable[[Any], None]], Any]


class BackgroundTask(QObject):
    """在背景執行緒執行一件可取消的工作

    progressed、finished、failed 都在主執行緒發出；finished 與 failed 只會發出其中一個，發出前 is_running 已轉為 False。
    取消只是要求：工作在下一次呼叫 should_cancel 時自行停下，照常以 finished 交回結果。
    """

    progressed = Signal(object)
    finished = Signal(object)
    failed = Signal(str)
    _reported = Signal(object)
    _done = Signal(object)
    _raised = Signal(str)

    def __init__(self, work: Work, parent=None):
        """
        Args:
            work: 在背景執行緒呼叫的工作函式
            parent: 父物件
        """
        super().__init__(parent)
        self._work = work
        self._cancel = threading.Event()
        self._thread = None
        self._running = False
        self._reported.connect(self.progressed)
        self._done.connect(self._deliver_result)
        self._raised.connect(self._deliver_error)

    @property
    def is_running(self) -> bool:
        """工作是否還沒交回結果"""
        return self._running

    @property
    def cancel_requested(self) -> bool:
        """是否已要求取消"""
        return self._cancel.is_set()

    def start(self) -> None:
        """在背景執行緒開始工作"""
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        """要求取消；工作在下一次檢查時停下"""
        self._cancel.set()

    def wait(self, timeout: float) -> bool:
        """等背景執行緒結束（關閉程式前讓取消的工作清掉暫存檔）

        Args:
            timeout: 最多等幾秒

        Returns:
            執行緒是否已結束
        """
        if self._thread is None:
            return True
        self._thread.join(timeout)
        return not self._thread.is_alive()

    def _run(self) -> None:
        try:
            result = self._work(self._cancel.is_set, self._reported.emit)
        except Exception as e:
            self._raised.emit(str(e))
            return
        self._done.emit(result)

    def _deliver_result(self, result: Any) -> None:
        self._running = False
        self.finished.emit(result)

    def _deliver_error(self, message: str) -> None:
        self._running = False
        self.failed.emit(message)
