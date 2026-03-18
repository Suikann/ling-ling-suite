# -*- coding: utf-8 -*-
"""
主視窗（PySide6）

應用程式的主要視窗，整合所有 UI 面板。
"""
import os
from typing import Optional
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QLineEdit, QCheckBox, QPushButton, QFileDialog,
    QMessageBox, QTabWidget, QMenu,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence
from core.constants import (
    DEFAULT_MASTER_TEMPLATE, DEFAULT_MASTER_TEMPLATE_EN,
    DEFAULT_SUBFOLDER_TEMPLATE, DEFAULT_SUBFOLDER_TEMPLATE_EN,
    TEMPLATE_VARIABLES,
)
from core.locale import t, get_locale, set_locale
from core.models import Project, Group, FileInfo
from services.file_service import FileService
from services.import_service import ImportService
from services.preferences_service import PreferencesService
from ui.instrument_list import InstrumentListEditor


class MainWindow(QMainWindow):
    """應用程式主視窗"""

    def __init__(self, preferences: PreferencesService, parent=None):
        super().__init__(parent)
        self.project = Project()
        self._preferences = preferences
        self.file_service = FileService()
        self.import_service = ImportService(self.file_service)
        self._project_path: Optional[str] = None
        self._suggested_name: str = ""
        self._modified = False
        self._project_service = None
        self._rename_service = None
        self._undo_service = None
        self._create_menu()
        self._create_ui()
        self._update_title()

    # --- 選單 ---

    def _create_menu(self):
        mb = self.menuBar()
        file_menu = mb.addMenu(t("menu.file"))
        self._add_action(file_menu, t("menu.file.new"), self._new_project, "Ctrl+N")
        self._add_action(file_menu, t("menu.file.open"), self._open_project, "Ctrl+O")
        self._recent_menu = file_menu.addMenu(t("menu.file.recent"))
        self._refresh_recent_menu()
        file_menu.addSeparator()
        self._add_action(file_menu, t("menu.file.save"), self._save_project, "Ctrl+S")
        self._add_action(file_menu, t("menu.file.save_as"), self._save_project_as)
        edit_menu = mb.addMenu(t("menu.edit"))
        self._add_action(edit_menu, t("menu.edit.undo"), self._undo_last, "Ctrl+Z")
        self._add_action(edit_menu, t("menu.edit.redo"), self._redo_last, "Ctrl+Y")
        import_menu = mb.addMenu(t("menu.import"))
        self._add_action(import_menu, t("menu.import.files"), self._import_files)
        self._add_action(import_menu, t("menu.import.folder"), self._import_folder)
        tools_menu = mb.addMenu(t("menu.tools"))
        self._add_action(tools_menu, t("menu.tools.split_pdf"), self._open_split_pdf)
        tools_menu.addSeparator()
        self._add_action(tools_menu, t("menu.tools.rotate_pdf"), self._open_rotate_pdf)
        view_menu = mb.addMenu(t("menu.view"))
        lang_menu = view_menu.addMenu(t("menu.view.language"))
        for code, label_key in [("zh_TW", "menu.view.language.zh_TW"), ("en", "menu.view.language.en")]:
            action = QAction(t(label_key), self)
            action.triggered.connect(lambda checked=False, c=code: self._set_language(c))
            lang_menu.addAction(action)

    def _add_action(self, menu, text, callback, shortcut=None):
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        menu.addAction(action)

    # --- UI 建構 ---

    def _create_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)
        splitter = QSplitter(Qt.Horizontal)
        main_layout.addWidget(splitter, stretch=1)
        self._instrument_editor = InstrumentListEditor(project=self.project)
        self._instrument_editor.setFixedWidth(260)
        self._instrument_editor.instruments_changed.connect(self._on_instruments_changed)
        splitter.addWidget(self._instrument_editor)
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(6, 0, 0, 0)
        right_layout.setSpacing(4)
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(0, 0, 0, 0)
        add_group_btn = QPushButton(t("group.add"))
        add_group_btn.clicked.connect(self._add_group)
        btn_bar.addWidget(add_group_btn)
        link_btn = QPushButton(t("group.link_movements"))
        link_btn.clicked.connect(self._link_as_movements)
        btn_bar.addWidget(link_btn)
        btn_bar.addStretch()
        right_layout.addLayout(btn_bar)
        self._tab_widget = QTabWidget()
        self._tab_widget.currentChanged.connect(self._on_tab_changed)
        right_layout.addWidget(self._tab_widget)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(1, 1)
        self._build_bottom_panel(main_layout)
        self._rebuild_tabs()

    def _build_bottom_panel(self, parent_layout):
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(8, 4, 8, 8)
        bl.setSpacing(4)
        template_row = QHBoxLayout()
        template_row.addWidget(QLabel(t("panel.master_template")))
        self._master_template_entry = QLineEdit(self.project.master_template)
        template_row.addWidget(self._master_template_entry, stretch=1)
        vars_btn = QPushButton(t("panel.insert_variable"))
        vars_btn.clicked.connect(self._show_variable_menu)
        template_row.addWidget(vars_btn)
        bl.addLayout(template_row)
        subfolder_row = QHBoxLayout()
        self._subfolder_check = QCheckBox(t("panel.subfolder"))
        self._subfolder_check.setChecked(self.project.use_subfolders)
        subfolder_row.addWidget(self._subfolder_check)
        subfolder_row.addWidget(QLabel(t("panel.subfolder_template")))
        self._subfolder_entry = QLineEdit(self.project.subfolder_template)
        subfolder_row.addWidget(self._subfolder_entry, stretch=1)
        bl.addLayout(subfolder_row)
        _btn_area_w = 200
        outdir_row = QHBoxLayout()
        outdir_row.addWidget(QLabel(t("panel.output_dir")))
        self._output_dir_label = QLabel(t("panel.output_dir_hint"))
        self._output_dir_label.setStyleSheet("color: gray;")
        outdir_row.addWidget(self._output_dir_label, stretch=1)
        btn_pair = QWidget()
        btn_pair.setFixedWidth(_btn_area_w)
        bp_lay = QHBoxLayout(btn_pair)
        bp_lay.setContentsMargins(0, 0, 0, 0)
        bp_lay.setSpacing(4)
        browse_btn = QPushButton(t("split.browse"))
        browse_btn.clicked.connect(self._browse_output_dir)
        bp_lay.addWidget(browse_btn, stretch=1)
        clear_btn = QPushButton(t("group.score_file.clear"))
        clear_btn.clicked.connect(self._clear_output_dir)
        bp_lay.addWidget(clear_btn, stretch=1)
        outdir_row.addWidget(btn_pair)
        bl.addLayout(outdir_row)
        action_row = QHBoxLayout()
        self._status_label = QLabel(t("status.ready"))
        action_row.addWidget(self._status_label, stretch=1)
        preview_btn = QPushButton(t("panel.preview_rename"))
        preview_btn.setFixedWidth(_btn_area_w)
        preview_btn.setStyleSheet("font-size: 14px; font-weight: bold; padding: 8px 0;")
        preview_btn.clicked.connect(self._preview_and_rename)
        action_row.addWidget(preview_btn)
        bl.addLayout(action_row)
        parent_layout.addWidget(bottom)

    # --- 分頁管理 ---

    def _rebuild_tabs(self):
        self._tab_widget.clear()
        self._tab_widget.addTab(
            self._create_ungrouped_tab(), t("group.ungrouped"),
        )
        for group in self.project.groups:
            self._add_group_tab(group)

    def _create_ungrouped_tab(self):
        from ui.group_panel import UngroupedTab
        tab = UngroupedTab(self.project, self)
        return tab

    def _add_group_tab(self, group: Group):
        from ui.group_panel import GroupTab
        tab = GroupTab(group, self.project, self)
        self._tab_widget.addTab(tab, group.name or group.id[:8])

    def _add_group(self):
        group = Group(name=t("group.new_name", number=len(self.project.groups) + 1))
        self.project.groups.append(group)
        self._mark_modified()
        self._rebuild_tabs()
        self._tab_widget.setCurrentIndex(self._tab_widget.count() - 1)

    def _link_as_movements(self):
        if len(self.project.groups) < 2:
            QMessageBox.information(self, t("dialog.info"), t("group.link_movements.need_two"))
            return
        from ui.merge_dialog import LinkMovementsDialog
        dialog = LinkMovementsDialog(self.project.groups, self._on_link_confirmed, self)
        dialog.exec()

    def _on_link_confirmed(self, selected_groups, piece_name):
        for i, group in enumerate(selected_groups):
            group.piece_name = piece_name
            group.movement_number = str(i + 1)
            if not group.movement_name:
                group.movement_name = group.name
        self._mark_modified()
        self._rebuild_tabs()

    def _on_tab_changed(self, index: int):
        widget = self._tab_widget.widget(index)
        if widget and hasattr(widget, '_group'):
            self._sync_instrument_editor_to_group(widget._group)

    def _sync_instrument_editor_to_group(self, group):
        if group:
            self._instrument_editor._group = group
            self._instrument_editor.instruments_changed.disconnect(self._on_instruments_changed)
            self._instrument_editor.set_instruments(group.instruments)
            self._instrument_editor.instruments_changed.connect(self._on_instruments_changed)

    # --- 樂器表回呼 ---

    def _on_instruments_changed(self, instruments):
        self._mark_modified()
        widget = self._tab_widget.currentWidget()
        if widget and hasattr(widget, '_group'):
            widget._group.instruments = instruments
            widget._group.selected_instruments = list(range(len(instruments)))
            if hasattr(widget, 'on_instruments_changed'):
                widget.on_instruments_changed(instruments)

    # --- 檔案操作 ---

    def _import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, t("filedialog.select_pdf"), "",
            f"{t('filedialog.pdf_files')} (*.pdf)",
        )
        if not paths:
            return
        files = self.import_service.import_files(paths)
        self.project.ungrouped_files.extend(files)
        self._mark_modified()
        self._rebuild_tabs()
        self._set_status(t("status.imported_files", count=len(files)))

    def _import_folder(self):
        folder = QFileDialog.getExistingDirectory(self, t("filedialog.select_folder"))
        if not folder:
            return
        groups, ungrouped = self.import_service.import_folder(folder)
        self.project.ungrouped_files.extend(ungrouped)
        for g in groups:
            self.project.groups.append(g)
        self._mark_modified()
        self._rebuild_tabs()
        if not self._project_path and not self._suggested_name:
            self._suggested_name = os.path.basename(folder)
            self._update_title()
        self._set_status(
            t("status.imported_groups", groups=len(groups), files=len(ungrouped)),
        )

    # --- 專案管理 ---

    def _new_project(self):
        if self._modified and not self._confirm_discard():
            return
        locale = get_locale()
        self.project = Project()
        self.project.master_template = (
            DEFAULT_MASTER_TEMPLATE_EN if locale == "en" else DEFAULT_MASTER_TEMPLATE
        )
        self.project.subfolder_template = (
            DEFAULT_SUBFOLDER_TEMPLATE_EN if locale == "en" else DEFAULT_SUBFOLDER_TEMPLATE
        )
        self._project_path = None
        self._suggested_name = ""
        self._modified = False
        self._sync_ui_from_project()

    def _open_project(self):
        if self._modified and not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, t("filedialog.open_project"), "",
            f"{t('filedialog.project_files')} (*.llproj)",
        )
        if not path:
            return
        self._do_open_project(path)

    def _do_open_project(self, path: str):
        try:
            if not self._project_service:
                from services.project_service import ProjectService
                self._project_service = ProjectService()
            self.project = self._project_service.load_project(path)
            self._project_path = path
            self._suggested_name = ""
            self._modified = False
            self._sync_ui_from_project()
            self._set_status(t("status.opened", path=path))
            self._add_recent_project(path)
        except Exception as e:
            QMessageBox.critical(self, t("dialog.error"), t("dialog.error.open_failed", error=e))

    def _save_project(self):
        if not self._project_path:
            self._save_project_as()
            return
        self._do_save(self._project_path)

    def _save_project_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, t("filedialog.save_project"), "",
            f"{t('filedialog.project_files')} (*.llproj)",
        )
        if path:
            self._do_save(path)

    def _do_save(self, path: str):
        try:
            self._sync_project_from_ui()
            if not self._project_service:
                from services.project_service import ProjectService
                self._project_service = ProjectService()
            self._project_service.save_project(self.project, path)
            self._project_path = path
            self._modified = False
            self._update_title()
            self._set_status(t("status.saved", path=path))
            self._add_recent_project(path)
        except Exception as e:
            QMessageBox.critical(self, t("dialog.error"), t("dialog.error.save_failed", error=e))

    def _sync_ui_from_project(self):
        self._instrument_editor._project = self.project
        self._master_template_entry.setText(self.project.master_template)
        self._subfolder_check.setChecked(self.project.use_subfolders)
        self._subfolder_entry.setText(self.project.subfolder_template)
        if self.project.output_directory:
            self._output_dir_label.setText(self.project.output_directory)
            self._output_dir_label.setStyleSheet("")
        else:
            self._output_dir_label.setText(t("panel.output_dir_hint"))
            self._output_dir_label.setStyleSheet("color: gray;")
        self._rebuild_tabs()
        if self.project.groups:
            self._tab_widget.setCurrentIndex(1)
        else:
            self._instrument_editor.set_instruments([])
        self._update_title()

    def _sync_project_from_ui(self):
        self.project.master_template = self._master_template_entry.text()
        self.project.use_subfolders = self._subfolder_check.isChecked()
        self.project.subfolder_template = self._subfolder_entry.text()
        for i in range(self._tab_widget.count()):
            widget = self._tab_widget.widget(i)
            if hasattr(widget, 'sync_to_group'):
                widget.sync_to_group()

    # --- 工具 ---

    def _preview_and_rename(self):
        self._sync_project_from_ui()
        if not self.project.master_template.strip():
            QMessageBox.warning(self, t("dialog.warning"), t("dialog.warning.empty_template"))
            return
        if not self._rename_service:
            from services.rename_service import RenameService
            self._rename_service = RenameService(self.file_service)
        plan = self._rename_service.generate_rename_plan(self.project)
        if not plan:
            QMessageBox.information(self, t("dialog.info"), t("dialog.info.no_files"))
            return
        if len(self.project.groups) > 1:
            selected_ids = self._select_groups_for_rename()
            if selected_ids is None:
                return
            plan = [e for e in plan if e.group_id in selected_ids]
            if not plan:
                QMessageBox.information(self, t("dialog.info"), t("dialog.info.no_files"))
                return
        missing = [e for e in plan if not os.path.isfile(e.original_path)]
        if missing:
            names = [os.path.basename(e.original_path) for e in missing[:10]]
            detail = "\n".join(names)
            if len(missing) > 10:
                detail += f"\n{t('dialog.missing_files.more', count=len(missing))}"
            result = QMessageBox.warning(
                self, t("dialog.missing_files"),
                f"{t('dialog.missing_files.header', count=len(missing))}\n\n{detail}",
                QMessageBox.Ok,
            )
            plan = [e for e in plan if os.path.isfile(e.original_path)]
            if not plan:
                return
        conflicts = self._rename_service.detect_conflicts(plan)
        from ui.preview_dialog import PreviewDialog
        dialog = PreviewDialog(plan, conflicts, self._execute_rename, self)
        dialog.exec()

    def _execute_rename(self, plan):
        try:
            record = self._rename_service.execute_rename(plan, self.project)
            if not self._undo_service:
                from services.undo_service import UndoService
                self._undo_service = UndoService(self.file_service)
            self._undo_service.save_undo_record(record)
            self._update_project_paths(record.mappings)
            self._mark_modified()
            self._rebuild_tabs()
            self._set_status(t("status.renamed", count=len(record.mappings)))
            QMessageBox.information(
                self, t("dialog.complete"),
                t("dialog.complete.renamed", count=len(record.mappings)),
            )
        except Exception as e:
            QMessageBox.critical(self, t("dialog.error"), str(e))

    def _update_project_paths(self, mappings):
        """根據重新命名結果更新專案內的檔案路徑"""
        path_map = {m.original: m.renamed for m in mappings}
        for group in self.project.groups:
            if group.score_file and group.score_file.original_path in path_map:
                new_path = path_map[group.score_file.original_path]
                group.score_file.original_path = new_path
                group.score_file.display_name = os.path.basename(new_path)
            for f in group.files:
                if f.original_path in path_map:
                    new_path = path_map[f.original_path]
                    f.original_path = new_path
                    f.display_name = os.path.basename(new_path)
        for f in self.project.ungrouped_files:
            if f.original_path in path_map:
                new_path = path_map[f.original_path]
                f.original_path = new_path
                f.display_name = os.path.basename(new_path)

    def _save_operation_undo(self, op_type, description, **kwargs):
        """儲存操作的復原紀錄"""
        if not self._undo_service:
            from services.undo_service import UndoService
            self._undo_service = UndoService(self.file_service)
        from datetime import datetime
        from core.models import UndoRecord
        record = UndoRecord(
            timestamp=datetime.now().strftime("%Y%m%d_%H%M%S"),
            description=description,
            operation_type=op_type,
            created_files=kwargs.get("created_files", []),
            backup_path=kwargs.get("backup_path", ""),
            original_path=kwargs.get("original_path", ""),
        )
        self._undo_service.save_undo_record(record)

    def _undo_last(self):
        if not self._undo_service:
            from services.undo_service import UndoService
            self._undo_service = UndoService(self.file_service)
        record = self._undo_service.get_latest_undo_record()
        if not record:
            QMessageBox.information(self, t("dialog.info"), t("dialog.info.no_undo"))
            return
        result = QMessageBox.question(
            self, t("dialog.confirm_undo"),
            t("dialog.confirm_undo.message", description=record.description),
        )
        if result != QMessageBox.Yes:
            return
        try:
            self._undo_service.execute_undo(record)
            self._set_status(t("status.undone"))
        except Exception as e:
            QMessageBox.critical(self, t("dialog.error"), t("dialog.error.undo_failed", error=e))

    def _redo_last(self):
        if not self._undo_service:
            from services.undo_service import UndoService
            self._undo_service = UndoService(self.file_service)
        record = self._undo_service.get_latest_redo_record()
        if not record:
            QMessageBox.information(self, t("dialog.info"), t("dialog.info.no_redo"))
            return
        if record.operation_type != "rename":
            QMessageBox.information(self, t("dialog.info"), t("dialog.info.no_redo"))
            return
        result = QMessageBox.question(
            self, t("dialog.confirm_redo"),
            t("dialog.confirm_redo.message", description=record.description),
        )
        if result != QMessageBox.Yes:
            return
        try:
            self._undo_service.execute_redo(record)
            self._set_status(t("status.redone"))
        except Exception as e:
            QMessageBox.critical(self, t("dialog.error"), str(e))

    def _open_split_pdf(self):
        from ui.split_dialog import SplitPdfDialog
        current_group = None
        widget = self._tab_widget.currentWidget()
        if widget and hasattr(widget, '_group'):
            current_group = widget._group
        dialog = SplitPdfDialog(
            self.project, self._on_split_complete, current_group, self,
        )
        dialog.exec()

    def _on_split_complete(self, files, instruments, source_group, source_path):
        selected = list(range(len(files)))
        if source_group:
            source_group.files = [
                f for f in source_group.files if f.original_path != source_path
            ]
            source_group.files.extend(files)
            source_group.instruments = instruments
            source_group.selected_instruments = selected
        else:
            source_name = os.path.splitext(os.path.basename(source_path))[0]
            new_group = Group(
                name=source_name, files=files,
                instruments=instruments, selected_instruments=selected,
            )
            self.project.groups.append(new_group)
        self._save_operation_undo(
            "split", t("undo.split_description", count=len(files)),
            created_files=[f.original_path for f in files],
        )
        self._mark_modified()
        self._rebuild_tabs()
        self._set_status(t("split.files_added", count=len(files)))

    def _open_rotate_pdf(self):
        from ui.rotate_dialog import RotatePdfDialog
        current_group = None
        widget = self._tab_widget.currentWidget()
        if widget and hasattr(widget, '_group'):
            current_group = widget._group
        dialog = RotatePdfDialog(
            self.project, self._on_rotate_complete, current_group, self,
        )
        dialog.exec()

    def _on_rotate_complete(self, backup_path, original_path):
        self._save_operation_undo(
            "rotate", t("undo.rotate_description"),
            backup_path=backup_path, original_path=original_path,
        )

    def _set_language(self, lang_code: str):
        if lang_code == get_locale():
            return
        self._sync_project_from_ui()
        set_locale(lang_code)
        self._preferences.set("language", lang_code)
        self._preferences.save()
        self.menuBar().clear()
        self._create_menu()
        self._create_ui()
        self._sync_ui_from_project()
        self._update_title()

    # --- 輔助 ---

    def _select_groups_for_rename(self):
        """顯示群組選擇對話框，回傳選取的群組 ID 集合，取消時回傳 None"""
        from PySide6.QtWidgets import QDialog, QCheckBox
        dlg = QDialog(self)
        dlg.setWindowTitle(t("panel.select_groups"))
        dlg.resize(320, 200)
        lay = QVBoxLayout(dlg)
        select_all = QCheckBox(t("panel.select_groups.all"))
        select_all.setChecked(True)
        lay.addWidget(select_all)
        cbs = []
        for g in self.project.groups:
            cb = QCheckBox(g.name or g.id[:8])
            cb.setChecked(True)
            cb.setProperty("gid", g.id)
            lay.addWidget(cb)
            cbs.append(cb)
        def toggle_all(checked):
            for c in cbs:
                c.blockSignals(True)
                c.setChecked(checked)
                c.blockSignals(False)
        select_all.toggled.connect(toggle_all)
        lay.addStretch()
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        ok_btn = QPushButton(t("preview.execute"))
        ok_btn.clicked.connect(dlg.accept)
        btn_row.addWidget(ok_btn)
        cancel_btn = QPushButton(t("preview.cancel"))
        cancel_btn.clicked.connect(dlg.reject)
        btn_row.addWidget(cancel_btn)
        lay.addLayout(btn_row)
        if dlg.exec() != QDialog.Accepted:
            return None
        return {cb.property("gid") for cb in cbs if cb.isChecked()}

    def _show_variable_menu(self):
        menu = QMenu(self)
        locale = get_locale()
        for var in TEMPLATE_VARIABLES:
            if locale == "en":
                label = f"{{{var.name_en}}} - {var.description}"
                var_name = var.name_en
            else:
                label = f"{{{var.name}}} - {var.description}"
                var_name = var.name
            action = menu.addAction(label)
            action.triggered.connect(
                lambda checked=False, v=var_name: self._insert_variable(v),
            )
        menu.exec(self.sender().mapToGlobal(self.sender().rect().bottomLeft()))

    def _insert_variable(self, var_name: str):
        self._master_template_entry.insert(f"{{{var_name}}}")

    def _browse_output_dir(self):
        folder = QFileDialog.getExistingDirectory(self, t("panel.output_dir"))
        if folder:
            self.project.output_directory = folder
            self._output_dir_label.setText(folder)
            self._output_dir_label.setStyleSheet("")
            self._mark_modified()

    def _clear_output_dir(self):
        self.project.output_directory = ""
        self._output_dir_label.setText(t("panel.output_dir_hint"))
        self._output_dir_label.setStyleSheet("color: gray;")
        self._mark_modified()

    def _mark_modified(self):
        if not self._modified:
            self._modified = True
            self._update_title()

    def _update_title(self):
        title = t("app.title")
        if self._project_path:
            title += f" - {os.path.basename(self._project_path)}"
        elif self._suggested_name:
            title += f" - {self._suggested_name}"
        else:
            title += f" - {t('app.unsaved_project')}"
        if self._modified:
            title += " *"
        self.setWindowTitle(title)

    def _set_status(self, text: str):
        self._status_label.setText(text)

    def _confirm_discard(self) -> bool:
        result = QMessageBox.question(
            self, t("dialog.unsaved"), t("dialog.unsaved.message"),
            QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
        )
        if result == QMessageBox.Cancel:
            return False
        if result == QMessageBox.Yes:
            self._save_project()
        return True

    def _refresh_recent_menu(self):
        self._recent_menu.clear()
        recent = self._preferences.get("recent_projects") or []
        if not recent:
            action = self._recent_menu.addAction(t("menu.file.recent.empty"))
            action.setEnabled(False)
            return
        for path in recent:
            action = self._recent_menu.addAction(os.path.basename(path))
            action.triggered.connect(
                lambda checked=False, p=path: self._open_recent(p),
            )

    def _open_recent(self, path: str):
        if not os.path.isfile(path):
            QMessageBox.critical(self, t("dialog.error"), f"File not found:\n{path}")
            return
        if self._modified and not self._confirm_discard():
            return
        self._do_open_project(path)

    def _add_recent_project(self, path: str):
        self._preferences.add_recent_project(path)
        self._preferences.save()
        self._refresh_recent_menu()

    def closeEvent(self, event):
        if self._modified:
            result = QMessageBox.question(
                self, t("dialog.close"), t("dialog.close.message"),
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
            )
            if result == QMessageBox.Cancel:
                event.ignore()
                return
            if result == QMessageBox.Yes:
                self._save_project()
        event.accept()
