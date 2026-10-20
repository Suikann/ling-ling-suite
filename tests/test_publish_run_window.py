# -*- coding: utf-8 -*-
"""
三段式發譜的接線測試（offscreen）

在發譜預覽按下「發譜」：先把新建曲目的編號與預覽的選擇存進專案檔，再在背景發譜，不擋主視窗、不鎖操作；
狀態列有停止鈕，主視窗下方是逐行紀錄，完成時列出沒發成的群組與原因，「重新發譜沒發成的群組」只對它們開預覽；
發譜進行中關閉時問要不要中斷；「開始發譜」寫不進譜庫時不發並顯示提示列。主視窗接假 Google 與假 HuaXia，不測畫面。
"""
import os
import sys
import threading
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QLabel, QMessageBox, QPlainTextEdit, QPushButton, QTableWidget

from core.catalog_errors import CatalogErrorKind
from core.locale import t
from services.project_service import ProjectService
from tests.test_main_window import answering_prompts, wait_until
from tests.test_publish_files_window import FilesWindowTestCase, go_to
from tests.test_publish_window import button, choose, previewing
from ui.catalog_error_bar import CatalogErrorBar
from ui.instrumentation_table import InstrumentationTable


class PublishRunWindowTestCase(FilesWindowTestCase):
    """「烽火操」對到 HuaXia 的「長城隨想－烽火操」，按下「發譜」就照預覽發"""

    def press_publish(self, before=lambda: None, new_piece: bool = False):
        """按「發譜」、走完預覽（new_piece 時新建曲目）後按下預覽的「發譜」；before 在按下之前呼叫"""
        def operate(dialog):
            if new_piece:
                choose(dialog, "烽火操", "new")
                for _ in range(4):
                    button(dialog, "publish.next").click()
            else:
                go_to(dialog, 4)
            before()
            button(dialog, "publish.publish").click()
        return self.publish(operate=operate)

    def hold_publishing(self) -> threading.Event:
        """發譜在背景寫「開始發譜」之前卡住，直到回傳的 Event 被 set"""
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.google.before_request(self.google.requests + 1, lambda: gate.wait(10))
        return gate

    def finish(self):
        """等背景的發譜結束"""
        wait_until(lambda: not self.window._publishing.busy)

    def scores(self):
        """「長城隨想－烽火操」登記的樂譜"""
        return [row for row in self.huaxia.tables["scores"] if row["piece_id"] == self.fenghuo]

    def log_text(self) -> str:
        """主視窗下方這次發譜的逐行紀錄"""
        return self.window.findChild(QPlainTextEdit, "publish_log").toPlainText()


class TestPublishingInBackground(PublishRunWindowTestCase):
    """發譜在背景進行：主視窗照常可以編輯專案；做完後 HuaXia 上有這次的樂譜，逐行紀錄寫出經過"""

    def test_the_project_stays_editable_while_publishing(self):
        gate = []
        self.press_publish(before=lambda: gate.append(self.hold_publishing()))
        self.assertTrue(self.window._publishing.busy)
        self.assertTrue(self.window.findChild(InstrumentationTable).isEnabled())
        self.assertTrue(self.window._rename_btn.isEnabled())
        self.assertTrue(self.window._tab_widget.widget(1).isEnabled())
        self.assertFalse(self.window._publish_btn.isEnabled())
        cancel = next(b for b in self.window.findChildren(QPushButton) if b.text() == t("publish.run.cancel"))
        self.assertFalse(cancel.isHidden())
        self.window.project.update_group(self.window.project.groups[0], composer="發譜中改的")
        gate[0].set()
        self.finish()
        self.assertEqual(len(self.scores()), 3)
        self.assertIn(t("publish.run.line.switched", group="烽火操"), self.log_text())
        self.assertTrue(self.window._publish_btn.isEnabled())
        self.assertTrue(cancel.isHidden())

    def test_the_new_piece_number_and_choices_are_saved_before_publishing(self):
        gate = []
        self.press_publish(before=lambda: gate.append(self.hold_publishing()), new_piece=True)
        self.assertFalse(self.window.project.is_modified())
        saved = ProjectService().load_project(self.path).groups[0].huaxia_link
        self.assertTrue(saved.piece_id)
        self.assertEqual(saved.new_piece.title, "烽火操")
        gate[0].set()
        self.finish()
        self.assertEqual([row["id"] for row in self.huaxia.tables["pieces"] if row["title"] == "烽火操"], [saved.piece_id])


