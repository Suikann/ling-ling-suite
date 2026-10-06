# -*- coding: utf-8 -*-
"""
主視窗（PySide6）

應用程式的主要視窗，整合所有 UI 面板。
"""
import os
from typing import Iterable, NamedTuple, Optional
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
    LOCALE_EN, LOCALE_ZH_TW, TEMPLATE_VARIABLES, OperationKind,
)
from core.locale import t, get_locale, is_english, localized, set_locale
from core.models import Project, Group, SplitResult, UndoMapping
from core.paths import same_path
from services.file_service import FileService
from services.import_service import ImportService
from services.move_history import MoveHistory, MoveResult, PendingMove, RenameVerdict
from services.workspace_service import WorkspaceService
from services.preferences_service import PreferencesService
from services.project_access import AccessResult, ProjectAccess
from services.rename_service import assign_default_sections
from services.split_service import SplitService
from ui.instrument_list import InstrumentListEditor


class _PendingMoveTexts(NamedTuple):
    """中斷提示的字串鍵：標題、未搬完時的訊息、已搬完時的訊息"""
    title: str
    message: str
    finished_message: str


# 中斷提示依被中斷的操作種類說明
_PENDING_MOVE_TEXTS = {
    OperationKind.RENAME: _PendingMoveTexts(
        "dialog.pending_move.title", "dialog.pending_move.message", "dialog.pending_move.finished_message",
    ),
    OperationKind.UNDO: _PendingMoveTexts(
        "dialog.pending_move.undo.title", "dialog.pending_move.undo.message",
        "dialog.pending_move.undo.finished_message",
    ),
    OperationKind.REDO: _PendingMoveTexts(
        "dialog.pending_move.redo.title", "dialog.pending_move.redo.message",
        "dialog.pending_move.redo.finished_message",
    ),
}


