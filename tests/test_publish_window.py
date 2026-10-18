# -*- coding: utf-8 -*-
"""
發譜按鈕與發譜預覽「曲目」步的接線測試（offscreen）

社團設定（這台電腦記住的）啟用 HuaXia 時主視窗才有「發譜」；按下時重讀社團設定、重查幹部身分（沒登入先登入），
擋下時寫出原因、不開預覽。預覽每個群組一列：擋下的群組寫出原因，其餘挑 HuaXia 的現有曲目或新建，
選擇經編輯操作寫進專案；編制表上方顯示群組對到的 HuaXia 曲目。主視窗接假 Google 與假 HuaXia，不測畫面。
"""
import os
import sys
import unittest
from contextlib import contextmanager
from typing import Callable
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import (
    QComboBox, QLabel, QMessageBox, QPushButton, QRadioButton, QTableWidget, QTabWidget, QWidget,
)

from core.constants import CATALOG_SPREADSHEET_ID_KEY, CLUB_PUBLISHING_PLATFORM_KEY, HuaXiaEnsemble
from core.locale import t
from core.models import FileInfo, Headcount, HuaXiaLink, NewHuaXiaPiece, Project
from fake_google_catalog import FakeGoogle
from services.activity_log import ActivityLog
from services.catalog_service import CatalogService
from services.file_service import FileService
from services.huaxia_auth_service import AccountStanding, HuaXiaAuthService, NotOfficer
from services.huaxia_client import PUBLISH_INTERFACE_VERSION, HuaXiaClient
from services.secure_storage import SecureStorage
from test_catalog_service import empty_catalog
from tests.fake_browser import CallbackBrowser
from tests.fake_huaxia import FakeHuaXia
from tests.memory_keyring import MemoryKeyring
from tests.test_main_window import MainWindowTestCase, answering_prompts
from ui.catalog_settings_dialog import not_officer_text
from ui.instrumentation_table import InstrumentationTable
from ui.publish_dialog import PublishPreviewDialog


@contextmanager
def previewing(operate: Callable[[PublishPreviewDialog], None] = lambda dialog: None):
    """模擬發譜預覽開啟後由 operate 操作、再按取消關閉

    Yields:
        開啟過的預覽（離開區塊時 operate 拋出的例外判定失敗）
    """
    opened, errors = [], []

    def fake_exec(dialog):
        opened.append(dialog)
        try:
            operate(dialog)
        except Exception as e:
            errors.append(e)
        dialog.reject()
        return dialog.result()

    with mock.patch.object(PublishPreviewDialog, "exec", fake_exec):
        yield opened
    if errors:
        raise errors[0]


def group_cell(dialog: PublishPreviewDialog, name: str) -> QWidget:
    """「曲目」步裡這個群組那一列的右欄"""
    table = dialog.findChild(QTableWidget, "piece_step")
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).text() == name)
    return table.cellWidget(row, 1)


def blocked_reason(dialog: PublishPreviewDialog, name: str) -> str:
    """這個群組被擋下的原因；沒擋下時為空字串"""
    label = group_cell(dialog, name).findChild(QLabel, "blocked_reason")
    return label.text() if label is not None else ""


def piece_choices(dialog: PublishPreviewDialog, name: str) -> list:
    """這個群組可以挑的現有曲目"""
    combo = group_cell(dialog, name).findChild(QComboBox, "existing_pieces")
    return [combo.itemText(i) for i in range(combo.count())]


def choose(dialog: PublishPreviewDialog, name: str, option: str, piece: int = 0):
    """這個群組按「挑選現有」並挑第 piece 首，或按「新建」"""
    cell = group_cell(dialog, name)
    if option == "existing":
        cell.findChild(QComboBox, "existing_pieces").setCurrentIndex(piece)
    button = next(b for b in cell.findChildren(QRadioButton) if b.text() == t(f"publish.piece.{option}"))
    button.click()


