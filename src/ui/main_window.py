# -*- coding: utf-8 -*-
"""
主視窗（PySide6）

應用程式的主要視窗，整合所有 UI 面板。
"""
import os
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable, Iterable, List, NamedTuple, Optional, Sequence, Tuple
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QSplitter,
    QLabel, QLineEdit, QCheckBox, QComboBox, QPushButton, QFileDialog,
    QMessageBox, QTabWidget, QMenu, QProgressBar,
)
from PySide6.QtCore import QEvent, QStandardPaths, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from core.constants import (
    CATALOG_ROOT_FOLDER_ID_KEY, CATALOG_SPREADSHEET_ID_KEY, DEFAULT_MASTER_TEMPLATE, DEFAULT_MASTER_TEMPLATE_EN,
    DEFAULT_SUBFOLDER_TEMPLATE, DEFAULT_SUBFOLDER_TEMPLATE_EN,
    LOCALE_EN, LOCALE_ZH_TW, PROJECT_EXTENSION, TEMPLATE_VARIABLES, OperationKind, OrchestraType,
)
from core.catalog_constants import PublishingPlatform
from core.catalog_errors import CatalogError, CatalogErrorKind, CatalogFailure
from core.catalog_sync import PlaceWrite, SyncOutcome, WriteSource, place_writes
from core.clear_local import ClearPlan
from core.deposit_revert import LocalProject, RevertChoice, RevertPreview
from core.launch_env import app_version, current_build
from core.locale import t, get_locale, localized, set_locale
from core.models import Project, Group, SplitResult, UndoMapping
from core.paths import same_path
from core.voice_names import duplicate_rows
from core.voice_suggestions import VoiceCandidates
from services.activity_log import ActivityLog
from services.clear_local_service import ClearLocalService, ClearOutcome
from services.club_settings_service import ClubSettingsService
from services.compression_output_service import CompressionBatch, CompressionOutputService
from services.compression_service import CompressionResult, CompressionService
from services.deposit_revert_service import DepositRevertService, RevertOutcome
from services.deposit_service import DepositOutcome, DepositService
from services.download_service import DownloadOutcome, DownloadService
from services.external_opener import ExternalOpener
from services.file_service import FileService
from services.google_auth_service import GoogleAuthService
from services.huaxia_auth_service import HuaXiaAuthService
from services.huaxia_client import PUBLISH_INTERFACE_VERSION, HuaXiaError
from services.import_service import ImportService
from services.move_history import MoveHistory, MoveResult, PendingMove, RenameVerdict
from services.workspace_service import WorkspaceService
from services.preferences_service import PreferencesService
from services.project_access import AccessResult, ProjectAccess
from services.publish_runner import PublishOutcome, PublishProgress, PublishRunner
from services.recall_reminder import RecallReminder
from services.publish_service import (
    FunctionProblem, ProjectBlock, PublishReadiness, PublishService, check_groups, project_blocks,
)
from services.rename_service import assign_default_sections
from services.split_service import SplitService
from ui.background_task import BackgroundTask, TaskSlot, TaskTexts, Work
from ui.catalog_error_bar import CatalogErrorBar
from ui.recall_reminder_bar import RecallReminderBar
from ui.catalog_tasks import catalog_work, wait_for_catalog
from ui.compress_pdf import size_text
from ui.instrumentation_table import InstrumentationTable
from ui.huaxia_prompts import ensure_signed_in, huaxia_error_text, not_officer_text
from ui.waiting_dialog import run_while_waiting
from ui.widgets import ask_save_path, not_downloaded_text

if TYPE_CHECKING:
    from services.catalog_service import CatalogService
    from ui.publish_dialog import PublishPreviewDialog


class _WriteBack(Enum):
    """存檔或開啟專案時寫回譜庫的結果"""
    WRITTEN = "written"      # 已寫入（或已完成），快照已更新
    FAILED = "failed"        # 寫不進去（斷網、API 失敗）
    CANCELLED = "cancelled"  # 在衝突畫面取消


# 各種背景工作的字串鍵
_COMPRESS_TEXTS = TaskTexts.of("compress")
_DEPOSIT_TEXTS = TaskTexts.of("deposit")
_CLEAR_TEXTS = TaskTexts.of("clear")
_DOWNLOAD_TEXTS = TaskTexts.of("download")
_REVERT_TEXTS = TaskTexts.of("revert")
_PUBLISH_TEXTS = TaskTexts.of("publish.run")
# 清除本機檔案不刪的系統標準使用者資料夾（含在地化的名稱，例如 Linux 的「桌面」）
_STANDARD_LOCATIONS = (
    QStandardPaths.HomeLocation, QStandardPaths.DesktopLocation, QStandardPaths.DocumentsLocation,
    QStandardPaths.DownloadLocation, QStandardPaths.MusicLocation, QStandardPaths.PicturesLocation,
    QStandardPaths.MoviesLocation, QStandardPaths.PublicShareLocation, QStandardPaths.TemplatesLocation,
)


def _standard_folders() -> List[str]:
    """系統的標準使用者資料夾（家目錄、桌面、文件、下載、音樂等），清除本機檔案時就算空了也不刪"""
    return [folder for location in _STANDARD_LOCATIONS for folder in QStandardPaths.standardLocations(location)]


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


# 擋下整個發譜的原因的說明
_PROJECT_BLOCK_KEYS = {
    ProjectBlock.UNSAVED: "publish.blocked.unsaved",
    ProjectBlock.NOT_CHINESE_ORCHESTRA: "publish.blocked.not_chinese_orchestra",
}


def _not_ready_text(readiness: PublishReadiness) -> str:
    """HuaXia 那一側不能發的說明：不是幹部的分類，或發譜函式缺少、版本不符"""
    if readiness.not_officer is not None:
        return not_officer_text(readiness.not_officer)
    if readiness.function_problem is FunctionProblem.MISSING:
        return t("publish.function_missing")
    return t("publish.function_mismatch", found=readiness.function_version, expected=PUBLISH_INTERFACE_VERSION)


