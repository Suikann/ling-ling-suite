# -*- coding: utf-8 -*-
"""
譜庫設定對話框

引導使用者完成：登入 Google → 選擇譜庫資料夾 → 自動偵測或建立譜庫目錄。
"""
import re
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QGroupBox,
    QTreeWidget, QTreeWidgetItem, QDialogButtonBox,
)
from PySide6.QtCore import Qt
from core.locale import t
from core.catalog_constants import CATALOG_SPREADSHEET_NAME
from services.google_auth_service import GoogleAuthService
from services.preferences_service import PreferencesService


def _extract_folder_id(text: str) -> str:
    """從網址或純 ID 中擷取資料夾 ID"""
    text = text.strip()
    match = re.search(r'/folders/([a-zA-Z0-9_-]+)', text)
    if match:
        return match.group(1)
    return text


class CatalogSettingsDialog(QDialog):
    """譜庫設定對話框"""

    def __init__(
        self, auth_service: GoogleAuthService,
        preferences: PreferencesService, parent=None,
    ):
        super().__init__(parent)
        self._auth = auth_service
        self._prefs = preferences
        self.setWindowTitle(t("catalog.settings.title"))
        self.setMinimumWidth(560)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        # --- 步驟 1：登入 ---
        step1 = QGroupBox(t("catalog.settings.step1"))
        step1_layout = QHBoxLayout(step1)
        self._auth_status = QLabel()
        step1_layout.addWidget(self._auth_status)
        step1_layout.addStretch()
        self._login_btn = QPushButton()
        self._login_btn.clicked.connect(self._toggle_auth)
        step1_layout.addWidget(self._login_btn)
        layout.addWidget(step1)
        # --- 步驟 2：選擇資料夾 ---
        step2 = QGroupBox(t("catalog.settings.step2"))
        step2_layout = QVBoxLayout(step2)
        folder_row = QHBoxLayout()
        self._folder_entry = QLineEdit(
            self._prefs.get("catalog_root_folder_id") or "",
        )
        self._folder_entry.setPlaceholderText(
            t("catalog.settings.folder_hint"),
        )
        folder_row.addWidget(self._folder_entry)
        browse_btn = QPushButton(t("catalog.settings.browse"))
        browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_btn)
        step2_layout.addLayout(folder_row)
        self._folder_status = QLabel("")
        self._folder_status.setWordWrap(True)
        step2_layout.addWidget(self._folder_status)
        layout.addWidget(step2)
        # --- 底部按鈕 ---
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton(t("catalog.settings.save"))
        save_btn.clicked.connect(self._save_settings)
        btn_row.addWidget(save_btn)
        cancel_btn = QPushButton(t("catalog.cancel"))
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)
        self._update_auth_display()
        if self._folder_entry.text().strip():
            self._check_catalog_in_folder()

    def _update_auth_display(self):
        """更新認證狀態顯示"""
        if self._auth.is_authenticated:
            self._auth_status.setText(t("catalog.auth.connected"))
            self._auth_status.setStyleSheet("color: #2ecc71; font-weight: bold;")
            self._login_btn.setText(t("catalog.auth.disconnect"))
        else:
            self._auth_status.setText(t("catalog.auth.not_connected"))
            self._auth_status.setStyleSheet("color: #e74c3c;")
            self._login_btn.setText(t("catalog.auth.connect"))

    def _toggle_auth(self):
        """登入或登出"""
        try:
            if self._auth.is_authenticated:
                self._auth.logout()
                self._update_auth_display()
                return
        except Exception:
            pass
        try:
            self._auth.authenticate()
            self._update_auth_display()
            QMessageBox.information(
                self, t("catalog.auth.title"), t("catalog.auth.success"),
            )
        except FileNotFoundError:
            QMessageBox.warning(
                self, t("catalog.auth.title"),
                t("catalog.auth.credentials_missing",
                  path=self._auth.client_secrets_path),
            )
        except Exception as e:
            self._update_auth_display()
            QMessageBox.critical(
                self, t("catalog.auth.title"),
                t("catalog.auth.failed", error=str(e)),
            )

    def _browse_folder(self):
        """以階層式樹狀結構瀏覽 Drive 資料夾"""
        creds = self._auth.get_credentials()
        if not creds:
            QMessageBox.warning(
                self, t("catalog.settings.title"), t("catalog.no_connection"),
            )
            return
        try:
            from googleapiclient.discovery import build
            service = build("drive", "v3", credentials=creds)
            dialog = _DriveFolderBrowser(service, self)
            if dialog.exec() == QDialog.Accepted and dialog.selected_id:
                self._folder_entry.setText(dialog.selected_id)
                self._check_catalog_in_folder()
        except Exception as e:
            QMessageBox.critical(
                self, t("catalog.settings.title"),
                t("catalog.error", error=str(e)),
            )

    def _check_catalog_in_folder(self):
        """在選定的資料夾中尋找現有的譜庫目錄試算表"""
        creds = self._auth.get_credentials()
        if not creds:
            return
        folder_id = _extract_folder_id(self._folder_entry.text())
        if not folder_id:
            return
        try:
            from googleapiclient.discovery import build
            drive = build("drive", "v3", credentials=creds)
            results = drive.files().list(
                q=(
                    f"'{folder_id}' in parents"
                    " and mimeType='application/vnd.google-apps.spreadsheet'"
                    " and trashed=false"
                ),
                fields="files(id, name)",
                pageSize=50,
            ).execute()
            files = results.get("files", [])
            catalog = None
            for f in files:
                if f["name"] == CATALOG_SPREADSHEET_NAME:
                    catalog = f
                    break
            if catalog:
                self._prefs.set("catalog_spreadsheet_id", catalog["id"])
                self._folder_status.setText(
                    t("catalog.settings.catalog_found", name=catalog["name"]),
                )
                self._folder_status.setStyleSheet("color: #2ecc71;")
            else:
                self._folder_status.setText(
                    t("catalog.settings.catalog_not_found"),
                )
                self._folder_status.setStyleSheet("color: #f39c12;")
                self._offer_create_catalog(folder_id)
        except Exception as e:
            self._folder_status.setText(
                t("catalog.error", error=str(e)),
            )
            self._folder_status.setStyleSheet("color: #e74c3c;")

    def _offer_create_catalog(self, folder_id: str):
        """詢問是否在此資料夾建立譜庫目錄"""
        result = QMessageBox.question(
            self, t("catalog.settings.title"),
            t("catalog.settings.create_confirm"),
        )
        if result != QMessageBox.Yes:
            return
        creds = self._auth.get_credentials()
        if not creds:
            return
        try:
            from services.sheets_service import SheetsService
            from googleapiclient.discovery import build
            service = SheetsService(creds)
            spreadsheet_id = service.create_catalog_spreadsheet()
            drive = build("drive", "v3", credentials=creds)
            drive.files().update(
                fileId=spreadsheet_id,
                addParents=folder_id,
                removeParents="root",
            ).execute()
            self._prefs.set("catalog_spreadsheet_id", spreadsheet_id)
            self._folder_status.setText(
                t("catalog.settings.catalog_created"),
            )
            self._folder_status.setStyleSheet("color: #2ecc71;")
        except Exception as e:
            QMessageBox.critical(
                self, t("catalog.settings.title"),
                t("catalog.error", error=str(e)),
            )

    def _save_settings(self):
        """儲存設定"""
        folder_id = _extract_folder_id(self._folder_entry.text())
        self._prefs.set("catalog_root_folder_id", folder_id)
        self._prefs.save()
        self.accept()


