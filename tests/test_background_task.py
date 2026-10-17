# -*- coding: utf-8 -*-
"""
背景工作的生命週期測試（offscreen）

放棄（abandon）之後，背景執行緒不再碰這個物件：父物件（主視窗）先被刪掉，工作之後才結束也不會出錯。
"""
import os
import sys
import threading
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import shiboken6
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from ui.background_task import BackgroundTask


class TestAbandonedTask(unittest.TestCase):
    """關閉程式時等不到工作停下：放棄它、刪掉父物件，工作之後才結束"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.errors = []
        hook = threading.excepthook
        threading.excepthook = lambda args: self.errors.append(args.exc_value)
        self.addCleanup(setattr, threading, "excepthook", hook)

    def test_work_that_ends_after_its_parent_is_deleted_reports_nothing(self):
        release = threading.Event()
        workers = []

        def work(should_cancel, report):
            """等測試放行才結束的工作"""
            workers.append(threading.current_thread())
            release.wait(5)
            report("late progress")
            return "late result"
        parent = QObject()
        task = BackgroundTask(work, parent)
        task.start()
        task.cancel()
        self.assertFalse(task.wait(0.05))
        task.abandon()
        shiboken6.delete(parent)
        release.set()
        workers[0].join(5)
        QApplication.processEvents()
        self.assertFalse(workers[0].is_alive())
        self.assertEqual(self.errors, [])

    def test_work_that_raises_after_its_parent_is_deleted_reports_nothing(self):
        release = threading.Event()
        workers = []

        def work(should_cancel, report):
            """等測試放行才結束的工作"""
            workers.append(threading.current_thread())
            release.wait(5)
            raise OSError("disk gone")
        parent = QObject()
        task = BackgroundTask(work, parent)
        task.start()
        task.abandon()
        shiboken6.delete(parent)
        release.set()
        workers[0].join(5)
        QApplication.processEvents()
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
