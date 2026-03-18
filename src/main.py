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
    palette.setColor(QPalette.Window, QColor(22, 23, 30))
    palette.setColor(QPalette.WindowText, QColor(216, 218, 232))
    palette.setColor(QPalette.Base, QColor(30, 32, 41))
    palette.setColor(QPalette.AlternateBase, QColor(36, 38, 48))
    palette.setColor(QPalette.ToolTipBase, QColor(46, 48, 60))
    palette.setColor(QPalette.ToolTipText, QColor(216, 218, 232))
    palette.setColor(QPalette.Text, QColor(216, 218, 232))
    palette.setColor(QPalette.Button, QColor(42, 44, 56))
    palette.setColor(QPalette.ButtonText, QColor(216, 218, 232))
    palette.setColor(QPalette.BrightText, QColor(170, 160, 255))
    palette.setColor(QPalette.Link, QColor(160, 150, 230))
    palette.setColor(QPalette.Highlight, QColor(110, 105, 200))
    palette.setColor(QPalette.HighlightedText, QColor(238, 242, 255))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(110, 114, 130))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(110, 114, 130))
    app.setPalette(palette)
    app.setStyleSheet(_QSS)


_QSS = """
* { font-size: 14px; }
QMainWindow, QDialog { font-size: 14px; }
QMenuBar {
    background: #1a1b26; border-bottom: 1px solid #363848;
    padding: 2px; font-size: 14px;
}
QMenuBar::item { padding: 5px 10px; }
QMenuBar::item:selected { background: #363848; border-radius: 4px; }
QMenu { background: #24262f; border: 1px solid #363848; padding: 4px; font-size: 13px; }
QMenu::item { padding: 6px 28px; border-radius: 3px; }
QMenu::item:selected { background: #4338CA; color: #eef2ff; }
QMenu::separator { height: 1px; background: #363848; margin: 4px 8px; }
QTabWidget::pane {
    border: 1px solid #363848; border-top: none;
    background: #1e2029; padding: 2px;
}
QTabBar::tab {
    padding: 7px 20px; margin-right: 2px; font-size: 14px;
    background: #24262f; border: 1px solid #363848;
    border-bottom: 1px solid #363848;
    border-radius: 6px 6px 0 0;
    color: #a0a4b8;
}
QTabBar::tab:selected {
    background: #1e2029; border-bottom: 1px solid #1e2029;
    border-top: 2px solid #7c6ddf; color: #e8eaf4;
    margin-bottom: -1px;
}
QTabBar::tab:hover:!selected { background: #2a2c38; }
QPushButton {
    padding: 6px 16px; border: 1px solid #42445c;
    border-radius: 5px; background: #2e3040;
    min-height: 20px; color: #d8dae8;
}
QPushButton:hover { background: #383a4c; border-color: #5a5d72; }
QPushButton:pressed { background: #282a38; }
QPushButton:disabled { color: #6e7288; background: #24262f; border-color: #363848; }
QLineEdit, QComboBox {
    padding: 6px 10px; border: 1px solid #42445c;
    border-radius: 5px; background: #24262f;
    min-height: 22px; color: #d8dae8;
}
QLineEdit:focus, QComboBox:focus { border-color: #7c6ddf; }
QComboBox::drop-down {
    border: none; width: 28px; background: transparent;
}
QComboBox { combobox-popup: 0; }
QComboBox QAbstractItemView {
    background: #24262f; border: 1px solid #42445c;
    selection-background-color: #4338CA;
    padding: 4px; color: #d8dae8;
}
QComboBox QAbstractItemView::item { padding: 6px 10px; }
QListWidget {
    border: 1px solid #363848; border-radius: 5px;
    background: #1e2029; outline: none;
    padding: 4px;
}
QListWidget::item {
    padding: 5px 10px; border-radius: 3px; color: #d8dae8;
    margin: 1px 0;
}
QListWidget::item:selected {
    background: #312e81; color: #eef2ff;
}
QListWidget::item:hover:!selected { background: #2a2c38; }
QListWidget::indicator { width: 0; height: 0; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator {
    width: 18px; height: 18px; border-radius: 4px;
    border: 1px solid #5a5d72; background: #24262f;
}
QCheckBox::indicator:checked {
    background: #6366F1; border-color: #6366F1;
}
QRadioButton { spacing: 8px; }
QRadioButton::indicator {
    width: 18px; height: 18px; border-radius: 9px;
    border: 1px solid #5a5d72; background: #24262f;
}
QRadioButton::indicator:checked {
    background: #6366F1; border-color: #6366F1;
}
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical {
    width: 8px; background: transparent; margin: 2px;
}
QScrollBar::handle:vertical {
    background: #4a4e62; border-radius: 4px; min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #5e6278; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    height: 8px; background: transparent; margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #4a4e62; border-radius: 4px; min-width: 24px;
}
QScrollBar::handle:horizontal:hover { background: #5e6278; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QLabel { padding: 1px; }
QSplitter::handle { background: #363848; width: 3px; }
QSplitter::handle:hover { background: #7c6ddf; }
"""


if __name__ == "__main__":
    main()
