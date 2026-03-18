# -*- coding: utf-8 -*-
"""
預覽對話框（PySide6）

提供輸出設定（輸出位置、子資料夾）與重新命名預覽。
"""
import os
from typing import Callable, Optional, Set
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QLineEdit, QScrollArea, QWidget, QFileDialog,
    QMessageBox,
)
from core.locale import t
from core.models import Project


class PreviewDialog(QDialog):
    """預覽重新命名對話框"""

    def __init__(
        self, project: Project, rename_service,
        on_execute: Callable, selected_group_ids: Optional[Set[str]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(t("preview.title"))
        self.resize(750, 560)
        self._project = project
        self._rename_service = rename_service
        self._on_execute = on_execute
        self._selected_ids = selected_group_ids
        self._plan = []
        self._conflicts = {}
        self._build_ui()
        self._refresh_plan()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        outdir_row = QHBoxLayout()
        outdir_row.addWidget(QLabel(t("panel.output_dir")))
        self._output_label = QLabel(
            self._project.output_directory or t("panel.output_dir_hint"),
        )
        if not self._project.output_directory:
            self._output_label.setStyleSheet("color: gray;")
        outdir_row.addWidget(self._output_label, stretch=1)
        browse_btn = QPushButton(t("split.browse"))
        browse_btn.clicked.connect(self._browse_output)
        outdir_row.addWidget(browse_btn)
        clear_btn = QPushButton(t("group.score_file.clear"))
        clear_btn.clicked.connect(self._clear_output)
        outdir_row.addWidget(clear_btn)
        layout.addLayout(outdir_row)
        subfolder_row = QHBoxLayout()
        self._subfolder_cb = QCheckBox(t("panel.subfolder"))
        self._subfolder_cb.setChecked(self._project.use_subfolders)
        self._subfolder_cb.toggled.connect(self._on_settings_changed)
        subfolder_row.addWidget(self._subfolder_cb)
        subfolder_row.addWidget(QLabel(t("panel.subfolder_template")))
        self._subfolder_entry = QLineEdit(self._project.subfolder_template)
        self._subfolder_entry.editingFinished.connect(self._on_settings_changed)
        subfolder_row.addWidget(self._subfolder_entry, stretch=1)
        layout.addLayout(subfolder_row)
        self._warn_label = QLabel("")
        self._warn_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
        self._warn_label.setVisible(False)
        layout.addWidget(self._warn_label)
        self._count_label = QLabel("")
        layout.addWidget(self._count_label)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll_container = QWidget()
        self._scroll_layout = QVBoxLayout(self._scroll_container)
        self._scroll_layout.setContentsMargins(4, 4, 4, 4)
        self._scroll_layout.setSpacing(2)
        self._scroll.setWidget(self._scroll_container)
        layout.addWidget(self._scroll, stretch=1)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel_btn = QPushButton(t("preview.cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        self._exec_btn = QPushButton(t("preview.execute"))
        self._exec_btn.setStyleSheet("font-weight: bold;")
        self._exec_btn.clicked.connect(self._execute)
        btn_row.addWidget(self._exec_btn)
        layout.addLayout(btn_row)

    def _browse_output(self):
        folder = QFileDialog.getExistingDirectory(self, t("panel.output_dir"))
        if folder:
            self._project.output_directory = folder
            self._output_label.setText(folder)
            self._output_label.setStyleSheet("")
            self._refresh_plan()

    def _clear_output(self):
        self._project.output_directory = ""
        self._output_label.setText(t("panel.output_dir_hint"))
        self._output_label.setStyleSheet("color: gray;")
        self._refresh_plan()

    def _on_settings_changed(self):
        self._project.use_subfolders = self._subfolder_cb.isChecked()
        self._project.subfolder_template = self._subfolder_entry.text()
        self._refresh_plan()

    def _refresh_plan(self):
        self._plan = self._rename_service.generate_rename_plan(self._project)
        if self._selected_ids is not None:
            self._plan = [e for e in self._plan if e.group_id in self._selected_ids]
        missing = [e for e in self._plan if not os.path.isfile(e.original_path)]
        if missing:
            self._plan = [e for e in self._plan if os.path.isfile(e.original_path)]
        self._conflicts = self._rename_service.detect_conflicts(self._plan)
        self._render_list()

    def _render_list(self):
        while self._scroll_layout.count():
            item = self._scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        if not self._plan:
            self._count_label.setText(t("dialog.info.no_files"))
            self._exec_btn.setEnabled(False)
            return
        self._exec_btn.setEnabled(True)
        conflict_keys = {k.lower() for k in self._conflicts}
        if self._conflicts:
            self._warn_label.setText(
                t("preview.conflict_warning", count=len(self._conflicts)),
            )
            self._warn_label.setVisible(True)
            self._exec_btn.setText(t("preview.execute_with_suffix"))
        else:
            self._warn_label.setVisible(False)
            self._exec_btn.setText(t("preview.execute"))
        self._count_label.setText(t("preview.file_count", count=len(self._plan)))
        for entry in self._plan:
            row = QLabel(f"{entry.original_path}\n  \u2192 {entry.new_path}")
            row.setWordWrap(True)
            if entry.new_path.lower() in conflict_keys:
                row.setStyleSheet("color: #e74c3c;")
            self._scroll_layout.addWidget(row)
        self._scroll_layout.addStretch()

    def _execute(self):
        plan = self._plan
        if self._conflicts:
            plan = self._rename_service.apply_auto_suffix(plan)
        self._on_execute(plan)
        self.accept()
