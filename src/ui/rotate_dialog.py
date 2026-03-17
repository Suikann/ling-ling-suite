# -*- coding: utf-8 -*-
"""
PDF 旋轉對話框（PySide6）
"""
import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QCheckBox, QButtonGroup,
    QFileDialog, QMessageBox,
)
from core.locale import t


class RotatePdfDialog(QDialog):
    """PDF 旋轉對話框"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("rotate.title"))
        self.resize(520, 400)
        self._pdf_path = None
        self._page_count = 0
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        file_row = QHBoxLayout()
        file_row.addWidget(QLabel(t("rotate.select_file")))
        self._file_label = QLabel(t("rotate.no_file"))
        self._file_label.setStyleSheet("color: gray;")
        file_row.addWidget(self._file_label, stretch=1)
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

    def _browse_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, t("rotate.select_file"), "",
            f"{t('filedialog.pdf_files')} (*.pdf)",
        )
        if not path:
            return
        self._pdf_path = path
        self._file_label.setText(os.path.basename(path))
        self._file_label.setStyleSheet("")
        try:
            from services.pdf_service import get_page_count
            self._page_count = get_page_count(path)
            self._page_info.setText(t("rotate.page_count", count=self._page_count))
            self._exec_btn.setEnabled(True)
        except Exception as e:
            QMessageBox.critical(self, t("dialog.error"), str(e))

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
