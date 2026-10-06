# -*- coding: utf-8 -*-
"""
分割對話框測試（offscreen）

測訊息組裝與對話框到分割模組、主視窗的接線，不測畫面；以 QT_QPA_PLATFORM=offscreen 執行，不需要顯示器。
從主視窗開啟的對話框以替換 exec 的方式模擬使用者操作：選檔、點縮圖標記分割點、按執行分割。
"""
import os
import sys
import time
import unittest
from contextlib import contextmanager
from typing import Callable, List
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PyPDF2 import PdfWriter
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QComboBox, QDialog, QLabel, QLineEdit, QListWidget, QMessageBox, QPushButton, QTabWidget,
)

from core.locale import get_locale, set_locale, t
from core.models import FileInfo, Group, Project, WorkspaceOwner
from services.file_service import FileService
from services.project_service import ProjectService
from services.split_service import SplitRequest, SplitSegment, SplitService
from ui.split_dialog import SplitPdfDialog
from tests.test_main_window import MainWindowTestCase, _settle, answering_prompts, button_in


class TestResplitMessage(unittest.TestCase):
    """重新分割確認訊息：同專案不點名；他專案點名，找不到時加註"""

    def setUp(self):
        self._locale = get_locale()
        set_locale("zh_TW")

    def tearDown(self):
        set_locale(self._locale)

    def test_same_project_does_not_name_an_owner(self):
        text = SplitPdfDialog._resplit_message(3, None)
        self.assertIn("3 個分譜", text)
        self.assertNotIn("屬於專案", text)

    def test_other_project_is_named(self):
        text = SplitPdfDialog._resplit_message(3, WorkspaceOwner("/proj/冬季.llproj", exists=True))
        self.assertIn("屬於專案「/proj/冬季.llproj」", text)
        self.assertNotIn("找不到", text)

    def test_missing_other_project_is_flagged(self):
        text = SplitPdfDialog._resplit_message(3, WorkspaceOwner("/proj/冬季.llproj", exists=False))
        self.assertIn("屬於專案「/proj/冬季.llproj」（找不到）", text)


@contextmanager
def splitting(operate: Callable[[QDialog], None]):
    """模擬使用者在分割對話框的操作

    替換 SplitPdfDialog.exec：對話框開啟時呼叫 operate，之後若對話框還開著就以取消關閉。
    operate 拋出的例外（在 Qt 事件裡會被吞掉）與對話框沒有開啟，都在離開區塊時判定失敗。

    Args:
        operate: 接收對話框、操作其元件的函式
    """
    opened = []
    errors = []

    def fake_exec(dialog):
        opened.append(dialog)
        try:
            operate(dialog)
        except Exception as e:
            errors.append(e)
        if dialog.isVisible() or dialog.result() != QDialog.Accepted:
            dialog.reject()
        return dialog.result()

    with mock.patch.object(SplitPdfDialog, "exec", fake_exec):
        yield
    if errors:
        raise errors[0]
    if not opened:
        raise AssertionError("分割對話框沒有開啟")


def _wait_until(condition: Callable[[], bool], timeout: float = 10.0):
    """處理事件直到 condition 成立（縮圖在背景執行緒產生，完成時以 signal 通知）"""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("等候逾時")
        QTest.qWait(20)


def _make_pdf(path: str, pages: int) -> str:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=100, height=100)
    with open(path, "wb") as f:
        writer.write(f)
    return path


