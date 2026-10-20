# -*- coding: utf-8 -*-
"""
pytest 共用設定

把使用者資料目錄導到暫存目錄，測試不會把紀錄寫進真實的 ~/.config/LingLingSuite/。
core.constants 在匯入時就決定路徑，所以必須在任何測試模組匯入前設定環境變數。

關掉自動垃圾回收，改在主執行緒回收：自動回收在哪個執行緒配置物件時達到門檻就在那裡執行，
測試留下的 Qt 物件（沒有父物件的對話框、主視窗）若在背景執行緒（例如壓縮）被解構，
計時器不會從主執行緒取消，事件迴圈之後觸發到已釋放的物件就 segfault。
"""
import atexit
import gc
import os
import shutil
import tempfile

import pytest

_data_dir = tempfile.mkdtemp(prefix="lingling-test-")
os.environ["XDG_CONFIG_HOME"] = _data_dir
os.environ["APPDATA"] = _data_dir
atexit.register(shutil.rmtree, _data_dir, True)

gc.disable()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    """測試收尾（含釋放測試案例）之後，在主執行緒回收它留下的循環參照

    自動回收關著，測試期間新建的物件都還在最年輕的一代，只回收這一代就夠，也比完整回收快得多。
    """
    yield
    gc.collect(0)


def pytest_sessionfinish(session, exitstatus):
    """全部測試跑完、QApplication 還在時，在主執行緒做一次完整回收"""
    gc.collect()
