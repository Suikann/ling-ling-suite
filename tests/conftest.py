# -*- coding: utf-8 -*-
"""
pytest 共用設定

把使用者資料目錄導到暫存目錄，測試不會把紀錄寫進真實的 ~/.config/LingLingSuite/。
core.constants 在匯入時就決定路徑，所以必須在任何測試模組匯入前設定環境變數。
"""
import atexit
import os
import shutil
import tempfile

_data_dir = tempfile.mkdtemp(prefix="lingling-test-")
os.environ["XDG_CONFIG_HOME"] = _data_dir
os.environ["APPDATA"] = _data_dir
atexit.register(shutil.rmtree, _data_dir, True)
