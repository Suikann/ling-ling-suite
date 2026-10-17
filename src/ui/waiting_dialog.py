# -*- coding: utf-8 -*-
"""
等待視窗

在背景執行緒做一件會卡住的事（例如等瀏覽器登入回來），期間顯示一個有「取消」的等待視窗，介面不會凍結。
按「取消」或關閉視窗時呼叫呼叫端給的取消函式，等那件事真的結束才關閉視窗。背景執行緒由 ui/background_task.py 管。

使用範例：
    from ui.waiting_dialog import run_while_waiting
    creds = run_while_waiting(self, title, message, auth.authenticate, auth.cancel_login)
"""
from typing import Any, Callable, Optional, Tuple

from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from core.locale import t
from ui.background_task import BackgroundTask


class _WaitingDialog(QDialog):
    """等待視窗：一段說明與「取消」；取消時只呼叫 cancel，由背景的工作結束時關閉"""

    def __init__(self, parent: Optional[QWidget], title: str, message: str, cancel: Callable[[], None]):
        super().__init__(parent)
        self._cancel = cancel
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(message))
        row = QHBoxLayout()
        row.addStretch()
        self._cancel_btn = QPushButton(t("dialog.cancel_btn"))
        self._cancel_btn.clicked.connect(self.reject)
        row.addWidget(self._cancel_btn)
        layout.addLayout(row)

    def reject(self):
        """按「取消」、Esc 或關閉視窗：請背景的工作停下，視窗等它結束才關"""
        self._cancel_btn.setEnabled(False)
        self._cancel()


def run_while_waiting(
    parent: Optional[QWidget], title: str, message: str, work: Callable[[], Any], cancel: Callable[[], None],
) -> Any:
    """在背景執行 work，期間顯示有「取消」的等待視窗

    Args:
        parent: 等待視窗的父元件
        title: 等待視窗的標題
        message: 等待視窗的說明
        work: 在背景執行緒執行、會卡住的工作
        cancel: 按「取消」時呼叫，要讓 work 盡快結束（可從別的執行緒呼叫）

    Returns:
        work 的回傳值

    Raises:
        work 拋出的例外
    """
    dialog = _WaitingDialog(parent, title, message, cancel)
    outcome = []

    def run(should_cancel, report) -> Tuple[Any, Optional[Exception]]:
        # BackgroundTask 只交回例外的訊息，這裡連同例外本身當成結果交回，呼叫端才分得出原因
        try:
            return work(), None
        except Exception as e:
            return None, e

    def finish(result: Tuple[Any, Optional[Exception]]) -> None:
        outcome.append(result)
        dialog.accept()
    task = BackgroundTask(run)
    task.finished.connect(finish)
    task.start()
    # 結果經事件迴圈回到主執行緒，最早也要等這個視窗開始等待時才送到
    dialog.exec()
    dialog.deleteLater()
    value, error = outcome[0]
    if error is not None:
        raise error
    return value
