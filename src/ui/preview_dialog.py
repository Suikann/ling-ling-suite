# -*- coding: utf-8 -*-
"""
預覽對話框（PySide6）

提供輸出設定（輸出位置、子資料夾）與重新命名預覽。設定一經修改即透過專案的編輯操作寫入。
顯示的是搬移歷程的預檢判定（RenameVerdict）；按下執行時交出的就是這份判定。
"""
from typing import Callable, List
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QCheckBox, QLineEdit, QScrollArea, QWidget, QFileDialog,
    QRadioButton, QButtonGroup,
)
from core.constants import NON_BLOCKING_RENAME_PROBLEMS, PartsOutputMode, RenameProblem
from core.locale import t
from core.models import Project
from services.move_history import RenameVerdict

# 阻擋的問題種類 → 預覽的警告訊息（訊息可用 count 與 files）
_BLOCKING_WARNINGS = {
    RenameProblem.OUTSIDE_OUTPUT: "preview.outside_output_warning",
    RenameProblem.EMPTY_NAME: "preview.empty_name_warning",
    RenameProblem.DUPLICATE_SOURCE: "preview.duplicate_source_warning",
    RenameProblem.DUPLICATE_TARGET: "preview.duplicate_target_warning",
    RenameProblem.TARGET_OCCUPIED: "preview.occupied_warning",
    RenameProblem.STAGING_TAKEN: "preview.staging_warning",
}


def _variable_list(names: List[str]) -> str:
    """變數名稱以「{名稱}」的寫法列成一行"""
    return " ".join(f"{{{name}}}" for name in names)


