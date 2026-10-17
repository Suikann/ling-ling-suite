# -*- coding: utf-8 -*-
"""
譜庫的背景讀寫

在背景執行緒呼叫譜庫服務（網路請求會卡住），結果或錯誤回到主執行緒交給呼叫端。
BackgroundTask 失敗時只交回例外的訊息，這裡把例外本身當成結果交回，呼叫端才拿得到錯誤類別；
不是 CatalogError 的例外（程式的錯）歸為「其他」，也照樣交回，不會被吞掉。
視窗關閉時以 abandon_all 放棄還在跑的工作：背景執行緒之後不再碰視窗。

使用範例：
    from ui.catalog_tasks import CatalogTasks
    tasks = CatalogTasks(window)
    tasks.run(lambda: catalog.overview(root_id), show_overview, show_failure)
    tasks.abandon_all()   # closeEvent
"""
from typing import Any, Callable, List, Tuple

from PySide6.QtCore import QObject

from core.catalog_errors import CatalogError, CatalogErrorKind
from ui.background_task import BackgroundTask


class CatalogTasks:
    """一個視窗的譜庫背景工作：每件各自在背景執行，交回時在主執行緒刪掉"""

    def __init__(self, parent: QObject):
        """
        Args:
            parent: 背景工作的父物件（通常是視窗）
        """
        self._parent = parent
        self._running: List[BackgroundTask] = []

    def run(
        self, work: Callable[[], Any], done: Callable[[Any], None], failed: Callable[[CatalogError], None],
    ) -> None:
        """在背景執行 work，完成時在主執行緒呼叫 done(結果)，失敗時呼叫 failed(錯誤)

        Args:
            work: 在背景執行緒呼叫的工作，通常是譜庫服務的方法
            done: 成功時以 work 的回傳值呼叫
            failed: 失敗時以 CatalogError 呼叫
        """
        def capture(should_cancel, report) -> Tuple[Any, Any]:
            """執行 work，把結果或錯誤一起交回"""
            try:
                return work(), None
            except CatalogError as e:
                return None, e
            except Exception as e:
                return None, CatalogError(CatalogErrorKind.OTHER, f"{type(e).__name__}: {e}")

        task = BackgroundTask(capture, self._parent)

        def finished(outcome: Tuple[Any, Any]) -> None:
            """交回了：先刪掉工作物件，再交給 done 或 failed"""
            self._running.remove(task)
            task.deleteLater()
            value, error = outcome
            if error is None:
                done(value)
            else:
                failed(error)
        task.finished.connect(finished)
        self._running.append(task)
        task.start()

    @property
    def busy(self) -> bool:
        """是否還有工作在跑"""
        return bool(self._running)

    def abandon_all(self) -> None:
        """放棄所有還在跑的工作：之後不再呼叫 done 或 failed"""
        for task in self._running:
            task.abandon()
        self._running.clear()
