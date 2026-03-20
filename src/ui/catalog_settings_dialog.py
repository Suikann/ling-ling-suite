# -*- coding: utf-8 -*-
"""
譜庫設定對話框

提供譜庫試算表 ID 與根目錄設定。
認證使用 Service Account，無需使用者操作。
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
        status_group = QGroupBox(t("catalog.auth.title"))
        status_layout = QVBoxLayout(status_group)
        if self._auth.has_key_file:
            status_label = QLabel(t("catalog.auth.connected"))
            status_label.setStyleSheet("color: #2ecc71; font-weight: bold;")
        else:
            status_label = QLabel(
                t("catalog.auth.credentials_missing",
                  path=self._auth.key_file_path),
            )
            status_label.setStyleSheet("color: #e74c3c;")
            status_label.setWordWrap(True)
        status_layout.addWidget(status_label)
        layout.addWidget(status_group)
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

    def _create_spreadsheet(self):
        """建立新的譜庫試算表"""
        creds = self._auth.get_credentials()
        if not creds:
            QMessageBox.warning(
                self, t("catalog.settings.title"),
                t("catalog.auth.credentials_missing",
                  path=self._auth.key_file_path),
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
