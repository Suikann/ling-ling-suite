# -*- coding: utf-8 -*-
"""
群組管理面板（PySide6）

提供群組標籤管理、樂器勾選與檔案清單。
"""
import os
from typing import List, TYPE_CHECKING
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QListWidget, QListWidgetItem,
    QScrollArea, QFrame, QMessageBox, QMenu, QFileDialog,
    QAbstractItemView,
)
from ui.widgets import DragListWidget
from PySide6.QtCore import Qt
from core.locale import t
from core.models import Group, Project, FileInfo
from core.template_engine import detect_piece_name

if TYPE_CHECKING:
    from ui.main_window import MainWindow


class UngroupedTab(QWidget):
    """未分組標籤"""

    def __init__(self, project: Project, main_window: "MainWindow", parent=None):
        super().__init__(parent)
        self.project = project
        self.main_window = main_window
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        action_row = QHBoxLayout()
        self._select_all = QCheckBox(t("ungrouped.select_all"))
        self._select_all.toggled.connect(self._toggle_select_all)
        action_row.addWidget(self._select_all)
        move_btn = QPushButton(t("ungrouped.move_selected"))
        move_btn.clicked.connect(self._move_selected)
        action_row.addWidget(move_btn)
        new_grp_btn = QPushButton(t("ungrouped.new_group_from_selected"))
        new_grp_btn.clicked.connect(self._new_group_from_selected)
        action_row.addWidget(new_grp_btn)
        action_row.addStretch()
        layout.addLayout(action_row)
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.MultiSelection)
        layout.addWidget(self._list)
        self._refresh()

    def _refresh(self):
        self._list.clear()
        self._select_all.setChecked(False)
        if not self.project.ungrouped_files:
            item = QListWidgetItem(t("ungrouped.empty"))
            item.setFlags(Qt.NoItemFlags)
            self._list.addItem(item)
            return
        for f in self.project.ungrouped_files:
            self._list.addItem(f.display_name)

    def _toggle_select_all(self, checked):
        for i in range(self._list.count()):
            self._list.item(i).setSelected(checked)

    def _get_selected_indices(self) -> List[int]:
        return [i for i in range(self._list.count()) if self._list.item(i).isSelected()]

    def _move_selected(self):
        selected = self._get_selected_indices()
        if not selected or not self.project.groups:
            if not self.project.groups:
                QMessageBox.information(self, t("dialog.info"), t("dialog.info.create_group_first"))
            return
        menu = QMenu(self)
        for group in self.project.groups:
            action = menu.addAction(group.name or group.id[:8])
            action.triggered.connect(
                lambda checked=False, g=group: self._do_batch_move(g),
            )
        menu.exec(self.sender().mapToGlobal(self.sender().rect().bottomLeft()))

    def _do_batch_move(self, group: Group):
        selected = self._get_selected_indices()
        files_to_move = [self.project.ungrouped_files[i] for i in selected]
        for i in sorted(selected, reverse=True):
            self.project.ungrouped_files.pop(i)
        group.files.extend(files_to_move)
        self.main_window._mark_modified()
        self.main_window._rebuild_tabs()

    def _new_group_from_selected(self):
        selected = self._get_selected_indices()
        if not selected:
            return
        files_to_move = [self.project.ungrouped_files[i] for i in selected]
        for i in sorted(selected, reverse=True):
            self.project.ungrouped_files.pop(i)
        new_group = Group(
            name=t("group.new_name", number=len(self.project.groups) + 1),
            files=files_to_move,
        )
        self.project.groups.append(new_group)
        self.main_window._mark_modified()
        self.main_window._rebuild_tabs()


