# -*- coding: utf-8 -*-
"""
撤回的接線測試（offscreen）

撤回對話框照服務算出的預覽顯示：只能勾群組（逐聲部的對照表不能改），上方各類的數量與對照表一致，底部寫出撤回後
沒有譜的聲部數。譜庫視窗工具列的「發譜紀錄」列出每次發譜與撤回，選一次發譜按「撤回…」開預覽，按「撤回」後
撤掉 HuaXia 上的東西、寫一列「撤回」、寫出結果。不測畫面。
"""
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton

from core.locale import t
from core.publish_log import PublishAction
from services.activity_log import ActivityLog
from services.file_service import FileService
from services.google_auth_service import GoogleAuthService
from services.preferences_service import PreferencesService
from services.retract_service import RetractEffect, RetractReason
from services.secure_storage import SecureStorage
from test_publish_retract import RetractTestCase
from test_publish_run import TwoPiecesTestCase
from tests.fake_browser import CallbackBrowser
from tests.fake_google import FakeTokenEndpoint, write_client_secrets
from tests.memory_keyring import MemoryKeyring
from tests.test_main_window import answering_prompts, wait_until
from ui.catalog_window import CatalogWindow
from ui.publish_retract_dialog import PublishLogPanel, RetractDialog


class DialogTestCase(RetractTestCase):
    """撤回的預覽對話框（情境見 RetractTestCase）"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        super().setUp()
        self.retract_preview = self.retracter.preview(self.entry())
        self.dialog = RetractDialog(self.retract_preview)
        self.addCleanup(self.dialog.deleteLater)


class TestRetractDialog(DialogTestCase):
    """只能勾群組；各類數量與對照表一致；底部寫出撤回後沒有譜的聲部數"""

    def test_only_groups_can_be_checked(self):
        item = self.dialog.groups.item(0)
        self.assertTrue(item.flags() & Qt.ItemIsUserCheckable)
        for page in self.dialog.pages:
            table = page.table
            for row in range(table.rowCount()):
                for column in range(table.columnCount()):
                    flags = table.item(row, column).flags()
                    self.assertFalse(flags & (Qt.ItemIsUserCheckable | Qt.ItemIsEditable), (row, column))

    def test_counts_match_the_table(self):
        [group] = self.retract_preview.groups
        [page] = self.dialog.pages
        in_table = {effect: 0 for effect in RetractEffect}
        for row in range(page.table.rowCount()):
            effect = page.table.item(row, 0).data(Qt.UserRole)
            if effect is not None:
                in_table[effect] += 1
        self.assertEqual(in_table, group.counts())
        self.assertEqual([label.text() for label in page.counts], [
            t("retract.count", kind=t(f"retract.effect.{effect.value}"), count=in_table[effect])
            for effect in RetractEffect
        ])
        self.assertEqual(in_table[RetractEffect.DELETE_PART], 1)
        last = page.table.rowCount() - 1
        self.assertEqual(page.table.item(last, 0).text(), t("retract.events"))
        self.assertEqual(page.table.item(last, 2).text(), t("retract.after.untouched"))

    def test_the_bottom_tells_how_many_parts_are_left_without_scores(self):
        self.assertEqual(self.dialog.groups.item(0).checkState(), Qt.Checked)
        self.assertEqual(self.dialog.missing.text(), t("retract.missing", count=3))
        self.assertEqual(self.dialog.confirm.text(), t("retract.confirm", count=1))
        self.assertEqual(self.dialog.chosen(), [self.group.id])
        self.dialog.groups.item(0).setCheckState(Qt.Unchecked)
        self.assertFalse(self.dialog.confirm.isEnabled())
        self.assertTrue(self.dialog.missing.isHidden())
        self.assertEqual(self.dialog.chosen(), [])


class TestGroupsNotPublished(TwoPiecesTestCase):
    """沒發成的群組不能勾，頁面寫出原因"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_a_group_that_was_not_published_cannot_be_checked(self):
        from core.publish_log import publish_entries
        from services.retract_service import RetractService
        self.huaxia.fail_switch(self.moli)
        outcome = self.run_publish(self.press_publish())
        entry = next(e for e in publish_entries(self.catalog.publish_log()) if e.publish_id == outcome.publish_id)
        dialog = RetractDialog(RetractService(self.auth).preview(entry))
        self.addCleanup(dialog.deleteLater)
        names = [dialog.groups.item(row).text().split("\n")[0] for row in range(dialog.groups.count())]
        moli = names.index("茉莉花")
        self.assertFalse(dialog.groups.item(moli).flags() & Qt.ItemIsUserCheckable)
        self.assertEqual(dialog.pages[moli].notice.text(), t("retract.block.failed"))
        self.assertEqual(dialog.chosen(), [self.group.id])


