# -*- coding: utf-8 -*-
"""
譜庫設定對話框

提供 Google 帳號登入與譜庫試算表設定。
支援貼上完整網址自動擷取 ID，以及瀏覽 Drive 選取資料夾。
"""
import re
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QGroupBox, QFormLayout,
    QTreeWidget, QTreeWidgetItem, QDialogButtonBox,
)
from PySide6.QtCore import Qt
from core.locale import t
from services.google_auth_service import GoogleAuthService
from services.preferences_service import PreferencesService


def _extract_spreadsheet_id(text: str) -> str:
    """從網址或純 ID 中擷取試算表 ID"""
    text = text.strip()
    match = re.search(r'/spreadsheets/d/([a-zA-Z0-9_-]+)', text)
    if match:
        return match.group(1)
    return text


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
        auth_group = QGroupBox(t("catalog.auth.title"))
        auth_layout = QVBoxLayout(auth_group)
        status_row = QHBoxLayout()
        self._auth_status = QLabel()
        status_row.addWidget(self._auth_status)
        status_row.addStretch()
        self._login_btn = QPushButton()
        self._login_btn.clicked.connect(self._toggle_auth)
        status_row.addWidget(self._login_btn)
        auth_layout.addLayout(status_row)
        layout.addWidget(auth_group)
        sheet_group = QGroupBox(t("catalog.settings.title"))
        sheet_layout = QFormLayout(sheet_group)
        spreadsheet_row = QHBoxLayout()
        self._spreadsheet_entry = QLineEdit(
            self._prefs.get("catalog_spreadsheet_id") or "",
        )
        self._spreadsheet_entry.setPlaceholderText(
            t("catalog.settings.spreadsheet_hint"),
        )
        spreadsheet_row.addWidget(self._spreadsheet_entry)
        browse_sheet_btn = QPushButton(t("catalog.settings.browse"))
        browse_sheet_btn.clicked.connect(self._browse_spreadsheet)
        spreadsheet_row.addWidget(browse_sheet_btn)
        sheet_layout.addRow(
            t("catalog.settings.spreadsheet_id"), spreadsheet_row,
        )
        folder_row = QHBoxLayout()
        self._root_folder_entry = QLineEdit(
            self._prefs.get("catalog_root_folder_id") or "",
        )
        self._root_folder_entry.setPlaceholderText(
            t("catalog.settings.folder_hint"),
        )
        folder_row.addWidget(self._root_folder_entry)
        browse_folder_btn = QPushButton(t("catalog.settings.browse"))
        browse_folder_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse_folder_btn)
        sheet_layout.addRow(
            t("catalog.settings.root_folder"), folder_row,
        )
        create_btn = QPushButton(t("catalog.settings.create_new"))
        create_btn.clicked.connect(self._create_spreadsheet)
        sheet_layout.addRow("", create_btn)
        layout.addWidget(sheet_group)
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

    def _browse_spreadsheet(self):
        """瀏覽 Google Drive 選取試算表"""
        creds = self._auth.get_credentials()
        if not creds:
            QMessageBox.warning(
                self, t("catalog.settings.title"), t("catalog.no_connection"),
            )
            return
        try:
            from googleapiclient.discovery import build
            service = build("drive", "v3", credentials=creds)
            results = service.files().list(
                q="mimeType='application/vnd.google-apps.spreadsheet' and trashed=false",
                fields="files(id, name)",
                orderBy="name",
                pageSize=100,
            ).execute()
            files = results.get("files", [])
            if not files:
                QMessageBox.information(
                    self, t("catalog.settings.title"),
                    t("catalog.settings.no_spreadsheets"),
                )
                return
            dialog = _DrivePickerDialog(
                files, t("catalog.settings.pick_spreadsheet"), self,
            )
            if dialog.exec() == QDialog.Accepted and dialog.selected_id:
                self._spreadsheet_entry.setText(dialog.selected_id)
        except Exception as e:
            QMessageBox.critical(
                self, t("catalog.settings.title"),
                t("catalog.error", error=str(e)),
            )

    def _browse_folder(self):
        """瀏覽 Google Drive 選取資料夾"""
        creds = self._auth.get_credentials()
        if not creds:
            QMessageBox.warning(
                self, t("catalog.settings.title"), t("catalog.no_connection"),
            )
            return
        try:
            from googleapiclient.discovery import build
            service = build("drive", "v3", credentials=creds)
            results = service.files().list(
                q="mimeType='application/vnd.google-apps.folder' and trashed=false",
                fields="files(id, name)",
                orderBy="name",
                pageSize=200,
            ).execute()
            files = results.get("files", [])
            if not files:
                QMessageBox.information(
                    self, t("catalog.settings.title"),
                    t("catalog.settings.no_folders"),
                )
                return
            dialog = _DrivePickerDialog(
                files, t("catalog.settings.pick_folder"), self,
            )
            if dialog.exec() == QDialog.Accepted and dialog.selected_id:
                self._root_folder_entry.setText(dialog.selected_id)
        except Exception as e:
            QMessageBox.critical(
                self, t("catalog.settings.title"),
                t("catalog.error", error=str(e)),
            )

    def _create_spreadsheet(self):
        """建立新的譜庫試算表"""
        creds = self._auth.get_credentials()
        if not creds:
            QMessageBox.warning(
                self, t("catalog.settings.title"), t("catalog.no_connection"),
            )
            return
        try:
            from services.sheets_service import SheetsService
            service = SheetsService(creds)
            spreadsheet_id = service.create_catalog_spreadsheet()
            self._spreadsheet_entry.setText(spreadsheet_id)
            QMessageBox.information(
                self, t("catalog.settings.title"),
                t("catalog.settings.created"),
            )
        except Exception as e:
            QMessageBox.critical(
                self, t("catalog.settings.title"),
                t("catalog.error", error=str(e)),
            )

    def _save_settings(self):
        """儲存設定，自動從網址擷取 ID"""
        spreadsheet_text = self._spreadsheet_entry.text()
        folder_text = self._root_folder_entry.text()
        self._prefs.set(
            "catalog_spreadsheet_id",
            _extract_spreadsheet_id(spreadsheet_text),
        )
        self._prefs.set(
            "catalog_root_folder_id",
            _extract_folder_id(folder_text),
        )
        self._prefs.save()
        self.accept()


class _DrivePickerDialog(QDialog):
    """Google Drive 檔案/資料夾選取對話框"""

    def __init__(self, items, title, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(450, 400)
        self.selected_id = ""
        layout = QVBoxLayout(self)
        self._search = QLineEdit()
        self._search.setPlaceholderText(t("catalog.toolbar.search"))
        self._search.textChanged.connect(self._on_search)
        layout.addWidget(self._search)
        self._tree = QTreeWidget()
        self._tree.setHeaderHidden(True)
        self._tree.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._tree)
        self._items = items
        self._populate(items)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel,
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _populate(self, items):
        """填入項目清單"""
        self._tree.clear()
        for item in items:
            node = QTreeWidgetItem([item["name"]])
            node.setData(0, Qt.UserRole, item["id"])
            self._tree.addTopLevelItem(node)

    def _on_search(self, text):
        """搜尋篩選"""
        q = text.lower()
        filtered = [
            item for item in self._items if q in item["name"].lower()
        ]
        self._populate(filtered)

    def _on_double_click(self, item, column):
        """雙擊選取"""
        self.selected_id = item.data(0, Qt.UserRole) or ""
        if self.selected_id:
            self.accept()

    def _on_accept(self):
        """確認選取"""
        current = self._tree.currentItem()
        if current:
            self.selected_id = current.data(0, Qt.UserRole) or ""
        self.accept()
