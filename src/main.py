# -*- coding: utf-8 -*-
"""
泠靈小工具 - 應用程式進入點

啟動 PySide6 主視窗。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from core.locale import t, set_locale
from core.models import Project
from services.preferences_service import PreferencesService


def main():
    prefs = PreferencesService()
    prefs.load()
    language = prefs.get("language") or "zh_TW"
    set_locale(language)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    _apply_dark_theme(app)
    from ui.main_window import MainWindow
    window = MainWindow(prefs)
    window.setWindowTitle(t("app.title"))
    window.resize(1200, 800)
    window.setMinimumSize(900, 600)
    window.show()
    sys.exit(app.exec())


def _apply_dark_theme(app: QApplication):
    from PySide6.QtGui import QPalette, QColor
    palette = QPalette()
    palette.setColor(QPalette.Window, QColor(30, 30, 30))
    palette.setColor(QPalette.WindowText, QColor(220, 220, 220))
    palette.setColor(QPalette.Base, QColor(40, 40, 40))
    palette.setColor(QPalette.AlternateBase, QColor(50, 50, 50))
    palette.setColor(QPalette.ToolTipBase, QColor(60, 60, 60))
    palette.setColor(QPalette.ToolTipText, QColor(220, 220, 220))
    palette.setColor(QPalette.Text, QColor(220, 220, 220))
    palette.setColor(QPalette.Button, QColor(50, 50, 50))
    palette.setColor(QPalette.ButtonText, QColor(220, 220, 220))
    palette.setColor(QPalette.BrightText, QColor(255, 50, 50))
    palette.setColor(QPalette.Link, QColor(70, 130, 230))
    palette.setColor(QPalette.Highlight, QColor(70, 130, 230))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(120, 120, 120))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(120, 120, 120))
    app.setPalette(palette)


if __name__ == "__main__":
    main()
