# -*- coding: utf-8 -*-
"""
Drive 重新命名對話框

讓使用者設定模板、樂器表與群組元資料，預覽後直接在 Drive 上重新命名。
"""
from typing import List
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSplitter, QListWidget, QListWidgetItem,
    QFormLayout, QGroupBox, QTableWidget, QTableWidgetItem,
    QMessageBox, QWidget, QTextEdit, QComboBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from core.locale import t, get_locale
from core.constants import (
    DEFAULT_MASTER_TEMPLATE, DEFAULT_MASTER_TEMPLATE_EN,
    TEMPLATE_VARIABLES, INSTRUMENT_PRESETS,
)
from core.models import Group
from services.drive_service import DriveService
from services.drive_rename_service import (
    generate_drive_rename_plan, detect_conflicts, execute_drive_rename,
)


class DriveRenameDialog(QDialog):
    """Drive 重新命名對話框"""

    def __init__(
        self, drive: DriveService, groups: List[Group], parent=None,
    ):
        super().__init__(parent)
        self._drive = drive
        self._groups = groups
        self.setWindowTitle(t("catalog.rename.title"))
        self.setMinimumSize(900, 650)
        self._build_ui()
        if self._groups:
            self._group_list.setCurrentRow(0)
        self._update_preview()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        template_row = QHBoxLayout()
        template_row.addWidget(QLabel(t("catalog.rename.template_label")))
        default_tmpl = (
            DEFAULT_MASTER_TEMPLATE_EN if get_locale() == "en"
            else DEFAULT_MASTER_TEMPLATE
        )
        self._template_entry = QLineEdit(default_tmpl)
        self._template_entry.textChanged.connect(self._update_preview)
        template_row.addWidget(self._template_entry, stretch=1)
        layout.addLayout(template_row)
        var_names = ", ".join(f"{{{v.name}}}" for v in TEMPLATE_VARIABLES)
        hint = QLabel(t("catalog.rename.template_hint", vars=var_names))
        hint.setStyleSheet("color: gray; font-size: 12px; padding: 0 0 4px 0;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel(t("catalog.rename.group_label")))
        self._group_list = QListWidget()
        for group in self._groups:
            count = len(group.files)
            self._group_list.addItem(f"{group.name}  ({count})")
        self._group_list.currentRowChanged.connect(self._on_group_selected)
        left_layout.addWidget(self._group_list)
        inst_group = QGroupBox(t("catalog.rename.instruments"))
        inst_layout = QVBoxLayout(inst_group)
        preset_row = QHBoxLayout()
        self._preset_combo = QComboBox()
        self._preset_combo.addItem("")
        for preset in INSTRUMENT_PRESETS:
            name = preset.name if get_locale() != "en" else preset.name_en
            self._preset_combo.addItem(name)
        self._preset_combo.currentIndexChanged.connect(self._on_preset_selected)
        preset_row.addWidget(self._preset_combo, stretch=1)
        inst_layout.addLayout(preset_row)
        self._inst_edit = QTextEdit()
        self._inst_edit.setPlaceholderText(t("catalog.rename.instruments_hint"))
        self._inst_edit.setMaximumHeight(120)
        self._inst_edit.textChanged.connect(self._update_preview)
        inst_layout.addWidget(self._inst_edit)
        left_layout.addWidget(inst_group)
        splitter.addWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self._meta_group = QGroupBox(t("catalog.rename.metadata"))
        meta_layout = QFormLayout(self._meta_group)
        self._piece_name_edit = QLineEdit()
        self._piece_name_edit.textChanged.connect(self._sync_meta_to_group)
        meta_layout.addRow(t("catalog.rename.piece_name"), self._piece_name_edit)
        self._composer_edit = QLineEdit()
        self._composer_edit.textChanged.connect(self._sync_meta_to_group)
        meta_layout.addRow(t("catalog.rename.composer"), self._composer_edit)
        self._genre_edit = QLineEdit()
        self._genre_edit.textChanged.connect(self._sync_meta_to_group)
        meta_layout.addRow(t("catalog.rename.genre"), self._genre_edit)
        self._mov_num_edit = QLineEdit()
        self._mov_num_edit.textChanged.connect(self._sync_meta_to_group)
        meta_layout.addRow(t("catalog.rename.movement_num"), self._mov_num_edit)
        self._mov_name_edit = QLineEdit()
        self._mov_name_edit.textChanged.connect(self._sync_meta_to_group)
        meta_layout.addRow(t("catalog.rename.movement_name"), self._mov_name_edit)
        right_layout.addWidget(self._meta_group)
        right_layout.addWidget(QLabel(t("catalog.rename.preview")))
        self._preview_table = QTableWidget()
        self._preview_table.setColumnCount(3)
        self._preview_table.setHorizontalHeaderLabels([
            t("catalog.rename.group_label"),
            t("catalog.rename.original"),
            t("catalog.rename.new_name"),
        ])
        self._preview_table.horizontalHeader().setStretchLastSection(True)
        self._preview_table.setColumnWidth(0, 120)
        self._preview_table.setColumnWidth(1, 250)
        right_layout.addWidget(self._preview_table, stretch=1)
        self._conflict_label = QLabel("")
        right_layout.addWidget(self._conflict_label)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, stretch=1)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        execute_btn = QPushButton(t("catalog.rename.execute"))
        execute_btn.clicked.connect(self._execute_rename)
        btn_row.addWidget(execute_btn)
        cancel_btn = QPushButton(t("catalog.cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def _on_group_selected(self, row: int):
        """群組選取變更，更新元資料欄位"""
        if row < 0 or row >= len(self._groups):
            return
        group = self._groups[row]
        self._piece_name_edit.blockSignals(True)
        self._composer_edit.blockSignals(True)
        self._genre_edit.blockSignals(True)
        self._mov_num_edit.blockSignals(True)
        self._mov_name_edit.blockSignals(True)
        self._piece_name_edit.setText(group.piece_name)
        self._composer_edit.setText(group.composer)
        self._genre_edit.setText(group.genre)
        self._mov_num_edit.setText(group.movement_number)
        self._mov_name_edit.setText(group.movement_name)
        self._piece_name_edit.blockSignals(False)
        self._composer_edit.blockSignals(False)
        self._genre_edit.blockSignals(False)
        self._mov_num_edit.blockSignals(False)
        self._mov_name_edit.blockSignals(False)

    def _sync_meta_to_group(self):
        """將元資料欄位同步回目前選取的群組"""
        row = self._group_list.currentRow()
        if row < 0 or row >= len(self._groups):
            return
        group = self._groups[row]
        group.piece_name = self._piece_name_edit.text()
        group.composer = self._composer_edit.text()
        group.genre = self._genre_edit.text()
        group.movement_number = self._mov_num_edit.text()
        group.movement_name = self._mov_name_edit.text()
        self._update_preview()

    def _on_preset_selected(self, index: int):
        """樂器預設編制選取"""
        if index <= 0:
            return
        preset = INSTRUMENT_PRESETS[index - 1]
        self._inst_edit.setPlainText("\n".join(preset.instruments))

    def _get_instruments(self) -> List[str]:
        """取得樂器表"""
        text = self._inst_edit.toPlainText().strip()
        if not text:
            return []
        return [line.strip() for line in text.split("\n") if line.strip()]

    def _update_preview(self):
        """重新產生預覽"""
        template = self._template_entry.text()
        instruments = self._get_instruments()
        plan = generate_drive_rename_plan(
            self._groups, template, instruments or None,
        )
        self._current_plan = plan
        self._preview_table.setRowCount(len(plan))
        conflicts = detect_conflicts(plan)
        conflict_names = set()
        for names in conflicts.values():
            conflict_names.update(names)
        for row, entry in enumerate(plan):
            group_item = QTableWidgetItem(entry.group_name)
            group_item.setFlags(group_item.flags() & ~Qt.ItemIsEditable)
            self._preview_table.setItem(row, 0, group_item)
            orig_item = QTableWidgetItem(entry.original_name)
            orig_item.setFlags(orig_item.flags() & ~Qt.ItemIsEditable)
            self._preview_table.setItem(row, 1, orig_item)
            new_item = QTableWidgetItem(entry.new_name)
            new_item.setFlags(new_item.flags() & ~Qt.ItemIsEditable)
            if entry.original_name in conflict_names:
                new_item.setForeground(QColor("#e74c3c"))
            elif entry.original_name != entry.new_name:
                new_item.setForeground(QColor("#2ecc71"))
            self._preview_table.setItem(row, 2, new_item)
        if conflicts:
            self._conflict_label.setText(
                t("catalog.rename.conflict_warning", count=len(conflicts)),
            )
            self._conflict_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
        else:
            self._conflict_label.setText(
                t("catalog.rename.file_count", count=len(plan)),
            )
            self._conflict_label.setStyleSheet("color: gray;")

    def _execute_rename(self):
        """執行重新命名"""
        if not hasattr(self, "_current_plan") or not self._current_plan:
            return
        result = QMessageBox.question(
            self, t("catalog.rename.title"),
            t("catalog.rename.confirm"),
        )
        if result != QMessageBox.Yes:
            return
        success, errors = execute_drive_rename(self._drive, self._current_plan)
        total = len(self._current_plan)
        msg = t("catalog.rename.success", success=success, total=total)
        if errors:
            msg += "\n" + t("catalog.rename.errors", files=", ".join(errors))
        QMessageBox.information(self, t("catalog.rename.title"), msg)
        if success > 0:
            self.accept()