class TestSplitFromMainWindow(MainWindowTestCase):
    """從主視窗開分割對話框：分段帶入目前群組的聲部名稱，分割完成後專案立即反映"""

    def setUp(self):
        super().setUp()
        self.merged = _make_pdf(os.path.join(self.temp_dir, "merged.pdf"), 4)
        self.group = Group(name="g", files=[FileInfo(self.merged, "merged.pdf")], score_label="總譜")
        self.open_from_menu(Project(groups=[self.group]))

    def type_voices(self, *voices: str):
        """在目前群組的樂器表逐一輸入聲部"""
        entry = next(
            e for e in self.window.findChildren(QLineEdit) if e.placeholderText() == t("instrument.placeholder")
        )
        for voice in voices:
            QTest.keyClicks(entry, voice)
            QTest.keyClick(entry, Qt.Key_Return)

    @staticmethod
    def load_merged(dialog: QDialog):
        """在對話框選取合併譜，等縮圖出現"""
        combo = next(c for c in dialog.findChildren(QComboBox) if c.findText("merged.pdf") >= 0)
        combo.setCurrentIndex(combo.findText("merged.pdf"))
        _wait_until(lambda: button_in(dialog, QPushButton, t("split.execute")).isEnabled())

    @staticmethod
    def mark_split_before_page(dialog: QDialog, page: int):
        """點第 page 頁（從 1 起算）的縮圖，標記分割點"""
        _settle()
        thumbnails = [label for label in dialog.findChildren(QLabel) if not label.pixmap().isNull()]
        QTest.mouseClick(thumbnails[page - 1], Qt.LeftButton)

    @staticmethod
    def segment_names(dialog: QDialog) -> List[str]:
        _settle()
        return [e.text() for e in dialog.findChildren(QLineEdit)]

    def shown_parts(self) -> List[str]:
        """目前分頁的分譜清單列出的檔名"""
        _settle()
        listing = self.window.findChild(QTabWidget).currentWidget().findChild(QListWidget)
        return [listing.item(i).text().rsplit("  |  ", 1)[-1] for i in range(listing.count())]

    def test_segments_take_the_voices_typed_for_the_current_group(self):
        self.type_voices("Flute", "Oboe")
        seen = {}

        def operate(dialog):
            self.load_merged(dialog)
            self.mark_split_before_page(dialog, 3)
            seen["names"] = self.segment_names(dialog)

        with splitting(operate):
            self.trigger_menu(t("menu.tools.split_pdf"))
        self.assertEqual(seen["names"], ["Flute", "Oboe"])

    def test_split_marks_unsaved_and_the_group_tab_lists_the_new_parts(self):
        self.type_voices("Flute", "Oboe")
        with answering_prompts():
            self.trigger_menu(t("menu.file.save"))
        self.assertFalse(self.is_marked_unsaved())

        def operate(dialog):
            self.load_merged(dialog)
            self.mark_split_before_page(dialog, 3)
            with answering_prompts():
                button_in(dialog, QPushButton, t("split.execute")).click()

        with splitting(operate):
            self.trigger_menu(t("menu.tools.split_pdf"))
        self.assertTrue(self.is_marked_unsaved())
        self.assertEqual(self.shown_parts(), ["Flute.pdf", "Oboe.pdf"])

    def test_segments_with_the_same_name_are_named_in_the_error_and_nothing_is_written(self):
        shown = []

        def operate(dialog):
            self.load_merged(dialog)
            self.mark_split_before_page(dialog, 3)
            _settle()
            for entry in dialog.findChildren(QLineEdit):
                entry.clear()
                QTest.keyClicks(entry, "Flute")
            with answering_prompts() as prompts:
                button_in(dialog, QPushButton, t("split.execute")).click()
            shown.extend(prompts)

        with splitting(operate):
            self.trigger_menu(t("menu.tools.split_pdf"))
        segments = t("split.segment_separator").join(["1", "2"])
        error = t("split.error.duplicate_names", segments=segments, name="Flute.pdf")
        self.assertEqual(shown, [(t("dialog.error"), error)])
        workspace_folder = SplitService(FileService(), self.window.workspace_service).output_folder(self.merged)
        self.assertFalse(os.path.exists(workspace_folder))
        self.assertFalse(self.is_marked_unsaved())

    def test_resplitting_another_projects_parts_names_that_project_and_asks_yes_or_no(self):
        theirs = os.path.join(self.temp_dir, "theirs.llproj")
        ProjectService().save_project(Project(), theirs)
        earlier = SplitService(FileService(), self.window.workspace_service)
        earlier.execute(earlier.check(SplitRequest(
            self.merged, [SplitSegment(0, 1, "Flute"), SplitSegment(2, 3, "Oboe")], project_path=theirs,
        )))
        shown = []

        def operate(dialog):
            self.load_merged(dialog)
            with answering_prompts(QMessageBox.No) as prompts:
                button_in(dialog, QPushButton, t("split.execute")).click()
            shown.extend(prompts)

        with splitting(operate):
            self.trigger_menu(t("menu.tools.split_pdf"))
        owner = "\n" + t("split.resplit_owner", project=theirs, missing="")
        self.assertEqual(shown, [(t("dialog.warning"), t("split.resplit_confirm", count=2, owner=owner))])
        self.assertFalse(self.is_marked_unsaved())


if __name__ == '__main__':
    unittest.main()
