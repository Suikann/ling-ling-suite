# -*- coding: utf-8 -*-
"""
壓縮的比較畫面

逐份並排比較原檔與壓縮後的同一頁：左邊是頁面縮圖，點或拖動選位置；右邊上下兩個放大鏡，
對照原檔與壓縮後的同一小塊，放大倍率可選、可翻頁。每份壓縮檔選「留下壓縮後的」或「不要這份」，
全部決定後才能按輸出；也可以用電腦預設的 PDF 閱讀器開啟壓縮後的暫存檔。
原樣複製、壓縮失敗與無法壓縮的只列出狀態，不需要比較。

只接壓縮結果，不管結果從哪裡來、決定之後怎麼交出：工具選單的獨立壓縮與發譜預覽都由這裡開啟。
頁面在背景執行緒算繪（services/pdf_service.py 的 render_page_gray），暫存的壓縮檔由呼叫端在對話框關閉後丟掉。

使用範例：
    dialog = CompressionCompareDialog([("Flute.pdf", result)], ExternalOpener(), parent)
    if dialog.exec() == QDialog.Accepted:
        deliver(dialog.kept())
"""
from typing import Callable, List, NamedTuple, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

from core.constants import CompressionStatus
from core.locale import t
from services.compression_service import CompressionResult
from services.external_opener import ExternalOpener
from services.pdf_service import render_page_gray
from ui.compress_pdf import level_text, problem_text, size_text
from ui.widgets import ThumbnailLoader

# 決定：還沒決定、留下壓縮後的、不要這份
_PENDING, _KEEP, _DROP = None, True, False
# 原檔與壓縮檔都以這個 dpi 算繪，放大鏡才看得出 600 dpi 原檔與降解析度後的差別
_RENDER_DPI = 600
# 縮圖的寬度（像素）
_OVERVIEW_WIDTH = 360
# 放大倍率：放大鏡顯示頁寬的幾分之一
_ZOOMS = (2, 4, 8)
_DEFAULT_ZOOM = 4
# 剛開啟一頁時看的位置（頁寬、頁高的比例）：左上角第一行，指法小字通常在這裡
_START_FOCUS = (0.25, 0.12)
_REGION_COLOR = QColor("#e0457b")


class _Pages(NamedTuple):
    """背景算繪出的一頁：原檔與壓縮檔的同一頁，與原檔的縮圖、頁數"""
    original: QImage
    compressed: QImage
    overview: QImage
    page_count: int


def _comparable(result: CompressionResult) -> bool:
    """這份有壓縮檔、要使用者比較後決定"""
    return result.output_path is not None


def _to_qimage(image) -> QImage:
    """灰階的 PIL Image 轉成自己持有資料的 QImage（可以在背景執行緒呼叫）"""
    data = image.tobytes()
    return QImage(data, image.width, image.height, image.width, QImage.Format_Grayscale8).copy()


def _render_pages(source: str, compressed: str, page: int) -> _Pages:
    """算繪原檔與壓縮檔的同一頁（在背景執行緒呼叫）"""
    original = render_page_gray(source, page, _RENDER_DPI)
    after = render_page_gray(compressed, page, _RENDER_DPI)
    original_image = _to_qimage(original.image)
    overview = original_image.scaledToWidth(_OVERVIEW_WIDTH, Qt.SmoothTransformation)
    return _Pages(original_image, _to_qimage(after.image), overview, original.page_count)


class _Loupe(QWidget):
    """放大鏡：把頁面上以焦點為中心、寬為頁寬 span 的一小塊放大顯示"""

    def __init__(self):
        super().__init__()
        self.image = QImage()
        self.focus = QPointF(*_START_FOCUS)
        self.span = 1 / _DEFAULT_ZOOM
        self.setMinimumSize(480, 200)

    def region(self) -> QRectF:
        """放大鏡看的那一塊，以頁寬、頁高的比例表示"""
        if self.image.isNull():
            return QRectF()
        height = self.span * self.image.width() * self.height() / max(1, self.width()) / self.image.height()
        return QRectF(self.focus.x() - self.span / 2, self.focus.y() - height / 2, self.span, height)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.white)
        if self.image.isNull():
            return
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        region = self.region()
        width, height = self.image.width(), self.image.height()
        source = QRectF(region.x() * width, region.y() * height, region.width() * width, region.height() * height)
        painter.drawImage(QRectF(self.rect()), self.image, source)


