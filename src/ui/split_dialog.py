# -*- coding: utf-8 -*-
"""
PDF 分割對話框（PySide6）

提供頁面縮圖預覽，讓使用者標記分割點，將合併的 PDF 拆分為各樂器的獨立檔案。
"""
import os
import threading
from typing import Callable, Dict, List, Optional, Set, Tuple
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QCheckBox, QScrollArea, QWidget,
    QFileDialog, QMessageBox, QFrame, QSplitter,
)
from PySide6.QtGui import QPixmap, QImage, QColor
from PySide6.QtCore import Qt, Signal, QObject
from core.locale import t
from core.models import FileInfo

SECTION_COLORS = [
    "#3B82F6", "#10B981", "#F59E0B", "#EF4444",
    "#8B5CF6", "#EC4899", "#06B6D4", "#F97316",
]

_THUMB_WIDTH = 150
_MAX_COLS = 4


class _ThumbnailSignals(QObject):
    ready = Signal(list)
    error = Signal(str)


class SplitPdfDialog(QDialog):
    """PDF 分割對話框"""

    def __init__(self, project, on_split_complete=None, initial_group=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("split.title"))
        self.resize(1100, 720)
        self.setMinimumSize(900, 520)
        self._project = project
        self._on_split_complete = on_split_complete
        self._filter_group = initial_group
        self._pdf_path: Optional[str] = None
        self._page_count = 0
        self._pil_thumbs = []
        self._pixmaps: List[QPixmap] = []
        self._split_starts: Set[int] = {0}
        self._deleted_pages: Set[int] = set()
        self._section_name_entries: Dict[int, QLineEdit] = {}
        self._source_group = None
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
        rl.addWidget(QLabel(t("split.assignments")))
        hint = QLabel(t("split.click_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray; font-size: 11px;")
        rl.addWidget(hint)
        clear_btn = QPushButton(t("split.clear_splits"))
        clear_btn.setStyleSheet("border: 1px solid #4a4a4a; color: #888;")
        clear_btn.clicked.connect(self._clear_all_splits)
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
        subfolder_row = QHBoxLayout()
        self._subfolder_cb = QCheckBox(t("split.use_subfolder"))
        self._subfolder_cb.setChecked(True)
        subfolder_row.addWidget(self._subfolder_cb)
        self._subfolder_entry = QLineEdit(t("split.subfolder_default"))
        self._subfolder_entry.setFixedWidth(80)
        subfolder_row.addWidget(self._subfolder_entry)
        rl.addLayout(subfolder_row)
        outdir_row = QHBoxLayout()
        outdir_row.addWidget(QLabel(t("split.output_dir")))
        self._dir_label = QLabel(t("split.same_as_source"))
        self._dir_label.setStyleSheet("color: gray; font-size: 11px;")
        outdir_row.addWidget(self._dir_label, stretch=1)
        browse_btn = QPushButton("...")
        browse_btn.setFixedWidth(32)
        browse_btn.clicked.connect(self._browse_dir)
        outdir_row.addWidget(browse_btn)
        rl.addLayout(outdir_row)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton(t("split.cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self._exec_btn = QPushButton(t("split.execute"))
        self._exec_btn.setStyleSheet("font-weight: bold;")
        self._exec_btn.setEnabled(False)
        self._exec_btn.clicked.connect(self._execute_split)
        btn_row.addWidget(self._exec_btn)
        layout.addLayout(btn_row)
        self._refresh_file_combo()

    # --- Group filter ---

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
            self._source_group = group
            self._load_pdf(path)

    # --- PDF loading ---

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
            loading = QLabel(t("split.loading", count=self._page_count))
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
        self._split_starts = {0}
        self._deleted_pages = set()
        self._render_grid()
        self._update_assignments()
        self._exec_btn.setEnabled(True)

    def _on_thumbnails_error(self, msg):
        QMessageBox.critical(self, t("dialog.error"), msg)

    # --- Thumbnail grid ---

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
            if sec_idx > 0:
                line = QFrame()
                line.setFrameShape(QFrame.HLine)
                line.setStyleSheet(f"background: {color}; border: none; max-height: 3px;")
                line.setFixedHeight(3)
                self._thumb_layout.addWidget(line)
            actual = sum(1 for p in range(start, end + 1) if p not in self._deleted_pages)
            total = end - start + 1
            pg = f"p.{start+1}" if start == end else f"p.{start+1}-{end+1}"
            cnt = f"({actual}/{total})" if actual < total else f"({total})"
            header = QLabel(f"  {sec_idx+1}  |  {pg}  {cnt}")
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
                is_deleted = page_idx in self._deleted_pages
                border_color = "#666" if is_deleted else color
                thumb = self._create_thumb_widget(page_idx, border_color, is_deleted)
                row_layout.addWidget(thumb)
                col += 1
                if col >= _MAX_COLS:
                    row_layout.addStretch()
                    col = 0
            if col > 0 and row_layout:
                row_layout.addStretch()
        self._thumb_layout.addStretch()

    def _create_thumb_widget(self, page_idx, color, is_deleted):
        frame = QWidget()
        frame.setStyleSheet(
            f"QWidget {{ border: 2px solid {color}; border-radius: 4px; "
            f"background: {'#333' if is_deleted else '#262626'}; }}"
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
        num_text = f"{page_idx+1} X" if is_deleted else str(page_idx + 1)
        num = QLabel(num_text)
        num.setAlignment(Qt.AlignCenter)
        num.setStyleSheet(f"border: none; color: {'#888' if is_deleted else '#ccc'}; font-size: 10px;")
        fl.addWidget(num)
        return frame

    def _on_thumb_click(self, event, page_idx):
        if event.button() == Qt.LeftButton:
            if page_idx == 0:
                return
            if page_idx in self._split_starts:
                self._split_starts.discard(page_idx)
            else:
                self._split_starts.add(page_idx)
            self._render_grid()
            self._update_assignments()
        elif event.button() == Qt.RightButton:
            self._open_preview(page_idx)

    def _get_sections(self) -> List[Tuple[int, int]]:
        starts = sorted(self._split_starts)
        sections = []
        for i, start in enumerate(starts):
            end = (starts[i + 1] - 1) if (i + 1 < len(starts)) else (self._page_count - 1)
            sections.append((start, end))
        return sections

    def _clear_all_splits(self):
        self._split_starts = {0}
        self._render_grid()
        self._section_name_entries = {}
        self._update_assignments()

    # --- Preview ---

    def _open_preview(self, page_idx):
        from ui.page_preview import PagePreviewDialog
        dialog = PagePreviewDialog(
            self._pdf_path, page_idx, self._page_count,
            self._split_starts, self._deleted_pages,
            self._get_sections, self,
        )
        dialog.exec()
        self._render_grid()
        self._update_assignments()

    # --- Assignment panel ---

    def _get_used_instruments(self) -> set:
        used = set()
        if not self._project:
            return used
        for group in self._project.groups:
            used.update(group.instruments)
        return used

    def _next_unused(self, used, start_after=-1):
        instruments = self._project.instruments if self._project else []
        for i in range(start_after + 1, len(instruments)):
            if instruments[i] not in used:
                return instruments[i]
        return t("split.part_default", index=len(used) + 1)

    def _update_assignments(self):
        old_names = {}
        for idx, entry in self._section_name_entries.items():
            old_names[idx] = entry.text()
        while self._assign_layout.count():
            item = self._assign_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._section_name_entries = {}
        sections = self._get_sections()
        instruments = self._project.instruments if self._project else []
        used = set(self._get_used_instruments())
        last_idx = -1
        for sec_idx, (start, end) in enumerate(sections):
            color = SECTION_COLORS[sec_idx % len(SECTION_COLORS)]
            row = QHBoxLayout()
            dot = QLabel("\u25CF")
            dot.setStyleSheet(f"color: {color}; font-size: 14px; border: none;")
            dot.setFixedWidth(18)
            row.addWidget(dot)
            if sec_idx in old_names:
                name = old_names[sec_idx]
            elif instruments:
                name = self._next_unused(used, start_after=last_idx)
            else:
                name = t("split.part_default", index=sec_idx + 1)
            used.add(name)
            try:
                last_idx = instruments.index(name)
            except (ValueError, AttributeError):
                pass
            if instruments:
                prev_btn = QPushButton("\u25C0")
                prev_btn.setFixedSize(22, 22)
                prev_btn.clicked.connect(
                    lambda checked=False, s=sec_idx: self._cycle_instrument(s, -1),
                )
                row.addWidget(prev_btn)
            entry = QLineEdit(name)
            entry.setFixedHeight(24)
            row.addWidget(entry, stretch=1)
            self._section_name_entries[sec_idx] = entry
            if instruments:
                next_btn = QPushButton("\u25B6")
                next_btn.setFixedSize(22, 22)
                next_btn.clicked.connect(
                    lambda checked=False, s=sec_idx: self._cycle_instrument(s, 1),
                )
                row.addWidget(next_btn)
            actual = sum(1 for p in range(start, end + 1) if p not in self._deleted_pages)
            total = end - start + 1
            pg = f"p.{start+1}" if start == end else f"p.{start+1}-{end+1}"
            cnt = f"({actual}/{total})" if actual < total else f"({total})"
            info = QLabel(f"{pg} {cnt}")
            info.setStyleSheet("color: gray; font-size: 11px;")
            row.addWidget(info)
            wrapper = QWidget()
            wrapper.setLayout(row)
            self._assign_layout.insertWidget(self._assign_layout.count() - 1, wrapper)

    def _cycle_instrument(self, sec_idx, direction):
        entry = self._section_name_entries.get(sec_idx)
        if not entry:
            return
        instruments = self._project.instruments if self._project else []
        if not instruments:
            return
        current = entry.text()
        try:
            idx = instruments.index(current)
        except ValueError:
            idx = -1 if direction > 0 else len(instruments)
        new_idx = (idx + direction) % len(instruments)
        entry.setText(instruments[new_idx])
        used = set()
        for i in range(sec_idx + 1):
            e = self._section_name_entries.get(i)
            if e:
                used.add(e.text())
        search_from = new_idx
        sections = self._get_sections()
        for i in range(sec_idx + 1, len(sections)):
            e = self._section_name_entries.get(i)
            if not e:
                continue
            name = self._next_unused(used, start_after=search_from)
            e.setText(name)
            used.add(name)
            try:
                search_from = instruments.index(name)
            except ValueError:
                search_from = len(instruments)

    # --- Output ---

    def _browse_dir(self):
        folder = QFileDialog.getExistingDirectory(self, t("split.output_dir"))
        if folder:
            self._output_dir = folder
            self._dir_label.setText(folder)
            self._dir_label.setStyleSheet("font-size: 11px;")

    def _execute_split(self):
        if not self._pdf_path:
            return
        base_dir = self._output_dir or os.path.dirname(self._pdf_path)
        if self._subfolder_cb.isChecked():
            sub = self._subfolder_entry.text().strip() or t("split.subfolder_default")
            output_dir = os.path.join(base_dir, sub)
        else:
            output_dir = base_dir
        sections = self._get_sections()
        try:
            from services.pdf_service import extract_pages
            os.makedirs(output_dir, exist_ok=True)
            split_files = []
            split_instruments = []
            for sec_idx, (start, end) in enumerate(sections):
                pages = [p for p in range(start, end + 1) if p not in self._deleted_pages]
                if not pages:
                    continue
                entry = self._section_name_entries.get(sec_idx)
                name = entry.text().strip() if entry else f"Part {sec_idx + 1}"
                safe = self._sanitize(name)
                if not safe.lower().endswith(".pdf"):
                    safe += ".pdf"
                out_path = os.path.join(output_dir, safe)
                extract_pages(self._pdf_path, pages, out_path)
                split_files.append(FileInfo(
                    original_path=out_path,
                    display_name=os.path.basename(out_path),
                ))
                split_instruments.append(name)
            if not split_files:
                QMessageBox.information(self, t("dialog.info"), t("dialog.info.no_files"))
                return
            if self._on_split_complete:
                self._on_split_complete(
                    split_files, split_instruments,
                    self._source_group, self._pdf_path,
                )
            QMessageBox.information(
                self, t("dialog.complete"),
                t("split.done", count=len(split_files)),
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, t("dialog.error"), str(e))

    @staticmethod
    def _sanitize(name):
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, "_")
        return name.strip() or "Part"
