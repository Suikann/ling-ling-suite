# -*- coding: utf-8 -*-
"""
測試期間的垃圾回收只在主執行緒

Python 的自動垃圾回收在哪個執行緒配置物件時達到門檻，就在那個執行緒回收。
測試留下的 Qt 物件（沒有父物件的對話框與主視窗）若在背景執行緒（例如壓縮）被回收，
解構會在主執行緒以外執行，計時器沒從主執行緒取消，事件迴圈之後觸發到已釋放的物件就 segfault。
tests/conftest.py 因此關掉自動回收，每個測試結束後才在主執行緒回收；這裡釘住這件事。
"""
import threading
import unittest
import weakref

from PySide6.QtCore import QObject


class TestGarbageCollectionThread(unittest.TestCase):
    """背景執行緒大量配置物件時，不會回收測試留下的 Qt 物件"""

    def test_qt_garbage_is_not_collected_on_a_background_thread(self):
        collected_on = []
        garbage = QObject()
        garbage.cycle = garbage
        weakref.finalize(garbage, lambda: collected_on.append(threading.current_thread().name))
        del garbage
        worker = threading.Thread(target=lambda: [[] for _ in range(100_000)], name="worker")
        worker.start()
        worker.join()
        self.assertNotIn("worker", collected_on)


if __name__ == '__main__':
    unittest.main()