class PublishWindowTestCase(MainWindowTestCase):
    """主視窗開著已存檔的國樂團專案：「烽火操」已重新命名（人數都填了），「春江花月夜」還沒重新命名

    社團設定啟用 HuaXia；假 HuaXia 上有已登入的幹部 officer_amy、相符的發譜函式與兩首曲目（一首已封存）。
    """

    def setUp(self):
        super().setUp()
        self.auth.authenticate()
        self.google = FakeGoogle()
        empty_catalog(self.google)
        self.google.sheets.create_tab("社團設定", [["鍵", "值"], ["發譜平台", "華夏國樂社"]])
        self.preferences.set(CATALOG_SPREADSHEET_ID_KEY, "sheet-1")
        self.preferences.set(CLUB_PUBLISHING_PLATFORM_KEY, "huaxia")
        self.huaxia = FakeHuaXia()
        self.huaxia.add_account("officer_amy", roles=("member", "officer"))
        self.huaxia.sign_in_as("officer_amy")
        self.huaxia.install_publish_function(PUBLISH_INTERFACE_VERSION)
        self.fenghuo = self.huaxia.add_piece("長城隨想－烽火操", composer="劉文金")
        self.huaxia.add_piece("春節序曲", archived=True)
        self.huaxia_browser = CallbackBrowser(provider=self.huaxia.authorize)
        self.addCleanup(self.huaxia_browser.finish)
        self.huaxia_auth = HuaXiaAuthService(
            HuaXiaClient(session=self.huaxia), storage=SecureStorage("test-huaxia", backend=MemoryKeyring()),
            opener=self.huaxia_browser,
        )
        self.huaxia_auth.authenticate()
        self.window = self.new_window()
        self.path = self.open_from_menu(self.concert())

    def new_window(self):
        """關掉目前的主視窗，換一個接假 Google 與假 HuaXia 的"""
        with answering_prompts(t("dialog.discard_btn")):
            self.window.close()
        from ui.main_window import MainWindow
        return MainWindow(
            self.preferences, history=self.history, opener=self.opener, auth_service=self.auth,
            compressor=self.compressor(), log=ActivityLog(FileService(), self.log_dir),
            huaxia_auth=self.huaxia_auth, catalog_factory=self.connect,
        )

    def connect(self) -> CatalogService:
        """接假 Google 的譜庫服務"""
        return CatalogService(self.google.sheets, self.google.drive, sleep=lambda s: None)

    def concert(self) -> Project:
        """「烽火操」的總譜與兩份分譜已在輸出位置；「春江花月夜」的分譜還在匯入的位置"""
        out = os.path.join(self.temp_dir, "out")
        os.makedirs(out)
        project = Project(master_template="{序號}-{樂器}.pdf", output_directory=out)
        fenghuo = project.add_group("烽火操", "總譜")
        project.update_group(fenghuo, piece_name="烽火操", composer="劉文金")
        project.set_instruments(fenghuo, ["二胡 I", "琵琶"])
        names = ("00-總譜.pdf", "01-二胡 I.pdf", "02-琵琶.pdf")
        project.add_files([FileInfo(os.path.join(out, n), n) for n in names], fenghuo)
        project.set_headcount(fenghuo, 0, Headcount(6, 8))
        project.set_headcount(fenghuo, 1, Headcount(2))
        project.set_duration(fenghuo, 440)
        chunjiang = project.add_group("春江花月夜", "總譜")
        project.update_group(chunjiang, piece_name="春江花月夜")
        project.set_instruments(chunjiang, ["簫"])
        project.add_files(self.create_files("xiao.pdf"), chunjiang)
        for path in project.all_file_paths():
            if not os.path.exists(path):
                with open(path, "w") as f:
                    f.write("pdf")
        return project

    def publish_button(self) -> QPushButton:
        """主視窗的「發譜」"""
        return next(b for b in self.window.findChildren(QPushButton) if b.text() == t("publish.button"))

    def publish(self, *answers, operate: Callable[[PublishPreviewDialog], None] = lambda dialog: None):
        """按「發譜」，提示框依序按 answers；回傳（跳出過的提示框，開啟過的預覽）"""
        with answering_prompts(*answers) as shown, previewing(operate) as opened:
            self.publish_button().click()
        return shown, opened


class TestPublishButton(PublishWindowTestCase):
    """發譜按鈕依這台電腦記住的社團設定顯示"""

    def test_shown_when_club_settings_enable_huaxia(self):
        self.assertFalse(self.publish_button().isHidden())

    def test_hidden_without_huaxia_in_club_settings(self):
        self.preferences.set(CLUB_PUBLISHING_PLATFORM_KEY, "")
        self.window = self.new_window()
        self.assertTrue(self.publish_button().isHidden())