class MainWindow(QMainWindow):
    """應用程式主視窗"""

    # 登入在背景執行緒進行：「沒存進安全儲存」的通知經這個訊號轉回主執行緒才提示
    _login_not_saved = Signal()
    _huaxia_login_not_saved = Signal()
    # 重讀社團設定多在背景執行緒完成（譜庫視窗、譜庫設定的「儲存設定」）：經這個訊號轉回主執行緒更新「發譜」
    _club_settings_read = Signal()

    def __init__(
        self, preferences: PreferencesService, parent=None, history: Optional[MoveHistory] = None,
        opener: Optional[ExternalOpener] = None,
        auth_service: Optional[GoogleAuthService] = None,
        compressor: Optional[CompressionService] = None,
        log: Optional[ActivityLog] = None,
        huaxia_auth: Optional[HuaXiaAuthService] = None,
        catalog_factory: Optional[Callable[[], "CatalogService"]] = None,
    ):
        """建立主視窗；刪掉舊版的明文權杖檔，不碰安全儲存

        Args:
            preferences: 使用者偏好
            parent: 父元件
            history: 搬移歷程；省略時以使用者資料目錄建立
            opener: 以預設程式開啟網址或檔案；省略時依目前的平台建立
            auth_service: Google 登入服務；省略時以系統的安全儲存建立
            compressor: PDF 壓縮服務；省略時以使用者資料目錄下的暫存資料夾建立
            log: 紀錄檔；省略時以使用者資料目錄下的紀錄檔資料夾建立
            huaxia_auth: HuaXia 登入服務；省略時以系統的安全儲存接真的 HuaXia
            catalog_factory: 在背景執行緒取得譜庫服務的函式（群組連到譜庫用）；省略時以登入服務接 Google
        """
        super().__init__(parent)
        self.project = self._blank_project()
        self.project.subscribe(self._on_project_changed)
        self._preferences = preferences
        self.file_service = FileService()
        self.import_service = ImportService(self.file_service)
        self.workspace_service = WorkspaceService(self.file_service)
        self._project_access = ProjectAccess(self.file_service, preferences, self.workspace_service)
        self._history = history or MoveHistory(self.file_service, self.workspace_service)
        self._splitter = SplitService(self.file_service, self.workspace_service)
        self._opener = opener or ExternalOpener()
        self._auth_service = auth_service or GoogleAuthService(
            self.file_service, opener=self._opener, preferences=preferences,
        )
        self._auth_service.remove_legacy_token()
        self._login_not_saved.connect(self._warn_login_not_saved)
        self._auth_service.subscribe_not_saved(self._login_not_saved.emit)
        # HuaXia 登入全程式一份：續期權杖只能用一次，同一時間只能有一個續期
        self._huaxia_auth = huaxia_auth or HuaXiaAuthService(opener=self._opener)
        self._huaxia_login_not_saved.connect(self._warn_huaxia_login_not_saved)
        self._huaxia_auth.subscribe_not_saved(self._huaxia_login_not_saved.emit)
        self._catalog_factory = catalog_factory or self._open_google_catalog
        self._compressor = compressor or CompressionService(self.file_service)
        self._compression_output = CompressionOutputService(self.file_service, self._compressor)
        self._log = log or ActivityLog(self.file_service)
        self._deposit_service = DepositService(self.file_service, self._log)
        self._clear_service = ClearLocalService(self.file_service, self._log, standard_folders=_standard_folders())
        self._download_service = DownloadService(self.file_service, self._log)
        self._revert_service = DepositRevertService(self.file_service, self._download_service, self._log)
        # 發譜按鈕依這台電腦記住的社團設定顯示（不連網、不讀安全儲存）；按下時重讀。
        # 社團設定服務全程式一份，傳給譜庫視窗與譜庫設定：哪裡重讀完成都通知這裡更新按鈕
        self._club_settings = ClubSettingsService(preferences, self._log)
        self._club_settings_read.connect(self._show_publish_button)
        self._club_settings.subscribe(self._club_settings_read.emit)
        self._publisher = PublishService(self._huaxia_auth)
        # 收回提醒依這台電腦記下的發譜紀錄摘要判斷（不連網、不讀安全儲存）；發譜寫完發譜紀錄後更新摘要
        self._recall_reminder = RecallReminder(preferences, self._log)
        self._publish_runner = PublishRunner(
            self._huaxia_auth, self.file_service, self._compressor, self._log, reminder=self._recall_reminder,
        )
        # 頂端收回提醒要寫出的曲名（切換介面語言重建畫面時照樣顯示；按了任一顆按鈕就清空，下次開泠靈再算）
        self._recall_titles: List[str] = self._recall_reminder.due()
        # 發譜有自己的背景工作（一次一件）：發出去的是快照，不鎖住其他操作，也不占用上面那一件
        self._publishing = TaskSlot(
            self, self._set_publish_status, self._show_publishing_state, self._show_publish_progress,
        )
        # 這次發譜的逐行紀錄與沒發成的群組（切換介面語言重建畫面時照樣顯示）
        self._publish_lines: List[str] = []
        self._publish_failures: List[str] = []
        # 上次發譜沒發成的群組 ID（「重新發譜沒發成的群組」只發這些）
        self._failed_group_ids: List[str] = []
        # 聲部名稱自動完成的候選全程式一份：譜庫視窗讀到的聲部名稱，編制表與分割視窗都用
        self._voice_candidates = VoiceCandidates()
        # 「關於」顯示的版號：打包版讀資源目錄的版號檔，開發版為 None
        build = current_build()
        self._version = app_version(build.frozen, build.resource_dir, self.file_service.read_text)
        # 背景工作（一次一件）：進度條與取消鈕在 _create_ui 建好後才接上
        self._tasks = TaskSlot(self, self._set_status, self._show_background_state, self._show_task_progress)
        # 進行中的背景工作是否連專案編輯也鎖住（存入譜庫、清除本機檔案、下載回原位、撤銷存入：結束時要把指紋寫回專案並存檔）
        self._task_locks_project = False
        self._project_path: Optional[str] = None
        self._suggested_name: str = ""
        # 紀錄寫不進去而留下進行中紀錄的那批搬移，其路徑變動已套用到的專案（之後保留結果時不再套用一次）
        self._pending_applied_to: Optional[Project] = None
        self._create_menu()
        self._create_ui()
        self._show_background_state()
        self._show_publishing_state()
        self._update_title()

    # --- 選單 ---

    def _create_menu(self):
        """建立選單列：檔案、編輯、匯入、譜庫、工具、檢視與說明"""
        mb = self.menuBar()
        file_menu = mb.addMenu(t("menu.file"))
        self._add_action(file_menu, t("menu.file.new"), self._new_project, "Ctrl+N")
        self._add_action(file_menu, t("menu.file.open"), self._open_project, "Ctrl+O")
        self._recent_menu = file_menu.addMenu(t("menu.file.recent"))
        self._recent_menu.setMinimumWidth(250)
        self._recent_menu.setToolTipsVisible(True)
        self._refresh_recent_menu()
        file_menu.addSeparator()
        # 唯讀的專案（較新版本建立的專案檔）停用的動作
        self._save_actions = [
            self._add_action(file_menu, t("menu.file.save"), self._save_project, "Ctrl+S"),
            self._add_action(file_menu, t("menu.file.save_as"), self._save_project_as),
        ]
        # 背景工作進行中停用的動作：會動到檔案的操作，以及另開一件背景工作
        self._file_actions: List[QAction] = []
        edit_menu = mb.addMenu(t("menu.edit"))
        undo = self._add_action(edit_menu, t("menu.edit.undo"), self._undo_last, "Ctrl+Z")
        redo = self._add_action(edit_menu, t("menu.edit.redo"), self._redo_last, "Ctrl+Y")
        import_menu = mb.addMenu(t("menu.import"))
        import_files = self._add_action(import_menu, t("menu.import.files"), self._import_files)
        import_folder = self._add_action(import_menu, t("menu.import.folder"), self._import_folder)
        catalog_menu = mb.addMenu(t("menu.catalog"))
        self._add_action(catalog_menu, t("menu.catalog.open"), self._open_catalog)
        self._add_action(catalog_menu, t("menu.catalog.settings"), self._open_catalog_settings)
        deposit = self._add_action(catalog_menu, t("menu.catalog.deposit"), self._deposit_to_catalog)
        clear = self._add_action(catalog_menu, t("menu.catalog.clear"), self._clear_local_copies)
        tools_menu = mb.addMenu(t("menu.tools"))
        split = self._add_action(tools_menu, t("menu.tools.split_pdf"), self._open_split_pdf)
        # 唯讀的專案停用的動作：會改專案，或搬動、產生專案引用的檔案（之後存不了檔，專案檔就對不上）
        self._edit_actions = [undo, redo, import_files, import_folder, split, deposit, clear]
        self._file_actions.extend([undo, redo, split, deposit, clear])
        tools_menu.addSeparator()
        self._file_actions.append(self._add_action(tools_menu, t("menu.tools.rotate_pdf"), self._open_rotate_pdf))
        self._file_actions.append(self._add_action(tools_menu, t("menu.tools.compress_pdf"), self._compress_files))
        self._file_actions.append(self._add_action(tools_menu, t("menu.tools.compress_folder"), self._compress_folder))
        tools_menu.addSeparator()
        self._add_action(tools_menu, t("menu.tools.open_workspace"), self._open_workspace_folder)
        self._add_action(tools_menu, t("menu.tools.cleanup_workspace"), self._open_workspace_cleanup)
        view_menu = mb.addMenu(t("menu.view"))
        lang_menu = view_menu.addMenu(t("menu.view.language"))
        for code, label_key in [(LOCALE_ZH_TW, "menu.view.language.zh_TW"), (LOCALE_EN, "menu.view.language.en")]:
            action = QAction(t(label_key), self)
            action.triggered.connect(lambda checked=False, c=code: self._set_language(c))
            lang_menu.addAction(action)
        help_menu = mb.addMenu(t("menu.help"))
        self._add_action(help_menu, t("menu.help.open_log_folder"), self._open_log_folder)
        self._add_action(help_menu, t("menu.help.about"), self._show_about)

    def _add_action(self, menu, text, callback, shortcut=None) -> QAction:
        """在 menu 加一個選單項目，觸發時呼叫 callback；有 shortcut 時設快捷鍵"""
        action = QAction(text, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        menu.addAction(action)
        return action

    # --- UI 建構 ---

    def _create_ui(self):
        """排出左邊的編制表、右邊的群組分頁與底部的命名格式、狀態列"""
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(4)
        # 存檔或開啟專案時寫不進譜庫的提示列；按「重試」等處理按鈕時再試一次
        self._catalog_error_bar = CatalogErrorBar(log=self._log)
        self._catalog_error_bar.connect_remedies(self._sign_in_google, self._open_catalog_settings)
        main_layout.addWidget(self._catalog_error_bar)
        self._recall_reminder_bar = RecallReminderBar()
        self._recall_reminder_bar.view_requested.connect(self._view_recall)
        self._recall_reminder_bar.dismissed.connect(self._dismiss_recall_reminder)
        self._recall_reminder_bar.show_titles(self._recall_titles)
        main_layout.addWidget(self._recall_reminder_bar)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        main_layout.addWidget(splitter, stretch=1)
        self._instrumentation_table = InstrumentationTable(
            project=self.project, file_service=self.file_service, voice_candidates=self._voice_candidates,
        )
        self._instrumentation_table.setMinimumWidth(300)
        self._instrumentation_table.instruments_changed.connect(self._on_instruments_changed)
        self._instrumentation_table.link_requested.connect(self._link_group_to_catalog)
        splitter.addWidget(self._instrumentation_table)
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
        # 唯讀的專案停用的元件（另有分頁內容、編制表與預覽並重新命名，見 _show_enabled_state）
        self._edit_widgets: List[QWidget] = [add_group_btn, link_btn]
        btn_bar.addStretch()
        right_layout.addLayout(btn_bar)
        self._tab_widget = QTabWidget()
        self._tab_widget.currentChanged.connect(self._on_tab_changed)
        right_layout.addWidget(self._tab_widget)
        splitter.addWidget(right_panel)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([560, 720])
        self._build_bottom_panel(main_layout)
        self._edit_widgets += [self._orchestra_combo, self._master_template_entry, self._vars_btn, self._publish_btn]
        self._rebuild_tabs()

    def _build_bottom_panel(self, parent_layout):
        """底部：樂團類型、總譜命名格式、發譜的逐行紀錄、狀態列、發譜與背景工作的進度與停止鈕、預覽並重新命名"""
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(8, 4, 8, 8)
        bl.setSpacing(4)
        template_row = QHBoxLayout()
        template_row.addWidget(QLabel(t("panel.orchestra_type")))
        self._orchestra_combo = QComboBox()
        for orchestra_type in OrchestraType:
            self._orchestra_combo.addItem(t(f"orchestra.{orchestra_type.value}"), orchestra_type.value)
        self._show_orchestra_type()
        self._orchestra_combo.currentIndexChanged.connect(self._on_orchestra_type_chosen)
        template_row.addWidget(self._orchestra_combo)
        template_row.addWidget(QLabel(t("panel.master_template")))
        self._master_template_entry = QLineEdit(self.project.master_template)
        self._master_template_entry.textEdited.connect(lambda text: self.project.set_master_template(text))
        self._master_template_entry.setToolTip(t("panel.template_segment_tip"))
        template_row.addWidget(self._master_template_entry, stretch=1)
        self._vars_btn = QPushButton(t("panel.insert_variable"))
        self._vars_btn.clicked.connect(self._show_variable_menu)
        template_row.addWidget(self._vars_btn)
        bl.addLayout(template_row)
        from ui.publish_run import PublishRunPanel
        self._publish_panel = PublishRunPanel()
        self._publish_panel.publish_failed_again.connect(self._publish_failed_again)
        self._publish_panel.show_record(self._publish_lines, self._publish_failures)
        self._publish_panel.setVisible(bool(self._publish_lines))
        bl.addWidget(self._publish_panel)
        action_row = QHBoxLayout()
        self._status_label = QLabel(t("status.ready"))
        action_row.addWidget(self._status_label, stretch=1)
        self._publish_status = QLabel()
        action_row.addWidget(self._publish_status)
        self._publish_progress = QProgressBar()
        self._publish_progress.setFixedWidth(120)
        self._publish_progress.setTextVisible(False)
        action_row.addWidget(self._publish_progress)
        self._publish_cancel_btn = QPushButton(t("publish.run.cancel"))
        self._publish_cancel_btn.clicked.connect(self._publishing.cancel)
        action_row.addWidget(self._publish_cancel_btn)
        self._task_progress = QProgressBar()
        self._task_progress.setFixedWidth(160)
        self._task_progress.setTextVisible(False)
        action_row.addWidget(self._task_progress)
        self._task_cancel_btn = QPushButton()
        self._task_cancel_btn.clicked.connect(self._cancel_background)
        action_row.addWidget(self._task_cancel_btn)
        self._publish_btn = QPushButton(t("publish.button"))
        self._publish_btn.clicked.connect(self._publish)
        action_row.addWidget(self._publish_btn)
        self._show_publish_button()
        self._rename_btn = QPushButton(t("panel.preview_rename"))
        self._rename_btn.setStyleSheet("font-size: 14px; font-weight: bold; padding: 8px 24px;")
        self._rename_btn.clicked.connect(self._preview_and_rename)
        action_row.addWidget(self._rename_btn)
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
        self._show_enabled_state()

    def _create_ungrouped_tab(self):
        from ui.group_panel import UngroupedTab
        tab = UngroupedTab(self.project)
        tab.groups_changed.connect(self._rebuild_tabs)
        return tab

    def _add_group_tab(self, group: Group):
        from ui.group_panel import GroupTab
        tab = GroupTab(group, self.project, self.file_service, self.import_service, self._deposit_service)
        tab.groups_changed.connect(self._rebuild_tabs)
        tab.download_requested.connect(lambda group_id: self.download_back([group_id]))
        self._tab_widget.addTab(tab, group.display_name())

    def _refresh_tab_labels(self):
        """群組分頁的標籤改成群組現在的名稱；不重建分頁，正在輸入的欄位不會失去焦點"""
        for index in range(self._tab_widget.count()):
            group = getattr(self._tab_widget.widget(index), "group", None)
            if group is not None:
                self._tab_widget.setTabText(index, group.display_name())

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
        """切換分頁：編制表改顯示這個群組，群組分頁重新比對未存入的檔"""
        self._instrumentation_table.show_group(self._current_group())
        self._show_undeposited()

    def _show_undeposited(self):
        """目前的群組分頁重新比對未存入的檔（本機的檔可能在別的程式裡改過）"""
        tab = self._tab_widget.currentWidget()
        if hasattr(tab, "show_undeposited"):
            tab.show_undeposited()

    def changeEvent(self, event):
        """切回這個視窗時：目前的群組分頁重新比對未存入的檔（本機的檔可能在別的程式裡改過）"""
        super().changeEvent(event)
        if event.type() == QEvent.ActivationChange and self.isActiveWindow():
            self._show_undeposited()

    # --- 編制表回呼 ---

    def _on_orchestra_type_chosen(self, _index: int):
        """改樂團類型：寫入專案，編制表的聲部組改列新的那一套"""
        self.project.set_orchestra_type(OrchestraType(self._orchestra_combo.currentData()))
        self._instrumentation_table.refresh()

    def _on_instruments_changed(self):
        """編制表已寫入目前群組：重新顯示各分頁（分譜與聲部的配對，以及移到未分組的分譜）"""
        for index in range(self._tab_widget.count()):
            self._tab_widget.widget(index).refresh()

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
        self.project.subscribe(self._on_project_changed)
        self._project_path = path
        self._suggested_name = ""
        self._catalog_error_bar.clear()
        self._sync_ui_from_project()

    def _new_project(self):
        if not self._confirm_unsaved():
            return
        self._set_project(self._blank_project(), None)

    def _open_project(self):
        """詢問要不要儲存目前的專案，再選一個專案檔開啟"""
        if not self._confirm_unsaved():
            return
        path, _ = QFileDialog.getOpenFileName(
            self, t("filedialog.open_project"), "",
            f"{t('filedialog.project_files')} (*{PROJECT_EXTENSION})",
        )
        if not path:
            return
        self._do_open_project(path)

    def _do_open_project(self, path: str, moved: Iterable[UndoMapping] = ()):
        """開啟專案檔；讀不到才顯示「無法開啟專案」，最近清單或 meta 寫不進去只在狀態列提示

        較新版本建立的專案檔唯讀開啟，並寫明原因。

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
        if result.project.read_only:
            QMessageBox.information(self, t("dialog.info"), t("project.newer_format"))
        if result.missing_files:
            QMessageBox.warning(
                self, t("dialog.warning"),
                t("missing.on_load", count=len(result.missing_files), files="\n".join(result.missing_files)),
            )
        self._sync_catalog_on_open()

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
        """存到目前的專案檔，尚未存過時改走另存新檔；回傳是否存成（唯讀的專案不存）"""
        if self.project.read_only:
            return False
        if not self._project_path:
            return self._save_project_as()
        return self._do_save(self._project_path)

    def _save_project_as(self) -> bool:
        """詢問位置後存檔；在對話框按取消也算沒存成，回傳是否存成（唯讀的專案不存）"""
        if self.project.read_only:
            return False
        path = ask_save_path(
            self, t("filedialog.save_project"), "", t("filedialog.project_files"), PROJECT_EXTENSION,
            self.file_service,
        )
        if not path:
            return False
        return self._do_save(path)

    def _do_save(self, path: str) -> bool:
        """存檔：連到譜庫的群組有改動時先寫回譜庫，再寫專案檔

        寫不進譜庫時照樣存專案檔，這些群組標未同步（下次存檔或開啟專案時重試）；在衝突畫面取消時不存。
        專案檔寫不成才顯示「儲存失敗」，最近清單或 meta 寫不進去只在狀態列提示。

        Args:
            path: 專案檔路徑

        Returns:
            專案檔是否寫成
        """
        writes = place_writes(self.project)
        if writes:
            synced = self._write_back_to_catalog(writes, self._save_project)
            if synced is _WriteBack.CANCELLED:
                return False
            if synced is _WriteBack.FAILED:
                self.project.mark_catalog_unsynced(write.group_id for write in writes)
            self._instrumentation_table.refresh()
        return self._write_project_file(path)

    def _write_project_file(self, path: str) -> bool:
        """只寫專案檔（不寫回譜庫）；寫不成時顯示「儲存失敗」，回傳是否寫成"""
        result = self._project_access.save(self.project, path)
        if result.error is not None:
            QMessageBox.critical(self, t("dialog.error"), t("dialog.error.save_failed", error=result.error))
            return False
        self._project_path = path
        self._update_title()
        self._refresh_recent_menu()
        self._show_access_status(t("status.saved", path=path), result)
        return True

    def _sync_catalog_on_open(self):
        """開啟專案後與譜庫同步：先重試未同步的群組，沒有寫不進去才從譜庫更新快照

        先重試再更新，未同步的改動才會以原本的快照比對，不會被當成譜庫的新值蓋掉。唯讀的專案兩者都不做。
        """
        if self._retry_unsynced() is not _WriteBack.FAILED:
            self._refresh_from_catalog()

    def _retry_unsynced(self) -> Optional[_WriteBack]:
        """開啟專案後重試未同步的群組：寫回成功時再存一次專案檔，清掉檔裡的未同步標記

        開啟時就有未存檔的改動（例如套用了中斷還原的路徑）時不自動存，交給使用者存檔。唯讀的專案不重試。
        寫不進去時頂端顯示提示列，按「重試」再試一次。

        Returns:
            寫回的結果；唯讀、沒有路徑或沒有未同步的群組時為 None
        """
        if self.project.read_only or not self._project_path:
            return None
        unsynced = {group.id for group in self.project.unsynced_groups()}
        writes = [write for write in place_writes(self.project) if write.group_id in unsynced]
        if not writes:
            return None
        modified = self.project.is_modified()
        written = self._write_back_to_catalog(writes, self._retry_unsynced)
        if written is not _WriteBack.WRITTEN:
            return written
        self._sync_ui_from_project()
        if not modified:
            self._write_project_file(self._project_path)
        return written

    def _refresh_from_catalog(self):
        """開啟專案時從譜庫更新連到譜庫的群組的快照：人數與時長直接套用，聲部清單變了時由編制表頂端的提示列決定

        各處一起讀（背景執行、期間顯示等待視窗）。開啟時沒有未存檔的改動、更新後有改動時再存一次專案檔。
        讀不到時照快照開啟，頂端顯示提示列，按處理按鈕再讀一次；連到的那處已不在譜庫的群組照快照，其他群組照常更新，
        提示列寫出是哪些群組。唯讀的專案不更新。
        """
        groups = [group for group in self.project.groups if group.catalog_link is not None]
        if self.project.read_only or not self._project_path or not groups:
            return
        orchestra_type = self.project.orchestra_type
        targets = [(group.catalog_link.edition_id, group.catalog_link.movement_id) for group in groups]

        def work() -> list:
            """在背景接譜庫並讀出各處"""
            return self._catalog_factory().read_places(targets, orchestra_type)
        read, places = wait_for_catalog(
            self, self._catalog_error_bar, t("catalog.refresh.title"), t("catalog.refresh.waiting"), work,
            t("catalog.refresh.heading"), self._refresh_from_catalog,
        )
        if not read:
            return
        self._catalog_error_bar.clear()
        modified = self.project.is_modified()
        gone = []
        for group, place in zip(groups, places):
            if place is None:
                gone.append(group.name)
            else:
                self.project.refresh_catalog(group, place)
        self._instrumentation_table.refresh()
        if not modified and self.project.is_modified():
            self._write_project_file(self._project_path)
        if gone:
            names = "、".join(gone)
            failure = CatalogFailure.from_error(
                CatalogError(CatalogErrorKind.GONE, f"places of {names} are no longer in the catalog"),
                t("catalog.refresh.title"),
            )
            self._catalog_error_bar.show_failure(
                t("catalog.refresh.gone", groups=names), failure, self._refresh_from_catalog,
            )

    def _confirm_unsynced(self) -> bool:
        """專案還有未同步的群組時提醒一次（關閉專案或程式前），回傳可不可以繼續關閉"""
        groups = self.project.unsynced_groups()
        if not groups:
            return True
        answer = QMessageBox.question(
            self, t("catalog.unsynced.title"), t("catalog.unsynced.close", groups="、".join(g.name for g in groups)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _confirm_undeposited(self) -> bool:
        """專案還有未存入的檔時提醒一次（關閉專案或程式前），回傳可不可以繼續關閉；只比對本機檔案，不連網"""
        groups = []
        for ref in self._deposit_service.undeposited(self.project):
            if ref.group is not None and ref.group not in groups:
                groups.append(ref.group)
        if not groups:
            return True
        answer = QMessageBox.question(
            self, t("deposit.undeposited.title"),
            t("deposit.undeposited.close", groups="、".join(group.name for group in groups)),
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _write_back_to_catalog(self, writes: List[PlaceWrite], retry: Callable[[], Any]) -> _WriteBack:
        """把連到譜庫的群組的改動寫回譜庫（ADR 0002）

        在背景寫入，期間顯示等待視窗。有衝突時開衝突畫面選邊後再寫一次（寫入前又重讀，選邊之後又被改的格子再問）；
        有群組要先確認（PlaceWrite.confirm）時，第一次只比對、開衝突畫面確認後才寫；
        寫完把結果交回專案（選「用譜庫的」的值與新的快照，清掉未同步），有無法寫回的聲部時說明。
        寫不進去時在頂端顯示提示列，按「重試」呼叫 retry。

        Args:
            writes: 要寫回的改動（place_writes）
            retry: 提示列按「重試」時呼叫

        Returns:
            已寫入、寫不進去、或在衝突畫面取消
        """
        operator = self._auth_service.account_email or ""
        orchestra_type = self.project.orchestra_type
        decisions: dict = {}
        # 選了「用專案的（存檔時寫回）」的群組：第一次只比對，在衝突畫面確認全部改動後才寫
        review_only = any(write.confirm for write in writes)
        while True:
            def work(chosen=dict(decisions), review=review_only) -> SyncOutcome:
                """在背景接譜庫並寫回（review 時只比對）"""
                return self._catalog_factory().sync_instrumentation(
                    writes, orchestra_type, operator, WriteSource.SAVE, chosen, review_only=review,
                )
            sent, outcome = wait_for_catalog(
                self, self._catalog_error_bar, t("catalog.sync.title"), t("catalog.sync.waiting"), work,
                t("catalog.unsynced.heading"), retry,
            )
            if not sent:
                return _WriteBack.FAILED
            if outcome.written:
                break
            from ui.catalog_conflict_dialog import CatalogConflictDialog
            dialog = CatalogConflictDialog(outcome, self)
            if dialog.exec() != CatalogConflictDialog.Accepted:
                return _WriteBack.CANCELLED
            review_only = False
            for group_id, chosen in dialog.decisions().items():
                decisions[group_id] = {**decisions.get(group_id, {}), **chosen}
        self._catalog_error_bar.clear()
        self.project.mark_catalog_synced(outcome)
        if outcome.blocked:
            voices = "、".join(review.item.voice for _, review in outcome.blocked)
            QMessageBox.warning(self, t("catalog.sync.title"), t("catalog.sync.blocked_saved", voices=voices))
        return _WriteBack.WRITTEN

    def _show_orchestra_type(self):
        """樂團類型選單改為顯示目前專案的值，不寫回專案"""
        self._orchestra_combo.blockSignals(True)
        self._orchestra_combo.setCurrentIndex(self._orchestra_combo.findData(self.project.orchestra_type.value))
        self._orchestra_combo.blockSignals(False)

    def _sync_ui_from_project(self):
        """畫面改為顯示目前的專案"""
        self._instrumentation_table.set_project(self.project)
        self._show_orchestra_type()
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
            assign_default_sections(self.project, selected_ids)
            return self._history.check_rename(self.project, selected_ids)
        groups = selected_ids if selected_ids is not None else [group.id for group in self.project.groups]
        dialog = PreviewDialog(
            self.project, check, self._execute_rename, self, download_back=lambda: self.download_back(groups),
        )
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
        """顯示搬移歷程動作的失敗，以及紀錄、工作區 meta 寫不進去的警告；參數與回傳值同 _apply_move_result

        meta 寫不回去只提示，不算失敗：檔案復原本身已完成。
        """
        if result.error is not None:
            message = t(failure_key, error=result.error) if failure_key else str(result.error)
            QMessageBox.critical(self, t("dialog.error"), message)
        if result.record_error is not None:
            QMessageBox.warning(self, t("dialog.warning"), t("history.record_not_saved", error=result.record_error))
        if result.meta_errors:
            folders = "\n".join(result.meta_errors)
            QMessageBox.warning(self, t("dialog.warning"), t("history.meta_not_restored", folders=folders))
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

        Args:
            pending: 已整批搬完、只差紀錄沒確認寫入的批次

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
        if self.project.deposited_files(change.renamed for change in result.changes):
            QMessageBox.information(self, t("deposit.undo_archived.title"), t("deposit.undo_archived"))

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
        """開啟分割對話框，預選目前的群組"""
        from ui.split_dialog import SplitPdfDialog
        dialog = SplitPdfDialog(
            self.project, self._on_split_complete, self._current_group(), self,
            splitter=self._splitter, project_path=self._project_path or "",
            voice_candidates=self._voice_candidates,
        )
        dialog.exec()

    # --- 工作區 ---

    def _open_workspace_folder(self):
        """以檔案管理員開啟工作區資料夾；資料夾還不存在或開不了時提示"""
        folder = self.workspace_service.workspace_dir
        if not self.file_service.directory_exists(folder):
            QMessageBox.information(self, t("dialog.info"), t("workspace.open_failed"))
            return
        if not self._opener.open(folder):
            QMessageBox.warning(self, t("dialog.error"), t("dialog.error.external_open_failed", target=folder))

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
        """開啟旋轉對話框，預選目前的群組"""
        from ui.rotate_dialog import RotatePdfDialog
        dialog = RotatePdfDialog(self.project, self._history, self._current_group(), self, self.file_service)
        dialog.exec()

    # --- 壓縮 PDF ---

    def _compress_files(self):
        """多選同一資料夾裡的 PDF（或另存單一檔）並在背景壓縮"""
        from ui import compress_pdf
        self._compress(compress_pdf.ask_files(self, self._compression_output, self.file_service))

    def _compress_folder(self):
        """選一個資料夾，連同子資料夾裡的 PDF 在背景壓縮"""
        from ui import compress_pdf
        self._compress(compress_pdf.ask_folder(self, self._compression_output))

    def _compress(self, batch: Optional[CompressionBatch]):
        """在背景一次一份壓完整批，再交給 _deliver_compressed；取消時已壓好的全部丟掉、什麼都不輸出

        Args:
            batch: 這一批；使用者在選檔或輸出位置時取消了就是 None，什麼都不做
        """
        if batch is None:
            return
        from ui import compress_pdf
        output = self._compression_output

        def work(should_cancel, report) -> Optional[Tuple[CompressionResult, ...]]:
            """在背景執行緒一次一份壓完整批"""
            return output.compress(batch, should_cancel, report)

        def finished(results: Optional[Tuple[CompressionResult, ...]], cancelled: bool):
            """壓完了：取消時丟掉已壓好的，否則交給 _deliver_compressed"""
            if cancelled or results is None:
                output.discard(results or ())
                self._set_status(t("compress.cancelled"))
                return
            self._deliver_compressed(batch, results)

        self._run_in_background(work, finished, _COMPRESS_TEXTS, describe=compress_pdf.describe_progress)

    def _deliver_compressed(self, batch: CompressionBatch, results: Sequence[CompressionResult]):
        """整批壓完、檢查完後先開比較畫面逐份確認，再把留下的一次交到輸出位置，最後丟掉暫存的壓縮檔

        比較畫面取消、或一份都沒留下時什麼都不交出；選「不要這份」的不交出。

        Args:
            batch: 這一批
            results: 各份的壓縮結果，壓縮檔還在暫存資料夾
        """
        from ui import compress_pdf
        from ui.compression_compare import CompressionCompareDialog
        output = self._compression_output
        try:
            entries = [(job.name, result) for job, result in zip(batch.jobs, results)]
            dialog = CompressionCompareDialog(entries, self._opener, self)
            confirmed = dialog.exec() == CompressionCompareDialog.Accepted
            kept = dialog.kept()
            dialog.deleteLater()
            if not confirmed:
                self._set_status(t("compress.cancelled"))
                return
            if not kept:
                self._set_status(t("compress.status.nothing"))
                return
            try:
                errors = output.deliver(batch, kept)
            except ValueError as e:
                self._set_status(t("status.ready"))
                QMessageBox.critical(self, t("dialog.error"), t("compress.deliver_failed", error=e))
                return
            compress_pdf.report(self, batch, kept, errors, self._compressor.size_limit)
            self._set_status(t("compress.status.done", name=os.path.basename(batch.output)))
        finally:
            output.discard(results)

    # --- 背景工作 ---

    @property
    def _task(self) -> Optional[BackgroundTask]:
        """進行中的背景工作；沒有時為 None"""
        return self._tasks.task

    def _run_in_background(
        self, work: Work, finished: Callable[[Any, bool], None], texts: TaskTexts, locks_project: bool = False,
        describe: Optional[Callable[[Any], tuple]] = None,
    ):
        """在背景執行一件可取消的工作（ui/background_task.py 的 TaskSlot）；期間停用會動到檔案的操作，狀態列顯示進度與取消鈕

        一次只執行一件：進行中時另開背景工作的動作也停用。

        Args:
            work: 在背景執行緒呼叫的工作（見 BackgroundTask）
            finished: 工作交回結果時在主執行緒呼叫，參數為結果與使用者是否要求過取消
            texts: 這種工作的字串鍵
            locks_project: 期間是否連專案編輯與存檔也停用（工作讀寫的是專案裡的檔，結束時還要改專案並存檔）
            describe: 把工作回報的進度轉成（狀態列文字，已完成數，總數）；省略時照 texts 的逐份進度
        """
        def done(result, cancelled: bool):
            """交回了：解除專案的鎖，再交給 finished"""
            self._task_locks_project = False
            self._show_background_state()
            finished(result, cancelled)

        self._task_locks_project = locks_project
        self._tasks.run(work, texts, done, describe)

    def _show_task_progress(self, done: int, total: int):
        """背景工作的進度條"""
        self._task_progress.setRange(0, max(total, 1))
        self._task_progress.setValue(done)

    def _cancel_background(self):
        """要求取消進行中的背景工作；工作在下一個停頓點停下後照常交回結果"""
        self._tasks.cancel()

    def _show_background_state(self):
        """依有沒有背景工作，停用或恢復會動到檔案的操作，顯示或隱藏進度與取消鈕"""
        busy = self._tasks.busy
        self._show_enabled_state()
        self._task_progress.setVisible(busy)
        self._task_cancel_btn.setText(t((self._tasks.texts or _COMPRESS_TEXTS).cancel))
        self._task_cancel_btn.setVisible(busy)
        self._task_cancel_btn.setEnabled(busy and not self._tasks.cancel_requested)
        if busy and self._tasks.status and not self._tasks.cancel_requested:
            self._set_status(self._tasks.status)

    def _show_enabled_state(self):
        """依專案唯不唯讀與有沒有背景工作，停用或恢復各個操作（唯讀與背景工作的判斷都只在這裡）

        唯讀的專案（較新版本建立的專案檔）存不了檔，所以停用存檔、另存，以及所有會改專案或搬動、
        產生它引用的檔案的操作：關閉時才不會有存不了的修改。分頁仍可切換，只停用內容。
        背景工作進行中停用會動到檔案的操作；鎖住專案的背景工作（存入譜庫、清除本機檔案、下載回原位、撤銷存入）進行中，比照唯讀停用專案編輯與存檔。
        發譜進行中只停用「發譜」（發出去的是快照，其他操作照常）。
        """
        busy = self._task is not None
        editable = not self.project.read_only and not (busy and self._task_locks_project)
        for action in self._save_actions:
            action.setEnabled(editable)
        for action in set(self._file_actions + self._edit_actions):
            blocked = (busy and action in self._file_actions) or (not editable and action in self._edit_actions)
            action.setEnabled(not blocked)
        pages = [self._tab_widget.widget(i) for i in range(self._tab_widget.count())]
        for widget in self._edit_widgets + pages:
            widget.setEnabled(editable)
        self._rename_btn.setEnabled(editable and not busy)
        self._publish_btn.setEnabled(editable and not self._publishing.busy)
        self._instrumentation_table.set_editable(editable)

    def _confirm_cancel_background(self) -> bool:
        """關閉程式時有背景工作就詢問要不要取消（見 TaskSlot.confirm_close）

        Returns:
            可以關閉時為 True（沒有背景工作，或選了取消）
        """
        return self._tasks.confirm_close()

    def _set_language(self, lang_code: str):
        """切換介面語言並記進偏好，轉換命名格式的語言後重建選單與畫面"""
        if lang_code == get_locale():
            return
        set_locale(lang_code)
        self._preferences.set("language", lang_code)
        self._preferences.save()
        self.project.convert_template_language(lang_code)
        self.menuBar().clear()
        self._create_menu()
        self._create_ui()
        self._show_background_state()
        self._show_publishing_state()
        self._sync_ui_from_project()
        self._update_title()

    # --- 說明 ---

    def _open_log_folder(self):
        """以檔案管理員開啟紀錄檔資料夾；還沒有紀錄檔或開不了時提示"""
        folder = self._log.folder
        if not self.file_service.directory_exists(folder):
            QMessageBox.information(self, t("dialog.info"), t("log.folder_missing"))
            return
        if not self._opener.open(folder):
            QMessageBox.warning(self, t("dialog.error"), t("dialog.error.external_open_failed", target=folder))

    def _show_about(self):
        """開啟「關於泠靈小工具」：版號、GitHub 網址與授權"""
        from ui.about_dialog import AboutDialog
        AboutDialog(self._version, self._opener, self).exec()

    # --- 譜庫 ---

    def _warn_login_not_saved(self):
        """登入或換發的續期權杖沒寫進安全儲存：提示下次啟動需要重新登入"""
        QMessageBox.warning(self, t("catalog.auth.title"), t("catalog.auth.reason.not_saved"))

    def _warn_huaxia_login_not_saved(self):
        """HuaXia 登入或續期換到的續期權杖沒寫進安全儲存：提示下次啟動需要重新登入"""
        QMessageBox.warning(self, t("huaxia.auth.title"), t("catalog.auth.reason.not_saved"))

    def _catalog_ready(self) -> bool:
        """譜庫已設定（登入 Google、有譜庫總表）；還沒有時先引導設定，回傳設定完成與否"""
        spreadsheet_id = self._preferences.get(CATALOG_SPREADSHEET_ID_KEY)
        if self._auth_service.get_credentials() is None or not spreadsheet_id:
            return self._open_catalog_settings()
        return True

    def _open_catalog(self) -> bool:
        """開啟譜庫瀏覽器，尚未設定時先引導設定；回傳開了沒有"""
        if not self._catalog_ready():
            return False
        from ui.catalog_window import CatalogWindow
        self._catalog_window = CatalogWindow(
            self._auth_service, self._preferences, catalog_factory=self._catalog_factory, log=self._log,
            huaxia_auth=self._huaxia_auth, voice_candidates=self._voice_candidates, reverter=self,
            downloads=self._download_service, orchestra_type=self.project.orchestra_type,
            club_settings=self._club_settings,
        )
        self._catalog_window.show()
        return True

    def _view_recall(self):
        """收回提醒按了「查看並收回」：開譜庫視窗（尚未設定時先引導設定），再開收回畫面（沒登入 HuaXia 時先登入）"""
        self._recall_titles = []
        if self._open_catalog():
            self._catalog_window.recall()

    def _dismiss_recall_reminder(self):
        """收回提醒按了「之後再說」：這次不再顯示，下次開泠靈再提醒"""
        self._recall_titles = []

    def _open_google_catalog(self) -> "CatalogService":
        """以登入服務的憑證接 Google 的譜庫服務（背景執行緒呼叫，拿不到時丟出 CatalogError）"""
        from services.google_catalog import open_google_catalog
        return open_google_catalog(self._auth_service, self._preferences.get(CATALOG_SPREADSHEET_ID_KEY) or "")

    def _link_group_to_catalog(self):
        """編制表按了「連到譜庫…」：樂器表有重名的列時寫出是哪幾列；譜庫還沒設定時先引導設定，再開連線對話框"""
        group = self._current_group()
        if group is None:
            return
        from ui.catalog_link_dialog import CatalogLinkDialog, duplicate_rows_text
        rows = duplicate_rows(group.instruments)
        if rows:
            QMessageBox.warning(self, t("catalog.link.title"), duplicate_rows_text(group, rows))
            return
        if not self._catalog_ready():
            return
        dialog = CatalogLinkDialog(
            self._catalog_factory, self.project, group, self._preferences.get(CATALOG_ROOT_FOLDER_ID_KEY) or "",
            self, log=self._log, open_settings=self._open_catalog_settings, voice_candidates=self._voice_candidates,
        )
        dialog.linked.connect(self._instrumentation_table.show_linked)
        dialog.finished.connect(dialog.deleteLater)
        dialog.open()

    def _deposit_to_catalog(self):
        """「存入譜庫…」：在等待視窗裡讀出存入計畫，開預覽，按「存入譜庫」後在背景存入

        讀不到譜庫時頂端顯示提示列，按「重試」再開一次。專案沒存過檔或有未存檔的改動時，預覽寫明原因、不能存入。
        """
        if self._task is not None or not self._catalog_ready():
            return
        project = self.project
        service = self._deposit_service
        stopped = []

        def read() -> tuple:
            """在背景接譜庫並算出計畫"""
            catalog = self._catalog_factory()
            return catalog, service.preview(project, catalog)
        done, result = wait_for_catalog(
            self, self._catalog_error_bar, t("deposit.title"), t("deposit.reading"), read,
            t("catalog.error.cannot_read"), self._deposit_to_catalog, lambda: stopped.append(True),
        )
        if not done or stopped:
            return
        catalog, plan = result
        self._catalog_error_bar.clear()
        from ui.deposit_dialog import DepositDialog
        unsaved = project.is_modified() or not self._project_path
        dialog = DepositDialog(plan, unsaved, self)
        dialog.link_requested.connect(self._link_group_by_id)
        accepted = dialog.exec() == DepositDialog.Accepted
        dialog.deleteLater()
        if not accepted:
            return
        operator = self._auth_service.account_email or ""
        clean = not project.is_modified()

        def work(should_cancel, report) -> DepositOutcome:
            """在背景照計畫存入"""
            return service.deposit(plan, catalog, operator, should_cancel, report)

        self._run_in_background(
            work, lambda outcome, _cancelled: self._deposit_finished(outcome, clean), _DEPOSIT_TEXTS,
            locks_project=True,
        )

    def _deposit_finished(self, outcome: DepositOutcome, clean: bool):
        """存入結束（做完、取消或中途失敗）：存進去的指紋交回專案並只存專案檔（_save_after_task），
        中途失敗時頂端提示列寫出已完成幾份"""
        if outcome.records:
            self.project.record_deposit(outcome.records)
            self._instrumentation_table.refresh()
            self._save_after_task(clean)
        if outcome.error is not None:
            failure = CatalogFailure.from_error(outcome.error, t("deposit.title"))
            self._catalog_error_bar.show_failure(
                t("deposit.stopped", count=outcome.completed), failure, self._deposit_to_catalog,
            )
            self._set_status(t("status.ready"))
        elif outcome.cancelled:
            self._set_status(t("deposit.cancelled", count=outcome.completed))
        else:
            self._set_status(t("deposit.done", count=outcome.completed))

    def _clear_local_copies(self):
        """「清除本機檔案…」：在等待視窗裡核對出計畫，開預覽，確認框按「永久刪除」後在背景清除

        讀不到譜庫（例如斷網）時頂端顯示提示列、不開預覽，按「重試」再開一次。
        專案沒存過檔或有未存檔的改動時，預覽寫明原因、不能清除（清除後要把指紋存進專案檔）。
        """
        if self._task is not None or not self._catalog_ready():
            return
        project = self.project
        service = self._clear_service
        stopped = []

        def read() -> tuple:
            """在背景接譜庫並核對出計畫"""
            catalog = self._catalog_factory()
            return catalog, service.preview(project, catalog)
        done, result = wait_for_catalog(
            self, self._catalog_error_bar, t("clear.title"), t("clear.reading"), read,
            t("catalog.error.cannot_read"), self._clear_local_copies, lambda: stopped.append(True),
        )
        if not done or stopped:
            return
        catalog, plan = result
        self._catalog_error_bar.clear()
        from ui.clear_dialog import ClearDialog
        dialog = ClearDialog(plan, project.is_modified() or not self._project_path, self)
        accepted = dialog.exec() == ClearDialog.Accepted
        group_ids = dialog.chosen_ids()
        dialog.deleteLater()
        if not accepted or not self._confirm_clear(plan, group_ids):
            return

        clean = not project.is_modified()

        def work(should_cancel, report) -> ClearOutcome:
            """在背景重讀譜庫核對後永久刪除"""
            return service.clear(plan, group_ids, catalog, should_cancel, report)

        self._run_in_background(
            work, lambda outcome, _cancelled: self._clear_finished(outcome, clean), _CLEAR_TEXTS, locks_project=True,
        )

    def _save_after_task(self, clean: bool):
        """鎖住專案的背景工作把指紋交回專案之後只存專案檔（不經寫回譜庫）：存入、清除、下載回原位與撤銷存入共用

        開始前就有未存檔的改動時不替使用者存檔；沒有專案檔、唯讀或專案沒變時也不存。

        Args:
            clean: 開始前專案是否沒有未存檔的改動
        """
        if clean and self._project_path and not self.project.read_only and self.project.is_modified():
            self._write_project_file(self._project_path)

    def _confirm_clear(self, plan: ClearPlan, group_ids: List[str]) -> bool:
        """清除前的確認框：寫出哪些群組、幾份、多大、不進回收處、清空的資料夾一併刪除與怎麼找回"""
        items = plan.chosen(group_ids)
        names = [group.sources.group_name for group in plan.groups if group.group_id in group_ids and group.clearable]
        box = QMessageBox(QMessageBox.Warning, t("clear.confirm.title", count=len(items)), t(
            "clear.confirm.message", groups="、".join(names), count=len(items),
            size=size_text(sum(item.size for item in items)),
        ), parent=self)
        box.addButton(t("dialog.cancel_btn"), QMessageBox.RejectRole)
        delete = box.addButton(t("clear.confirm.delete"), QMessageBox.DestructiveRole)
        box.exec()
        return box.clickedButton() is delete

    def _clear_finished(self, outcome: ClearOutcome, clean: bool):
        """清除結束（做完或取消）：清除了的指紋交回專案並只存專案檔（_save_after_task），重畫分頁標出只在譜庫的檔；
        沒刪的列出來；刪之前重讀譜庫讀不到時頂端顯示提示列（什麼都沒刪）"""
        if outcome.cleared:
            self.project.record_fingerprints(outcome.fingerprints)
            self._save_after_task(clean)
            self._rebuild_tabs()
        if outcome.error is not None:
            failure = CatalogFailure.from_error(outcome.error, t("clear.title"))
            self._catalog_error_bar.show_failure(t("catalog.error.cannot_read"), failure, self._clear_local_copies)
            self._set_status(t("status.ready"))
            return
        key = "clear.cancelled" if outcome.cancelled else "clear.done"
        self._set_status(t(key, count=len(outcome.cleared), size=size_text(outcome.freed)))
        kept = [item.path for item in outcome.changed + outcome.catalog_changed] + [item.path for item, _ in outcome.failed]
        if kept:
            QMessageBox.warning(self, t("clear.title"), t("clear.kept", count=len(kept), files="\n".join(kept)))

    def download_back(self, group_ids: Sequence[str]):
        """「從譜庫下載回來」：把這些群組裡只在譜庫的檔在背景下載回原位，下載後群組恢復正常

        群組分頁與重新命名預覽的按鈕都經這裡（發譜預覽也可以逐群組呼叫）。讀不到譜庫或下載到一半失敗時
        頂端顯示提示列，寫出已完成幾份，按「重試」再下載一次（已放回去的略過）。不放上復原堆疊。

        Args:
            group_ids: 要下載回原位的群組
        """
        if self._task is not None:
            return
        project = self.project
        groups = self._download_service.restore_sources(project, group_ids)
        if not groups or not self._catalog_ready():
            return
        service = self._download_service
        clean = not project.is_modified()
        work = catalog_work(
            self._catalog_factory, lambda catalog, cancel, report: service.restore(groups, catalog, cancel, report),
            lambda error: DownloadOutcome(total=sum(len(g.sources) for g in groups), error=error),
        )

        def finished(outcome: DownloadOutcome, _cancelled: bool):
            """下載結束"""
            self._download_back_finished(outcome, clean, lambda: self.download_back(group_ids))

        self._run_in_background(work, finished, _DOWNLOAD_TEXTS, locks_project=True)

    def _download_back_finished(self, outcome: DownloadOutcome, clean: bool, retry: Callable[[], None]):
        """下載回原位結束（做完、取消或中途失敗）：新的指紋交回專案，開始前沒有未存檔的改動時只存專案檔；
        目前的群組分頁照磁碟現況重新比對（其他分頁切過去時會比對），沒放回去的檔列出來

        Args:
            outcome: 下載的結果
            clean: 開始前專案是否沒有未存檔的改動（有的話不替使用者存檔）
            retry: 提示列按「重試」時再下載一次
        """
        if outcome.fingerprints:
            self.project.record_fingerprints(outcome.fingerprints)
            self._save_after_task(clean)
        self._show_undeposited()
        if outcome.error is not None:
            failure = CatalogFailure.from_error(outcome.error, t("download.title"))
            self._catalog_error_bar.show_failure(t("download.stopped", count=outcome.completed), failure, retry)
            self._set_status(t("status.ready"))
        else:
            self._catalog_error_bar.clear()
            key = "download.cancelled" if outcome.cancelled else "download.done"
            self._set_status(t(key, count=outcome.completed))
        text = not_downloaded_text(outcome)
        if text:
            QMessageBox.warning(self, t("download.title"), text)

    def revert_reader(self, deposit_id: str, paths: Sequence[str]) -> Callable[["CatalogService"], RevertPreview]:
        """譜庫視窗要撤銷一次存入：記下開著的專案（群組、本機已清除的檔、樂團類型），交回在背景讀預覽的函式

        預覽照開著的專案判定：那次存入的群組不在這個專案、或本機沒有那次存入的內容時，那個群組不撤銷。
        本機那份經專案找（存入之後重新命名過也找得到），所以只在譜庫的檔連同專案裡存入過的檔現在的路徑一起看。

        Args:
            deposit_id: 要撤銷的存入編號
            paths: 那次存入的檔的本機路徑（存入紀錄記下的）

        Returns:
            以譜庫服務讀撤銷預覽的函式（背景執行緒呼叫）
        """
        candidates = list(paths) + LocalProject.deposited_paths(self.project)
        local = LocalProject.of(self.project, self._deposit_service.catalog_only(self.project, candidates))
        orchestra_type = self.project.orchestra_type
        service = self._revert_service
        return lambda catalog: service.preview(deposit_id, catalog, orchestra_type, local)

    def revert_deposit(
        self, preview: RevertPreview, choices: Sequence[RevertChoice], done: Callable[[RevertOutcome], None],
    ) -> bool:
        """譜庫視窗按了「撤銷存入」：同存入在背景撤銷，期間鎖住專案編輯、存檔與檔案操作

        結束後下載回原位的指紋與撤掉的檔交回專案（開始前沒有未存檔的改動時只存專案檔），群組分頁照磁碟現況重新比對，
        再以結果呼叫 done（譜庫視窗顯示提示列與沒撤銷的群組）。

        Args:
            preview: 撤銷的預覽
            choices: 勾了的群組與要不要一起撤掉編制
            done: 結束時以結果呼叫

        Returns:
            已開始時為 True；已有背景工作在跑時不做，為 False
        """
        if self._task is not None:
            return False
        service = self._revert_service
        operator = self._auth_service.account_email or ""
        orchestra_type = self.project.orchestra_type
        clean = not self.project.is_modified()
        work = catalog_work(
            self._catalog_factory,
            lambda catalog, cancel, report: service.revert(
                preview, choices, catalog, operator, orchestra_type, cancel, report,
            ),
            lambda error: RevertOutcome(error=error),
        )

        def finished(outcome: RevertOutcome, _cancelled: bool):
            """撤銷結束：交回專案，再交給譜庫視窗"""
            self._revert_finished(outcome, clean)
            done(outcome)

        self._run_in_background(work, finished, _REVERT_TEXTS, locks_project=True)
        return True

    def _revert_finished(self, outcome: RevertOutcome, clean: bool):
        """撤銷存入結束：下載回原位的指紋記下、撤掉的檔拿掉存入指紋，開始前沒有未存檔的改動時只存專案檔

        Args:
            outcome: 撤銷的結果
            clean: 開始前專案是否沒有未存檔的改動（有的話不替使用者存檔）
        """
        self.project.record_fingerprints(outcome.fingerprints)
        self.project.forget_deposits(outcome.reverted_paths)
        self._save_after_task(clean)
        self._show_undeposited()
        if outcome.error is not None:
            self._set_status(t("status.ready"))
        else:
            key = "revert.cancelled" if outcome.cancelled else "revert.done"
            self._set_status(t(key, count=outcome.completed))

    def _link_group_by_id(self, group_id: str):
        """存入預覽的「連到譜庫…」：切到那個群組，開連線對話框"""
        self._show_group(group_id)
        self._link_group_to_catalog()

    def _sign_in_google(self) -> bool:
        """提示列的「重新登入 Google」：開瀏覽器登入，回傳是否登入成功"""
        from ui.catalog_settings_dialog import sign_in_google
        return sign_in_google(self, self._auth_service)

    def _open_catalog_settings(self) -> bool:
        """開啟譜庫設定，回傳是否設定完成"""
        from ui.catalog_settings_dialog import CatalogSettingsDialog
        dialog = CatalogSettingsDialog(
            self._auth_service, self._preferences, self, log=self._log, huaxia_auth=self._huaxia_auth,
            club_settings=self._club_settings,
        )
        accepted = dialog.exec() == CatalogSettingsDialog.Accepted
        self._show_publish_button()
        return accepted

    # --- 發譜 ---

    def _show_publish_button(self):
        """依這台電腦記住的社團設定顯示或隱藏「發譜」"""
        self._publish_btn.setVisible(self._club_settings.remembered() is PublishingPlatform.HUAXIA)

    def _publish(self):
        """按了「發譜」：檢查能不能發，不能就寫出原因；能就對全部群組開啟發譜預覽，在預覽按下「發譜」後開始發譜"""
        self._open_publish_preview(None)

    def _publish_failed_again(self):
        """按了「重新發譜沒發成的群組」：只對上次沒發成、還在專案裡的群組走同一套發譜"""
        ids = [group.id for group in self.project.groups if group.id in self._failed_group_ids]
        if not ids:
            QMessageBox.information(self, t("publish.title"), t("publish.run.failed_groups_gone"))
            return
        self._open_publish_preview(ids)

    def _open_publish_preview(self, group_ids: Optional[List[str]]):
        """檢查能不能發，不能就寫出原因；能就開啟發譜預覽（group_ids 時只列這幾個群組），在預覽按下「發譜」後開始發譜

        依序：專案層級的檢查（未存檔、不是國樂團，不連網）、重讀社團設定（已拿掉 HuaXia 就說明並隱藏按鈕；
        讀不到時頂端顯示提示列）、沒登入 HuaXia 時先登入、在背景重查幹部身分與發譜函式並讀曲目。發譜進行中不再開。
        """
        if self._publishing.busy:
            return
        blocks = project_blocks(self.project)
        if blocks:
            QMessageBox.warning(self, t("publish.title"), "\n".join(t(_PROJECT_BLOCK_KEYS[b]) for b in blocks))
            return
        if not self._club_settings_enable_huaxia():
            return
        if not ensure_signed_in(self, self._huaxia_auth, t("publish.title"), "publish.sign_in_first"):
            return
        try:
            readiness = run_while_waiting(
                self, t("publish.title"), t("publish.checking"), self._publisher.prepare, lambda: None,
            )
        except HuaXiaError as e:
            QMessageBox.warning(self, t("publish.title"), huaxia_error_text(e, "publish"))
            return
        if not readiness.ready:
            QMessageBox.warning(self, t("publish.title"), _not_ready_text(readiness))
            return
        from ui.publish_dialog import PublishPreviewDialog
        dialog = PublishPreviewDialog(
            self.project, check_groups(self.project, group_ids), readiness, self, compressor=self._compressor,
            file_service=self.file_service, opener=self._opener, download_back=self.download_back,
        )
        accepted = dialog.exec() == PublishPreviewDialog.Accepted
        if accepted:
            self._start_publishing(dialog)
        dialog.deleteLater()
        self._instrumentation_table.refresh()

    def _start_publishing(self, dialog: "PublishPreviewDialog"):
        """在預覽按下了「發譜」：新建曲目產生編號並自動存檔一次，在等待視窗裡重查並做快照，再在背景發譜

        存檔時專案只有預覽寫進去的改動（曲目的對應與手動挑的聲部配對），一起存下；存不成、重查不過或快照失敗時
        不發（預覽交出的壓縮檔一併丟掉）。發譜不鎖住主視窗的其他操作（發出去的是快照）。
        """
        runner = self._publish_runner
        dialog.assign_piece_ids()
        if self.project.is_modified() and not (self._project_path and self._write_project_file(self._project_path)):
            runner.give_up(dialog.publish_plan())
            return
        plans = dialog.publish_plan()
        try:
            readiness, snapshot = run_while_waiting(
                self, t("publish.title"), t("publish.run.preparing"), lambda: runner.start(plans), lambda: None,
            )
        except HuaXiaError as e:
            QMessageBox.warning(self, t("publish.title"), huaxia_error_text(e, "publish"))
            return
        except OSError as e:
            QMessageBox.warning(self, t("publish.title"), t("publish.run.snapshot_failed", error=e))
            return
        if snapshot is None:
            QMessageBox.warning(self, t("publish.title"), _not_ready_text(readiness))
            self._show_publish_button()
            return
        self._catalog_error_bar.clear()
        self._publish_lines, self._publish_failures = [t("publish.run.started", groups=len(snapshot.groups))], []
        self._failed_group_ids = []
        self._publish_panel.show_record(self._publish_lines, self._publish_failures)
        self._publish_panel.show()

        def work(should_cancel, report) -> PublishOutcome:
            """在背景照快照發譜"""
            return runner.run(snapshot, self._catalog_factory, should_cancel, report)

        self._publishing.run(work, _PUBLISH_TEXTS, self._publish_finished, self._describe_publish)

    def _describe_publish(self, progress: PublishProgress) -> tuple:
        """發譜的一次進度：接上一行逐行紀錄，交回（狀態列文字，已完成數，總數）"""
        from ui.publish_run import progress_line
        line = progress_line(progress)
        self._publish_lines.append(line)
        self._publish_panel.add_line(line)
        return t("publish.run.progress", done=progress.done, total=progress.total), progress.done, progress.total

    def _publish_finished(self, outcome: PublishOutcome, _cancelled: bool):
        """發譜結束：這次新建的聲部記成配對，逐行紀錄接上結果，列出沒發成的群組（可以只重發它們）；
        發譜紀錄寫不進譜庫時頂端顯示提示列"""
        from ui.publish_run import failure_line, summary_lines
        self.project.remember_huaxia_parts(outcome.created_parts())
        lines = summary_lines(outcome)
        self._publish_lines += lines
        self._publish_failures = [failure_line(group) for group in outcome.failed]
        self._failed_group_ids = [group.snapshot.group_id for group in outcome.failed]
        self._publish_panel.show_record(self._publish_lines, self._publish_failures)
        self._publish_panel.show()
        self._set_publish_status("")
        self._set_status(lines[0])
        error = outcome.error or outcome.log_error
        if error is not None:
            heading = t("publish.run.not_started") if outcome.error is not None else t("publish.run.result_not_logged")
            self._catalog_error_bar.show_failure(
                heading, CatalogFailure.from_error(error, t("publish.title")), self._publish,
            )

    def _set_publish_status(self, text: str):
        """狀態列發譜的那一段文字"""
        self._publish_status.setText(text)

    def _show_publish_progress(self, done: int, total: int):
        """發譜的總進度"""
        self._publish_progress.setRange(0, max(total, 1))
        self._publish_progress.setValue(done)

    def _show_publishing_state(self):
        """依有沒有發譜在進行，顯示或隱藏發譜的進度與停止鈕，停用或恢復「發譜」"""
        busy = self._publishing.busy
        self._publish_status.setVisible(busy)
        self._publish_progress.setVisible(busy)
        self._publish_cancel_btn.setVisible(busy)
        self._publish_cancel_btn.setEnabled(busy and not self._publishing.cancel_requested)
        self._show_enabled_state()

    def _club_settings_enable_huaxia(self) -> bool:
        """在背景重讀譜庫的社團設定；已拿掉 HuaXia 發譜時說明並隱藏「發譜」，讀不到時頂端顯示提示列（重試再按一次）"""
        def read():
            """在背景接譜庫並讀社團設定"""
            return self._club_settings.read(self._catalog_factory())
        done, platform = wait_for_catalog(
            self, self._catalog_error_bar, t("publish.title"), t("publish.checking"), read,
            t("publish.club_settings_failed"), self._publish, action=t("catalog.action.read_club_settings"),
        )
        if not done:
            return False
        self._catalog_error_bar.clear()
        self._show_publish_button()
        if platform is not PublishingPlatform.HUAXIA:
            QMessageBox.information(self, t("publish.title"), t("publish.club_settings_removed"))
            return False
        return True

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
            cb = QCheckBox(g.display_name())
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

    def _on_project_changed(self):
        """專案「已變更」通知：更新視窗標題與群組分頁的標籤"""
        self._update_title()
        self._refresh_tab_labels()

    def _update_title(self):
        title = t("app.title")
        if self._project_path:
            title += f" - {os.path.basename(self._project_path)}"
        elif self._suggested_name:
            title += f" - {self._suggested_name}"
        else:
            title += f" - {t('app.unsaved_project')}"
        if self.project.read_only:
            title += f" [{t('app.read_only')}]"
        if self.project.is_modified():
            title += " *"
        self.setWindowTitle(title)

    def _set_status(self, text: str):
        self._status_label.setText(text)

    def _confirm_unsaved(self) -> bool:
        """有未存檔的修改時詢問是否儲存（開新專案、開啟專案、開啟最近專案），還有未同步的群組或未存入的檔時再各提醒一次

        唯讀的專案存不了，不問是否儲存。

        Returns:
            可以繼續原本的操作時為 True；選「取消」、選「儲存」但沒存成、或在未同步、未存入的提醒選「否」時為 False
        """
        saved = self.project.read_only or not self.project.is_modified() or self._ask_to_save()
        return saved and self._confirm_unsynced() and self._confirm_undeposited()

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
        """關閉前除了未存檔判定，再和磁碟上的專案檔比對一次，任一不同就詢問是否儲存；還有未同步的群組、
        未存入的檔時各提醒一次；有背景工作時再問要不要取消

        唯讀的專案存不了，不詢問是否儲存。
        """
        unsaved = not self.project.read_only and (self.project.is_modified() or self._differs_from_project_file())
        if unsaved and not self._ask_to_save(t("dialog.close"), t("dialog.close.message")):
            event.ignore()
            return
        if not self._confirm_unsynced() or not self._confirm_undeposited():
            event.ignore()
            return
        if not self._confirm_cancel_background() or not self._publishing.confirm_close():
            event.ignore()
            return
        event.accept()
