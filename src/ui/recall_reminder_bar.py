# -*- coding: utf-8 -*-
"""
收回提醒的提示列

開泠靈時放在主視窗頂端的藍色提示列（原型 #63 收回提醒的變體 B）：寫出哪幾首的公演已結束、還沒收回，
右邊是「查看並收回」與「之後再說」，不擋操作。要提醒哪幾首由 services/recall_reminder.py 的 RecallReminder 判斷；
這裡只顯示。兩顆按鈕都收起提示列；「之後再說」不記任何東西，下次開泠靈照樣提醒。

使用範例：
    from ui.recall_reminder_bar import RecallReminderBar
    bar = RecallReminderBar(window)
    bar.view_requested.connect(open_recall)
    bar.show_titles(reminder.due())
"""
from typing import List, Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from core.constants import INFO_BANNER_STYLE
from core.locale import t


class RecallReminderBar(QFrame):
    """公演已結束、還沒收回的曲目提示列；沒有要提醒的曲目時隱藏"""

    view_requested = Signal()
    dismissed = Signal()

    def __init__(self, parent: Optional[QWidget] = None):
        """建立隱藏的提示列

        Args:
            parent: 父元件
        """
        super().__init__(parent)
        self.setObjectName("recallReminderBar")
        self.setStyleSheet(
            f"#recallReminderBar {{ {INFO_BANNER_STYLE} }}"
            f"#recallReminderBar QLabel {{ {INFO_BANNER_STYLE} padding: 0; }}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.message = QLabel()
        self.message.setWordWrap(True)
        row.addWidget(self.message, stretch=1)
        self.view_button = QPushButton(t("recall.reminder.view"))
        self.view_button.clicked.connect(self._view)
        row.addWidget(self.view_button)
        self.later_button = QPushButton(t("recall.reminder.later"))
        self.later_button.clicked.connect(self._later)
        row.addWidget(self.later_button)
        self.hide()

    def show_titles(self, titles: List[str]):
        """寫出這幾首的公演已結束、還沒收回；沒有曲目時隱藏"""
        self.message.setText(t(
            "recall.reminder.text", count=len(titles), titles=t("recall.reminder.separator").join(titles),
        ))
        self.setVisible(bool(titles))

    def _view(self):
        """按了「查看並收回」：收起並要求開啟收回畫面"""
        self.hide()
        self.view_requested.emit()

    def _later(self):
        """按了「之後再說」：收起（下次開泠靈再提醒）"""
        self.hide()
        self.dismissed.emit()
