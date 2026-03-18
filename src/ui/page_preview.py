# -*- coding: utf-8 -*-
"""
頁面預覽對話框（PySide6）

提供 PDF 單頁放大預覽、翻頁、分割點/段落邊界切換與刪除頁面功能。
支援分割模式（split）與旋轉模式（rotate）。
"""
from typing import Set, Callable
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QWidget,
)
from PySide6.QtGui import QPixmap, QImage, QKeySequence, QShortcut
from PySide6.QtCore import Qt
from core.locale import t

SECTION_COLORS = [
    "#3B82F6", "#10B981", "#F59E0B", "#EF4444",
    "#8B5CF6", "#EC4899", "#06B6D4", "#F97316",
]

_NAV_STYLE = (
    "QPushButton { font-size: 22px; font-weight: bold; border-radius: 24px; "
    "background: rgba(58, 50, 44, 25); color: rgba(222, 216, 208, 35); "
    "border: none; }"
    "QPushButton:hover { background: rgba(58, 50, 44, 210); "
    "color: #ded8d0; border: 1px solid #4e4438; }"
    "QPushButton:pressed { background: rgba(52, 44, 38, 230); color: #ded8d0; }"
    "QPushButton:disabled { background: transparent; color: transparent; }"
)


class _PageArea(QWidget):
    """頁面顯示區域，翻頁按鈕覆蓋於譜面上方"""

    def __init__(self, scroll, prev_btn, next_btn, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(scroll)
        self._scroll = scroll
        self._prev = prev_btn
        self._next = next_btn
        prev_btn.setParent(self)
        next_btn.setParent(self)
        prev_btn.raise_()
        next_btn.raise_()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        h = self.height()
        y = h // 2 - self._prev.height() // 2
        self._prev.move(8, y)
        self._next.move(self.width() - self._next.width() - 8, y)


class PagePreviewDialog(QDialog):
    """頁面放大預覽

    Args:
        mode: "split"（預設）或 "rotate"
    """

    def __init__(
        self, pdf_path: str, page_idx: int, page_count: int,
        split_starts: Set[int], deleted_pages: Set[int],
        get_sections: Callable, parent=None, *, mode: str = "split",
    ):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._page_idx = page_idx
        self._page_count = page_count
        self._split_starts = split_starts
        self._deleted_pages = deleted_pages
        self._get_sections = get_sections
        self._mode = mode
        self.setWindowTitle(t("split.page_label", num=page_idx + 1))
        screen_h = self.screen().availableGeometry().height()
        win_h = min(int(screen_h * 0.84), 950)
        chrome = 90
        self._render_w = min(600, int((win_h - chrome) / 1.414))
        self.resize(self._render_w + 80, win_h)
        self._build_ui()
        self._render_page()
        QShortcut(QKeySequence(Qt.Key_Left), self, self._prev)
        QShortcut(QKeySequence(Qt.Key_Right), self, self._next)
        QShortcut(QKeySequence(Qt.Key_Space), self, self._toggle_split)
        if mode == "split":
            QShortcut(QKeySequence(Qt.Key_Delete), self, self._toggle_delete)
        QShortcut(QKeySequence(Qt.Key_Escape), self, self.close)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        top = QHBoxLayout()
        self._section_dot = QLabel("\u25CF")
        self._section_dot.setFixedWidth(20)
        self._section_dot.setAlignment(Qt.AlignCenter)
        top.addWidget(self._section_dot)
        self._page_label = QLabel()
        self._page_label.setAlignment(Qt.AlignCenter)
        top.addWidget(self._page_label)
        layout.addLayout(top)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignCenter)
        scroll.setWidget(self._img_label)
        self._prev_btn = QPushButton("\u25C0")
        self._prev_btn.setFixedSize(48, 48)
        self._prev_btn.setStyleSheet(_NAV_STYLE)
        self._prev_btn.setCursor(Qt.PointingHandCursor)
        self._prev_btn.clicked.connect(self._prev)
        self._next_btn = QPushButton("\u25B6")
        self._next_btn.setFixedSize(48, 48)
        self._next_btn.setStyleSheet(_NAV_STYLE)
        self._next_btn.setCursor(Qt.PointingHandCursor)
        self._next_btn.clicked.connect(self._next)
        page_area = _PageArea(scroll, self._prev_btn, self._next_btn)
        layout.addWidget(page_area, stretch=1)
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self._split_btn = QPushButton()
        self._split_btn.setFixedHeight(36)
        self._split_btn.clicked.connect(self._toggle_split)
        bottom.addWidget(self._split_btn)
        if self._mode == "split":
            self._delete_btn = QPushButton()
            self._delete_btn.setFixedHeight(36)
            self._delete_btn.clicked.connect(self._toggle_delete)
            bottom.addWidget(self._delete_btn)
        else:
            self._delete_btn = None
        bottom.addStretch()
        layout.addLayout(bottom)

    def _render_page(self):
        try:
            from services.pdf_service import render_single_page
            pil = render_single_page(self._pdf_path, self._page_idx, max_width=self._render_w)
            data = pil.tobytes("raw", "RGB")
            qimg = QImage(data, pil.width, pil.height, pil.width * 3, QImage.Format_RGB888)
            self._img_label.setPixmap(QPixmap.fromImage(qimg))
        except Exception:
            pass
        self.setWindowTitle(t("split.page_label", num=self._page_idx + 1))
        sec_idx = self._get_section_for_page(self._page_idx)
        color = SECTION_COLORS[sec_idx % len(SECTION_COLORS)]
        self._section_dot.setStyleSheet(f"color: {color}; font-size: 16px;")
        self._page_label.setText(
            f"{self._page_idx + 1} / {self._page_count}"
        )
        self._prev_btn.setEnabled(self._page_idx > 0)
        self._next_btn.setEnabled(self._page_idx < self._page_count - 1)
        self._update_split_btn()
        if self._delete_btn:
            self._update_delete_btn()

    def _update_split_btn(self):
        idx = self._page_idx
        sec_idx = self._get_section_for_page(idx)
        color = SECTION_COLORS[sec_idx % len(SECTION_COLORS)]
        is_rotate = self._mode == "rotate"
        mark_key = "rotate.mark_segment" if is_rotate else "split.mark_split"
        remove_key = "rotate.remove_segment" if is_rotate else "split.remove_split"
        if idx == 0:
            self._split_btn.setText(t(mark_key))
            self._split_btn.setEnabled(False)
        elif idx in self._split_starts:
            self._split_btn.setText(t(remove_key))
            self._split_btn.setEnabled(True)
        else:
            self._split_btn.setText(t(mark_key))
            self._split_btn.setEnabled(True)
        self._split_btn.setStyleSheet(
            f"QPushButton {{ border-radius: 18px; font-weight: bold; color: white; "
            f"padding: 0 20px; background: {color}; border: none; }}"
            f"QPushButton:hover {{ opacity: 0.85; }}"
            f"QPushButton:disabled {{ background: #3a322c; color: #6e6458; }}"
        )

    def _update_delete_btn(self):
        if self._page_idx in self._deleted_pages:
            self._delete_btn.setText(t("split.restore_page"))
            self._delete_btn.setStyleSheet(
                "QPushButton { border-radius: 18px; padding: 0 20px; "
                "background: #2563EB; color: white; border: none; }"
                "QPushButton:hover { background: #3B82F6; }"
            )
        else:
            self._delete_btn.setText(t("split.delete_page"))
            self._delete_btn.setStyleSheet(
                "QPushButton { border-radius: 18px; padding: 0 20px; "
                "background: #8b2020; color: #eed8d0; border: none; }"
                "QPushButton:hover { background: #a02828; }"
            )

    def _get_section_for_page(self, page_idx):
        for sec_idx, (start, end) in enumerate(self._get_sections()):
            if start <= page_idx <= end:
                return sec_idx
        return 0

    def _prev(self):
        if self._page_idx > 0:
            self._page_idx -= 1
            self._render_page()

    def _next(self):
        if self._page_idx < self._page_count - 1:
            self._page_idx += 1
            self._render_page()

    def _toggle_split(self):
        if self._page_idx == 0:
            return
        if self._page_idx in self._split_starts:
            self._split_starts.discard(self._page_idx)
        else:
            self._split_starts.add(self._page_idx)
        self._render_page()

    def _toggle_delete(self):
        if not self._delete_btn:
            return
        if self._page_idx in self._deleted_pages:
            self._deleted_pages.discard(self._page_idx)
        else:
            self._deleted_pages.add(self._page_idx)
        self._update_delete_btn()
