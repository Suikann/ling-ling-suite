# -*- coding: utf-8 -*-
"""
譜庫設定對話框

提供 Google 帳號連結與譜庫試算表設定。
"""
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QGroupBox, QFormLayout,
)
from core.locale import t
from services.google_auth_service import GoogleAuthService
from services.preferences_service import PreferencesService


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
        self.setMinimumWidth(500)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        auth_group = QGroupBox(t("catalog.auth.title"))
        auth_layout = QVBoxLayout(auth_group)
        status_row = QHBoxLayout()
        self._auth_status = QLabel()
        status_row.addWidget(self._auth_status)
        status_row.addStretch()
        self._connect_btn = QPushButton()
        self._connect_btn.clicked.connect(self._toggle_auth)
        status_row.addWidget(self._connect_btn)
        auth_layout.addLayout(status_row)
        layout.addWidget(auth_group)
        sheet_group = QGroupBox(t("catalog.settings.title"))
        sheet_layout = QFormLayout(sheet_group)
        self._spreadsheet_entry = QLineEdit(
            self._prefs.get("catalog_spreadsheet_id") or "",
        )
        sheet_layout.addRow(
            t("catalog.settings.spreadsheet_id"), self._spreadsheet_entry,
        )
        self._root_folder_entry = QLineEdit(
            self._prefs.get("catalog_root_folder_id") or "",
        )
        sheet_layout.addRow(
            t("catalog.settings.root_folder"), self._root_folder_entry,
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
        if self._auth.get_credentials():
            self._auth_status.setText(t("catalog.auth.connected"))
            self._auth_status.setStyleSheet("color: #2ecc71; font-weight: bold;")
            self._connect_btn.setText(t("catalog.auth.disconnect"))
        else:
            self._auth_status.setText(t("catalog.auth.not_connected"))
            self._auth_status.setStyleSheet("color: #e74c3c;")
            self._connect_btn.setText(t("catalog.auth.connect"))

    def _toggle_auth(self):
        """切換 Google 帳號連結"""
        if self._auth.get_credentials():
            self._auth.logout()
            self._update_auth_display()
            return
        if not self._auth.has_credentials_file:
            QMessageBox.warning(
                self, t("catalog.auth.title"),
                t("catalog.auth.credentials_missing", path=self._auth._credentials_path),
            )
            return
        try:
            self._auth.authenticate()
            self._update_auth_display()
            QMessageBox.information(
                self, t("catalog.auth.title"), t("catalog.auth.success"),
            )
        except Exception as e:
            QMessageBox.critical(
                self, t("catalog.auth.title"),
                t("catalog.auth.failed", error=str(e)),
            )

    def _create_spreadsheet(self):
        """建立新的譜庫試算表"""
        creds = self._auth.get_credentials()
        if not creds:
            QMessageBox.warning(
                self, t("catalog.auth.title"), t("catalog.no_connection"),
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
        """儲存設定"""
        self._prefs.set(
            "catalog_spreadsheet_id",
            self._spreadsheet_entry.text().strip(),
        )
        self._prefs.set(
            "catalog_root_folder_id",
            self._root_folder_entry.text().strip(),
        )
        self._prefs.save()
        self.accept()
