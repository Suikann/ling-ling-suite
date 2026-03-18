# -*- coding: utf-8 -*-
"""
PDF 旋轉對話框（PySide6）

提供頁面縮圖預覽，讓使用者標記旋轉段落，為不同區段指定不同旋轉角度。
"""
import os
import threading
from typing import Dict, List, Optional, Set, Tuple
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QCheckBox, QScrollArea, QWidget,
    QFileDialog, QMessageBox, QFrame, QSplitter,
)
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtCore import Qt, Signal, QObject
from core.locale import t

SECTION_COLORS = [
    "#3B82F6", "#10B981", "#F59E0B", "#EF4444",
    "#8B5CF6", "#EC4899", "#06B6D4", "#F97316",
]
ROTATION_ANGLES = [0, 90, 180, 270]
ROTATION_LABELS = ["0\u00B0", "90\u00B0", "180\u00B0", "270\u00B0"]

_THUMB_WIDTH = 150
_MAX_COLS = 4


class _ThumbnailSignals(QObject):
    ready = Signal(list)
    error = Signal(str)


class RotatePdfDialog(QDialog):
    """PDF 旋轉對話框"""

    def __init__(self, project=None, on_rotate_complete=None, initial_group=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("rotate.title"))
        self.resize(1100, 720)
        self.setMinimumSize(900, 520)
        self._project = project
        self._on_rotate_complete = on_rotate_complete
        self._filter_group = initial_group
        self._pdf_path: Optional[str] = None
        self._page_count = 0
        self._pil_thumbs = []
        self._pixmaps: List[QPixmap] = []
        self._segment_starts: Set[int] = {0}
        self._section_angles: Dict[int, int] = {}
        self._section_combos: Dict[int, QComboBox] = {}
        self._output_dir: Optional[str] = None
        self._signals = _ThumbnailSignals()
        self._signals.ready.connect(self._on_thumbnails_ready)
        self._signals.error.connect(self._on_thumbnails_error)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        top = QHBoxLayout()
        self._group_filter = QComboBox()
        self._group_filter.setFixedWidth(160)
        self._build_group_filter()
        self._group_filter.currentIndexChanged.connect(self._on_group_filter_changed)
        top.addWidget(self._group_filter)
        self._file_combo = QComboBox()
        self._file_combo.setMinimumWidth(300)
        self._file_combo.currentIndexChanged.connect(self._on_file_selected)
        top.addWidget(self._file_combo, stretch=1)
        browse_btn = QPushButton(t("rotate.browse"))
        browse_btn.clicked.connect(self._browse_file)
        top.addWidget(browse_btn)
        self._page_info = QLabel("")
        top.addWidget(self._page_info)
        layout.addLayout(top)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, stretch=1)
        self._thumb_scroll = QScrollArea()
        self._thumb_scroll.setWidgetResizable(True)
        self._thumb_scroll.setMinimumWidth(500)
        self._thumb_container = QWidget()
        self._thumb_layout = QVBoxLayout(self._thumb_container)
        self._thumb_layout.setContentsMargins(4, 4, 4, 4)
        self._thumb_scroll.setWidget(self._thumb_container)
        splitter.addWidget(self._thumb_scroll)
        right = QWidget()
        right.setFixedWidth(280)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 4, 4)
        rl.setSpacing(4)
        rl.addWidget(QLabel(t("rotate.assignments")))
        hint = QLabel(t("rotate.click_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        rl.addWidget(hint)
        clear_btn = QPushButton(t("rotate.clear_segments"))
        clear_btn.setStyleSheet("border: 1px solid #4a4a4a; color: #888;")
        clear_btn.clicked.connect(self._clear_all_segments)
        rl.addWidget(clear_btn)
        self._assign_scroll = QScrollArea()
        self._assign_scroll.setWidgetResizable(True)
        self._assign_container = QWidget()
        self._assign_layout = QVBoxLayout(self._assign_container)
        self._assign_layout.setContentsMargins(2, 2, 2, 2)
        self._assign_layout.setSpacing(3)
        self._assign_layout.addStretch()
        self._assign_scroll.setWidget(self._assign_container)
        rl.addWidget(self._assign_scroll, stretch=1)
        self._overwrite_cb = QCheckBox(t("rotate.overwrite"))
        self._overwrite_cb.setChecked(True)
        rl.addWidget(self._overwrite_cb)
        outdir_row = QHBoxLayout()
        outdir_row.addWidget(QLabel(t("rotate.save_as") + ":"))
        self._dir_label = QLabel(t("split.same_as_source"))
        self._dir_label.setStyleSheet("color: gray; font-size: 11px;")
        outdir_row.addWidget(self._dir_label, stretch=1)
        dir_btn = QPushButton("...")
        dir_btn.setFixedWidth(32)
        dir_btn.clicked.connect(self._browse_dir)
        outdir_row.addWidget(dir_btn)
        rl.addLayout(outdir_row)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton(t("rotate.cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self._exec_btn = QPushButton(t("rotate.execute"))
        self._exec_btn.setStyleSheet("font-weight: bold;")
        self._exec_btn.setEnabled(False)
        self._exec_btn.clicked.connect(self._execute)
        btn_row.addWidget(self._exec_btn)
        layout.addLayout(btn_row)
        self._refresh_file_combo()

    # --- 群組篩選與檔案選擇 ---

    def _build_group_filter(self):
        self._group_filter.blockSignals(True)
        self._group_filter.clear()
        self._group_filter.addItem(t("group.ungrouped"), None)
        sel_idx = 0
        if self._project:
            for i, g in enumerate(self._project.groups):
                self._group_filter.addItem(g.name or g.id[:8], g)
                if g is self._filter_group:
                    sel_idx = i + 1
        self._group_filter.setCurrentIndex(sel_idx)
        self._group_filter.blockSignals(False)

    def _on_group_filter_changed(self, index):
        self._filter_group = self._group_filter.currentData()
        self._refresh_file_combo()

    def _refresh_file_combo(self):
        self._file_combo.blockSignals(True)
        self._file_combo.clear()
        files = self._collect_files()
        if files:
            self._file_combo.addItem(t("split.no_file"), None)
            for label, path, group in files:
                self._file_combo.addItem(label, (path, group))
        else:
            self._file_combo.addItem(t("split.no_project_files"), None)
        self._file_combo.blockSignals(False)

    def _collect_files(self):
        files = []
        if not self._project:
            return files
        if self._filter_group is None:
            for f in self._project.ungrouped_files:
                if f.original_path.lower().endswith(".pdf"):
                    files.append((f.display_name, f.original_path, None))
        else:
            if self._filter_group.score_file:
                sf = self._filter_group.score_file
                if sf.original_path.lower().endswith(".pdf"):
                    files.append((sf.display_name, sf.original_path, self._filter_group))
            for f in self._filter_group.files:
                if f.original_path.lower().endswith(".pdf"):
                    files.append((f.display_name, f.original_path, self._filter_group))
        return files

    def _on_file_selected(self, index):
        data = self._file_combo.currentData()
        if data:
            path, group = data
            self._load_pdf(path)

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, t("rotate.select_file"), "",
            f"{t('filedialog.pdf_files')} (*.pdf)",
        )
        if not path:
            return
        self._file_combo.blockSignals(True)
        self._file_combo.addItem(os.path.basename(path), (path, None))
        self._file_combo.setCurrentIndex(self._file_combo.count() - 1)
        self._file_combo.blockSignals(False)
        self._load_pdf(path)

    # --- PDF 載入 ---

    def _load_pdf(self, path):
        if not os.path.isfile(path):
            QMessageBox.critical(self, t("dialog.error"), f"File not found:\n{path}")
            return
        try:
            from services.pdf_service import get_page_count
            self._pdf_path = path
            self._page_count = get_page_count(path)
            self._page_info.setText(t("split.page_count", count=self._page_count))
            self._exec_btn.setEnabled(False)
            self._clear_thumb_area()
            loading = QLabel(t("rotate.loading", count=self._page_count))
            loading.setStyleSheet("color: gray; font-size: 14px;")
            loading.setAlignment(Qt.AlignCenter)
            self._thumb_layout.addWidget(loading)
            threading.Thread(
                target=self._render_bg, args=(path,), daemon=True,
            ).start()
        except Exception as e:
            QMessageBox.critical(self, t("dialog.error"), str(e))

    def _render_bg(self, path):
        try:
            from services.pdf_service import render_page_thumbnails
            pil_images = render_page_thumbnails(path, max_width=_THUMB_WIDTH)
            self._signals.ready.emit(pil_images)
        except Exception as e:
            self._signals.error.emit(str(e))

    def _on_thumbnails_ready(self, pil_images):
        self._pil_thumbs = pil_images
        self._pixmaps = []
        for img in pil_images:
            data = img.tobytes("raw", "RGB")
            qimg = QImage(data, img.width, img.height, img.width * 3, QImage.Format_RGB888)
            self._pixmaps.append(QPixmap.fromImage(qimg))
        self._segment_starts = {0}
        self._section_angles = {}
        self._render_grid()
        self._update_assignments()
        self._exec_btn.setEnabled(True)

    def _on_thumbnails_error(self, msg):
        QMessageBox.critical(self, t("dialog.error"), msg)

    # --- 縮圖格線 ---

    def _clear_thumb_area(self):
        while self._thumb_layout.count():
            item = self._thumb_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _render_grid(self):
        self._clear_thumb_area()
        if not self._pixmaps:
            return
        sections = self._get_sections()
        for sec_idx, (start, end) in enumerate(sections):
            color = SECTION_COLORS[sec_idx % len(SECTION_COLORS)]
            angle = self._section_angles.get(sec_idx, 90)
            if sec_idx > 0:
                line = QFrame()
                line.setFrameShape(QFrame.HLine)
                line.setStyleSheet(f"background: {color}; border: none; max-height: 3px;")
                line.setFixedHeight(3)
                self._thumb_layout.addWidget(line)
            total = end - start + 1
            pg = f"p.{start+1}" if start == end else f"p.{start+1}-{end+1}"
            header = QLabel(f"  {sec_idx+1}  |  {pg}  ({total})  |  {angle}\u00B0")
            header.setStyleSheet(f"color: {color}; font-weight: bold; font-size: 11px;")
            self._thumb_layout.addWidget(header)
            row_widget = None
            row_layout = None
            col = 0
            for page_idx in range(start, end + 1):
                if col == 0:
                    row_widget = QWidget()
                    row_layout = QHBoxLayout(row_widget)
                    row_layout.setContentsMargins(4, 2, 4, 2)
                    row_layout.setSpacing(6)
                    self._thumb_layout.addWidget(row_widget)
                thumb = self._create_thumb_widget(page_idx, color, angle)
                row_layout.addWidget(thumb)
                col += 1
                if col >= _MAX_COLS:
                    row_layout.addStretch()
                    col = 0
            if col > 0 and row_layout:
                row_layout.addStretch()
        self._thumb_layout.addStretch()

    def _create_thumb_widget(self, page_idx, color, angle):
        frame = QWidget()
        frame.setStyleSheet(
            f"QWidget {{ border: 2px solid {color}; border-radius: 4px; "
            f"background: #262626; }}"
        )
        fl = QVBoxLayout(frame)
        fl.setContentsMargins(3, 3, 3, 3)
        fl.setSpacing(2)
        img_label = QLabel()
        img_label.setPixmap(self._pixmaps[page_idx])
        img_label.setAlignment(Qt.AlignCenter)
        img_label.setCursor(Qt.PointingHandCursor)
        img_label.mousePressEvent = lambda e, idx=page_idx: self._on_thumb_click(e, idx)
        fl.addWidget(img_label)
        info_text = str(page_idx + 1)
        if angle != 0:
            info_text += f"  {angle}\u00B0"
        num = QLabel(info_text)
        num.setAlignment(Qt.AlignCenter)
        num.setStyleSheet(f"border: none; color: #ccc; font-size: 10px;")
        fl.addWidget(num)
        return frame

    def _on_thumb_click(self, event, page_idx):
        if event.button() == Qt.LeftButton:
            if page_idx == 0:
                return
            if page_idx in self._segment_starts:
                self._segment_starts.discard(page_idx)
            else:
                self._segment_starts.add(page_idx)
            self._render_grid()
            self._update_assignments()
        elif event.button() == Qt.RightButton:
            self._open_preview(page_idx)

    def _get_sections(self) -> List[Tuple[int, int]]:
        starts = sorted(self._segment_starts)
        sections = []
        for i, start in enumerate(starts):
            end = (starts[i + 1] - 1) if (i + 1 < len(starts)) else (self._page_count - 1)
            sections.append((start, end))
        return sections

    def _clear_all_segments(self):
        self._segment_starts = {0}
        self._section_angles = {}
        self._render_grid()
        self._update_assignments()

    # --- 預覽 ---

    def _open_preview(self, page_idx):
        from ui.page_preview import PagePreviewDialog
        dialog = PagePreviewDialog(
            self._pdf_path, page_idx, self._page_count,
            self._segment_starts, set(),
            self._get_sections, self, mode="rotate",
        )
        dialog.exec()
        self._render_grid()
        self._update_assignments()

    # --- 旋轉指派面板 ---

    def _update_assignments(self):
        old_angles = dict(self._section_angles)
        while self._assign_layout.count():
            item = self._assign_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._section_combos = {}
        self._section_angles = {}
        sections = self._get_sections()
        for sec_idx, (start, end) in enumerate(sections):
            color = SECTION_COLORS[sec_idx % len(SECTION_COLORS)]
            angle = old_angles.get(sec_idx, 90)
            self._section_angles[sec_idx] = angle
            row = QHBoxLayout()
            dot = QLabel("\u25CF")
            dot.setStyleSheet(f"color: {color}; font-size: 14px; border: none;")
            dot.setFixedSize(18, 24)
            dot.setAlignment(Qt.AlignCenter)
            row.addWidget(dot)
            combo = QComboBox()
            combo.addItems(ROTATION_LABELS)
            try:
                combo.setCurrentIndex(ROTATION_ANGLES.index(angle))
            except ValueError:
                combo.setCurrentIndex(1)
            combo.currentIndexChanged.connect(
                lambda idx, s=sec_idx: self._on_angle_changed(s, ROTATION_ANGLES[idx]),
            )
            combo.setFixedWidth(75)
            row.addWidget(combo)
            self._section_combos[sec_idx] = combo
            total = end - start + 1
            pg = f"p.{start+1}" if start == end else f"p.{start+1}-{end+1}"
            info = QLabel(f"{pg} ({total})")
            info.setStyleSheet("color: gray; font-size: 11px;")
            row.addWidget(info)
            row.addStretch()
            wrapper = QWidget()
            wrapper.setLayout(row)
            self._assign_layout.insertWidget(self._assign_layout.count(), wrapper)
        self._assign_layout.addStretch()

    def _on_angle_changed(self, sec_idx, angle):
        self._section_angles[sec_idx] = angle
        self._render_grid()

    # --- 輸出 ---

    def _browse_dir(self):
        folder = QFileDialog.getExistingDirectory(self, t("split.output_dir"))
        if folder:
            self._output_dir = folder
            self._dir_label.setText(folder)
            self._dir_label.setStyleSheet("font-size: 11px;")

    def _execute(self):
        if not self._pdf_path:
            return
        sections = self._get_sections()
        rotation_ops = []
        for sec_idx, (start, end) in enumerate(sections):
            angle = self._section_angles.get(sec_idx, 90)
            rotation_ops.append((start, end, angle))
        has_rotation = any(angle != 0 for _, _, angle in rotation_ops)
        if not has_rotation:
            QMessageBox.information(self, t("dialog.info"), t("rotate.no_rotation_needed"))
            return
        if self._overwrite_cb.isChecked():
            output_path = self._pdf_path
        else:
            output_path, _ = QFileDialog.getSaveFileName(
                self, t("rotate.save_as"), os.path.basename(self._pdf_path),
                f"{t('filedialog.pdf_files')} (*.pdf)",
            )
            if not output_path:
                return
        try:
            backup_path = ""
            if self._overwrite_cb.isChecked():
                from services.undo_service import UndoService
                backup_path = UndoService.create_backup(self._pdf_path)
            from services.pdf_service import rotate_pdf_sections
            rotate_pdf_sections(self._pdf_path, rotation_ops, output_path)
            if self._on_rotate_complete:
                if self._overwrite_cb.isChecked():
                    self._on_rotate_complete(backup_path, self._pdf_path)
                else:
                    self._on_rotate_complete("", output_path)
            QMessageBox.information(self, t("dialog.complete"), t("rotate.done"))
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, t("dialog.error"), str(e))