class PreviewDialog(QDialog):
    """預覽重新命名對話框"""

    _PARTS_MODES = tuple(PartsOutputMode)

    def __init__(
        self, project: Project, check: Callable[[], RenameVerdict],
        on_execute: Callable[[RenameVerdict], None], parent=None,
    ):
        """
        Args:
            project: 專案（輸出設定直接寫入）
            check: 依專案目前的設定做重新命名預檢，回傳判定
            on_execute: 按下執行時呼叫，參數為目前顯示的判定
            parent: 父元件
        """
        super().__init__(parent)
        self.setWindowTitle(t("preview.title"))
        self.resize(750, 560)
        self._project = project
        self._check = check
        self._on_execute = on_execute
        self._verdict = RenameVerdict()
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
        self._subfolder_cb.toggled.connect(
            lambda checked: self._write_settings(use_subfolders=checked),
        )
        subfolder_row.addWidget(self._subfolder_cb)
        subfolder_row.addWidget(QLabel(t("panel.subfolder_template")))
        self._subfolder_entry = QLineEdit(self._project.subfolder_template)
        self._subfolder_entry.textEdited.connect(
            lambda text: self._project.set_output_settings(subfolder_template=text),
        )
        self._subfolder_entry.editingFinished.connect(self._refresh_plan)
        subfolder_row.addWidget(self._subfolder_entry, stretch=1)
        layout.addLayout(subfolder_row)
        parts_row = QHBoxLayout()
        parts_row.addWidget(QLabel(t("panel.parts_output_mode")))
        self._parts_group = QButtonGroup(self)
        self._radio_root = QRadioButton(t("panel.parts_mode_root"))
        self._radio_parts = QRadioButton(t("panel.parts_mode_parts"))
        self._radio_section = QRadioButton(t("panel.parts_mode_section"))
        for mode_id, radio in enumerate((self._radio_root, self._radio_parts, self._radio_section)):
            self._parts_group.addButton(radio, mode_id)
        mode = self._project.parts_output_mode
        mode_id = self._PARTS_MODES.index(mode) if mode in self._PARTS_MODES else 0
        self._parts_group.button(mode_id).setChecked(True)
        self._parts_group.idClicked.connect(
            lambda clicked_id: self._write_settings(parts_output_mode=self._PARTS_MODES[clicked_id]),
        )
        parts_row.addWidget(self._radio_root)
        parts_row.addWidget(self._radio_parts)
        parts_row.addWidget(self._radio_section)
        self._parts_entry = QLineEdit(self._project.parts_subfolder_name)
        self._parts_entry.setFixedWidth(120)
        self._parts_entry.textEdited.connect(
            lambda text: self._project.set_output_settings(parts_subfolder_name=text),
        )
        self._parts_entry.editingFinished.connect(self._refresh_plan)
        parts_row.addWidget(self._parts_entry)
        layout.addLayout(parts_row)
        self._warn_label = QLabel("")
        self._warn_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
        self._warn_label.setWordWrap(True)
        self._warn_label.setVisible(False)
        layout.addWidget(self._warn_label)
        self._notes_label = QLabel("")
        self._notes_label.setStyleSheet("color: #e0b060;")
        self._notes_label.setWordWrap(True)
        self._notes_label.setVisible(False)
        layout.addWidget(self._notes_label)
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
            self._output_label.setText(folder)
            self._output_label.setStyleSheet("")
            self._write_settings(output_directory=folder)

    def _clear_output(self):
        self._output_label.setText(t("panel.output_dir_hint"))
        self._output_label.setStyleSheet("color: gray;")
        self._write_settings(output_directory="")

    def _write_settings(self, **settings):
        """把輸出設定寫入專案並重新產生預覽"""
        self._project.set_output_settings(**settings)
        self._refresh_plan()

    def _refresh_plan(self):
        self._verdict = self._check()
        self._render_list()

    def _blocking_warnings(self) -> List[str]:
        """判定中阻擋執行的原因"""
        verdict = self._verdict
        warnings = []
        if verdict.unsafe_folder:
            warnings.append(t("preview.unsafe_folder_warning", name=verdict.unsafe_folder))
        if verdict.folder_variables:
            warnings.append(t("preview.folder_variable_warning", names=_variable_list(verdict.folder_variables)))
        for kind, key in _BLOCKING_WARNINGS.items():
            sources = verdict.sources(kind)
            if sources:
                warnings.append(t(key, count=len(sources), files="\n".join(sources)))
        return warnings

    def _notes(self) -> List[str]:
        """判定中不阻擋、但要讓使用者知道的事"""
        verdict = self._verdict
        missing = verdict.sources(RenameProblem.MISSING_SOURCE)
        extra = verdict.sources(RenameProblem.EXTRA_FILE)
        notes = []
        if missing:
            notes.append(t("preview.missing_warning", count=len(missing), files="\n".join(missing)))
        if extra:
            notes.append(t("preview.extra_files_warning", files="\n".join(extra)))
        if verdict.unknown_variables:
            notes.append(t("preview.unknown_variables_warning", names=_variable_list(verdict.unknown_variables)))
        return notes

    def _render_list(self):
        while self._scroll_layout.count():
            item = self._scroll_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        verdict = self._verdict
        notes = self._notes()
        self._notes_label.setText("\n\n".join(notes))
        self._notes_label.setVisible(bool(notes))
        blocking = self._blocking_warnings()
        suffixed = verdict.sources(RenameProblem.SUFFIXED)
        if blocking:
            self._warn_label.setText("\n\n".join(blocking))
        elif suffixed:
            self._warn_label.setText(t("preview.conflict_warning", count=len(suffixed)))
        self._warn_label.setVisible(bool(blocking or suffixed))
        self._exec_btn.setText(t("preview.execute_with_suffix" if suffixed and not blocking else "preview.execute"))
        self._exec_btn.setEnabled(verdict.runnable)
        if not verdict.plan:
            self._count_label.setText("" if verdict.unsafe_folder else t("dialog.info.no_files"))
            return
        self._count_label.setText(t("preview.file_count", count=len(verdict.plan)))
        for entry in verdict.plan:
            row = QLabel(f"{entry.original_path}\n  \u2192 {entry.new_path}")
            row.setWordWrap(True)
            if self._needs_attention(entry.original_path):
                row.setStyleSheet("color: #e74c3c;")
            self._scroll_layout.addWidget(row)
        self._scroll_layout.addStretch()

    def _needs_attention(self, source: str) -> bool:
        """這一項的目標加了後綴，或有阻擋的問題"""
        return any(
            kind == RenameProblem.SUFFIXED or kind not in NON_BLOCKING_RENAME_PROBLEMS
            for kind in self._verdict.problems.get(source, ())
        )

    def _execute(self):
        self._on_execute(self._verdict)
        self.accept()
