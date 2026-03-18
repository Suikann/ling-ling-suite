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
    palette.setColor(QPalette.Window, QColor(32, 28, 26))
    palette.setColor(QPalette.WindowText, QColor(222, 216, 208))
    palette.setColor(QPalette.Base, QColor(38, 34, 32))
    palette.setColor(QPalette.AlternateBase, QColor(46, 42, 38))
    palette.setColor(QPalette.ToolTipBase, QColor(58, 52, 46))
    palette.setColor(QPalette.ToolTipText, QColor(222, 216, 208))
    palette.setColor(QPalette.Text, QColor(222, 216, 208))
    palette.setColor(QPalette.Button, QColor(52, 47, 43))
    palette.setColor(QPalette.ButtonText, QColor(222, 216, 208))
    palette.setColor(QPalette.BrightText, QColor(255, 180, 60))
    palette.setColor(QPalette.Link, QColor(210, 160, 80))
    palette.setColor(QPalette.Highlight, QColor(180, 130, 55))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 248))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(120, 112, 100))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(120, 112, 100))
    app.setPalette(palette)
    app.setStyleSheet(_QSS)


_QSS = """
* { font-size: 14px; }
QMainWindow, QDialog { font-size: 14px; }
QMenuBar {
    background: #271f1b; border-bottom: 1px solid #3e3630;
    padding: 2px; font-size: 14px;
}
QMenuBar::item { padding: 5px 10px; }
QMenuBar::item:selected { background: #3e3630; border-radius: 4px; }
QMenu { background: #2c2622; border: 1px solid #3e3630; padding: 4px; font-size: 13px; }
QMenu::item { padding: 6px 28px; border-radius: 3px; }
QMenu::item:selected { background: #8b6914; color: #fff8ee; }
QMenu::separator { height: 1px; background: #3e3630; margin: 4px 8px; }
QTabWidget::pane {
    border: 1px solid #3e3630; border-radius: 6px;
    background: #28211d; padding: 2px;
}
QTabBar::tab {
    padding: 7px 20px; margin-right: 2px; font-size: 14px;
    background: #2c2622; border: 1px solid #3e3630;
    border-bottom: none; border-radius: 6px 6px 0 0;
    color: #b0a898;
}
QTabBar::tab:selected {
    background: #38302a; border-bottom: 2px solid #c89530;
    color: #e8ddd0;
}
QTabBar::tab:hover:!selected { background: #342c26; }
QPushButton {
    padding: 6px 16px; border: 1px solid #4e4438;
    border-radius: 5px; background: #3a322c;
    min-height: 20px; color: #ded8d0;
}
QPushButton:hover { background: #463c34; border-color: #5e5246; }
QPushButton:pressed { background: #342c26; }
QPushButton:disabled { color: #6e6458; background: #2c2622; border-color: #3e3630; }
QLineEdit, QComboBox {
    padding: 6px 10px; border: 1px solid #4e4438;
    border-radius: 5px; background: #2c2622;
    min-height: 22px; color: #ded8d0;
}
QLineEdit:focus, QComboBox:focus { border-color: #c89530; }
QComboBox::drop-down {
    border: none; width: 28px; background: transparent;
}
QComboBox QAbstractItemView {
    background: #2c2622; border: 1px solid #4e4438;
    selection-background-color: #8b6914;
    padding: 4px; color: #ded8d0;
}
QComboBox QAbstractItemView::item { padding: 4px 8px; }
QListWidget {
    border: 1px solid #3e3630; border-radius: 5px;
    background: #28211d; outline: none;
    padding: 4px;
}
QListWidget::item {
    padding: 5px 10px; border-radius: 3px; color: #ded8d0;
    margin: 1px 0;
}
QListWidget::item:selected {
    background: #6b4e1a; color: #fff8ee;
}
QListWidget::item:hover:!selected { background: #342c26; }
QListWidget::indicator { width: 0; height: 0; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator {
    width: 18px; height: 18px; border-radius: 4px;
    border: 1px solid #5e5246; background: #2c2622;
}
QCheckBox::indicator:checked {
    background: #b48020; border-color: #b48020;
}
QRadioButton { spacing: 8px; }
QRadioButton::indicator {
    width: 18px; height: 18px; border-radius: 9px;
    border: 1px solid #5e5246; background: #2c2622;
}
QRadioButton::indicator:checked {
    background: #b48020; border-color: #b48020;
}
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical {
    width: 8px; background: transparent; margin: 2px;
}
QScrollBar::handle:vertical {
    background: #5a4e42; border-radius: 4px; min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #6e6054; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    height: 8px; background: transparent; margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #5a4e42; border-radius: 4px; min-width: 24px;
}
QScrollBar::handle:horizontal:hover { background: #6e6054; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QLabel { padding: 1px; }
QSplitter::handle { background: #3e3630; width: 3px; }
QSplitter::handle:hover { background: #c89530; }
"""


if __name__ == "__main__":
    main()