class TestPublishWithoutResult(TwoPiecesTestCase):
    """沒有發譜結果的那次也能撤回：列表的「撤回…」按得下去，頁面寫出聲部一律留著"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_it_can_be_checked_and_says_parts_are_kept(self):
        from core.catalog_errors import CatalogErrorKind
        from core.publish_log import publish_entries
        from services.retract_service import RetractService
        snapshot = self.press_publish()
        self.google.fail_at(self.google.requests + 6, CatalogErrorKind.OFFLINE)
        outcome = self.run_publish(snapshot)
        entry = next(e for e in publish_entries(self.catalog.publish_log()) if e.publish_id == outcome.publish_id)
        panel = PublishLogPanel([entry])
        self.addCleanup(panel.deleteLater)
        panel.table.selectRow(0)
        self.assertTrue(panel.retract_button.isEnabled())
        dialog = RetractDialog(RetractService(self.auth).preview(entry))
        self.addCleanup(dialog.deleteLater)
        self.assertTrue(dialog.groups.item(0).flags() & Qt.ItemIsUserCheckable)
        self.assertEqual(dialog.pages[0].notice.text(), t("retract.unfinished"))


class TestComposedTexts(unittest.TestCase):
    """每種結果與原因都有中英文的說明（鍵以值組出，tests/test_locale.py 掃不到）"""

    def test_every_effect_and_reason_has_a_text_in_both_languages(self):
        from core.locale import get_available_locales, get_keys
        keys = [f"retract.effect.{e.value}" for e in RetractEffect]
        keys += [f"retract.after.{e.value}" for e in RetractEffect if e is not RetractEffect.PARTIAL]
        keys += [f"retract.reason.{r.value}" for r in RetractReason]
        for language in get_available_locales():
            missing = [key for key in keys if key not in get_keys(language)]
            self.assertEqual(missing, [], language)


class TestRetractFromCatalogWindow(RetractTestCase):
    """譜庫視窗的「發譜紀錄」選那次發譜，按「撤回…」開預覽，按「撤回」後撤掉並寫一列「撤回」"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def open_catalog(self) -> CatalogWindow:
        """開譜庫視窗（接假 Google 與假 HuaXia），按工具列的「發譜紀錄」，等列表出現"""
        preferences = PreferencesService(os.path.join(self.temp_dir, "preferences.json"))
        browser = CallbackBrowser()
        self.addCleanup(browser.finish)
        google_auth = GoogleAuthService(
            storage=SecureStorage("test-google", backend=MemoryKeyring()), request=FakeTokenEndpoint(),
            opener=browser, preferences=preferences,
            client_secrets_path=write_client_secrets(os.path.join(self.temp_dir, "client_secret.json")),
            legacy_token_path=os.path.join(self.temp_dir, "google_token.json"),
        )
        window = CatalogWindow(
            google_auth, preferences, catalog_factory=lambda: self.catalog,
            log=ActivityLog(FileService(), os.path.join(self.temp_dir, "logs")), huaxia_auth=self.auth,
        )
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        button = next(b for b in window.findChildren(QPushButton) if b.text() == t("publish_log.button"))
        button.click()
        wait_until(lambda: window.findChild(PublishLogPanel) is not None)
        return window

    def test_the_log_lists_publishes_and_only_a_publish_can_be_retracted(self):
        self.retracter.retract(self.entry(), [self.group.id], self.catalog)
        panel = self.open_catalog().findChild(PublishLogPanel)
        self.assertEqual(panel.table.rowCount(), 2)
        self.assertEqual(panel.table.item(0, 2).text(), t("publish_log.action.retract"))
        self.assertEqual(panel.table.item(1, 2).text(), t("publish_log.action.publish"))
        self.assertFalse(panel.retract_button.isEnabled())
        panel.table.selectRow(0)
        self.assertFalse(panel.retract_button.isEnabled())
        panel.table.selectRow(1)
        self.assertTrue(panel.retract_button.isEnabled())

    def test_retracting_from_the_log_removes_the_files_and_writes_a_row(self):
        window = self.open_catalog()
        panel = window.findChild(PublishLogPanel)
        shown = []

        def confirm(dialog):
            shown.append(dialog.chosen())
            dialog.confirm.click()
            return dialog.result()

        panel.table.selectRow(0)
        with mock.patch.object(RetractDialog, "exec", confirm), answering_prompts() as prompts:
            panel.retract_button.click()
            wait_until(lambda: bool(self.log(PublishAction.RETRACT)))
            wait_until(lambda: bool(prompts))
        self.assertEqual(shown, [[self.group.id]])
        self.assertEqual(self.scores(self.fenghuo), [(self.pipa, self.bowing)])
        self.assertIn(t("retract.done", count=1), prompts[0][1])
        self.assertIn(t("retract.missing", count=3), prompts[0][1])
        # 結束後重新讀取列表：多了一列撤回（舊的列表等事件迴圈才刪掉）
        wait_until(lambda: any(panel.table.rowCount() == 2 for panel in window.findChildren(PublishLogPanel)))


if __name__ == "__main__":
    unittest.main()
