# -*- coding: utf-8 -*-
"""
背景工作

在背景執行緒執行一件可取消的長工作（例如壓縮 PDF），進度、結果與錯誤都回到主執行緒通知；
主視窗據此鎖住會動到檔案的操作、顯示進度與取消鈕，關閉時詢問要不要取消。
關閉時等不到工作停下就放棄它（abandon）：背景執行緒之後不再碰這個物件，物件可以隨父物件一起刪掉。

使用範例：
    task = BackgroundTask(lambda should_cancel, report: service.compress(path, should_cancel, report), window)
    task.progressed.connect(show_progress)
    task.finished.connect(show_result)
    task.start()
"""
import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal

# 工作函式：接收「是否已要求取消」與「回報進度」兩個函式，回傳結果
Work = Callable[[Callable[[], bool], Callable[[Any], None]], Any]


class _Channel:
    """背景執行緒送回主執行緒的唯一管道：放棄之後什麼都不送，送的當下不會被放棄"""

    def __init__(self, reported: Callable[[Any], None], done: Callable[[Any], None], raised: Callable[[str], None]):
        """
        Args:
            reported: 送出進度的訊號 emit
            done: 送出結果的訊號 emit
            raised: 送出錯誤訊息的訊號 emit
        """
        self._lock = threading.Lock()
        self._abandoned = False
        self._reported = reported
        self._done = done
        self._raised = raised

    def abandon(self) -> None:
        """之後什麼都不送；正在送的那一則送完才返回"""
        with self._lock:
            self._abandoned = True

    def report(self, progress: Any) -> None:
        """送出進度（背景執行緒呼叫）"""
        self._send(self._reported, progress)

    def done(self, result: Any) -> None:
        """送出結果（背景執行緒呼叫）"""
        self._send(self._done, result)

    def raised(self, message: str) -> None:
        """送出錯誤訊息（背景執行緒呼叫）"""
        self._send(self._raised, message)

    def _send(self, emit: Callable[[Any], None], value: Any) -> None:
        """還沒被放棄時發出訊號；跨執行緒的訊號只排進主執行緒的事件佇列，持鎖時間很短"""
        with self._lock:
            if not self._abandoned:
                emit(value)


def _run(work: Work, should_cancel: Callable[[], bool], channel: _Channel) -> None:
    """背景執行緒的進入點：執行工作，結果或錯誤經 channel 送回；不碰 BackgroundTask 物件本身"""
    try:
        result = work(should_cancel, channel.report)
    except Exception as e:
        channel.raised(str(e))
        return
    channel.done(result)


class BackgroundTask(QObject):
    """在背景執行緒執行一件可取消的工作

    progressed、finished、failed 都在主執行緒發出；finished 與 failed 只會發出其中一個，發出前 is_running 已轉為 False。
    取消只是要求：工作在下一次呼叫 should_cancel 時自行停下，照常以 finished 交回結果。
    工作還在跑時要刪掉這個物件（例如關閉主視窗），先呼叫 abandon。
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
            parent: 父物件；由它在主執行緒刪掉這個物件
        """
        super().__init__(parent)
        self._work = work
        self._cancel = threading.Event()
        self._channel = _Channel(self._reported.emit, self._done.emit, self._raised.emit)
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
        self._thread = threading.Thread(
            target=_run, args=(self._work, self._cancel.is_set, self._channel), daemon=True,
        )
        self._thread.start()

    def cancel(self) -> None:
        """要求取消；工作在下一次檢查時停下"""
        self._cancel.set()

    def abandon(self) -> None:
        """放棄這件工作：之後背景執行緒不再發出任何訊號，這個物件可以在工作結束前刪掉

        不會讓工作停下（停下要靠 cancel）；放棄後不再有 progressed、finished 或 failed。
        """
        self._channel.abandon()

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

    def _deliver_result(self, result: Any) -> None:
        """在主執行緒交回工作的結果"""
        self._running = False
        self.finished.emit(result)

    def _deliver_error(self, message: str) -> None:
        """在主執行緒交回工作拋出的錯誤訊息"""
        self._running = False
        self.failed.emit(message)
