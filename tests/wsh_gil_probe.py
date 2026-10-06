# -*- coding: utf-8 -*-
"""
[DEBUG-wsh] 暫時的探針：量測背景執行緒在主執行緒各種等候方式下能跑多少

比較 time.sleep、QTest.qWait 迴圈、processEvents 加 time.sleep 迴圈時，背景執行緒的計數，
以及背景執行緒在主執行緒以 QTest.qWait 等候時算繪縮圖要花多久。
"""
import os
import sys
import tempfile
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PyPDF2 import PdfWriter
from PySide6.QtCore import QCoreApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication


def spin_counts(app):
    count = [0]
    stop = [False]

    def spin():
        while not stop[0]:
            count[0] += 1

    worker = threading.Thread(target=spin, daemon=True)
    worker.start()
    time.sleep(0.3)
    results = {}
    count[0] = 0
    time.sleep(1.0)
    results["time.sleep"] = count[0]
    count[0] = 0
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        QTest.qWait(20)
    results["qWait loop"] = count[0]
    count[0] = 0
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.02)
    results["processEvents+sleep loop"] = count[0]
    stop[0] = True
    worker.join()
    print("[DEBUG-wsh] spin counts per second:", results, flush=True)


def render_time(waiter, label):
    folder = tempfile.mkdtemp()
    path = os.path.join(folder, "m.pdf")
    writer = PdfWriter()
    for _ in range(4):
        writer.add_blank_page(width=100, height=100)
    with open(path, "wb") as f:
        writer.write(f)
    done = {}

    def render():
        start = time.monotonic()
        from services.pdf_service import render_page_thumbnails
        render_page_thumbnails(path, max_width=150)
        done["seconds"] = time.monotonic() - start

    start = time.monotonic()
    worker = threading.Thread(target=render, daemon=True)
    worker.start()
    while "seconds" not in done and time.monotonic() - start < 60:
        waiter()
    print(f"[DEBUG-wsh] render while main waits with {label}: {done.get('seconds', 'not finished in 60 s')}", flush=True)


if __name__ == "__main__":
    app = QApplication.instance() or QApplication([])
    spin_counts(app)
    render_time(lambda: QTest.qWait(20), "QTest.qWait(20), first import")
    render_time(lambda: QTest.qWait(20), "QTest.qWait(20)")
    render_time(lambda: (QCoreApplication.processEvents(), time.sleep(0.02)), "processEvents+sleep")
