# -*- coding: utf-8 -*-
"""
譜庫的錯誤提示列

譜庫讀寫失敗時放在失敗的那個視窗頂端的一條紅色提示列（原型 #65 錯誤訊息的變體 B）：
左邊寫「讀不到譜庫｜類別：怎麼辦」，右邊是這一類的處理按鈕（重試、重新登入 Google、開啟譜庫設定）與「詳細資料」。
按「詳細資料」在提示列下方展開唯讀的時間、動作、檔名與原始訊息，內容即 CatalogFailure.detail_text()；
建立時給了紀錄檔的，每顯示一次失敗就把同一筆（CatalogFailure.log_entry()）寫進紀錄檔，兩邊讀起來一樣。
各類別的標題、怎麼辦與按鈕照 core/catalog_errors.py 的 CATALOG_ERROR_RULES。

使用範例：
    from ui.catalog_error_bar import CatalogErrorBar
    bar = CatalogErrorBar(window, log=activity_log)
    bar.retry_requested.connect(retry)
    bar.sign_in_requested.connect(sign_in)
    bar.settings_requested.connect(open_settings)
    bar.show_failure(t("catalog.error.cannot_read"), CatalogFailure.from_error(error, t("catalog.action.read")))
    bar.clear()
"""
from typing import Optional

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from core.catalog_errors import CATALOG_ERROR_RULES, CATALOG_REMEDY_KEYS, CatalogFailure, CatalogRemedy
from core.locale import t
from services.activity_log import ActivityLog

_BACKGROUND = "#5a2129"
_FOREGROUND = "#ff8a8a"
_DETAILS_HEIGHT = 80


class CatalogErrorBar(QFrame):
    """譜庫讀寫失敗的提示列：類別、怎麼辦、處理按鈕與可展開的詳細資料；沒有失敗時隱藏"""

    retry_requested = Signal()
    sign_in_requested = Signal()
    settings_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None, log: Optional[ActivityLog] = None):
        """建立隱藏的提示列

        Args:
            parent: 父元件
            log: 紀錄檔，顯示的每一次失敗都寫進去；省略時不寫
        """
        super().__init__(parent)
        self._log = log
        self.setObjectName("catalogErrorBar")
        self.setStyleSheet(
            f"#catalogErrorBar {{ background-color: {_BACKGROUND}; border-radius: 5px; }}"
            f"#catalogErrorBar QLabel {{ color: {_FOREGROUND}; }}"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        row = QHBoxLayout()
        self._message = QLabel()
        self._message.setWordWrap(True)
        row.addWidget(self._message, stretch=1)
        self._remedies = {}
        signals = {
            CatalogRemedy.RETRY: self.retry_requested,
            CatalogRemedy.SIGN_IN: self.sign_in_requested,
            CatalogRemedy.OPEN_SETTINGS: self.settings_requested,
        }
        for remedy, signal in signals.items():
            button = QPushButton(t(CATALOG_REMEDY_KEYS[remedy]))
            button.clicked.connect(signal)
            row.addWidget(button)
            self._remedies[remedy] = button
        self._details_button = QPushButton(t("catalog.error.details"))
        self._details_button.clicked.connect(self._toggle_details)
        row.addWidget(self._details_button)
        layout.addLayout(row)
        self._details = QTextEdit()
        self._details.setReadOnly(True)
        self._details.setFixedHeight(_DETAILS_HEIGHT)
        self._details.hide()
        layout.addWidget(self._details)
        self.hide()

    def show_failure(self, heading: str, failure: CatalogFailure) -> None:
        """顯示一次失敗並寫進紀錄檔：標題列寫 heading 與這一類的標題、怎麼辦，只留這一類的處理按鈕；詳細資料先收起

        Args:
            heading: 開頭，例如「讀不到譜庫」
            failure: 失敗紀錄
        """
        rule = CATALOG_ERROR_RULES[failure.kind]
        self._message.setText(t("catalog.error.banner", heading=heading, title=t(rule.title_key), advice=t(rule.advice_key)))
        for remedy, button in self._remedies.items():
            button.setVisible(remedy is rule.remedy)
        self._details.setPlainText(failure.detail_text())
        self._details.hide()
        self.show()
        if self._log is not None:
            self._log.record(failure.log_entry())

    def clear(self) -> None:
        """收起提示列"""
        self._details.hide()
        self.hide()

    def _toggle_details(self) -> None:
        """展開或收起詳細資料"""
        self._details.setVisible(self._details.isHidden())
