# -*- coding: utf-8 -*-
"""
譜庫的背景讀寫

在背景執行緒呼叫譜庫服務（網路請求會卡住），結果或錯誤回到主執行緒交給呼叫端。
BackgroundTask 失敗時只交回例外的訊息，這裡把例外本身當成結果交回，呼叫端才拿得到錯誤類別；
不是 CatalogError 的例外（程式的錯）歸為「其他」，也照樣交回，不會被吞掉。
視窗關閉時以 abandon_all 放棄還在跑的工作：背景執行緒之後不再碰視窗。
要接譜庫的背景長工作（下載、撤銷存入）以 catalog_work 包起來：接不上譜庫時把錯誤交回成結果，不拋出。
在等待視窗裡做一件碰譜庫（與 HuaXia）的事一律經 wait_for_catalog：失敗時譜庫的錯誤顯示在那個視窗的提示列，
HuaXia 的錯誤寫成對話框。

使用範例：
    from ui.catalog_tasks import CatalogTasks
    tasks = CatalogTasks(window)
    tasks.run(lambda: catalog.overview(root_id), show_overview, show_failure)
    tasks.abandon_all()   # closeEvent
    work = catalog_work(connect, lambda catalog, cancel, report: service.restore(groups, catalog, cancel, report),
                        lambda error: DownloadOutcome(error=error))
    done, result = wait_for_catalog(window, error_bar, title, waiting, work, heading, retry)
"""
from typing import Any, Callable, List, Optional, Tuple, TypeVar

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox, QWidget

from core.catalog_errors import CatalogError, CatalogFailure, as_catalog_error
from services.catalog_service import CatalogService
from services.huaxia_client import HuaXiaError
from ui.background_task import BackgroundTask, Work
from ui.catalog_error_bar import CatalogErrorBar
from ui.huaxia_prompts import huaxia_error_text
from ui.waiting_dialog import run_while_waiting

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


def wait_for_catalog(
    parent: QWidget, error_bar: CatalogErrorBar, title: str, waiting: str, work: Callable[[], T], heading: str,
    retry: Callable[[], Any], stop: Callable[[], None] = lambda: None, action: str = "", huaxia_action: str = "",
) -> Tuple[bool, Optional[T]]:
    """在等待視窗裡於背景做一件碰譜庫（與 HuaXia）的事；失敗時寫出原因，不拋出

    HuaXia 的錯誤（給了 huaxia_action 時）以對話框寫出原因；其他錯誤一律當成譜庫的錯誤（不是 CatalogError 的歸為
    其他），在 error_bar 顯示提示列並寫進紀錄檔，按處理按鈕呼叫 retry。

    Args:
        parent: 等待視窗與對話框的父元件
        error_bar: 那個視窗頂端的提示列
        title: 等待視窗與對話框的標題（也是失敗時記下的動作，除非另給 action）
        waiting: 等待視窗的說明
        work: 在背景執行緒呼叫的工作
        heading: 譜庫失敗時提示列的開頭
        retry: 提示列按處理按鈕時呼叫
        stop: 等待視窗按取消時呼叫
        action: 失敗時記進詳細資料與紀錄檔的動作；省略時為 title
        huaxia_action: HuaXia 失敗時說明的介面字串鍵開頭（huaxia_prompts.huaxia_error_text）；省略時 HuaXia 的錯誤
            也當成譜庫的錯誤

    Returns:
        （成功與否，work 的結果；失敗時為 None）
    """
    try:
        return True, run_while_waiting(parent, title, waiting, work, stop)
    except Exception as e:
        if huaxia_action and isinstance(e, HuaXiaError):
            QMessageBox.warning(parent, title, huaxia_error_text(e, huaxia_action))
        else:
            error_bar.show_failure(heading, CatalogFailure.from_error(as_catalog_error(e), action or title), retry)
        return False, None


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
