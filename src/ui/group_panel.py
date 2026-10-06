# -*- coding: utf-8 -*-
"""
群組管理面板（PySide6）

提供群組標籤管理與檔案清單。分頁只表達使用者的意圖，修改一律透過專案的編輯操作立即寫入；
影響其他分頁的修改（群組新增或刪除、檔案在群組與未分組之間搬動）以 groups_changed 通知主視窗重建分頁。
"""
from typing import List, Optional
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QListWidget, QListWidgetItem,
    QMessageBox, QMenu, QFileDialog, QAbstractItemView,
)
from ui.widgets import DragListWidget
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from core.locale import t
from core.models import FileInfo, Group, Project


class UngroupedTab(QWidget):
    """未分組標籤"""

    groups_changed = Signal()
    group: Optional[Group] = None

    def __init__(self, project: Project, parent=None):
        super().__init__(parent)
        self.project = project
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
        QShortcut(QKeySequence("Ctrl+A"), self._list, self._select_all_items)
        layout.addWidget(self._list)
        self.refresh()

    def refresh(self):
        """依專案重新列出未分組檔案"""
        self._list.clear()
        self._select_all.setChecked(False)
        if not self.project.ungrouped_files:
            item = QListWidgetItem(t("ungrouped.empty"))
            item.setFlags(Qt.NoItemFlags)
            self._list.addItem(item)
            return
        for f in self.project.ungrouped_files:
            self._list.addItem(f.display_name)

    def _select_all_items(self):
        self._list.selectAll()
        self._select_all.setChecked(True)

    def _toggle_select_all(self, checked):
        for i in range(self._list.count()):
            self._list.item(i).setSelected(checked)

    def _selected_files(self) -> List[FileInfo]:
        """清單中選取的未分組檔案"""
        files = self.project.ungrouped_files
        return [files[i] for i in range(min(self._list.count(), len(files))) if self._list.item(i).isSelected()]

    def _move_selected(self):
        if not self.project.groups:
            QMessageBox.information(self, t("dialog.info"), t("dialog.info.create_group_first"))
            return
        if not self._selected_files():
            return
        menu = QMenu(self)
        for group in self.project.groups:
            action = menu.addAction(group.name or group.id[:8])
            action.triggered.connect(
                lambda checked=False, g=group: self._do_batch_move(g),
            )
        menu.exec(self.sender().mapToGlobal(self.sender().rect().bottomLeft()))

    def _do_batch_move(self, group: Group):
        self.project.move_to_group(self._selected_files(), group)
        self.groups_changed.emit()

    def _new_group_from_selected(self):
        files = self._selected_files()
        if not files:
            return
        self.project.add_group(
            t("group.new_name", number=len(self.project.groups) + 1),
            score_label=t("group.score_label"), files=files,
        )
        self.groups_changed.emit()


