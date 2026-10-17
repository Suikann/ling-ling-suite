# -*- coding: utf-8 -*-
"""
譜庫的錯誤提示列

譜庫讀寫失敗時放在失敗的那個視窗頂端的一條紅色提示列（原型 #65 錯誤訊息的變體 B）：
左邊寫「讀不到譜庫｜類別：怎麼辦」（寫入失敗時開頭是「寫不進譜庫」），右邊是這一類的處理按鈕（重試、重新登入 Google、
開啟譜庫設定、重新讀取）與「詳細資料」。
按「詳細資料」在提示列下方展開唯讀的時間、動作、檔名與原始訊息，內容即 CatalogFailure.detail_text()；
建立時給了紀錄檔的，每顯示一次失敗就把同一筆（CatalogFailure.log_entry()）寫進紀錄檔，兩邊讀起來一樣。
各類別的標題、怎麼辦與按鈕照 core/catalog_errors.py 的 CATALOG_ERROR_RULES。
提示列記著這次失敗怎麼重做（show_failure 的 retry）：按「重試」，或「重新登入 Google」「開啟譜庫設定」做完時，收起提示列並重做。
「重新讀取」不重做：收起提示列後重新讀取畫面上的內容（要改的紀錄已經不在譜庫，重送不會成功）。

使用範例：
    from ui.catalog_error_bar import CatalogErrorBar
    bar = CatalogErrorBar(window, log=activity_log)
    bar.connect_remedies(sign_in=sign_in_again, open_settings=open_settings, reload=reload_view)
    bar.show_failure(t("catalog.error.cannot_read"), CatalogFailure.from_error(error, t("catalog.action.read")), retry)
    bar.clear()
"""
from typing import Callable, Optional

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
    reload_requested = Signal()

    def __init__(self, parent: Optional[QWidget] = None, log: Optional[ActivityLog] = None):
        """建立隱藏的提示列

        Args:
            parent: 父元件
            log: 紀錄檔，顯示的每一次失敗都寫進去；省略時不寫
        """
        super().__init__(parent)
        self._log = log
        self._retry: Callable[[], None] = lambda: None
        self._sign_in: Optional[Callable[[], bool]] = None
        self._open_settings: Optional[Callable[[], bool]] = None
        self._reload: Optional[Callable[[], None]] = None
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
            CatalogRemedy.RELOAD: self.reload_requested,
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
        self.retry_requested.connect(lambda: self._remedy_then_retry(None))
        self.sign_in_requested.connect(lambda: self._remedy_then_retry(self._sign_in))
        self.settings_requested.connect(lambda: self._remedy_then_retry(self._open_settings))
        self.reload_requested.connect(self._reload_view)
        self.hide()

    def show_failure(self, heading: str, failure: CatalogFailure, retry: Callable[[], None]) -> None:
        """顯示一次失敗並寫進紀錄檔：標題列寫 heading 與這一類的標題、怎麼辦，只留這一類的處理按鈕；詳細資料先收起

        Args:
            heading: 開頭，例如「讀不到譜庫」
            failure: 失敗紀錄
            retry: 重做失敗的那件事，處理按鈕做完時呼叫
        """
        self._retry = retry
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

    def connect_remedies(
        self, sign_in: Optional[Callable[[], bool]] = None, open_settings: Optional[Callable[[], bool]] = None,
        reload: Optional[Callable[[], None]] = None,
    ) -> None:
        """接上「重新登入 Google」「開啟譜庫設定」（做完回傳 True 時收起提示列並重做失敗的那件事）與「重新讀取」

        Args:
            sign_in: 重新登入，回傳是否登入成功；省略時這顆按鈕直接重試
            open_settings: 開啟譜庫設定，回傳是否改了設定；省略時這顆按鈕直接重試
            reload: 重新讀取畫面上的內容；省略時這顆按鈕只收起提示列
        """
        self._sign_in = sign_in
        self._open_settings = open_settings
        self._reload = reload

    def _reload_view(self) -> None:
        """「重新讀取」：收起提示列，重新讀取畫面上的內容，不重做失敗的那件事"""
        self.clear()
        if self._reload is not None:
            self._reload()

    def _remedy_then_retry(self, remedy: Optional[Callable[[], bool]]) -> None:
        """先做 remedy，成功（或沒有 remedy）就收起提示列並重做失敗的那件事"""
        if remedy is None or remedy():
            self.clear()
            self._retry()

    def _toggle_details(self) -> None:
        """展開或收起詳細資料"""
        self._details.setVisible(self._details.isHidden())