class MainWindow(QMainWindow):
    """應用程式主視窗"""

    def __init__(
        self, preferences: PreferencesService, parent=None, history: Optional[MoveHistory] = None,
    ):
        """
        Args:
            preferences: 使用者偏好
            parent: 父元件
            history: 搬移歷程；省略時以使用者資料目錄建立
        """
        super().__init__(parent)
        self.project = self._blank_project()
        self.project.subscribe(self._update_title)
        self._preferences = preferences
        self.file_service = FileService()
        self.import_service = ImportService(self.file_service)
        self.workspace_service = WorkspaceService(self.file_service)
        self._project_access = ProjectAccess(self.file_service, preferences, self.workspace_service)
        self._history = history or MoveHistory(self.file_service, self.workspace_service)
        self._splitter = SplitService(self.file_service, self.workspace_service)
        self._project_path: Optional[str] = None
        self._suggested_name: str = ""
        # 紀錄寫不進去而留下進行中紀錄的那批搬移，其路徑變動已套用到的專案（之後保留結果時不再套用一次）
        self._pending_applied_to: Optional[Project] = None
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
        self._recent_menu.setMinimumWidth(250)
        self._recent_menu.setToolTipsVisible(True)
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
        catalog_menu = mb.addMenu(t("menu.catalog"))
        self._add_action(catalog_menu, t("menu.catalog.open"), self._open_catalog)
        self._add_action(catalog_menu, t("menu.catalog.settings"), self._open_catalog_settings)
        tools_menu = mb.addMenu(t("menu.tools"))
        self._add_action(tools_menu, t("menu.tools.split_pdf"), self._open_split_pdf)
        tools_menu.addSeparator()
        self._add_action(tools_menu, t("menu.tools.rotate_pdf"), self._open_rotate_pdf)
        tools_menu.addSeparator()
        self._add_action(tools_menu, t("menu.tools.open_workspace"), self._open_workspace_folder)
        self._add_action(tools_menu, t("menu.tools.cleanup_workspace"), self._open_workspace_cleanup)
        view_menu = mb.addMenu(t("menu.view"))
        lang_menu = view_menu.addMenu(t("menu.view.language"))
        for code, label_key in [(LOCALE_ZH_TW, "menu.view.language.zh_TW"), (LOCALE_EN, "menu.view.language.en")]:
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
        self._master_template_entry.textEdited.connect(lambda text: self.project.set_master_template(text))
        template_row.addWidget(self._master_template_entry, stretch=1)
        vars_btn = QPushButton(t("panel.insert_variable"))
        vars_btn.clicked.connect(self._show_variable_menu)
        template_row.addWidget(vars_btn)
        bl.addLayout(template_row)
        action_row = QHBoxLayout()
        self._status_label = QLabel(t("status.ready"))
        action_row.addWidget(self._status_label, stretch=1)
        preview_btn = QPushButton(t("panel.preview_rename"))
        preview_btn.setStyleSheet("font-size: 14px; font-weight: bold; padding: 8px 24px;")
        preview_btn.clicked.connect(self._preview_and_rename)
        action_row.addWidget(preview_btn)
        bl.addLayout(action_row)
        parent_layout.addWidget(bottom)

    # --- 分頁管理 ---

    def _rebuild_tabs(self):
        """依專案重建所有分頁；舊分頁延後刪除（重建可能由舊分頁自己的按鈕觸發）"""
        old_tabs = [self._tab_widget.widget(i) for i in range(self._tab_widget.count())]
        self._tab_widget.clear()
        for widget in old_tabs:
            widget.deleteLater()
        self._tab_widget.addTab(
            self._create_ungrouped_tab(), t("group.ungrouped"),
        )
        for group in self.project.groups:
            self._add_group_tab(group)

    def _create_ungrouped_tab(self):
        from ui.group_panel import UngroupedTab
        tab = UngroupedTab(self.project)
        tab.groups_changed.connect(self._rebuild_tabs)
        return tab

    def _add_group_tab(self, group: Group):
        from ui.group_panel import GroupTab
        tab = GroupTab(group, self.project, self.file_service, self.import_service)
        tab.groups_changed.connect(self._rebuild_tabs)
        self._tab_widget.addTab(tab, group.name or group.id[:8])

    def _current_group(self) -> Optional[Group]:
        """目前分頁的群組；未分組分頁為 None"""
        return getattr(self._tab_widget.currentWidget(), "group", None)

    def _show_group(self, group_id: str):
        """切到指定 id 的群組的分頁"""
        for index in range(self._tab_widget.count()):
            group = getattr(self._tab_widget.widget(index), "group", None)
            if group is not None and group.id == group_id:
                self._tab_widget.setCurrentIndex(index)
                return

    def _add_group(self):
        self.project.add_group(
            t("group.new_name", number=len(self.project.groups) + 1),
            score_label=t("group.score_label"),
        )
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
        self.project.link_movements(selected_groups, piece_name)
        self._rebuild_tabs()

    def _on_tab_changed(self, index: int):
        self._instrument_editor.show_group(self._current_group())

    # --- 樂器表回呼 ---

    def _on_instruments_changed(self, instruments):
        """樂器表已寫入目前群組，重新顯示目前分頁的分譜與樂器對應"""
        widget = self._tab_widget.currentWidget()
        if widget is not None:
            widget.refresh()

    # --- 檔案操作 ---

    def _import_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, t("filedialog.select_pdf"), "",
            f"{t('filedialog.pdf_files')} (*.pdf)",
        )
        if not paths:
            return
        files = self.import_service.import_files(paths)
        self.project.add_files(files)
        self._rebuild_tabs()
        self._set_status(t("status.imported_files", count=len(files)))

    def _import_folder(self):
        folder = QFileDialog.getExistingDirectory(self, t("filedialog.select_folder"))
        if not folder:
            return
        groups, ungrouped = self.import_service.import_folder(folder)
        self.project.add_files(ungrouped)
        self.project.add_groups(groups, score_label=t("group.score_label"))
        self._rebuild_tabs()
        if not self._project_path and not self._suggested_name:
            self._suggested_name = os.path.basename(folder)
            self._update_title()
        self._set_status(
            t("status.imported_groups", groups=len(groups), files=len(ungrouped)),
        )

    # --- 專案管理 ---

    @staticmethod
    def _blank_project() -> Project:
        """依目前介面語言的預設模板建立新專案"""
        return Project(
            master_template=localized(DEFAULT_MASTER_TEMPLATE, DEFAULT_MASTER_TEMPLATE_EN),
            subfolder_template=localized(DEFAULT_SUBFOLDER_TEMPLATE, DEFAULT_SUBFOLDER_TEMPLATE_EN),
        )

    def _set_project(self, project: Project, path: Optional[str]):
        """換成另一個專案（新增或開啟）：訂閱它的「已變更」通知並重建畫面"""
        self.project = project
        self.project.subscribe(self._update_title)
        self._project_path = path
        self._suggested_name = ""
        self._sync_ui_from_project()

    def _new_project(self):
        if not self._confirm_unsaved():
            return
        self._set_project(self._blank_project(), None)

    def _open_project(self):
        if not self._confirm_unsaved():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, t("filedialog.open_project"), "",
            f"{t('filedialog.project_files')} (*.llproj)",
        )
        if not path:
            return
        self._do_open_project(path)

    def _do_open_project(self, path: str, moved: Iterable[UndoMapping] = ()):
        """開啟專案檔；讀不到才顯示「無法開啟專案」，最近清單或 meta 寫不進去只在狀態列提示

        Args:
            path: 專案檔路徑
            moved: 開啟後先套用的路徑變動（見 ProjectAccess.open）
        """
        result = self._project_access.open(path, moved)
        self._refresh_recent_menu()
        if result.error is not None:
            QMessageBox.critical(self, t("dialog.error"), t("dialog.error.open_failed", error=result.error))
            return
        self._set_project(result.project, path)
        self._show_access_status(t("status.opened", path=path), result)
        if result.missing_files:
            QMessageBox.warning(
                self, t("dialog.warning"),
                t("missing.on_load", count=len(result.missing_files), files="\n".join(result.missing_files)),
            )

    def _show_access_status(self, done: str, result: AccessResult):
        """開啟或存檔成功後的狀態列：附帶動作有失敗時改顯示失敗的提示

        Args:
            done: 都成功時顯示的訊息
            result: 開啟或存檔的結果
        """
        notices = []
        if result.recent_failed:
            notices.append(t("status.recent_failed"))
        if result.owner_failed:
            notices.append(t("status.workspace_owner_failed", count=len(result.owner_failed)))
        self._set_status(" ".join(notices) or done)

    def _save_project(self) -> bool:
        """存到目前的專案檔，尚未存過時改走另存新檔；回傳是否存成"""
        if not self._project_path:
            return self._save_project_as()
        return self._do_save(self._project_path)

    def _save_project_as(self) -> bool:
        """詢問位置後存檔；在對話框按取消也算沒存成，回傳是否存成"""
        path, _ = QFileDialog.getSaveFileName(
            self, t("filedialog.save_project"), "",
            f"{t('filedialog.project_files')} (*.llproj)",
        )
        if not path:
            return False
        return self._do_save(path)

    def _do_save(self, path: str) -> bool:
        """存檔；專案檔寫不成才顯示「儲存失敗」，最近清單或 meta 寫不進去只在狀態列提示

        Args:
            path: 專案檔路徑

        Returns:
            專案檔是否寫成
        """
        result = self._project_access.save(self.project, path)
        if result.error is not None:
            QMessageBox.critical(self, t("dialog.error"), t("dialog.error.save_failed", error=result.error))
            return False
        self._project_path = path
        self._update_title()
        self._refresh_recent_menu()
        self._show_access_status(t("status.saved", path=path), result)
        return True

    def _sync_ui_from_project(self):
        """畫面改為顯示目前的專案"""
        self._instrument_editor.set_project(self.project)
        self._master_template_entry.setText(self.project.master_template)
        self._rebuild_tabs()
        if self.project.groups:
            self._tab_widget.setCurrentIndex(1)
        self._update_title()

    # --- 工具 ---

    def _preview_and_rename(self):
        if not self.prompt_pending_recovery():
            return
        if not self.project.master_template.strip():
            QMessageBox.warning(self, t("dialog.warning"), t("dialog.warning.empty_template"))
            return
        selected_ids = None
        if len(self.project.groups) > 1:
            selected_ids = self._select_groups_for_rename()
            if selected_ids is None:
                return
        from ui.preview_dialog import PreviewDialog
        def check() -> RenameVerdict:
            assign_default_sections(self.project, is_english(), selected_ids)
            return self._history.check_rename(self.project, selected_ids)
        dialog = PreviewDialog(self.project, check, self._execute_rename, self)
        dialog.exec()

    def _execute_rename(self, verdict: RenameVerdict):
        result = self._history.rename(verdict, project_path=self._project_path or "")
        if self._apply_move_result(result):
            count = len(result.changes)
            self._set_status(t("status.renamed", count=count))
            QMessageBox.information(self, t("dialog.complete"), t("dialog.complete.renamed", count=count))

    def _apply_move_result(self, result: MoveResult, failure_key: Optional[str] = None) -> bool:
        """把搬移歷程的結果交給專案（含搬不回去的殘留、復原的分割）並顯示失敗

        Args:
            result: 搬移歷程的動作結果
            failure_key: 失敗訊息的字串鍵（以 error 帶入失敗原因）；省略時直接顯示失敗原因，
                搬移歷程的拒絕與失敗本身就是完整的訊息

        Returns:
            動作是否照計畫完成且紀錄已寫入（呼叫端據此顯示完成訊息）
        """
        moved = result.changes + result.residual
        if moved:
            self.project.replace_paths(moved)
        if result.split is not None:
            self.project.revert_split(result.split)
        if moved or result.split is not None:
            current = self._tab_widget.currentIndex()
            self._rebuild_tabs()
            self._tab_widget.setCurrentIndex(current)
        self._pending_applied_to = self.project if result.record_error is not None else None
        return self._report_move_result(result, failure_key)

    def _report_move_result(self, result: MoveResult, failure_key: Optional[str] = None) -> bool:
        """顯示搬移歷程動作的失敗；參數與回傳值同 _apply_move_result"""
        if result.error is not None:
            message = t(failure_key, error=result.error) if failure_key else str(result.error)
            QMessageBox.critical(self, t("dialog.error"), message)
        if result.record_error is not None:
            QMessageBox.warning(self, t("dialog.warning"), t("history.record_not_saved", error=result.record_error))
        return result.error is None and result.record_error is None

    def _show_skipped(self, result: MoveResult):
        """列出已不在紀錄位置而略過的檔案"""
        if result.skipped:
            QMessageBox.information(self, t("dialog.info"), t("history.skipped", files="\n".join(result.skipped)))

    def _show_not_restored(self, result: MoveResult):
        """復原的是重新分割時，列出被取代、沒有找回的檔案（仍在資源回收桶）"""
        if result.split is not None and result.split.replaced_files:
            files = "\n".join(result.split.replaced_files)
            QMessageBox.information(self, t("dialog.info"), t("history.split_replaced", files=files))

    # --- 中斷後還原 ---

    def prompt_pending_recovery(self) -> bool:
        """若上次的重新命名、復原或重做中途被中斷，詢問要把已搬動的檔案還原到原位還是保留結果

        啟動時呼叫；重新命名、復原、重做前也會再問一次，因為搬移歷程在紀錄仍在時拒絕執行。
        結果（含「保留結果」的路徑變動）交給專案套用。

        Returns:
            是否已沒有待處理的進行中紀錄（可以繼續執行搬移）
        """
        try:
            pending = self._history.pending()
        except (ValueError, OSError) as e:
            self._history.discard_pending()
            QMessageBox.warning(self, t("dialog.warning"), t("dialog.pending_move.unreadable", error=e))
            return True
        if not pending:
            return True
        texts = _PENDING_MOVE_TEXTS[pending.operation]
        choice = self._ask_pending_move(pending, texts) if pending.moved else "restore"
        if choice is None:
            return False
        if choice == "keep":
            return self._keep_pending(pending)
        result = self._history.recover()
        settled = self._apply_move_result(result, "dialog.pending_move.failed")
        if pending.moved and settled:
            self._show_recovery(result, t(texts.title))
        return settled

    def _keep_pending(self, pending: PendingMove) -> bool:
        """中斷提示選「保留結果」：路徑變動套用到這批搬移所屬的專案

        所屬專案不是目前專案時照一般流程開啟它（目前專案有未存檔的修改時先詢問是否儲存）再套用，
        開啟後判為未存檔；所屬專案當時尚未存檔、目前專案也沒有引用這批檔案時，告知路徑無法更新。
        舊版進行中紀錄沒有記所屬專案，套用到目前專案。目前專案在本次執行中已套用過這批搬移
        （紀錄寫不進去而留下進行中紀錄時）就不再套用，對調與連鎖套用兩次會錯。

        Returns:
            是否已沒有待處理的進行中紀錄；在「是否儲存」選取消時為 False，什麼都不做
        """
        owner = pending.project_path
        applied = self._pending_applied_to is self.project
        elsewhere = not applied and bool(owner) and not (self._project_path and same_path(owner, self._project_path))
        if elsewhere and not self._confirm_unsaved():
            return False
        result = self._history.keep_result()
        if elsewhere and result.error is None:
            self._do_open_project(owner, moved=result.changes)
        elif not applied:
            if owner == "" and not self.project.references_any(
                    path for m in result.changes for path in (m.original, m.renamed)):
                QMessageBox.information(self, t("dialog.info"), t("dialog.pending_move.project_unsaved"))
            return self._apply_move_result(result)
        self._pending_applied_to = self.project if result.record_error is not None else None
        return self._report_move_result(result)

    def _show_recovery(self, result: MoveResult, title: str):
        """中斷還原完成：還原了幾個檔，略過與搬不回去的各列在後"""
        lines = [t("dialog.pending_move.done", count=len(result.changes))]
        if result.skipped:
            lines.append(t("history.skipped", files="\n".join(result.skipped)))
        if result.residual:
            lines.append(t("dialog.pending_move.residual", files="\n".join(m.renamed for m in result.residual)))
        QMessageBox.information(self, title, "\n\n".join(lines))

    def _ask_pending_move(self, pending: PendingMove, texts: _PendingMoveTexts) -> Optional[str]:
        """中斷提示：「還原」／「稍後」，已整批搬完時多一個「保留結果」

        Returns:
            "restore" 或 "keep"；選「稍後」回傳 None
        """
        finished = pending.complete
        msg = QMessageBox(self)
        msg.setWindowTitle(t(texts.title))
        msg.setText(t(texts.finished_message if finished else texts.message, count=pending.moved))
        msg.setIcon(QMessageBox.Question)
        keep_btn = msg.addButton(t("dialog.pending_move.keep"), QMessageBox.AcceptRole) if finished else None
        restore_btn = msg.addButton(t("dialog.pending_move.restore"), QMessageBox.AcceptRole)
        msg.addButton(t("dialog.pending_move.later"), QMessageBox.RejectRole)
        msg.setDefaultButton(keep_btn or restore_btn)
        msg.exec()
        if msg.clickedButton() == restore_btn:
            return "restore"
        if keep_btn and msg.clickedButton() == keep_btn:
            return "keep"
        return None

    # --- 復原／重做 ---

    def _undo_last(self):
        if not self.prompt_pending_recovery():
            return
        record = self._history.latest_undo()
        if not record:
            QMessageBox.information(self, t("dialog.info"), t("dialog.info.no_undo"))
            return
        answer = QMessageBox.question(
            self, t("dialog.confirm_undo"),
            t("dialog.confirm_undo.message", description=record.description),
        )
        if answer != QMessageBox.Yes:
            return
        result = self._history.undo(project_path=self._project_path or "")
        if self._apply_move_result(result, "dialog.error.undo_failed"):
            self._set_status(t("status.undone"))
            self._show_skipped(result)
            self._show_not_restored(result)

    def _redo_last(self):
        if not self.prompt_pending_recovery():
            return
        record = self._history.latest_redo()
        if not record:
            QMessageBox.information(self, t("dialog.info"), t("dialog.info.no_redo"))
            return
        answer = QMessageBox.question(
            self, t("dialog.confirm_redo"),
            t("dialog.confirm_redo.message", description=record.description),
        )
        if answer != QMessageBox.Yes:
            return
        result = self._history.redo(project_path=self._project_path or "")
        if self._apply_move_result(result):
            self._set_status(t("status.redone"))
            self._show_skipped(result)

    def _open_split_pdf(self):
        from ui.split_dialog import SplitPdfDialog
        dialog = SplitPdfDialog(
            self.project, self._on_split_complete, self._current_group(), self,
            splitter=self._splitter, project_path=self._project_path or "",
        )
        dialog.exec()

    # --- 工作區 ---

    def _open_workspace_folder(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        folder = self.workspace_service.workspace_dir
        if not self.file_service.directory_exists(folder):
            QMessageBox.information(self, t("dialog.info"), t("workspace.open_failed"))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _scan_workspace(self):
        """掃描工作區（最近清單中已不存在的專案檔會被移除，選單跟著更新）"""
        scan = self._project_access.scan_workspace(self.project)
        self._refresh_recent_menu()
        return scan

    def _open_workspace_cleanup(self):
        from ui.workspace_dialog import WorkspaceCleanupDialog
        dialog = WorkspaceCleanupDialog(self.workspace_service, self._scan_workspace, self)
        dialog.exec()

    def _on_split_complete(self, result: SplitResult):
        """分割完成：結果交給專案套用、切到接手新分譜的群組，分割紀錄（帶專案回報的安置方式）交給搬移歷程"""
        placement = self.project.apply_split(result, score_label=t("group.score_label"))
        self._rebuild_tabs()
        self._show_group(placement.group_id)
        self._set_status(t("split.files_added", count=len(result.parts)))
        try:
            self._history.record_split(result.record(placement))
        except OSError as e:
            QMessageBox.warning(self, t("dialog.warning"), t("history.record_not_saved", error=e))

    def _open_rotate_pdf(self):
        from ui.rotate_dialog import RotatePdfDialog
        dialog = RotatePdfDialog(self.project, self._history, self._current_group(), self)
        dialog.exec()

    def _set_language(self, lang_code: str):
        if lang_code == get_locale():
            return
        set_locale(lang_code)
        self._preferences.set("language", lang_code)
        self._preferences.save()
        self.project.convert_template_language(lang_code)
        self.menuBar().clear()
        self._create_menu()
        self._create_ui()
        self._sync_ui_from_project()
        self._update_title()

    # --- 譜庫 ---

    def _get_auth_service(self):
        """取得或建立 Google 認證服務"""
        if not hasattr(self, "_auth_service"):
            from services.google_auth_service import GoogleAuthService
            self._auth_service = GoogleAuthService(self.file_service)
        return self._auth_service

    def _open_catalog(self):
        """開啟譜庫瀏覽器，尚未設定時先引導設定"""
        auth = self._get_auth_service()
        spreadsheet_id = self._preferences.get("catalog_spreadsheet_id")
        if not auth.is_authenticated or not spreadsheet_id:
            if not self._open_catalog_settings():
                return
        from ui.catalog_window import CatalogWindow
        self._catalog_window = CatalogWindow(auth, self._preferences)
        self._catalog_window.show()

    def _open_catalog_settings(self) -> bool:
        """開啟譜庫設定，回傳是否設定完成"""
        from ui.catalog_settings_dialog import CatalogSettingsDialog
        auth = self._get_auth_service()
        dialog = CatalogSettingsDialog(auth, self._preferences, self)
        return dialog.exec() == CatalogSettingsDialog.Accepted

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
        def update_select_all():
            select_all.blockSignals(True)
            select_all.setChecked(all(c.isChecked() for c in cbs))
            select_all.blockSignals(False)
        select_all.toggled.connect(toggle_all)
        for cb in cbs:
            cb.toggled.connect(update_select_all)
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
        for var in TEMPLATE_VARIABLES:
            var_name = localized(var.name, var.name_en)
            action = menu.addAction(f"{{{var_name}}} - {var.description}")
            action.triggered.connect(
                lambda checked=False, v=var_name: self._insert_variable(v),
            )
        menu.exec(self.sender().mapToGlobal(self.sender().rect().bottomLeft()))

    def _insert_variable(self, var_name: str):
        self._master_template_entry.insert(f"{{{var_name}}}")

    def _update_title(self):
        title = t("app.title")
        if self._project_path:
            title += f" - {os.path.basename(self._project_path)}"
        elif self._suggested_name:
            title += f" - {self._suggested_name}"
        else:
            title += f" - {t('app.unsaved_project')}"
        if self.project.is_modified():
            title += " *"
        self.setWindowTitle(title)

    def _set_status(self, text: str):
        self._status_label.setText(text)

    def _confirm_unsaved(self) -> bool:
        """有未存檔的修改時詢問是否儲存（開新專案、開啟專案、開啟最近專案）

        Returns:
            可以繼續原本的操作時為 True；選「取消」或選「儲存」但沒存成時為 False
        """
        return not self.project.is_modified() or self._ask_to_save()

    def _differs_from_project_file(self) -> bool:
        """目前內容是否與磁碟上的專案檔不同；還沒存過檔時沒有可比對的檔案，交給未存檔判定"""
        if not self._project_path:
            return False
        return not self._project_access.matches_file(self.project, self._project_path)

    def _ask_to_save(self, title: Optional[str] = None, message: Optional[str] = None) -> bool:
        """詢問是否儲存目前的專案

        Args:
            title: 提示框標題，省略時用「未儲存的變更」
            message: 提示框訊息，省略時用「是否儲存目前的專案？」

        Returns:
            可以繼續原本的操作時為 True（選「不儲存」，或選「儲存」且存成）；
            選「取消」或選「儲存」但沒存成時為 False
        """
        msg = QMessageBox(self)
        msg.setWindowTitle(title or t("dialog.unsaved"))
        msg.setText(message or t("dialog.unsaved.message"))
        msg.setIcon(QMessageBox.Question)
        save_btn = msg.addButton(t("dialog.save_btn"), QMessageBox.AcceptRole)
        discard_btn = msg.addButton(t("dialog.discard_btn"), QMessageBox.DestructiveRole)
        msg.addButton(t("dialog.cancel_btn"), QMessageBox.RejectRole)
        msg.setDefaultButton(save_btn)
        msg.exec()
        if msg.clickedButton() == save_btn:
            return self._save_project()
        return msg.clickedButton() == discard_btn

    def _refresh_recent_menu(self):
        self._recent_menu.clear()
        recent = self._project_access.recent_projects()
        if not recent:
            action = self._recent_menu.addAction(t("menu.file.recent.empty"))
            action.setEnabled(False)
            return
        for path in recent:
            action = self._recent_menu.addAction(os.path.basename(path))
            action.setToolTip(path)
            action.triggered.connect(
                lambda checked=False, p=path: self._open_recent(p),
            )

    def _open_recent(self, path: str):
        """開啟最近清單中的專案；專案檔已不存在時提示並從清單移除，不詢問是否儲存"""
        if self._project_access.forget_if_missing(path):
            self._refresh_recent_menu()
            QMessageBox.critical(self, t("dialog.error"), t("dialog.error.file_not_found", path=path))
            return
        if not self._confirm_unsaved():
            return
        self._do_open_project(path)

    def closeEvent(self, event):
        """關閉前除了未存檔判定，再和磁碟上的專案檔比對一次；任一不同就詢問是否儲存"""
        unsaved = self.project.is_modified() or self._differs_from_project_file()
        if unsaved and not self._ask_to_save(t("dialog.close"), t("dialog.close.message")):
            event.ignore()
            return
        event.accept()