class GroupTab(QWidget):
    """群組標籤"""

    groups_changed = Signal()

    def __init__(self, group: Group, project: Project, parent=None):
        super().__init__(parent)
        self.group = group
        self.project = project
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        top_row = QHBoxLayout()
        top_row.addWidget(QLabel(t("group.name_label")))
        self._name_entry = QLineEdit()
        top_row.addWidget(self._name_entry, stretch=1)
        del_btn = QPushButton(t("group.delete"))
        del_btn.setStyleSheet("background-color: #c0392b; color: white;")
        del_btn.clicked.connect(self._delete_group)
        top_row.addWidget(del_btn)
        layout.addLayout(top_row)
        vars_row = QHBoxLayout()
        vars_row.addWidget(QLabel(t("group.piece_name_label")))
        self._piece_name_entry = QLineEdit()
        vars_row.addWidget(self._piece_name_entry)
        detect_btn = QPushButton(t("group.auto_detect"))
        detect_btn.clicked.connect(self._detect_piece_name)
        vars_row.addWidget(detect_btn)
        vars_row.addWidget(QLabel(t("group.movement_num_label")))
        self._movement_num_entry = QLineEdit()
        self._movement_num_entry.setFixedWidth(60)
        vars_row.addWidget(self._movement_num_entry)
        vars_row.addWidget(QLabel(t("group.movement_name_label")))
        self._movement_name_entry = QLineEdit()
        vars_row.addWidget(self._movement_name_entry)
        layout.addLayout(vars_row)
        vars_row2 = QHBoxLayout()
        vars_row2.addWidget(QLabel(t("group.composer_label")))
        self._composer_entry = QLineEdit()
        vars_row2.addWidget(self._composer_entry)
        vars_row2.addWidget(QLabel(t("group.genre_label")))
        self._genre_entry = QLineEdit()
        vars_row2.addWidget(self._genre_entry)
        vars_row2.addStretch()
        layout.addLayout(vars_row2)
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
        layout.addLayout(score_row)
        score_label_row = QHBoxLayout()
        score_label_row.addWidget(QLabel(t("group.score_file.label")))
        self._score_label_entry = QLineEdit()
        self._score_label_entry.setPlaceholderText(t("group.score_label"))
        self._score_label_entry.setFixedWidth(120)
        score_label_row.addWidget(self._score_label_entry)
        score_label_row.addStretch()
        layout.addLayout(score_label_row)
        self._mismatch_label = QLabel("")
        self._mismatch_label.setWordWrap(True)
        self._mismatch_label.setStyleSheet("font-size: 12px;")
        layout.addWidget(self._mismatch_label)
        layout.addWidget(QLabel(t("group.file_list")))
        self._file_list = DragListWidget()
        self._file_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._file_list.model().rowsMoved.connect(self._on_files_reordered)
        QShortcut(QKeySequence("Delete"), self._file_list, self._delete_selected_files)
        QShortcut(QKeySequence("Ctrl+A"), self._file_list, self._file_list.selectAll)
        layout.addWidget(self._file_list, stretch=1)
        file_btn_row = QHBoxLayout()
        add_btn = QPushButton(t("group.add_files"))
        add_btn.clicked.connect(self._add_files)
        file_btn_row.addWidget(add_btn)
        remove_btn = QPushButton("← " + t("group.ungrouped"))
        remove_btn.clicked.connect(self._remove_selected_files)
        file_btn_row.addWidget(remove_btn)
        delete_btn = QPushButton(t("file.delete_from_disk"))
        delete_btn.setStyleSheet("background-color: #c0392b; color: white;")
        delete_btn.clicked.connect(self._delete_selected_files)
        file_btn_row.addWidget(delete_btn)
        file_btn_row.addStretch()
        layout.addLayout(file_btn_row)
        bottom_row = QHBoxLayout()
        self._small_template_cb = QCheckBox(t("group.use_small_template"))
        bottom_row.addWidget(self._small_template_cb)
        self._small_template_entry = QLineEdit()
        self._small_template_cb.toggled.connect(self._small_template_entry.setEnabled)
        bottom_row.addWidget(self._small_template_entry, stretch=1)
        layout.addLayout(bottom_row)
        self._show_fields()
        self._write_user_edits()
        self.refresh()

    def _text_fields(self):
        """文字欄位與它對應的群組屬性"""
        return (
            (self._name_entry, "name"),
            (self._piece_name_entry, "piece_name"),
            (self._movement_num_entry, "movement_number"),
            (self._movement_name_entry, "movement_name"),
            (self._composer_entry, "composer"),
            (self._genre_entry, "genre"),
            (self._score_label_entry, "score_label"),
        )

    def _show_fields(self):
        """把群組的文字與小模板欄位顯示在畫面上（程式填值，不經過使用者編輯的訊號）"""
        for entry, attr in self._text_fields():
            entry.setText(getattr(self.group, attr))
        self._small_template_cb.setChecked(self.group.use_small_template)
        self._small_template_entry.setText(self.group.small_template)
        self._small_template_entry.setEnabled(self.group.use_small_template)

    def _write_user_edits(self):
        """使用者編輯欄位或切換小模板時立即寫入專案；只接使用者操作的訊號，程式填值不寫入"""
        for entry, attr in self._text_fields():
            entry.textEdited.connect(
                lambda text, a=attr: self.project.update_group(self.group, **{a: text.strip()}),
            )
        self._small_template_entry.textEdited.connect(
            lambda text: self.project.update_group(self.group, small_template=text),
        )
        self._small_template_cb.clicked.connect(
            lambda checked: self.project.update_group(self.group, use_small_template=checked),
        )

    def refresh(self):
        """依群組重新顯示分譜清單、總譜與檔案數是否符合樂器表"""
        self._refresh_file_list()
        self._update_score_display()
        self._check_mismatch()

    def _check_mismatch(self):
        n_inst = len(self.group.instruments)
        n_files = len(self.group.files)
        if n_files == 0 and n_inst == 0:
            self._mismatch_label.setText("")
        elif n_files != n_inst:
            self._mismatch_label.setText(
                t("group.mismatch", n_inst=n_inst, n_files=n_files),
            )
            self._mismatch_label.setStyleSheet("color: #e74c3c; font-size: 12px;")
        else:
            self._mismatch_label.setText(t("group.match", count=n_inst))
            self._mismatch_label.setStyleSheet("color: #2ecc71; font-size: 12px;")

    def _refresh_file_list(self):
        """列出分譜；每列記住它在群組中的位置，拖拉排序後依此還原新順序"""
        self._file_list.clear()
        instruments = self.group.instruments
        for i, f in enumerate(self.group.files):
            inst = f"{instruments[i]}  |  " if i < len(instruments) else ""
            item = QListWidgetItem(f"{inst}{f.display_name}")
            item.setData(Qt.UserRole, i)
            self._file_list.addItem(item)

    def _selected_files(self) -> List[FileInfo]:
        """清單中選取的分譜（依群組順序）"""
        rows = sorted(self._file_list.row(item) for item in self._file_list.selectedItems())
        return [self.group.files[i] for i in rows if 0 <= i < len(self.group.files)]

    def _on_files_reordered(self):
        order = [self._file_list.item(i).data(Qt.UserRole) for i in range(self._file_list.count())]
        if sorted(i for i in order if i is not None) != list(range(len(self.group.files))):
            return
        self.project.reorder_files(self.group, [self.group.files[i] for i in order])
        self.refresh()

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, t("filedialog.select_pdf"), "",
            f"{t('filedialog.pdf_files')} (*.pdf)",
        )
        if not paths:
            return
        from services.import_service import ImportService
        from services.file_service import FileService
        self.project.add_files(ImportService(FileService()).import_files(paths), self.group)
        self._piece_name_entry.setText(self.group.piece_name)
        self.refresh()

    def _remove_selected_files(self):
        files = self._selected_files()
        if not files:
            return
        self.project.move_to_ungrouped(files)
        self.groups_changed.emit()

    def _delete_selected_files(self):
        files = self._selected_files()
        if not files:
            return
        result = QMessageBox.question(
            self, t("file.delete_from_disk"),
            t("file.confirm_delete", name="\n".join(f.display_name for f in files)),
        )
        if result != QMessageBox.Yes:
            return
        from services.file_service import FileService
        fs = FileService()
        for f in files:
            try:
                fs.delete_file(f.original_path)
            except Exception:
                pass
        self.project.remove_paths([f.original_path for f in files])
        self.refresh()

    def _update_score_display(self):
        if self.group.score_file:
            self._score_label.setText(self.group.score_file.display_name)
            self._score_label.setStyleSheet("")
        else:
            self._score_label.setText(t("group.score_file.none"))
            self._score_label.setStyleSheet("color: gray;")

    def _set_score_file(self):
        if not self.group.files:
            return
        menu = QMenu(self)
        for f in self.group.files:
            action = menu.addAction(f.display_name)
            action.triggered.connect(
                lambda checked=False, file=f: self._do_set_score(file),
            )
        menu.exec(self.sender().mapToGlobal(self.sender().rect().bottomLeft()))

    def _do_set_score(self, file: FileInfo):
        self.project.set_score(self.group, file)
        self.refresh()

    def _clear_score_file(self):
        self.project.clear_score(self.group)
        self.refresh()

    def _detect_piece_name(self):
        """使用者按「自動偵測」重猜曲名"""
        detected = self.project.guess_piece_name(self.group)
        if detected:
            self._piece_name_entry.setText(detected)
        else:
            QMessageBox.information(self, t("dialog.info"), t("dialog.info.cannot_detect"))

    def _delete_group(self):
        result = QMessageBox.question(
            self, t("dialog.delete_group"),
            t("dialog.delete_group.message", name=self.group.name),
        )
        if result != QMessageBox.Yes:
            return
        self.project.delete_group(self.group)
        self.groups_changed.emit()