class GroupTab(QWidget):
    """群組標籤"""

    _SCORE_KEYWORDS = ("score", "full score", "conductor", "總譜", "指揮譜", "full")

    def __init__(self, group: Group, project: Project, main_window: "MainWindow", parent=None):
        super().__init__(parent)
        self._group = group
        self.project = project
        self.main_window = main_window
        self._build_ui()
        self._auto_detect_score()
        self._auto_detect_piece_name()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel(t("group.name_label")))
        self._name_entry = QLineEdit(self._group.name)
        top_row.addWidget(self._name_entry, stretch=1)
        del_btn = QPushButton(t("group.delete"))
        del_btn.setStyleSheet("background-color: #c0392b; color: white;")
        del_btn.clicked.connect(self._delete_group)
        top_row.addWidget(del_btn)
        layout.addLayout(top_row)
        vars_row = QHBoxLayout()
        vars_row.addWidget(QLabel(t("group.piece_name_label")))
        self._piece_name_entry = QLineEdit(self._group.piece_name)
        vars_row.addWidget(self._piece_name_entry)
        detect_btn = QPushButton(t("group.auto_detect"))
        detect_btn.clicked.connect(self._detect_piece_name)
        vars_row.addWidget(detect_btn)
        vars_row.addWidget(QLabel(t("group.movement_num_label")))
        self._movement_num_entry = QLineEdit(self._group.movement_number)
        self._movement_num_entry.setFixedWidth(60)
        vars_row.addWidget(self._movement_num_entry)
        vars_row.addWidget(QLabel(t("group.movement_name_label")))
        self._movement_name_entry = QLineEdit(self._group.movement_name)
        vars_row.addWidget(self._movement_name_entry)
        layout.addLayout(vars_row)
        middle = QHBoxLayout()
        left_widget = QWidget()
        left_widget.setFixedWidth(210)
        left_col = QVBoxLayout(left_widget)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.addWidget(QLabel(t("group.instrument_check")))
        self._select_all_cb = QCheckBox(t("group.select_all_instruments"))
        self._select_all_cb.toggled.connect(self._toggle_select_all)
        self._select_all_cb.setContentsMargins(4, 0, 0, 0)
        left_col.addWidget(self._select_all_cb)
        self._inst_scroll = QScrollArea()
        self._inst_scroll.setWidgetResizable(True)
        self._inst_container = QWidget()
        self._inst_layout = QVBoxLayout(self._inst_container)
        self._inst_layout.setContentsMargins(4, 4, 4, 4)
        self._inst_layout.setSpacing(2)
        self._inst_scroll.setWidget(self._inst_container)
        left_col.addWidget(self._inst_scroll)
        self._mismatch_label = QLabel("")
        self._mismatch_label.setWordWrap(True)
        self._mismatch_label.setStyleSheet("color: #e74c3c; font-size: 12px;")
        left_col.addWidget(self._mismatch_label)
        middle.addWidget(left_widget)
        right_col = QVBoxLayout()
        score_row = QHBoxLayout()
        score_row.addWidget(QLabel(t("group.score_file")))
        self._score_label = QLabel(t("group.score_file.none"))
        self._score_label.setStyleSheet("color: gray;")
        score_row.addWidget(self._score_label, stretch=1)
        set_score_btn = QPushButton(t("group.score_file.set"))
        set_score_btn.clicked.connect(self._set_score_file)
        score_row.addWidget(set_score_btn)
        clear_score_btn = QPushButton(t("group.score_file.clear"))
        clear_score_btn.clicked.connect(self._clear_score_file)
        score_row.addWidget(clear_score_btn)
        right_col.addLayout(score_row)
        score_label_row = QHBoxLayout()
        score_label_row.addWidget(QLabel(t("group.score_file.label")))
        self._score_label_entry = QLineEdit(
            self._group.score_label or t("group.score_label"),
        )
        self._score_label_entry.setFixedWidth(120)
        score_label_row.addWidget(self._score_label_entry)
        score_label_row.addStretch()
        right_col.addLayout(score_label_row)
        right_col.addWidget(QLabel(t("group.file_list")))
        self._file_list = DragListWidget()
        self._file_list.model().rowsMoved.connect(self._on_files_reordered)
        right_col.addWidget(self._file_list)
        file_btn_row = QHBoxLayout()
        add_btn = QPushButton(t("group.add_files"))
        add_btn.clicked.connect(self._add_files)
        file_btn_row.addWidget(add_btn)
        remove_btn = QPushButton("\u2190 " + t("group.ungrouped"))
        remove_btn.clicked.connect(self._remove_selected_files)
        file_btn_row.addWidget(remove_btn)
        delete_btn = QPushButton(t("file.delete_from_disk"))
        delete_btn.setStyleSheet("background-color: #c0392b; color: white;")
        delete_btn.clicked.connect(self._delete_selected_files)
        file_btn_row.addWidget(delete_btn)
        file_btn_row.addStretch()
        right_col.addLayout(file_btn_row)
        middle.addLayout(right_col, stretch=1)
        layout.addLayout(middle, stretch=1)
        bottom_row = QHBoxLayout()
        self._small_template_cb = QCheckBox(t("group.use_small_template"))
        self._small_template_cb.setChecked(self._group.use_small_template)
        bottom_row.addWidget(self._small_template_cb)
        self._small_template_entry = QLineEdit(self._group.small_template)
        self._small_template_entry.setEnabled(self._group.use_small_template)
        self._small_template_cb.toggled.connect(self._small_template_entry.setEnabled)
        bottom_row.addWidget(self._small_template_entry, stretch=1)
        layout.addLayout(bottom_row)
        self._refresh_instruments()
        self._refresh_file_list()
        self._update_score_display()

    def _refresh_instruments(self):
        while self._inst_layout.count():
            item = self._inst_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._inst_vars: List[QCheckBox] = []
        instruments = self._group.instruments
        if not instruments:
            self._inst_layout.addWidget(QLabel(t("group.no_instruments")))
            return
        for i, name in enumerate(instruments):
            cb = QCheckBox(name)
            cb.setChecked(i in self._group.selected_instruments)
            cb.toggled.connect(self._on_instrument_check_changed)
            self._inst_layout.addWidget(cb)
            self._inst_vars.append(cb)
        self._inst_layout.addStretch()
        all_checked = len(self._group.selected_instruments) == len(instruments) and len(instruments) > 0
        self._select_all_cb.blockSignals(True)
        self._select_all_cb.setChecked(all_checked)
        self._select_all_cb.blockSignals(False)
        self._check_mismatch()

    def _toggle_select_all(self, checked):
        for cb in self._inst_vars:
            cb.blockSignals(True)
            cb.setChecked(checked)
            cb.blockSignals(False)
        self._on_instrument_check_changed()

    def _on_instrument_check_changed(self):
        self._group.selected_instruments = [
            i for i, cb in enumerate(self._inst_vars) if cb.isChecked()
        ]
        all_checked = len(self._group.selected_instruments) == len(self._inst_vars) and len(self._inst_vars) > 0
        self._select_all_cb.blockSignals(True)
        self._select_all_cb.setChecked(all_checked)
        self._select_all_cb.blockSignals(False)
        self._check_mismatch()
        self.main_window._mark_modified()

    def _check_mismatch(self):
        n_inst = len(self._group.selected_instruments)
        n_files = len(self._group.files)
        if n_files == 0 and n_inst == 0:
            self._mismatch_label.setText("")
        elif n_files != n_inst:
            self._mismatch_label.setText(
                t("group.mismatch", n_inst=n_inst, n_files=n_files),
            )
            self._mismatch_label.setStyleSheet("color: #e74c3c;")
        else:
            self._mismatch_label.setText(t("group.match", count=n_inst))
            self._mismatch_label.setStyleSheet("color: #2ecc71;")

    def _refresh_file_list(self):
        self._file_list.clear()
        instruments = self._group.instruments
        selected = self._group.selected_instruments
        for i, f in enumerate(self._group.files):
            inst = ""
            if i < len(selected) and selected[i] < len(instruments):
                inst = f"{instruments[selected[i]]}  |  "
            self._file_list.addItem(f"{inst}{f.display_name}")

    def _on_files_reordered(self):
        new_order = []
        for i in range(self._file_list.count()):
            text = self._file_list.item(i).text()
            name = text.split("|")[-1].strip() if "|" in text else text.strip()
            for f in self._group.files:
                if f.display_name == name and f not in new_order:
                    new_order.append(f)
                    break
        if len(new_order) == len(self._group.files):
            self._group.files = new_order
            self.main_window._mark_modified()

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, t("filedialog.select_pdf"), "",
            f"{t('filedialog.pdf_files')} (*.pdf)",
        )
        if not paths:
            return
        from services.import_service import ImportService
        from services.file_service import FileService
        files = ImportService(FileService()).import_files(paths)
        self._group.files.extend(files)
        self._refresh_file_list()
        self._check_mismatch()
        self.main_window._mark_modified()

    def _remove_selected_files(self):
        indices = sorted(
            [self._file_list.row(item) for item in self._file_list.selectedItems()],
            reverse=True,
        )
        for i in indices:
            if 0 <= i < len(self._group.files):
                removed = self._group.files.pop(i)
                self.project.ungrouped_files.append(removed)
        self._refresh_file_list()
        self._check_mismatch()
        self.main_window._mark_modified()
        self.main_window._rebuild_tabs()

    def _delete_selected_files(self):
        indices = sorted(
            [self._file_list.row(item) for item in self._file_list.selectedItems()],
            reverse=True,
        )
        if not indices:
            return
        names = [self._group.files[i].display_name for i in indices if i < len(self._group.files)]
        result = QMessageBox.question(
            self, t("file.delete_from_disk"),
            t("file.confirm_delete", name="\n".join(names)),
        )
        if result != QMessageBox.Yes:
            return
        from services.file_service import FileService
        fs = FileService()
        for i in indices:
            if 0 <= i < len(self._group.files):
                try:
                    fs.delete_file(self._group.files[i].original_path)
                except Exception:
                    pass
                self._group.files.pop(i)
        self._refresh_file_list()
        self._check_mismatch()
        self.main_window._mark_modified()

    def _update_score_display(self):
        if self._group.score_file:
            self._score_label.setText(self._group.score_file.display_name)
            self._score_label.setStyleSheet("")
        else:
            self._score_label.setText(t("group.score_file.none"))
            self._score_label.setStyleSheet("color: gray;")

    def _set_score_file(self):
        if not self._group.files:
            return
        menu = QMenu(self)
        for i, f in enumerate(self._group.files):
            action = menu.addAction(f.display_name)
            action.triggered.connect(
                lambda checked=False, idx=i: self._do_set_score(idx),
            )
        menu.exec(self.sender().mapToGlobal(self.sender().rect().bottomLeft()))

    def _do_set_score(self, index: int):
        if self._group.score_file:
            self._group.files.append(self._group.score_file)
        if 0 <= index < len(self._group.files):
            self._group.score_file = self._group.files.pop(index)
        self._update_score_display()
        self._refresh_file_list()
        self._check_mismatch()
        self.main_window._mark_modified()

    def _clear_score_file(self):
        if self._group.score_file:
            self._group.files.insert(0, self._group.score_file)
            self._group.score_file = None
            self._update_score_display()
            self._refresh_file_list()
            self._check_mismatch()
            self.main_window._mark_modified()

    def _auto_detect_score(self):
        if self._group.score_file:
            return
        for i, f in enumerate(self._group.files):
            name_lower = os.path.splitext(f.display_name)[0].lower()
            for kw in self._SCORE_KEYWORDS:
                if kw in name_lower:
                    self._do_set_score(i)
                    return

    def _auto_detect_piece_name(self):
        if self._piece_name_entry.text().strip():
            return
        if not self._group.files:
            return
        detected = detect_piece_name([f.display_name for f in self._group.files])
        if detected:
            self._piece_name_entry.setText(detected)
            self._group.piece_name = detected

    def _detect_piece_name(self):
        filenames = [f.display_name for f in self._group.files]
        detected = detect_piece_name(filenames)
        if detected:
            self._piece_name_entry.setText(detected)
            self.main_window._mark_modified()
        else:
            QMessageBox.information(self, t("dialog.info"), t("dialog.info.cannot_detect"))

    def _delete_group(self):
        result = QMessageBox.question(
            self, t("dialog.delete_group"),
            t("dialog.delete_group.message", name=self._group.name),
        )
        if result != QMessageBox.Yes:
            return
        self.project.ungrouped_files.extend(self._group.files)
        if self._group in self.project.groups:
            self.project.groups.remove(self._group)
        self.main_window._mark_modified()
        self.main_window._rebuild_tabs()

    def on_instruments_changed(self, instruments):
        self._group.instruments = list(instruments)
        valid = set(range(len(instruments)))
        self._group.selected_instruments = [
            i for i in self._group.selected_instruments if i in valid
        ]
        self._refresh_instruments()
        self._refresh_file_list()

    def sync_to_group(self):
        self._group.name = self._name_entry.text().strip()
        self._group.piece_name = self._piece_name_entry.text().strip()
        self._group.movement_number = self._movement_num_entry.text().strip()
        self._group.movement_name = self._movement_name_entry.text().strip()
        self._group.use_small_template = self._small_template_cb.isChecked()
        if self._group.use_small_template:
            self._group.small_template = self._small_template_entry.text()
        self._group.selected_instruments = [
            i for i, cb in enumerate(self._inst_vars) if cb.isChecked()
        ]
        self._group.score_label = self._score_label_entry.text().strip()
