# -*- coding: utf-8 -*-
"""
收回的接線測試（offscreen）

收回對話框照服務算出的預覽顯示：依公演分組，建議收回的預設勾選，還要演的預設不勾、勾了才出現紅色警告；
別人放的檔排前面。譜庫視窗的「發譜紀錄」按「收回…」開預覽，按「收回」或「勾選的不收回」後寫進 HuaXia 與發譜紀錄。
不測畫面。
"""
import datetime
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.locale import t
from core.publish_log import PublishAction
from test_publish_recall import RecallTestCase
from test_publish_retract_window import TestRetractFromCatalogWindow
from tests.test_main_window import answering_prompts, wait_until
from ui.publish_recall_dialog import RecallDialog
from ui.publish_retract_dialog import PublishLogPanel


class DialogTestCase(RecallTestCase):
    """收回對話框（情境見 RecallTestCase）"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def open_dialog(self) -> RecallDialog:
        """以現在的預覽開收回對話框"""
        dialog = RecallDialog(self.recaller.preview(self.catalog))
        self.addCleanup(dialog.deleteLater)
        return dialog


class TestRecallDialog(DialogTestCase):
    """建議收回的預設勾選；還要演的預設不勾，勾了才出現紅色警告；別人放的檔排前面"""

    def test_a_piece_whose_concerts_are_over_is_checked_and_no_warning_shows(self):
        dialog = self.open_dialog()
        [item] = dialog.piece_items
        self.assertEqual(item.checkState(0), Qt.Checked)
        self.assertEqual(item.parent().text(0), t(
            "recall.group.ended", event="期末音樂會", date=dialog.preview.pieces[0].concert.ends_at.astimezone().date().isoformat(),
        ))
        self.assertTrue(dialog.warning.isHidden())
        self.assertEqual([piece.piece_id for piece in dialog.chosen()], [self.fenghuo])
        self.assertTrue(dialog.confirm.isEnabled())

    def test_a_piece_still_to_play_is_unchecked_and_checking_it_warns(self):
        self.still_playing(days=5)
        dialog = self.open_dialog()
        [item] = dialog.piece_items
        self.assertEqual(item.checkState(0), Qt.Unchecked)
        start = (self.now + datetime.timedelta(days=5)).astimezone().date().isoformat()
        self.assertIn(t("recall.still_playing", event="校慶音樂會", date=start, days=5), item.text(2))
        self.assertTrue(dialog.warning.isHidden())
        self.assertFalse(dialog.confirm.isEnabled())
        item.setCheckState(0, Qt.Checked)
        self.assertFalse(dialog.warning.isHidden())
        self.assertEqual(dialog.warning.text(), t(
            "recall.warning", pieces=t("recall.warning.piece", piece="長城隨想－烽火操", event="校慶音樂會", date=start),
            count=6,
        ))
        item.setCheckState(0, Qt.Unchecked)
        self.assertTrue(dialog.warning.isHidden())

    def test_scores_others_put_come_first_and_are_marked(self):
        dialog = self.open_dialog()
        [item] = dialog.piece_items
        sources = [item.child(row).text(2) for row in range(item.childCount())]
        self.assertEqual(sources[0], t("recall.source.others"))
        self.assertEqual(set(sources[1:]), {t("recall.source.lingling")})
        self.assertNotEqual(item.child(0).foreground(0).color(), item.child(1).foreground(0).color())
        self.assertEqual(item.child(0).text(1), "琵琶")

    def test_keep_records_the_checked_pieces(self):
        dialog = self.open_dialog()
        dialog.keep_button.click()
        self.assertEqual(dialog.result(), RecallDialog.Accepted)
        self.assertIs(dialog.decision, PublishAction.KEEP)
        self.assertEqual([piece.piece_id for piece in dialog.chosen()], [self.fenghuo])

    def test_nothing_to_recall_disables_both_buttons(self):
        self.recaller.recall([self.piece()], self.catalog)
        dialog = self.open_dialog()
        self.assertEqual(dialog.piece_items, [])
        self.assertFalse(dialog.confirm.isEnabled())
        self.assertFalse(dialog.keep_button.isEnabled())


class TestRecallFromCatalogWindow(RecallTestCase):
    """譜庫視窗的「發譜紀錄」按「收回…」開預覽，按「收回」或「勾選的不收回」後寫進 HuaXia 與發譜紀錄"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    open_catalog = TestRetractFromCatalogWindow.open_catalog

    def press(self, button_name: str):
        """按「收回…」，在收回對話框勾烽火操、按 button_name 那顆按鈕；回傳（對話框顯示的曲目，譜庫視窗的提示）"""
        window = self.open_catalog()
        panel = window.findChild(PublishLogPanel)
        shown = []

        def answer(dialog):
            shown.append([piece.piece_id for piece in dialog.preview.pieces])
            for item in dialog.piece_items:
                item.setCheckState(0, Qt.Checked)
            getattr(dialog, button_name).click()
            return dialog.result()

        with mock.patch.object(RecallDialog, "exec", answer), answering_prompts() as prompts:
            self.assertTrue(panel.recall_button.isEnabled())
            panel.recall_button.click()
            wait_until(lambda: bool(prompts))
        wait_until(lambda: any(p.table.rowCount() == 2 for p in window.findChildren(PublishLogPanel)))
        return shown, prompts

    def test_recalling_from_the_log_deletes_every_score_and_writes_a_row(self):
        shown, prompts = self.press("confirm")
        self.assertEqual(shown, [[self.fenghuo]])
        self.assertEqual(self.scores(self.fenghuo), [])
        self.assertEqual(len(self.log(PublishAction.RECALL)), 1)
        self.assertIn(t("recall.done", count=1, scores=6), prompts[0][1])

    def test_keeping_from_the_log_writes_a_row_and_changes_nothing_on_huaxia(self):
        before = self.tables()
        shown, prompts = self.press("keep_button")
        self.assertEqual(self.tables(), before)
        self.assertEqual(len(self.log(PublishAction.KEEP)), 1)
        self.assertIn(t("recall.kept", count=1), prompts[0][1])

    def test_the_log_names_recall_and_keep(self):
        self.recaller.keep([self.piece()], self.catalog)
        self.recaller.recall([self.piece()], self.catalog)
        panel = self.open_catalog().findChild(PublishLogPanel)
        self.assertEqual(panel.table.rowCount(), 3)
        actions = [panel.table.item(row, 2).text() for row in range(3)]
        self.assertEqual(actions, [
            t("publish_log.action.recall"), t("publish_log.action.keep"), t("publish_log.action.publish"),
        ])
        self.assertIn("長城隨想－烽火操", panel.table.item(0, 3).text())
        panel.table.selectRow(0)
        self.assertFalse(panel.retract_button.isEnabled())


class TestComposedTexts(unittest.TestCase):
    """以變數組出的鍵都有中英文（tests/test_locale.py 掃不到）"""

    def test_log_actions_and_catalog_window_prompts_have_texts_in_both_languages(self):
        from core.locale import get_available_locales, get_keys
        keys = [f"publish_log.action.{action.value}" for action in (PublishAction.RECALL, PublishAction.KEEP)]
        keys += [
            f"{action}.{name}" for action in ("retract", "recall")
            for name in ("title_short", "sign_in_first", "unreachable", "failed")
        ]
        for language in get_available_locales():
            missing = [key for key in keys if key not in get_keys(language)]
            self.assertEqual(missing, [], language)


if __name__ == "__main__":
    unittest.main()
