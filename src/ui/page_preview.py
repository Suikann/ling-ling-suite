# -*- coding: utf-8 -*-
"""
頁面預覽對話框（PySide6）

提供 PDF 單頁放大預覽、翻頁、分割點切換與刪除頁面功能。
"""
from typing import Set, Callable, List, Tuple
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


class PagePreviewDialog(QDialog):
    """頁面放大預覽"""

    def __init__(
        self, pdf_path: str, page_idx: int, page_count: int,
        split_starts: Set[int], deleted_pages: Set[int],
        get_sections: Callable, parent=None,
    ):
        super().__init__(parent)
        self._pdf_path = pdf_path
        self._page_idx = page_idx
        self._page_count = page_count
        self._split_starts = split_starts
        self._deleted_pages = deleted_pages
        self._get_sections = get_sections
        self.setWindowTitle(t("split.page_label", num=page_idx + 1))
        screen_h = self.screen().availableGeometry().height()
        win_h = min(int(screen_h * 0.84), 950)
        chrome = 90
        self._render_w = min(600, int((win_h - chrome) / 1.414))
        self.resize(self._render_w + 130, win_h)
        self._build_ui()
        self._render_page()
        QShortcut(QKeySequence(Qt.Key_Left), self, self._prev)
        QShortcut(QKeySequence(Qt.Key_Right), self, self._next)
        QShortcut(QKeySequence(Qt.Key_Space), self, self._toggle_split)
        QShortcut(QKeySequence(Qt.Key_Delete), self, self._toggle_delete)
        QShortcut(QKeySequence(Qt.Key_Escape), self, self.close)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        top = QHBoxLayout()
        self._page_label = QLabel()
        self._page_label.setAlignment(Qt.AlignCenter)
        top.addWidget(self._page_label)
        layout.addLayout(top)
        mid = QHBoxLayout()
        nav_style = (
            "QPushButton { font-size: 20px; font-weight: bold; border-radius: 22px; "
            "background: #3a322c; color: #ded8d0; border: 1px solid #4e4438; }"
            "QPushButton:hover { background: #463c34; border-color: #c89530; }"
            "QPushButton:pressed { background: #342c26; }"
            "QPushButton:disabled { color: #6e6458; background: #2c2622; border-color: #3e3630; }"
        )
        self._prev_btn = QPushButton("\u25C0")
        self._prev_btn.setFixedSize(44, 44)
        self._prev_btn.setStyleSheet(nav_style)
        self._prev_btn.clicked.connect(self._prev)
        mid.addWidget(self._prev_btn, alignment=Qt.AlignVCenter)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet("QScrollArea { border: none; }")
        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignCenter)
        scroll.setWidget(self._img_label)
        mid.addWidget(scroll, stretch=1)
        self._next_btn = QPushButton("\u25B6")
        self._next_btn.setFixedSize(44, 44)
        self._next_btn.setStyleSheet(nav_style)
        self._next_btn.clicked.connect(self._next)
        mid.addWidget(self._next_btn, alignment=Qt.AlignVCenter)
        mid_widget = QWidget()
        mid_widget.setLayout(mid)
        layout.addWidget(mid_widget, stretch=1)
        bottom = QHBoxLayout()
        self._split_btn = QPushButton()
        self._split_btn.setFixedHeight(32)
        self._split_btn.clicked.connect(self._toggle_split)
        bottom.addWidget(self._split_btn)
        self._delete_btn = QPushButton()
        self._delete_btn.setFixedHeight(32)
        self._delete_btn.clicked.connect(self._toggle_delete)
        bottom.addWidget(self._delete_btn)
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
        self._page_label.setText(f"{self._page_idx + 1} / {self._page_count}")
        self._prev_btn.setEnabled(self._page_idx > 0)
        self._next_btn.setEnabled(self._page_idx < self._page_count - 1)
        self._update_split_btn()
        self._update_delete_btn()

    def _update_split_btn(self):
        idx = self._page_idx
        sec_idx = self._get_section_for_page(idx)
        color = SECTION_COLORS[sec_idx % len(SECTION_COLORS)]
        if idx == 0:
            self._split_btn.setText(t("split.mark_split"))
            self._split_btn.setEnabled(False)
        elif idx in self._split_starts:
            self._split_btn.setText(t("split.remove_split"))
            self._split_btn.setEnabled(True)
        else:
            self._split_btn.setText(t("split.mark_split"))
            self._split_btn.setEnabled(True)
        self._split_btn.setStyleSheet(
            f"QPushButton {{ border-radius: 16px; font-weight: bold; color: white; "
            f"padding: 0 16px; background: {color}; border: none; }}"
            f"QPushButton:hover {{ opacity: 0.85; }}"
            f"QPushButton:disabled {{ background: #3a322c; color: #6e6458; }}"
        )

    def _update_delete_btn(self):
        if self._page_idx in self._deleted_pages:
            self._delete_btn.setText(t("split.restore_page"))
            self._delete_btn.setStyleSheet(
                "QPushButton { border-radius: 16px; padding: 0 16px; "
                "background: #2563EB; color: white; border: none; }"
                "QPushButton:hover { background: #3B82F6; }"
            )
        else:
            self._delete_btn.setText(t("split.delete_page"))
            self._delete_btn.setStyleSheet(
                "QPushButton { border-radius: 16px; padding: 0 16px; "
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
        self._update_split_btn()

    def _toggle_delete(self):
        if self._page_idx in self._deleted_pages:
            self._deleted_pages.discard(self._page_idx)
        else:
            self._deleted_pages.add(self._page_idx)
        self._update_delete_btn()