class TestPressingPublish(PublishWindowTestCase):
    """按下發譜：重讀社團設定、重查幹部身分，擋下時說明原因、不開預覽"""

    def test_club_settings_no_longer_enabling_huaxia_explains_and_hides_the_button(self):
        self.google.sheets.create_tab("社團設定", [["鍵", "值"]])
        shown, opened = self.publish()
        self.assertEqual(opened, [])
        self.assertIn(t("publish.club_settings_removed"), [text for _, text in shown])
        self.assertTrue(self.publish_button().isHidden())

    def test_someone_no_longer_an_officer_is_signed_out_and_told_why(self):
        self.huaxia.remove_roles("officer_amy")
        shown, opened = self.publish()
        self.assertEqual(opened, [])
        expected = not_officer_text(NotOfficer(AccountStanding.NOT_OFFICER, "officer_amy"))
        self.assertIn(expected, [text for _, text in shown])
        self.assertFalse(self.huaxia_auth.status().signed_in)

    def test_unsaved_changes_block_without_contacting_anyone(self):
        self.window.project.update_group(self.window.project.groups[0], composer="改了")
        before = (self.google.requests, len(self.huaxia.requests))
        shown, opened = self.publish()
        self.assertEqual(opened, [])
        self.assertIn(t("publish.blocked.unsaved"), [text for _, text in shown])
        self.assertEqual((self.google.requests, len(self.huaxia.requests)), before)

    def test_a_missing_publish_function_explains_the_merge_request(self):
        self.huaxia.install_publish_function(None)
        shown, opened = self.publish()
        self.assertEqual(opened, [])
        self.assertIn(t("publish.function_missing"), [text for _, text in shown])

    def test_signed_out_officers_sign_in_first(self):
        self.huaxia_auth.logout()
        shown, opened = self.publish(QMessageBox.Yes)
        self.assertEqual(len(opened), 1)
        self.assertTrue(self.huaxia_auth.status().signed_in)


class TestPieceStep(PublishWindowTestCase):
    """「曲目」步：擋下的群組寫出原因；其餘挑現有曲目或新建，選擇寫進專案"""

    def test_a_blocked_group_shows_its_reason(self):
        def operate(dialog):
            self.assertIn(t("publish.group.not_renamed"), blocked_reason(dialog, "春江花月夜"))
            self.assertIsNone(group_cell(dialog, "春江花月夜").findChild(QRadioButton))
            self.assertEqual(blocked_reason(dialog, "烽火操"), "")
        _, opened = self.publish(operate=operate)
        self.assertEqual(len(opened), 1)

    def test_archived_pieces_are_not_offered(self):
        def operate(dialog):
            self.assertEqual(piece_choices(dialog, "烽火操"), ["長城隨想－烽火操（劉文金）"])
        self.publish(operate=operate)

    def test_choosing_an_existing_piece_writes_the_link(self):
        self.publish(operate=lambda dialog: choose(dialog, "烽火操", "existing"))
        group = self.window.project.groups[0]
        self.assertEqual(group.huaxia_link, HuaXiaLink(piece_id=self.fenghuo, piece_title="長城隨想－烽火操"))
        self.assertTrue(self.is_marked_unsaved())

    def test_choosing_new_writes_the_defaults(self):
        self.publish(operate=lambda dialog: choose(dialog, "烽火操", "new"))
        new = self.window.project.groups[0].huaxia_link.new_piece
        self.assertEqual(new, NewHuaXiaPiece("烽火操", "劉文金", 7.5, HuaXiaEnsemble.TUTTI))

    def test_the_instrumentation_table_shows_the_huaxia_piece(self):
        self.window.findChild(QTabWidget).setCurrentIndex(1)
        self.publish(operate=lambda dialog: choose(dialog, "烽火操", "existing"))
        label = self.window.findChild(InstrumentationTable).findChild(QLabel, "huaxia_piece")
        self.assertFalse(label.isHidden())
        self.assertEqual(label.text(), t("publish.huaxia_piece", title="長城隨想－烽火操"))


if __name__ == "__main__":
    unittest.main()
