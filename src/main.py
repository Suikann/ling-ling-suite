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
    palette.setColor(QPalette.Base, QColor(38, 38, 38))
    palette.setColor(QPalette.AlternateBase, QColor(46, 46, 46))
    palette.setColor(QPalette.ToolTipBase, QColor(60, 60, 60))
    palette.setColor(QPalette.ToolTipText, QColor(220, 220, 220))
    palette.setColor(QPalette.Text, QColor(220, 220, 220))
    palette.setColor(QPalette.Button, QColor(50, 50, 50))
    palette.setColor(QPalette.ButtonText, QColor(220, 220, 220))
    palette.setColor(QPalette.BrightText, QColor(255, 50, 50))
    palette.setColor(QPalette.Link, QColor(86, 140, 220))
    palette.setColor(QPalette.Highlight, QColor(66, 133, 244))
    palette.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(110, 110, 110))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(110, 110, 110))
    app.setPalette(palette)
    app.setStyleSheet(_QSS)


_QSS = """
* { font-size: 14px; }
QMainWindow, QDialog { font-size: 14px; }
QMenuBar {
    background: #252525; border-bottom: 1px solid #3a3a3a;
    padding: 2px; font-size: 14px;
}
QMenuBar::item { padding: 5px 10px; }
QMenuBar::item:selected { background: #3a3a3a; border-radius: 4px; }
QMenu { background: #2a2a2a; border: 1px solid #3a3a3a; padding: 4px; }
QMenu::item { padding: 6px 28px; border-radius: 3px; }
QMenu::item:selected { background: #4285f4; }
QMenu::separator { height: 1px; background: #3a3a3a; margin: 4px 8px; }
QTabWidget::pane {
    border: 1px solid #3a3a3a; border-radius: 6px;
    background: #262626; padding: 2px;
}
QTabBar::tab {
    padding: 7px 20px; margin-right: 2px; font-size: 14px;
    background: #2a2a2a; border: 1px solid #3a3a3a;
    border-bottom: none; border-radius: 6px 6px 0 0;
}
QTabBar::tab:selected {
    background: #363636; border-bottom: 2px solid #4285f4;
}
QTabBar::tab:hover:!selected { background: #333333; }
QPushButton {
    padding: 6px 16px; border: 1px solid #4a4a4a;
    border-radius: 5px; background: #383838;
    min-height: 20px;
}
QPushButton:hover { background: #444444; border-color: #5a5a5a; }
QPushButton:pressed { background: #333333; }
QPushButton:disabled { color: #666666; background: #2a2a2a; border-color: #3a3a3a; }
QLineEdit, QComboBox {
    padding: 6px 10px; border: 1px solid #4a4a4a;
    border-radius: 5px; background: #2a2a2a;
    min-height: 22px;
}
QLineEdit:focus, QComboBox:focus { border-color: #4285f4; }
QComboBox::drop-down {
    border: none; width: 28px; background: transparent;
}
QComboBox QAbstractItemView {
    background: #2a2a2a; border: 1px solid #4a4a4a;
    selection-background-color: #4285f4;
    padding: 4px;
}
QComboBox QAbstractItemView::item { padding: 4px 8px; }
QListWidget {
    border: 1px solid #3a3a3a; border-radius: 5px;
    background: #262626; outline: none;
}
QListWidget::item {
    padding: 5px 10px; border-radius: 3px;
}
QListWidget::item:selected {
    background: #3d5a80; color: white;
}
QListWidget::item:hover:!selected { background: #323232; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator {
    width: 18px; height: 18px; border-radius: 4px;
    border: 1px solid #5a5a5a; background: #2a2a2a;
}
QCheckBox::indicator:checked {
    background: #4285f4; border-color: #4285f4;
}
QRadioButton { spacing: 8px; }
QRadioButton::indicator {
    width: 18px; height: 18px; border-radius: 9px;
    border: 1px solid #5a5a5a; background: #2a2a2a;
}
QRadioButton::indicator:checked {
    background: #4285f4; border-color: #4285f4;
}
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical {
    width: 8px; background: transparent; margin: 2px;
}
QScrollBar::handle:vertical {
    background: #555555; border-radius: 4px; min-height: 24px;
}
QScrollBar::handle:vertical:hover { background: #666666; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal {
    height: 8px; background: transparent; margin: 2px;
}
QScrollBar::handle:horizontal {
    background: #555555; border-radius: 4px; min-width: 24px;
}
QScrollBar::handle:horizontal:hover { background: #666666; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QLabel { padding: 1px; }
QSplitter::handle { background: #3a3a3a; width: 3px; }
QSplitter::handle:hover { background: #4285f4; }
"""


if __name__ == "__main__":
    main()
