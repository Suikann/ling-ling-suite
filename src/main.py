# -*- coding: utf-8 -*-
"""
泠靈小工具 - 應用程式進入點

啟動前補上輸入法環境變數、設定應用程式識別名，再啟動 PySide6 主視窗；同一時間只允許一個程式執行。
"""
import sys
import os
from typing import Optional

sys.path.insert(0, os.path.dirname(__file__))

from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QProxyStyle, QStyleFactory
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from core.constants import APP_ID, INSTANCE_LOCK_FILE, LOCALE_ZH_TW, LOG_DIR, PREFERENCES_FILE, TEMP_DIR
from core.launch_env import qt_environment_defaults
from core.locale import t, set_locale
from core.models import Project
from services.activity_log import ActivityLog
from services.compression_service import CompressionService
from services.file_service import FileService
from services.instance_lock import InstanceLock
from services.preferences_service import PreferencesService


class _AppStyle(QProxyStyle):
    """子選單不與父選單重疊"""

    def __init__(self):
        super().__init__(QStyleFactory.create("Fusion"))

    def pixelMetric(self, metric, option=None, widget=None):
        from PySide6.QtWidgets import QStyle
        if metric == QStyle.PM_SubMenuOverlap:
            return 6
        return super().pixelMetric(metric, option, widget)


def main():
    """啟動程式：載入偏好與語言、建立 QApplication，取得單一實例鎖後開主視窗並進入事件迴圈"""
    prepare_application()
    prefs = PreferencesService(PREFERENCES_FILE)
    prefs.load()
    language = prefs.get("language") or LOCALE_ZH_TW
    set_locale(language)
    app = QApplication(sys.argv)
    app.setStyle(_AppStyle())
    _apply_dark_theme(app)
    lock = InstanceLock(INSTANCE_LOCK_FILE)
    window = launch(prefs, lock)
    if window is None:
        return
    try:
        code = app.exec()
    finally:
        lock.release()
    sys.exit(code)


def prepare_application() -> None:
    """建立 QApplication 前的設定：補上缺的 Qt 環境變數，設定應用程式名稱與 desktop file name

    環境變數要在 QApplication 建立前設好，Qt 才會依它挑輸入法外掛。
    """
    os.environ.update(qt_environment_defaults(os.environ, sys.platform))
    QGuiApplication.setApplicationName(APP_ID)
    QGuiApplication.setDesktopFileName(APP_ID)


def launch(
    prefs: PreferencesService, lock: InstanceLock, temp_dir: str = TEMP_DIR, log_dir: str = LOG_DIR,
) -> Optional[QMainWindow]:
    """取得單一實例鎖後清掉暫存資料夾的殘檔與過期的紀錄檔、開啟主視窗，並排定檢查上次中斷的重新命名

    鎖要在檢查進行中紀錄、清暫存資料夾與紀錄檔之前取得：另一個程式執行中時，它的進行中紀錄不是上次中斷留下的，
    暫存資料夾裡的檔也還在用，紀錄檔也還在寫。所以鎖檔建不起來或開不了時也不繼續，顯示錯誤後結束。

    Args:
        prefs: 已載入的使用者偏好
        lock: 單一實例鎖
        temp_dir: 暫存資料夾
        log_dir: 紀錄檔資料夾

    Returns:
        主視窗；已有另一個程式在執行，或鎖檔建不起來、開不了時，提示後回傳 None
    """
    try:
        acquired = lock.acquire()
    except OSError as e:
        QMessageBox.critical(None, t("app.title"), t("app.lock_failed", error=e))
        return None
    if not acquired:
        QMessageBox.information(None, t("app.title"), t("app.already_running"))
        return None
    from ui.main_window import MainWindow
    compressor = CompressionService(FileService(), temp_dir)
    compressor.clear_leftovers()
    log = ActivityLog(FileService(), log_dir)
    log.remove_expired()
    window = MainWindow(prefs, compressor=compressor, log=log)
    window.setWindowTitle(t("app.title"))
    window.resize(1200, 800)
    window.setMinimumSize(900, 600)
    window.show()
    QTimer.singleShot(0, window.prompt_pending_recovery)
    return window


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
    check_svg = os.path.join(os.path.dirname(__file__), "assets", "check.svg").replace("\\", "/")
    qss = _QSS + (
        f'\nQCheckBox::indicator:checked {{ image: url("{check_svg}"); }}'
    )
    app.setStyleSheet(qss)


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
    border: 1px solid #363848;
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
    border-bottom-left-radius: 0; border-bottom-right-radius: 0;
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