class TestRememberingCreatedParts(PublishRunWindowTestCase):
    """發成之後，這次新建的聲部記成那些聲部的配對，下次發譜照它對"""

    def test_the_new_parts_are_remembered_in_the_project(self):
        self.press_publish(new_piece=True)
        self.finish()
        link = self.window.project.groups[0].huaxia_link
        created = {
            row["name"]: row["id"] for row in self.huaxia.tables["piece_parts"] if row["piece_id"] == link.piece_id
        }
        self.assertEqual(link.part_ids, created)
        self.assertEqual(set(created), {"二胡 I", "琵琶"})


class TestFinishing(PublishRunWindowTestCase):
    """完成時列出沒發成的群組與原因，附「重新發譜沒發成的群組」（只對它們開預覽）；「開始發譜」寫不進譜庫時不發並顯示提示列"""

    def test_a_group_that_did_not_publish_is_listed_with_its_reason(self):
        self.huaxia.fail_switch(self.fenghuo)
        self.press_publish()
        self.finish()
        failed = self.window.findChild(QLabel, "publish_failed")
        self.assertFalse(failed.isHidden())
        self.assertIn("烽火操", failed.text())
        self.assertIn(t("publish.run.failure.switch"), failed.text())
        self.assertFalse(self.window.findChild(QPushButton, "publish_failed_again").isHidden())

    def test_publishing_the_failed_groups_again_previews_only_them(self):
        self.huaxia.fail_switch(self.fenghuo)
        self.press_publish()
        self.finish()
        self.assertEqual(self.preview_groups(), ["烽火操", "春江花月夜"])
        with answering_prompts(), previewing() as opened:
            self.window.findChild(QPushButton, "publish_failed_again").click()
        [dialog] = opened
        self.assertEqual(self.piece_rows(dialog), ["烽火操"])

    def test_publishing_again_after_the_failed_groups_were_deleted_says_so(self):
        self.huaxia.fail_switch(self.fenghuo)
        self.press_publish()
        self.finish()
        self.window.project.delete_group(self.window.project.groups[0])
        with answering_prompts() as shown, previewing() as opened:
            self.window.findChild(QPushButton, "publish_failed_again").click()
        self.assertEqual(opened, [])
        self.assertEqual([text for _, text in shown], [t("publish.run.failed_groups_gone")])

    def test_no_button_to_publish_again_when_every_group_published(self):
        self.press_publish()
        self.finish()
        self.assertTrue(self.window.findChild(QLabel, "publish_failed").isHidden())
        self.assertTrue(self.window.findChild(QPushButton, "publish_failed_again").isHidden())

    def preview_groups(self) -> list:
        """按主視窗的「發譜」時預覽列出的群組（開了就取消）"""
        with answering_prompts(), previewing() as opened:
            self.publish_button().click()
        return self.piece_rows(opened[0])

    @staticmethod
    def piece_rows(dialog) -> list:
        """預覽「曲目」步列出的群組"""
        table = dialog.findChild(QTableWidget, "piece_step")
        return [table.item(row, 0).text() for row in range(table.rowCount())]

    def test_when_the_start_cannot_be_logged_nothing_is_published(self):
        self.press_publish(before=lambda: self.google.fail_next(CatalogErrorKind.OFFLINE, times=3))
        self.finish()
        self.assertEqual(self.scores(), [])
        self.assertFalse(self.window.findChild(CatalogErrorBar).isHidden())


class TestClosing(PublishRunWindowTestCase):
    """發譜進行中關閉時問要不要中斷，並說明重發會從中斷處接著做"""

    def test_closing_while_publishing_asks_and_staying_keeps_it_running(self):
        gate = []
        self.press_publish(before=lambda: gate.append(self.hold_publishing()))
        with answering_prompts(QMessageBox.No) as shown:
            self.assertFalse(self.window.close())
        self.assertEqual(shown, [(t("dialog.close"), t("publish.run.close.message"))])
        gate[0].set()
        self.finish()
        self.assertEqual(len(self.scores()), 3)


if __name__ == '__main__':
    unittest.main()
