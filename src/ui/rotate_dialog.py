# -*- coding: utf-8 -*-
"""
PDF 旋轉對話框（PySide6）

提供從專案檔案或磁碟選擇 PDF，執行頁面旋轉。
"""
import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QCheckBox, QButtonGroup,
    QComboBox, QFileDialog, QMessageBox,
)
from core.locale import t


class RotatePdfDialog(QDialog):
    """PDF 旋轉對話框"""

    def __init__(self, project=None, initial_group=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("rotate.title"))
        self.resize(560, 420)
        self._project = project
        self._filter_group = initial_group
        self._pdf_path = None
        self._page_count = 0
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        file_row = QHBoxLayout()
        self._group_filter = QComboBox()
        self._group_filter.setFixedWidth(160)
        self._build_group_filter()
        self._group_filter.currentIndexChanged.connect(self._on_group_filter_changed)
        file_row.addWidget(self._group_filter)
        self._file_combo = QComboBox()
        self._file_combo.setMinimumWidth(200)
        self._file_combo.currentIndexChanged.connect(self._on_file_selected)
        file_row.addWidget(self._file_combo, stretch=1)
        browse_btn = QPushButton(t("rotate.browse"))
        browse_btn.clicked.connect(self._browse_file)
        file_row.addWidget(browse_btn)
        layout.addLayout(file_row)
        self._page_info = QLabel("")
        layout.addWidget(self._page_info)
        layout.addWidget(QLabel(t("rotate.angle")))
        angle_row = QHBoxLayout()
        self._angle_group = QButtonGroup(self)
        for value, label_key in [("90", "rotate.cw_90"), ("180", "rotate.180"), ("270", "rotate.ccw_90")]:
            rb = QRadioButton(t(label_key))
            rb.setProperty("angle", value)
            self._angle_group.addButton(rb)
            angle_row.addWidget(rb)
            if value == "90":
                rb.setChecked(True)
        angle_row.addStretch()
        layout.addLayout(angle_row)
        layout.addWidget(QLabel(t("rotate.scope")))
        self._scope_all = QRadioButton(t("rotate.all_pages"))
        self._scope_all.setChecked(True)
        layout.addWidget(self._scope_all)
        custom_row = QHBoxLayout()
        self._scope_custom = QRadioButton(t("rotate.custom_pages"))
        custom_row.addWidget(self._scope_custom)
        self._pages_entry = QLineEdit()
        self._pages_entry.setPlaceholderText(t("rotate.pages_hint"))
        self._pages_entry.setEnabled(False)
        custom_row.addWidget(self._pages_entry, stretch=1)
        layout.addLayout(custom_row)
        self._scope_custom.toggled.connect(self._pages_entry.setEnabled)
        scope_group = QButtonGroup(self)
        scope_group.addButton(self._scope_all)
        scope_group.addButton(self._scope_custom)
        self._overwrite_cb = QCheckBox(t("rotate.overwrite"))
        self._overwrite_cb.setChecked(True)
        layout.addWidget(self._overwrite_cb)
        layout.addStretch()
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton(t("rotate.cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self._exec_btn = QPushButton(t("rotate.execute"))
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
            for f in self._filter_group.files:
                if f.original_path.lower().endswith(".pdf"):
                    files.append((f.display_name, f.original_path, self._filter_group))
        return files

    def _on_file_selected(self, index):
        data = self._file_combo.currentData()
        if data:
            path, group = data
            self._load_file_info(path)

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
        self._load_file_info(path)

    def _load_file_info(self, path):
        if not os.path.isfile(path):
            QMessageBox.critical(self, t("dialog.error"), f"File not found:\n{path}")
            return
        self._pdf_path = path
        try:
            from services.pdf_service import get_page_count
            self._page_count = get_page_count(path)
            self._page_info.setText(t("rotate.page_count", count=self._page_count))
            self._exec_btn.setEnabled(True)
        except Exception as e:
            QMessageBox.critical(self, t("dialog.error"), str(e))

    # --- 執行旋轉 ---

    def _execute(self):
        if not self._pdf_path:
            return
        checked = self._angle_group.checkedButton()
        angle = int(checked.property("angle")) if checked else 90
        page_ranges = []
        if self._scope_custom.isChecked():
            parsed = self._parse_ranges(self._pages_entry.text().strip())
            if parsed is None:
                QMessageBox.warning(self, t("dialog.warning"), t("rotate.invalid_pages"))
                return
            page_ranges = parsed
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
            from services.pdf_service import rotate_pdf
            rotate_pdf(self._pdf_path, angle, page_ranges, output_path)
            QMessageBox.information(self, t("dialog.complete"), t("rotate.done"))
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, t("dialog.error"), str(e))

    @staticmethod
    def _parse_ranges(text):
        ranges = []
        for part in text.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                pieces = part.split("-", 1)
                try:
                    s, e = int(pieces[0].strip()), int(pieces[1].strip())
                    if s < 1 or e < s:
                        return None
                    ranges.append((s, e))
                except ValueError:
                    return None
            else:
                try:
                    p = int(part)
                    if p < 1:
                        return None
                    ranges.append((p, p))
                except ValueError:
                    return None
        return ranges if ranges else None
