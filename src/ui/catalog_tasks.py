# -*- coding: utf-8 -*-
"""
譜庫的背景讀寫

在背景執行緒呼叫譜庫服務（網路請求會卡住），結果或錯誤回到主執行緒交給呼叫端。
BackgroundTask 失敗時只交回例外的訊息，這裡把例外本身當成結果交回，呼叫端才拿得到錯誤類別；
不是 CatalogError 的例外（程式的錯）歸為「其他」，也照樣交回，不會被吞掉。
視窗關閉時以 abandon_all 放棄還在跑的工作：背景執行緒之後不再碰視窗。
要接譜庫的背景長工作（下載、撤銷存入）以 catalog_work 包起來：接不上譜庫時把錯誤交回成結果，不拋出。

使用範例：
    from ui.catalog_tasks import CatalogTasks
    tasks = CatalogTasks(window)
    tasks.run(lambda: catalog.overview(root_id), show_overview, show_failure)
    tasks.abandon_all()   # closeEvent
    work = catalog_work(connect, lambda catalog, cancel, report: service.restore(groups, catalog, cancel, report),
                        lambda error: DownloadOutcome(error=error))
"""
from typing import Any, Callable, List, Tuple, TypeVar

from PySide6.QtCore import QObject

from core.catalog_errors import CatalogError, as_catalog_error
from services.catalog_service import CatalogService
from ui.background_task import BackgroundTask, Work

T = TypeVar("T")


def catalog_work(
    connect: Callable[[], CatalogService],
    run: Callable[[CatalogService, Callable[[], bool], Callable[[Any], None]], T],
    unreachable: Callable[[CatalogError], T],
) -> Work:
    """要接譜庫的背景長工作：先接譜庫，接上了才 run；接不上時以 unreachable(錯誤) 的結果交回，不拋出

    Args:
        connect: 取得譜庫服務（背景執行緒呼叫，拿不到時丟出例外）
        run: 以譜庫服務、是否已要求取消、回報進度執行工作
        unreachable: 接不上譜庫時交回的結果（例如帶著錯誤的下載結果）

    Returns:
        交給 BackgroundTask 的工作
    """
    def work(should_cancel: Callable[[], bool], report: Callable[[Any], None]) -> T:
        """接譜庫，再執行"""
        try:
            catalog = connect()
        except Exception as e:
            return unreachable(as_catalog_error(e))
        return run(catalog, should_cancel, report)
    return work


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
            except Exception as e:
                return None, as_catalog_error(e)

        task = BackgroundTask(capture, self._parent)

        def finished(outcome: Tuple[Any, Any]) -> None:
            """交回了：先刪掉工作物件，再交給 done 或 failed；放棄前就排進事件佇列的結果不交"""
            if task not in self._running:
                return
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
