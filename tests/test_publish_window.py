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
    QCheckBox, QComboBox, QDialog, QGroupBox, QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QTableWidget,
    QTabWidget, QWidget,
)

from core.constants import CATALOG_SPREADSHEET_ID_KEY, CLUB_PUBLISHING_PLATFORM_KEY, HuaXiaEnsemble, OrchestraType
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
from ui.huaxia_prompts import not_officer_text
from ui.instrumentation_table import InstrumentationTable
from services.publish_service import GroupCheck, PublishService
from ui.publish_dialog import PublishPreviewDialog


@contextmanager
def previewing(operate: Callable[[PublishPreviewDialog], None] = lambda dialog: None):
    """模擬發譜預覽開啟後由 operate 操作、再按取消關閉（operate 已按下「發譜」時不再按取消）

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
        if dialog.result() != QDialog.Accepted:
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


def button(dialog: PublishPreviewDialog, key: str) -> QPushButton:
    """預覽底部這個字串鍵的按鈕"""
    return next(b for b in dialog.findChildren(QPushButton) if b.text() == t(key))


def voices_group(dialog: PublishPreviewDialog, name: str) -> QGroupBox:
    """「聲部」步裡這個群組的那一塊"""
    return next(box for box in dialog.findChildren(QGroupBox, "voices_group") if box.title() == name)


def voice_cell(dialog: PublishPreviewDialog, group: str, voice: str) -> QWidget:
    """「聲部」步裡這個群組這個聲部那一列的「決定」欄"""
    table = voices_group(dialog, group).findChild(QTableWidget, "voices_table")
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).text() == voice)
    return table.cellWidget(row, 4)


def part_choices(dialog: PublishPreviewDialog, group: str, voice: str) -> list:
    """這個聲部可以挑的 HuaXia 聲部（選單裡挑得到的各項，不含「請挑選」）"""
    combo = voice_cell(dialog, group, voice).findChild(QComboBox, "part_choice")
    return [combo.itemText(i) for i in range(combo.count()) if combo.model().item(i).isEnabled()]


def choose_part(dialog: PublishPreviewDialog, group: str, voice: str, text: str):
    """這個聲部在選單裡挑寫著 text 的那一項"""
    combo = voice_cell(dialog, group, voice).findChild(QComboBox, "part_choice")
    combo.setCurrentIndex(combo.findText(text))


def voices_problem(dialog: PublishPreviewDialog, group: str) -> str:
    """「聲部」步裡這個群組不能發的原因；沒有時為空字串"""
    label = voices_group(dialog, group).findChild(QLabel, "voices_problem")
    return label.text() if label is not None and not label.isHidden() else ""


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

    def save(self):
        """從選單儲存專案"""
        with answering_prompts():
            self.trigger_menu(t("menu.file.save"))

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

    def test_a_western_orchestra_project_is_blocked(self):
        self.window.project.set_orchestra_type(OrchestraType.WESTERN)
        self.save()
        shown, opened = self.publish()
        self.assertEqual(opened, [])
        self.assertIn(t("publish.blocked.not_chinese_orchestra"), [text for _, text in shown])

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

    def test_a_voice_without_a_minimum_headcount_is_named(self):
        self.window.project.set_headcount(self.window.project.groups[0], 1, Headcount())
        self.save()

        def operate(dialog):
            self.assertIn(t("publish.group.unfilled", voices="琵琶"), blocked_reason(dialog, "烽火操"))
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

    def test_editing_a_new_piece_keeps_the_number_it_was_given(self):
        group = self.window.project.groups[0]
        new = NewHuaXiaPiece("烽火操", "劉文金", 7.5, HuaXiaEnsemble.TUTTI)
        self.window.project.set_huaxia_link(group, HuaXiaLink(piece_id="hx-new", piece_title="烽火操", new_piece=new))
        self.window.project.mark_saved()

        def operate(dialog):
            title = group_cell(dialog, "烽火操").findChildren(QLineEdit)[0]
            title.setText("長城隨想－烽火操")
            title.editingFinished.emit()
        self.publish(operate=operate)
        self.assertEqual(
            (group.huaxia_link.piece_id, group.huaxia_link.new_piece.title), ("hx-new", "長城隨想－烽火操"),
        )

    def test_the_instrumentation_table_shows_the_huaxia_piece(self):
        self.window.findChild(QTabWidget).setCurrentIndex(1)
        self.publish(operate=lambda dialog: choose(dialog, "烽火操", "existing"))
        label = self.window.findChild(InstrumentationTable).findChild(QLabel, "huaxia_piece")
        self.assertFalse(label.isHidden())
        self.assertEqual(label.text(), t("publish.huaxia_piece", title="長城隨想－烽火操"))


class TestNextStep(PublishWindowTestCase):
    """「下一步」要每個沒擋下的群組都對到曲目才按得下去"""

    def test_next_waits_for_every_group_that_can_be_published_to_have_a_piece(self):
        def operate(dialog):
            self.assertFalse(button(dialog, "publish.next").isEnabled())
            choose(dialog, "烽火操", "existing")
            self.assertTrue(button(dialog, "publish.next").isEnabled())
        self.publish(operate=operate)

    def test_two_groups_on_one_piece_say_why_and_next_stays_off(self):
        project = self.window.project
        fenghuo, chunjiang = project.groups
        readiness = PublishService(self.huaxia_auth).prepare()
        dialog = PublishPreviewDialog(project, [GroupCheck(fenghuo), GroupCheck(chunjiang)], readiness)
        self.addCleanup(dialog.deleteLater)
        choose(dialog, "烽火操", "existing")
        choose(dialog, "春江花月夜", "existing")
        label = group_cell(dialog, "烽火操").findChild(QLabel, "shared_piece")
        self.assertFalse(label.isHidden())
        self.assertEqual(label.text(), t("publish.piece.shared", groups="春江花月夜"))
        self.assertFalse(button(dialog, "publish.next").isEnabled())
        choose(dialog, "春江花月夜", "new")
        self.assertTrue(group_cell(dialog, "烽火操").findChild(QLabel, "shared_piece").isHidden())
        self.assertTrue(button(dialog, "publish.next").isEnabled())

    def test_no_group_to_publish_says_so_and_next_stays_off(self):
        self.window.project.set_headcount(self.window.project.groups[0], 1, Headcount())
        self.save()

        def operate(dialog):
            label = dialog.findChild(QLabel, "nothing_to_publish")
            self.assertFalse(label.isHidden())
            self.assertEqual(label.text(), t("publish.piece.nothing_to_publish"))
            self.assertFalse(button(dialog, "publish.next").isEnabled())
        _, opened = self.publish(operate=operate)
        self.assertEqual(len(opened), 1)

    def test_the_nothing_to_publish_line_is_hidden_when_a_group_can_be_published(self):
        def operate(dialog):
            self.assertTrue(dialog.findChild(QLabel, "nothing_to_publish").isHidden())
        self.publish(operate=operate)

    def test_a_new_piece_without_a_title_is_not_enough(self):
        def operate(dialog):
            choose(dialog, "烽火操", "new")
            title = group_cell(dialog, "烽火操").findChildren(QLineEdit)[0]
            title.setText(" ")
            title.editingFinished.emit()
            self.assertFalse(button(dialog, "publish.next").isEnabled())
        self.publish(operate=operate)


class TestVoicesStep(PublishWindowTestCase):
    """「聲部」步：每個群組列出泠靈的聲部對到 HuaXia 的哪個聲部、變動與決定"""

    def to_voices(self, dialog: PublishPreviewDialog):
        """「烽火操」挑現有的曲目，按下一步"""
        choose(dialog, "烽火操", "existing")
        button(dialog, "publish.next").click()

    def test_several_name_matches_offer_only_those(self):
        self.huaxia.add_part(self.fenghuo, "二胡I", sort=1)
        self.huaxia.add_part(self.fenghuo, "二胡 1", sort=2)
        self.huaxia.add_part(self.fenghuo, "高胡", sort=3)

        def operate(dialog):
            self.to_voices(dialog)
            self.assertEqual(part_choices(dialog, "烽火操", "二胡 I"), ["二胡I", "二胡 1"])
            combo = voice_cell(dialog, "烽火操", "二胡 I").findChild(QComboBox, "part_choice")
            self.assertEqual(combo.currentText(), t("publish.voices.pick"))
            self.assertIn(t("publish.voices.undecided", voices="二胡 I"), voices_problem(dialog, "烽火操"))
        self.publish(operate=operate)

    def test_a_part_chosen_by_one_voice_is_no_longer_offered_to_another(self):
        self.huaxia.add_part(self.fenghuo, "高胡")

        def operate(dialog):
            self.to_voices(dialog)
            self.assertIn("高胡", part_choices(dialog, "烽火操", "琵琶"))
            choose_part(dialog, "烽火操", "二胡 I", "高胡")
            self.assertNotIn("高胡", part_choices(dialog, "烽火操", "琵琶"))
            self.assertTrue(button(dialog, "publish.next").isEnabled())
        self.publish(operate=operate)

    def test_a_chosen_part_is_remembered_in_the_project(self):
        web = self.huaxia.add_part(self.fenghuo, "二胡")

        def operate(dialog):
            self.to_voices(dialog)
            self.assertEqual(
                part_choices(dialog, "烽火操", "二胡 I"), [t("publish.voices.new", voice="二胡 I"), "二胡"],
            )
            choose_part(dialog, "烽火操", "二胡 I", "二胡")
        self.publish(operate=operate)
        self.assertEqual(self.window.project.groups[0].huaxia_link.part_ids, {"二胡 I": web})

    def test_differences_are_overwritten_by_default(self):
        self.huaxia.add_part(self.fenghuo, "二胡1", needed="6", needed_min=6, section="拉弦")

        def operate(dialog):
            self.to_voices(dialog)
            self.assertTrue(voice_cell(dialog, "烽火操", "二胡 I").findChild(QCheckBox, "overwrite").isChecked())
        self.publish(operate=operate)

    def test_a_blocked_group_shows_its_reason(self):
        def operate(dialog):
            self.to_voices(dialog)
            label = voices_group(dialog, "春江花月夜").findChild(QLabel, "blocked_reason")
            self.assertIn(t("publish.group.not_renamed"), label.text())
        self.publish(operate=operate)


if __name__ == "__main__":
    unittest.main()
