# -*- coding: utf-8 -*-
"""
發譜進行中與結果的呈現

照原型 #63 的變體 C：主視窗下方是這次發譜的逐行紀錄（PublishRunPanel），完成時在紀錄下方列出沒發成的群組與原因，
附「重新發譜沒發成的群組」（按下時發出 publish_failed_again，由主視窗只對那幾個群組開發譜預覽）；
狀態列的總進度與「停止發譜」由主視窗的發譜工作（TaskSlot）負責。這裡只把發譜服務回報的進度與結果寫成文字，
發譜的規則在 services/publish_runner.py。

使用範例：
    panel = PublishRunPanel()
    panel.publish_failed_again.connect(publish_those_groups_again)
    panel.show_record(lines, failures)
    lines.append(progress_line(progress))
"""
from typing import List

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from core.locale import t
from services.publish_runner import GroupOutcome, PublishOutcome, PublishProgress

# 逐行紀錄的高度（像素）：不擠掉群組分頁
_LOG_HEIGHT = 110
# 沒發成的群組的文字顏色
_FAILED_COLOR = "#b3261e"


def progress_line(progress: PublishProgress) -> str:
    """發譜回報的一次進度寫成逐行紀錄的一行"""
    return t(
        f"publish.run.line.{progress.event.value}", group=progress.group, name=progress.name, detail=progress.detail,
    )


def failure_line(group: GroupOutcome) -> str:
    """一個沒發成的群組與原因"""
    reason = t(f"publish.run.failure.{group.failure.value}")
    if group.detail:
        reason = t("publish.run.failure_detail", reason=reason, detail=group.detail)
    return t("publish.run.failed_group", group=group.group.group, reason=reason)


def summary_lines(outcome: PublishOutcome) -> List[str]:
    """發譜結束時接在逐行紀錄最後的幾行：發成幾個群組（或沒開始），結果寫不進譜庫、留下孤兒檔時另寫一行"""
    if outcome.error is not None:
        return [t("publish.run.not_started")]
    published = len(outcome.groups) - len(outcome.failed)
    key = "publish.run.cancelled" if outcome.cancelled else "publish.run.done"
    lines = [t(key, published=published, total=len(outcome.groups))]
    if outcome.orphans:
        lines.append(t("publish.run.orphans", count=len(outcome.orphans)))
    if outcome.log_error is not None:
        lines.append(t("publish.run.result_not_logged"))
    return lines


class PublishRunPanel(QWidget):
    """主視窗下方這次發譜的逐行紀錄與沒發成的群組；還沒發譜過或按了「關閉紀錄」時隱藏"""

    # 按了「重新發譜沒發成的群組」
    publish_failed_again = Signal()

    def __init__(self, parent=None):
        """建立隱藏的紀錄區"""
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        heading = QHBoxLayout()
        heading.addWidget(QLabel(t("publish.run.heading")))
        heading.addStretch()
        close = QPushButton(t("publish.run.close_log"))
        close.clicked.connect(self.hide)
        heading.addWidget(close)
        layout.addLayout(heading)
        self._log = QPlainTextEdit()
        self._log.setObjectName("publish_log")
        self._log.setReadOnly(True)
        self._log.setFixedHeight(_LOG_HEIGHT)
        layout.addWidget(self._log)
        self._failed = QLabel()
        self._failed.setObjectName("publish_failed")
        self._failed.setWordWrap(True)
        self._failed.setStyleSheet(f"color: {_FAILED_COLOR};")
        failed_row = QHBoxLayout()
        failed_row.addWidget(self._failed, 1)
        self._again = QPushButton(t("publish.run.publish_failed_again"))
        self._again.setObjectName("publish_failed_again")
        self._again.clicked.connect(self.publish_failed_again)
        failed_row.addWidget(self._again)
        layout.addLayout(failed_row)
        self.show_record([], [])
        self.hide()

    def show_record(self, lines: List[str], failures: List[str]) -> None:
        """顯示這次發譜的逐行紀錄與沒發成的群組（沒有時不顯示那一塊與「重新發譜沒發成的群組」）"""
        self._log.setPlainText("\n".join(lines))
        self._log.verticalScrollBar().setValue(self._log.verticalScrollBar().maximum())
        self._failed.setText("\n".join([t("publish.run.failed_heading")] + failures) if failures else "")
        self._failed.setVisible(bool(failures))
        self._again.setVisible(bool(failures))

    def add_line(self, line: str) -> None:
        """接上一行逐行紀錄"""
        self._log.appendPlainText(line)