class _Overview(QWidget):
    """頁面縮圖：框出放大鏡看的位置，點或拖動換位置"""

    def __init__(self, region: Callable[[], QRectF], on_focus: Callable[[QPointF], None]):
        super().__init__()
        self.image = QImage()
        self._region = region
        self._on_focus = on_focus
        self.setFixedWidth(_OVERVIEW_WIDTH)
        self.setMinimumHeight(int(_OVERVIEW_WIDTH * 1.414))
        self.setCursor(Qt.CrossCursor)

    def set_image(self, image: QImage):
        self.image = image
        self.setFixedHeight(image.height() if not image.isNull() else int(_OVERVIEW_WIDTH * 1.414))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.white)
        if self.image.isNull():
            return
        painter.drawImage(0, 0, self.image)
        region = self._region()
        painter.setPen(QPen(_REGION_COLOR, 2))
        painter.drawRect(QRectF(
            region.x() * self.image.width(), region.y() * self.image.height(),
            region.width() * self.image.width(), region.height() * self.image.height(),
        ))

    def mousePressEvent(self, event):
        self.mouseMoveEvent(event)

    def mouseMoveEvent(self, event):
        if self.image.isNull():
            return
        x = min(1.0, max(0.0, event.position().x() / self.image.width()))
        y = min(1.0, max(0.0, event.position().y() / self.image.height()))
        self._on_focus(QPointF(x, y))


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
        self._page = 0
        self._page_count = 0
        self._loader = ThumbnailLoader(_render_pages, self._show_pages, self._show_render_error)
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

    def done(self, code: int):
        """關閉時作廢還沒算繪完的頁面，放掉算繪好的頁面（600 dpi 的一頁有數十 MB）"""
        self._loader.cancel()
        for loupe in (self._loupe_original, self._loupe_compressed):
            loupe.image = QImage()
        self._overview.set_image(QImage())
        super().done(code)

    def _build(self, accept_text: str):
        """排出左邊的檔案清單、右邊的比較區與底部的按鈕"""
        self._files = QListWidget()
        self._files.setMaximumWidth(320)
        for _ in self._results:
            self._files.addItem(QListWidgetItem())
        self._files.currentRowChanged.connect(self._pick)
        self._status = QLabel()
        self._status.setWordWrap(True)
        self._status.setAlignment(Qt.AlignTop | Qt.AlignLeft)
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
        """比較區：翻頁與放大倍率、縮圖與兩個放大鏡、「開啟」「不要這份」「留下壓縮後的」"""
        self._prev = QPushButton(t("compare.prev"))
        self._prev.clicked.connect(lambda: self._turn(-1))
        self._next = QPushButton(t("compare.next"))
        self._next.clicked.connect(lambda: self._turn(1))
        self._page_label = QLabel()
        self._zoom = QComboBox()
        for factor in _ZOOMS:
            self._zoom.addItem(t("compare.zoom", factor=factor), factor)
        self._zoom.setCurrentIndex(_ZOOMS.index(_DEFAULT_ZOOM))
        self._zoom.currentIndexChanged.connect(self._set_zoom)
        tools = QHBoxLayout()
        tools.addWidget(self._prev)
        tools.addWidget(self._page_label)
        tools.addWidget(self._next)
        tools.addStretch()
        tools.addWidget(self._zoom)
        self._loupe_original, self._loupe_compressed = _Loupe(), _Loupe()
        self._overview = _Overview(self._loupe_original.region, self._set_focus)
        self._note = QLabel()
        left = QVBoxLayout()
        left.addWidget(QLabel(t("compare.hint")))
        left.addWidget(self._overview)
        left.addWidget(self._note)
        left.addStretch()
        loupes = QVBoxLayout()
        loupes.addWidget(QLabel(t("compare.original")))
        loupes.addWidget(self._loupe_original, 1)
        loupes.addWidget(QLabel(t("compare.compressed")))
        loupes.addWidget(self._loupe_compressed, 1)
        view = QHBoxLayout()
        view.addLayout(left)
        view.addLayout(loupes, 1)
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
        layout.addLayout(tools)
        layout.addLayout(view, 1)
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
        """選到一份：有壓縮檔時從第一頁開始比較，否則只顯示狀態"""
        if row < 0:
            return
        self._current = row
        result = self._results[row]
        if _comparable(result):
            self._stack.setCurrentIndex(0)
            self._page_count = 0
            self._show_page(0)
        else:
            self._loader.cancel()
            self._status.setText(f"{self._names[row]}\n{self._state_text(row)}")
            self._stack.setCurrentIndex(1)

    def _turn(self, step: int):
        """往前或往後翻一頁"""
        self._show_page(min(max(0, self._page + step), max(0, self._page_count - 1)))

    def _show_page(self, page: int):
        """開始在背景算繪目前這份的第 page 頁（從 0 起）；算繪完成前翻頁鈕停用"""
        result = self._results[self._current]
        self._page = page
        self._prev.setEnabled(False)
        self._next.setEnabled(False)
        self._note.setText(t("compare.loading"))
        self._loader.load(result.source, result.output_path, page)

    def _show_pages(self, pages: _Pages):
        """背景算繪完成：顯示縮圖、兩個放大鏡與頁碼"""
        self._page_count = pages.page_count
        self._note.setText("")
        self._page_label.setText(t("compare.page", page=self._page + 1, count=pages.page_count))
        self._prev.setEnabled(self._page > 0)
        self._next.setEnabled(self._page < pages.page_count - 1)
        self._loupe_original.image = pages.original
        self._loupe_compressed.image = pages.compressed
        self._overview.set_image(pages.overview)
        self._loupe_original.update()
        self._loupe_compressed.update()

    def _show_render_error(self, message: str):
        """背景算繪失敗：寫出原因，翻頁鈕照頁數恢復"""
        self._note.setText(t("compare.render_failed", error=message))
        self._prev.setEnabled(self._page > 0)
        self._next.setEnabled(self._page < self._page_count - 1)

    def _set_zoom(self, index: int):
        """換放大倍率：兩個放大鏡一起換"""
        span = 1 / self._zoom.itemData(index)
        for loupe in (self._loupe_original, self._loupe_compressed):
            loupe.span = span
            loupe.update()
        self._overview.update()

    def _set_focus(self, focus: QPointF):
        """換放大鏡看的位置：兩個放大鏡看同一塊"""
        for loupe in (self._loupe_original, self._loupe_compressed):
            loupe.focus = focus
            loupe.update()
        self._overview.update()

    def _decide(self, decision: bool):
        """記下目前這份的決定，跳到下一份還沒決定的"""
        self._decisions[self._current] = decision
        self._refresh_files()
        pending = self._pending_rows()
        if pending:
            self._files.setCurrentRow(next((i for i in pending if i > self._current), pending[0]))

    def _open_compressed(self):
        """以預設的 PDF 閱讀器開啟目前這份的壓縮檔"""
        if not self._opener.open(self._results[self._current].output_path):
            QMessageBox.warning(self, t("dialog.warning"), t("compare.open_failed"))

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
