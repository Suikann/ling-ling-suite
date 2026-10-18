# -*- coding: utf-8 -*-
"""
背景工作

在背景執行緒執行一件可取消的長工作（例如壓縮 PDF），進度、結果與錯誤都回到主執行緒通知；
關閉時等不到工作停下就放棄它（abandon）：背景執行緒之後不再碰這個物件，物件可以隨父物件一起刪掉。

一個視窗一次一件的長工作經 TaskSlot：狀態列的進度、取消、拋出例外時的錯誤框與關閉時詢問要不要取消都在這裡，
主視窗（存入、清除、下載回原位、撤銷存入、壓縮）與譜庫視窗（一般下載）共用；各種工作的字串鍵是 TaskTexts。

使用範例：
    task = BackgroundTask(lambda should_cancel, report: service.compress(path, should_cancel, report), window)
    task.progressed.connect(show_progress)
    task.finished.connect(show_result)
    task.start()

    slot = TaskSlot(window, status_bar.showMessage, show_state)
    slot.run(work, TaskTexts.of("download"), finished)   # finished(結果, 是否要求過取消)
    if not slot.confirm_close(): event.ignore()           # closeEvent
"""
import threading
from typing import Any, Callable, NamedTuple, Optional, Tuple

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QMessageBox, QWidget

from core.constants import CLOSE_WAIT_SECONDS
from core.locale import t

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


class TaskTexts(NamedTuple):
    """一種背景工作的字串鍵：取消鈕、取消中的狀態列、拋出例外時的錯誤訊息（以 error 帶入）、進行中關閉時的詢問，
    與逐份進度的狀態列（以 done、total、name 帶入）"""
    cancel: str
    cancelling: str
    failure: str
    close_message: str
    progress: str

    @classmethod
    def of(cls, prefix: str) -> "TaskTexts":
        """以字串鍵的開頭組出全部的鍵（例如 "deposit" → "deposit.cancel"…）"""
        return cls(
            f"{prefix}.cancel", f"{prefix}.cancelling", f"{prefix}.error", f"{prefix}.close.message",
            f"{prefix}.progress",
        )

    def describe(self, progress: Any) -> Tuple[str, int, int]:
        """逐份進度（有 done、total、file_name 的進度）轉成（狀態列文字，已完成數，總數）"""
        text = t(self.progress, done=progress.done, total=progress.total, name=progress.file_name)
        return text, progress.done, progress.total


class TaskSlot:
    """一個視窗一次一件、可取消的背景長工作：進度、取消、錯誤框與關閉時詢問"""

    def __init__(
        self, parent: QWidget, show_status: Callable[[str], None], changed: Callable[[], None],
        show_progress: Optional[Callable[[int, int], None]] = None,
    ):
        """
        Args:
            parent: 工作物件的父物件與錯誤框、詢問框的父視窗
            show_status: 在狀態列寫一段文字
            changed: 開始、交回、要求取消時呼叫（視窗據此停用或恢復操作、顯示取消鈕）
            show_progress: 進度條的（已完成數，總數）；沒有進度條時省略
        """
        self._parent = parent
        self._show_status = show_status
        self._changed = changed
        self._show_progress = show_progress
        self.task: Optional[BackgroundTask] = None
        self.texts: Optional[TaskTexts] = None
        # 進行中的狀態列文字（要求取消之後不再寫到狀態列）
        self.status = ""

    @property
    def busy(self) -> bool:
        """有沒有工作在跑"""
        return self.task is not None

    @property
    def cancel_requested(self) -> bool:
        """進行中的工作是否已要求取消"""
        return self.task is not None and self.task.cancel_requested

    def run(
        self, work: Work, texts: TaskTexts, finished: Callable[[Any, bool], None],
        describe: Optional[Callable[[Any], Tuple[str, int, int]]] = None,
    ) -> None:
        """在背景執行一件工作；呼叫端先確認沒有工作在跑

        Args:
            work: 在背景執行緒呼叫的工作（見 BackgroundTask）
            texts: 這種工作的字串鍵
            finished: 工作交回結果時在主執行緒呼叫，參數為結果與使用者是否要求過取消
            describe: 把回報的進度轉成（狀態列文字，已完成數，總數）；省略時照 texts.describe
        """
        task = BackgroundTask(work, self._parent)
        self.task, self.texts, self.status = task, texts, ""
        describe = describe or texts.describe

        def progressed(progress):
            """寫進度條；沒要求取消時也寫到狀態列"""
            text, done, total = describe(progress)
            self.status = text
            if self._show_progress is not None:
                self._show_progress(done, total)
            if not task.cancel_requested:
                self._show_status(text)

        def settle():
            """交回了：在主執行緒延後刪掉工作物件"""
            self.task = None
            task.deleteLater()
            self._changed()

        def on_finished(result):
            """交回結果"""
            settle()
            finished(result, task.cancel_requested)

        def on_failed(message: str):
            """工作拋出例外：錯誤框"""
            settle()
            self._show_status(t("status.ready"))
            QMessageBox.critical(self._parent, t("dialog.error"), t(texts.failure, error=message))

        task.progressed.connect(progressed)
        task.finished.connect(on_finished)
        task.failed.connect(on_failed)
        task.start()
        self._changed()

    def cancel(self) -> None:
        """要求取消；工作在下一個停頓點停下後照常交回結果"""
        if self.task is not None:
            self.task.cancel()
            self._show_status(t(self.texts.cancelling))
            self._changed()

    def confirm_close(self) -> bool:
        """關閉視窗時有工作就問要不要取消；選取消就要求取消、等它停下（最多 CLOSE_WAIT_SECONDS 秒），再放棄它

        等不到它停下也照樣關閉：放棄之後背景執行緒不再送回任何結果，工作物件隨視窗刪掉也安全。

        Returns:
            可以關閉時為 True（沒有工作在跑，或選了取消）
        """
        task = self.task
        if task is None:
            return True
        if QMessageBox.question(self._parent, t("dialog.close"), t(self.texts.close_message)) != QMessageBox.Yes:
            return False
        task.finished.disconnect()
        task.failed.disconnect()
        task.cancel()
        task.wait(CLOSE_WAIT_SECONDS)
        task.abandon()
        self.task = None
        return True