class _DriveFolderBrowser(QDialog):
    """階層式 Google Drive 資料夾瀏覽器"""

    _FOLDER_MIME = "application/vnd.google-apps.folder"

    def __init__(self, drive_service, parent=None):
        super().__init__(parent)
        self.setWindowTitle(t("catalog.settings.pick_folder"))
        self.setMinimumSize(500, 450)
        self.selected_id = ""
        self._drive = drive_service
        layout = QVBoxLayout(self)
        self._path_label = QLabel("My Drive")
        self._path_label.setStyleSheet("font-weight: bold; padding: 4px;")
        layout.addWidget(self._path_label)
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.itemExpanded.connect(self._on_expand)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        self._tree.currentItemChanged.connect(self._on_select)
        layout.addWidget(self._tree)
        self._selected_label = QLabel("")
        self._selected_label.setStyleSheet("color: #7c6ddf; padding: 4px;")
        layout.addWidget(self._selected_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._load_children(None, "root")

    def _list_subfolders(self, parent_id: str):
        """列出指定資料夾的子資料夾"""
        query = (
            f"'{parent_id}' in parents"
            f" and mimeType='{self._FOLDER_MIME}'"
            " and trashed=false"
        )
        results = self._drive.files().list(
            q=query,
            fields="files(id, name)",
            orderBy="name",
            pageSize=200,
        ).execute()
        return results.get("files", [])

    def _load_children(self, parent_node, folder_id: str):
        """載入子資料夾並加入樹狀結構"""
        try:
            folders = self._list_subfolders(folder_id)
        except Exception:
            return
        for folder in folders:
            node = QTreeWidgetItem([folder["name"]])
            node.setData(0, Qt.UserRole, folder["id"])
            node.setData(0, Qt.UserRole + 1, False)
            placeholder = QTreeWidgetItem([t("catalog.loading")])
            node.addChild(placeholder)
            if parent_node is None:
                self._tree.addTopLevelItem(node)
            else:
                parent_node.addChild(node)

    def _on_expand(self, item):
        """展開資料夾時載入子資料夾"""
        already_loaded = item.data(0, Qt.UserRole + 1)
        if already_loaded:
            return
        item.setData(0, Qt.UserRole + 1, True)
        item.takeChildren()
        folder_id = item.data(0, Qt.UserRole) or ""
        if folder_id:
            self._load_children(item, folder_id)
        if item.childCount() == 0:
            empty = QTreeWidgetItem([t("catalog.settings.no_folders")])
            empty.setFlags(Qt.NoItemFlags)
            item.addChild(empty)

    def _on_select(self, current, previous):
        """選取變更時更新顯示"""
        if current and current.data(0, Qt.UserRole):
            self._selected_label.setText(current.text(0))

    def _on_double_click(self, item, column):
        """雙擊選取資料夾"""
        folder_id = item.data(0, Qt.UserRole) or ""
        if folder_id:
            self.selected_id = folder_id
            self.accept()

    def _on_accept(self):
        """確認選取"""
        current = self._tree.currentItem()
        if current:
            self.selected_id = current.data(0, Qt.UserRole) or ""
        self.accept()
