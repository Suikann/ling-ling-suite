# -*- coding: utf-8 -*-
"""
Drive 重新命名對話框

讓使用者設定模板、樂器表與群組元資料，預覽後直接在 Drive 上重新命名。
改名經譜庫服務在背景執行；改不了時頂端顯示錯誤提示列並寫進紀錄檔，按處理按鈕後從失敗的那份接著改。
"""
from typing import Callable, List, Optional
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QSplitter, QListWidget, QListWidgetItem,
    QFormLayout, QGroupBox, QTableWidget, QTableWidgetItem,
    QMessageBox, QWidget, QTextEdit, QComboBox,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from core.locale import localized, t
from core.constants import (
    DEFAULT_MASTER_TEMPLATE, DEFAULT_MASTER_TEMPLATE_EN,
    TEMPLATE_VARIABLES, INSTRUMENT_PRESETS,
)
from core.catalog_errors import CatalogError, CatalogFailure
from core.models import DriveRenameEntry, Group
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.drive_rename_service import generate_drive_rename_plan, execute_drive_rename
from ui.catalog_error_bar import CatalogErrorBar
from ui.catalog_tasks import CatalogTasks


class DriveRenameDialog(QDialog):
    """Drive 重新命名對話框"""

    def __init__(
        self, connect: Callable[[], CatalogService], groups: List[Group], parent=None,
        sign_in: Optional[Callable[[], bool]] = None, open_settings: Optional[Callable[[], bool]] = None,
        log: Optional[ActivityLog] = None,
    ):
        """
        Args:
            connect: 在背景執行緒取得譜庫服務的函式，拿不到時丟出 CatalogError
            groups: 這次要命名的 Drive 群組
            parent: 父元件
            sign_in: 提示列「重新登入 Google」時呼叫，回傳是否登入成功；省略時直接重試
            open_settings: 提示列「開啟譜庫設定」時呼叫，回傳是否改了設定；省略時直接重試
            log: 紀錄檔，改名失敗時寫進去；省略時不寫
        """
        super().__init__(parent)
        self._connect = connect
        self._tasks = CatalogTasks(self)
        self._log = log
        # 這次執行還沒改名的檔案，與這次執行共有幾份
        self._pending: List[DriveRenameEntry] = []
        self._total = 0
        self._sign_in = sign_in
        self._open_settings = open_settings
        # 這次要命名的 Drive 群組（不屬於任何專案、不存檔），群組資訊的修改經過 Group.update
        self._groups = list(groups)
        self.setWindowTitle(t("catalog.rename.title"))
        self.setMinimumSize(900, 650)
        self._build_ui()
        if self._groups:
            self._group_list.setCurrentRow(0)
        self._update_preview()

    def done(self, result: int):
        """關閉對話框：放棄還在跑的改名，之後它不再碰這個對話框"""
        self._tasks.abandon_all()
        super().done(result)

    def _build_ui(self):
        """排出提示列、命名模板、群組與樂器表、群組資訊、預覽與按鈕"""
        layout = QVBoxLayout(self)
        self._error_bar = CatalogErrorBar(log=self._log)
        self._error_bar.connect_remedies(self._sign_in, self._open_settings)
        layout.addWidget(self._error_bar)
        template_row = QHBoxLayout()
        template_row.addWidget(QLabel(t("catalog.rename.template_label")))
        self._template_entry = QLineEdit(localized(DEFAULT_MASTER_TEMPLATE, DEFAULT_MASTER_TEMPLATE_EN))
        self._template_entry.textChanged.connect(self._update_preview)
        self._template_entry.setToolTip(t("panel.template_segment_tip"))
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
            self._preset_combo.addItem(localized(preset.name, preset.name_en))
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
        self._execute_btn = QPushButton(t("catalog.rename.execute"))
        self._execute_btn.clicked.connect(self._execute_rename)
        btn_row.addWidget(self._execute_btn)
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
        self._groups[row].update(
            piece_name=self._piece_name_edit.text(),
            composer=self._composer_edit.text(),
            genre=self._genre_edit.text(),
            movement_number=self._mov_num_edit.text(),
            movement_name=self._mov_name_edit.text(),
        )
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
        """重新產生預覽；有撞名時停用執行"""
        plan = generate_drive_rename_plan(self._groups, self._template_entry.text(), self._get_instruments())
        self._current_plan = plan
        conflict_ids = {file_id for ids in plan.conflicts for file_id in ids}
        self._preview_table.setRowCount(len(plan.entries))
        for row, entry in enumerate(plan.entries):
            group_item = QTableWidgetItem(entry.group_name)
            group_item.setFlags(group_item.flags() & ~Qt.ItemIsEditable)
            self._preview_table.setItem(row, 0, group_item)
            orig_item = QTableWidgetItem(entry.original_name)
            orig_item.setFlags(orig_item.flags() & ~Qt.ItemIsEditable)
            self._preview_table.setItem(row, 1, orig_item)
            new_item = QTableWidgetItem(entry.new_name)
            new_item.setFlags(new_item.flags() & ~Qt.ItemIsEditable)
            if entry.file_id in conflict_ids:
                new_item.setForeground(QColor("#e74c3c"))
            elif entry.original_name != entry.new_name:
                new_item.setForeground(QColor("#2ecc71"))
            self._preview_table.setItem(row, 2, new_item)
        if plan.conflicts:
            self._conflict_label.setText(
                t("catalog.rename.conflict_warning", count=len(plan.conflicts)),
            )
            self._conflict_label.setStyleSheet("color: #e74c3c; font-weight: bold;")
        else:
            self._conflict_label.setText(
                t("catalog.rename.file_count", count=len(plan.entries)),
            )
            self._conflict_label.setStyleSheet("color: gray;")
        self._execute_btn.setEnabled(not plan.conflicts)

    def _execute_rename(self):
        """確認後在背景執行預覽的計畫"""
        plan = self._current_plan
        if not plan.entries or plan.conflicts:
            return
        result = QMessageBox.question(
            self, t("catalog.rename.title"),
            t("catalog.rename.confirm"),
        )
        if result != QMessageBox.Yes:
            return
        self._pending = list(plan.entries)
        self._total = len(plan.entries)
        self._continue_rename()

    def _continue_rename(self):
        """在背景改名還沒改的檔案；全部改好就關閉對話框，改不了時顯示提示列"""
        if not self._pending:
            return
        self._execute_btn.setEnabled(False)
        pending = self._pending

        def renamed(_result):
            """全部改好了"""
            QMessageBox.information(
                self, t("catalog.rename.title"),
                t("catalog.rename.success", success=self._total, total=self._total),
            )
            self.accept()

        def failed(error: CatalogError):
            """改不了：提示列寫出失敗的那份；改計畫後可再執行"""
            self._execute_btn.setEnabled(not self._current_plan.conflicts)
            file_name = pending[0].original_name if pending else ""
            self._error_bar.show_failure(
                t("catalog.error.cannot_write"),
                CatalogFailure.from_error(error, t("catalog.rename.title"), file_name=file_name), self._continue_rename,
            )
        self._error_bar.clear()
        self._tasks.run(lambda: execute_drive_rename(self._connect(), pending), renamed, failed)
