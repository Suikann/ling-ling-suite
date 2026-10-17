# -*- coding: utf-8 -*-
"""
「關於泠靈小工具」視窗（PySide6）

顯示版號、GitHub 網址與授權；網址經外部開啟（services/external_opener.py）以瀏覽器開啟。
版號由呼叫端以 core.launch_env.app_version 取得，開發版顯示「開發版」。

使用範例：
    from ui.about_dialog import AboutDialog
    AboutDialog(version, opener, parent).exec()
"""
from typing import Optional
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout
from core.constants import APP_REPOSITORY_URL
from core.locale import t
from services.external_opener import ExternalOpener


class AboutDialog(QDialog):
    """「關於泠靈小工具」視窗"""

    def __init__(self, version: Optional[str], opener: ExternalOpener, parent=None):
        """
        Args:
            version: 版號；開發版為 None
            opener: 開啟 GitHub 網址用的外部開啟
            parent: 父元件
        """
        super().__init__(parent)
        self.setWindowTitle(t("menu.help.about"))
        self._opener = opener
        layout = QVBoxLayout(self)
        title = QLabel(t("app.title"))
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        version_label = QLabel(t("about.version", version=version or t("about.dev_version")))
        version_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(version_label)
        link = QLabel(f'<a href="{APP_REPOSITORY_URL}">{APP_REPOSITORY_URL}</a>')
        link.setTextFormat(Qt.RichText)
        link.setOpenExternalLinks(False)
        link.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard)
        link.linkActivated.connect(self._open_link)
        layout.addWidget(link)
        layout.addWidget(QLabel(t("about.license")))
        buttons = QHBoxLayout()
        buttons.addStretch()
        close = QPushButton(t("about.close"))
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _open_link(self, url: str):
        """以瀏覽器開啟網址；開不了時提示"""
        if not self._opener.open(url):
            QMessageBox.warning(self, t("dialog.error"), t("dialog.error.external_open_failed", target=url))
