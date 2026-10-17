# -*- coding: utf-8 -*-
"""
壓縮的比較畫面

逐份並排比較原檔與壓縮後的同一頁：左邊是頁面縮圖，點或拖動選位置；右邊上下兩個放大鏡，
對照原檔與壓縮後的同一小塊，放大倍率可選、可翻頁。每份壓縮檔選「留下壓縮後的」或「不要這份」，
全部決定後才能按輸出；也可以用電腦預設的 PDF 閱讀器開啟壓縮後的暫存檔。
原樣複製、壓縮失敗與無法壓縮的只列出狀態，不需要比較。

只接壓縮結果，不管結果從哪裡來、決定之後怎麼交出：工具選單的獨立壓縮與發譜預覽都由這裡開啟。
暫存的壓縮檔由呼叫端在對話框關閉後丟掉。

使用範例：
    dialog = CompressionCompareDialog([("Flute.pdf", result)], ExternalOpener(), parent)
    if dialog.exec() == QDialog.Accepted:
        deliver(dialog.kept())
"""
from typing import List, Optional, Sequence, Tuple

from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QStackedWidget, QVBoxLayout, QWidget,
)

from core.constants import CompressionStatus
from core.locale import t
from services.compression_service import CompressionResult
from services.external_opener import ExternalOpener
from ui.compress_pdf import level_text, problem_text, size_text

# 決定：還沒決定、留下壓縮後的、不要這份
_PENDING, _KEEP, _DROP = None, True, False


def _comparable(result: CompressionResult) -> bool:
    """這份有壓縮檔、要使用者比較後決定"""
    return result.output_path is not None


class CompressionCompareDialog(QDialog):
    """逐份比較原檔與壓縮檔、決定要不要留下的對話框"""

    def __init__(
        self, entries: Sequence[Tuple[str, CompressionResult]], opener: ExternalOpener,
        parent: Optional[QWidget] = None, accept_text: Optional[str] = None,
    ):
        """
        Args:
            entries: 各份的（顯示名稱，壓縮結果），依顯示順序
            opener: 以預設的 PDF 閱讀器開啟壓縮檔
            parent: 父元件
            accept_text: 確認鈕的文字；省略時為「輸出」
        """
        super().__init__(parent)
        self._names = [name for name, _ in entries]
        self._results = [result for _, result in entries]
        self._decisions: List[Optional[bool]] = [_PENDING] * len(self._results)
        self._opener = opener
        self._current = 0
        self.setWindowTitle(t("compare.title"))
        self.resize(1400, 860)
        self._build(accept_text or t("compare.output"))
        self._files.setCurrentRow(self._first_pending())

    def kept(self) -> List[CompressionResult]:
        """要交出的結果：留下的壓縮檔與原樣複製的原檔，依顯示順序"""
        return [
            result for result, decision in zip(self._results, self._decisions)
            if result.status is CompressionStatus.SMALL_ENOUGH or (_comparable(result) and decision is _KEEP)
        ]

    def _build(self, accept_text: str):
        """排出左邊的檔案清單、右邊的比較區與底部的按鈕"""
        self._files = QListWidget()
        self._files.setMaximumWidth(320)
        for _ in self._results:
            self._files.addItem(QListWidgetItem())
        self._files.currentRowChanged.connect(self._pick)
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._stack = QStackedWidget()
        self._stack.addWidget(self._build_compare())
        self._stack.addWidget(self._status)
        body = QHBoxLayout()
        body.addWidget(self._files)
        body.addWidget(self._stack, 1)
        self._pending = QLabel()
        cancel = QPushButton(t("compare.cancel"))
        cancel.clicked.connect(self.reject)
        self._accept = QPushButton(accept_text)
        self._accept.setDefault(True)
        self._accept.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addWidget(self._pending)
        bottom.addStretch()
        bottom.addWidget(cancel)
        bottom.addWidget(self._accept)
        layout = QVBoxLayout(self)
        layout.addLayout(body, 1)
        layout.addLayout(bottom)
        self._refresh_files()

    def _build_compare(self) -> QWidget:
        """比較區：放大鏡與「開啟」「不要這份」「留下壓縮後的」"""
        self._open = QPushButton(t("compare.open"))
        self._open.clicked.connect(self._open_compressed)
        drop = QPushButton(t("compare.drop"))
        drop.clicked.connect(lambda: self._decide(_DROP))
        keep = QPushButton(t("compare.keep"))
        keep.clicked.connect(lambda: self._decide(_KEEP))
        actions = QHBoxLayout()
        actions.addWidget(self._open)
        actions.addStretch()
        actions.addWidget(drop)
        actions.addWidget(keep)
        compare = QWidget()
        layout = QVBoxLayout(compare)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch()
        layout.addLayout(actions)
        return compare

    def _first_pending(self) -> int:
        """第一份還沒決定的壓縮檔；都決定了（或沒有壓縮檔）時為第一份"""
        return next((i for i in self._pending_rows()), 0)

    def _pending_rows(self) -> List[int]:
        """還沒決定的壓縮檔"""
        return [
            i for i, result in enumerate(self._results)
            if _comparable(result) and self._decisions[i] is _PENDING
        ]

    def _pick(self, row: int):
        """選到一份：有壓縮檔時顯示比較區，否則只顯示狀態"""
        if row < 0:
            return
        self._current = row
        result = self._results[row]
        if _comparable(result):
            self._stack.setCurrentIndex(0)
        else:
            self._status.setText(self._state_text(row))
            self._stack.setCurrentIndex(1)

    def _decide(self, decision: bool):
        """記下目前這份的決定，跳到下一份還沒決定的"""
        self._decisions[self._current] = decision
        self._refresh_files()
        pending = self._pending_rows()
        if pending:
            self._files.setCurrentRow(next((i for i in pending if i > self._current), pending[0]))

    def _open_compressed(self):
        """以預設的 PDF 閱讀器開啟目前這份的壓縮檔"""
        self._opener.open(self._results[self._current].output_path)

    def _refresh_files(self):
        """更新清單各份的狀態、待決定的份數與輸出鈕"""
        for row in range(len(self._results)):
            self._files.item(row).setText(f"{self._names[row]}\n{self._state_text(row)}")
        pending = len(self._pending_rows())
        self._pending.setText(t("compare.pending", count=pending) if pending else "")
        self._accept.setEnabled(pending == 0)

    def _state_text(self, row: int) -> str:
        """一份在清單裡的狀態：級數與前後大小加上決定，或沒有壓縮檔的原因"""
        result = self._results[row]
        status = result.status
        if status is CompressionStatus.SMALL_ENOUGH:
            return t("compare.state.small_enough")
        if status is CompressionStatus.FAILED:
            return t("compare.state.failed", reason=problem_text(result))
        if status is CompressionStatus.UNCOMPRESSIBLE:
            return t("compare.state.uncompressible", reason=problem_text(result))
        sizes = t(
            "compare.state.sizes", level=level_text(result.level),
            before=size_text(result.original_size), after=size_text(result.compressed_size),
        )
        if status is CompressionStatus.STILL_TOO_LARGE:
            sizes = t("compare.state.still_too_large", sizes=sizes)
        decision = {_PENDING: "compare.state.pending", _KEEP: "compare.state.kept", _DROP: "compare.state.dropped"}
        return f"{sizes}\n{t(decision[self._decisions[row]])}"
