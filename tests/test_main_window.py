# -*- coding: utf-8 -*-
"""
主視窗測試（offscreen）

只測主視窗與服務層的接線，不測畫面；以 QT_QPA_PLATFORM=offscreen 執行，不需要顯示器。
提示框、檔案對話框與預覽對話框以替換的方式模擬使用者的操作，不會跳出真的對話框；文字欄位以 QTest 模擬鍵入（QTest 只能輸入 ASCII 字元，中文會讓 Qt 直接中止）。
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from typing import Callable, List
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QLabel, QLineEdit, QListWidget, QMenu, QMessageBox,
    QPushButton, QRadioButton, QTabWidget, QWidget,
)

from core.constants import CompressionStatus, PartsOutputMode
from core.locale import get_locale, set_locale, t
from core.models import FileInfo, Group, Project, RenameEntry
from services.activity_log import ActivityLog
from services.compression_service import CompressionService
from services.file_service import FileService
from services.google_auth_service import GoogleAuthService
from services.secure_storage import SecureStorage
from main import launch, prepare_application
from services.instance_lock import InstanceLock
from services.move_history import MoveHistory, RenameVerdict
from services.preferences_service import PreferencesService
from services.project_service import ProjectService
from services.workspace_service import WorkspaceService
from ui.background_task import BackgroundTask
from ui.compression_compare import CompressionCompareDialog
from ui.main_window import MainWindow
from ui.preview_dialog import PreviewDialog
from tests.failing_writes import FailingWrites
from tests.fake_browser import CallbackBrowser
from tests.fake_google import FakeTokenEndpoint, write_client_secrets
from tests.memory_keyring import MemoryKeyring
from tests.other_instance import OtherInstance
from tests.synthetic_pdfs import file_digest, scanned_pdf


@contextmanager
def answering_prompts(*answers):
    """模擬使用者在提示框的選擇

    替換 QMessageBox.exec：每跳出一個提示框，依序按下文字等於下一個答案的按鈕。
    「是／否」類的詢問框（QMessageBox.question）依序以下一個答案作為按下的標準按鈕（QMessageBox.Yes、No）。
    提示框比答案多、或找不到該按鈕時不按任何按鈕，離開區塊時才判定失敗（Qt 事件裡拋出的例外會被吞掉）。
    QMessageBox.critical、warning、information 也一併替換，只記錄標題與訊息，不會卡住測試。

    Args:
        answers: 依序要按下的按鈕文字，或「是／否」詢問框要按的標準按鈕

    Yields:
        跳出過的提示框（標題，訊息）清單
    """
    remaining = list(answers)
    shown = []
    unexpected = []

    def fake_exec(box):
        shown.append((box.windowTitle(), box.text()))
        answer = remaining.pop(0) if remaining else None
        button = next((b for b in box.buttons() if b.text() == answer), None)
        if button is None:
            unexpected.append((box.text(), answer))
            return QMessageBox.Cancel
        button.click()
        return box.result()

    def fake_question(parent, title, text, *args, **kwargs):
        shown.append((title, text))
        answer = remaining.pop(0) if remaining else None
        if not isinstance(answer, QMessageBox.StandardButton):
            unexpected.append((text, answer))
            return QMessageBox.Cancel
        return answer

    def fake_static(parent, title, text, *args, **kwargs):
        shown.append((title, text))
        return QMessageBox.Ok

    with mock.patch.object(QMessageBox, "exec", fake_exec), \
            mock.patch.object(QMessageBox, "question", fake_question), \
            mock.patch.object(QMessageBox, "critical", fake_static), \
            mock.patch.object(QMessageBox, "warning", fake_static), \
            mock.patch.object(QMessageBox, "information", fake_static):
        yield shown
    if unexpected:
        raise AssertionError(f"提示框與預期的答案不符（訊息，答案）：{unexpected}")


def saving_as(path: str):
    """模擬另存對話框的選擇；path 為空字串表示按了取消"""
    return mock.patch.object(QFileDialog, "getSaveFileName", return_value=(path, ""))


def opening(path: str):
    """模擬開啟專案對話框選了 path"""
    return mock.patch.object(QFileDialog, "getOpenFileName", return_value=(path, ""))


@contextmanager
def previewing(operate: Callable[[QDialog], None]):
    """模擬使用者在預覽對話框操作後按取消關閉

    替換 PreviewDialog.exec：對話框開啟時呼叫 operate 操作設定元件，接著以取消關閉，不執行重新命名。
    operate 拋出的例外（在 Qt 事件裡會被吞掉）與對話框沒有開啟，都在離開區塊時判定失敗。

    Args:
        operate: 接收對話框、操作其設定元件的函式
    """
    opened = []
    errors = []

    def fake_exec(dialog):
        opened.append(dialog)
        try:
            operate(dialog)
        except Exception as e:
            errors.append(e)
        dialog.reject()
        return dialog.result()

    with mock.patch.object(PreviewDialog, "exec", fake_exec):
        yield
    if errors:
        raise errors[0]
    if not opened:
        raise AssertionError("預覽對話框沒有開啟")


@contextmanager
def executing_preview():
    """模擬使用者在預覽對話框直接按下執行鈕（不論它顯示「執行重新命名」還是「繼續（自動加後綴）」）

    執行鈕停用（按不下去）或對話框沒有開啟，都在離開區塊時判定失敗。

    Yields:
        按下的執行鈕文字（清單，對話框開啟後才有）
    """
    pressed = []

    def fake_exec(dialog):
        button = next(
            b for b in dialog.findChildren(QPushButton)
            if b.text() in (t("preview.execute"), t("preview.execute_with_suffix"))
        )
        if button.isEnabled():
            pressed.append(button.text())
            button.click()
        else:
            dialog.reject()
        return dialog.result()

    with mock.patch.object(PreviewDialog, "exec", fake_exec):
        yield pressed
    if not pressed:
        raise AssertionError("預覽對話框沒有開啟，或執行鈕停用")


def keep_every_compressed_file(dialog: QDialog) -> None:
    """在比較畫面把每份壓縮檔都留下，再按輸出"""
    files = dialog.findChild(QListWidget)
    for row in range(files.count()):
        files.setCurrentRow(row)
        keep = button_in(dialog, QPushButton, t("compare.keep"))
        if keep.isVisibleTo(dialog):
            keep.click()
    button_in(dialog, QPushButton, t("compare.output")).click()


@contextmanager
def comparing(operate: Callable[[QDialog], None] = keep_every_compressed_file):
    """模擬使用者在壓縮的比較畫面操作

    替換 CompressionCompareDialog.exec：對話框開啟時呼叫 operate 操作它（按留下、不要、輸出或取消），
    operate 沒有按輸出時視同取消。operate 拋出的例外（在 Qt 事件裡會被吞掉）在離開區塊時判定失敗。

    Args:
        operate: 接收對話框、操作它的函式；省略時每份壓縮檔都留下再按輸出

    Yields:
        開啟過的比較畫面
    """
    opened = []
    errors = []

    def fake_exec(dialog):
        """取代 exec：記下開啟的對話框並操作它，不真的進入事件迴圈"""
        opened.append(dialog)
        try:
            operate(dialog)
        except Exception as e:
            errors.append(e)
        return dialog.result()

    with mock.patch.object(CompressionCompareDialog, "exec", fake_exec):
        yield opened
    if errors:
        raise errors[0]


class RecordingOpener:
    """外部開啟的替身：記下要開啟的網址或路徑，不真的啟動外部程式；opens 為 False 時像外部程式啟動不了"""

    def __init__(self):
        """建立替身：開得起來、還沒開過任何東西"""
        self.opens = True
        self.opened: List[str] = []

    def open(self, target: str) -> bool:
        """記下要開啟的目標；opens 為 False 時回報開不了"""
        self.opened.append(target)
        return self.opens


def _settle():
    """處理延後刪除的元件，如同事件迴圈跑過一輪：重建分頁後被換掉的舊分頁不會再被找到"""
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def button_in(parent: QWidget, button_type, text: str):
    """parent 中文字相符的按鈕（勾選框、選項按鈕等）"""
    _settle()
    return next(b for b in parent.findChildren(button_type) if b.text() == text)


def field_in(parent: QWidget, text: str) -> QLineEdit:
    """parent 中目前顯示指定文字的欄位"""
    _settle()
    return next(e for e in parent.findChildren(QLineEdit) if e.text() == text)


class MainWindowTestCase(unittest.TestCase):
    """主視窗測試的共用骨架；收尾時「是否儲存」一律按「不儲存」，留下未存檔標記也不會卡住測試"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.preferences_path = os.path.join(self.temp_dir, "preferences.json")
        self.preferences = PreferencesService(self.preferences_path, self.preferences_file_service())
        self.history_files = FileService()
        self.history = MoveHistory(
            self.history_files, WorkspaceService(self.history_files, os.path.join(self.temp_dir, "workspace")),
            undo_dir=os.path.join(self.temp_dir, "undo"), redo_dir=os.path.join(self.temp_dir, "redo"),
            journal_path=os.path.join(self.temp_dir, "pending_move.json"),
            backup_dir=os.path.join(self.temp_dir, "backups"),
        )
        self.opener = RecordingOpener()
        self.keyring = MemoryKeyring()
        self.legacy_token = os.path.join(self.temp_dir, "google_token.json")
        self.auth = GoogleAuthService(
            storage=SecureStorage("test-google", backend=self.keyring),
            request=FakeTokenEndpoint(),
            opener=CallbackBrowser(),
            client_secrets_path=write_client_secrets(os.path.join(self.temp_dir, "client_secret.json")),
            legacy_token_path=self.legacy_token,
        )
        self.log_dir = os.path.join(self.temp_dir, "logs")
        self.window = MainWindow(
            self.preferences, history=self.history, opener=self.opener, auth_service=self.auth,
            compressor=self.compressor(), log=ActivityLog(FileService(), self.log_dir),
        )

    def compressor(self) -> CompressionService:
        """主視窗用的壓縮服務；暫存資料夾在臨時目錄下"""
        return CompressionService(FileService(), os.path.join(self.temp_dir, "temp"))

    def preferences_file_service(self) -> FileService:
        """偏好設定寫入用的檔案服務；要模擬最近清單寫不進去的測試換成寫不進去的"""
        return FileService()

    def tearDown(self):
        with answering_prompts(t("dialog.discard_btn")):
            self.window.close()

    def trigger_menu(self, text: str):
        """觸發主視窗選單中文字相符的動作"""
        action = next(a for a in self.window.findChildren(QAction) if a.text() == text)
        action.trigger()

    def click_button(self, text: str):
        """按下主視窗中文字相符的按鈕"""
        button_in(self.window, QPushButton, text).click()

    def is_marked_unsaved(self) -> bool:
        """視窗標題是否帶有未存檔標記"""
        return self.window.windowTitle().endswith(" *")

    def status_shows(self, text: str) -> bool:
        """狀態列是否顯示這段文字"""
        return any(label.text() == text for label in self.window.findChildren(QLabel))

    def recent_menu(self) -> QMenu:
        """「最近開啟的專案」選單"""
        return next(m for m in self.window.findChildren(QMenu) if m.title() == t("menu.file.recent"))

    def recent_menu_paths(self) -> List[str]:
        """最近專案選單列出的專案檔路徑（各項的提示文字）"""
        return [a.toolTip() for a in self.recent_menu().actions() if a.isEnabled()]

    def open_from_menu(self, project: Project) -> str:
        """把專案存成檔案後從選單開啟，回傳專案檔路徑"""
        path = os.path.join(self.temp_dir, "opened.llproj")
        ProjectService().save_project(project, path)
        with answering_prompts(), opening(path):
            self.trigger_menu(t("menu.file.open"))
        return path

    def save_from_menu(self, path: str) -> Project:
        """從選單儲存（尚未存過檔時存到 path），讀回存出的專案檔"""
        with answering_prompts(), saving_as(path):
            self.trigger_menu(t("menu.file.save"))
        return ProjectService().load_project(path)

    def create_files(self, *names: str) -> List[FileInfo]:
        """在暫存目錄建立檔案，回傳對應的檔案資訊"""
        files = []
        for name in names:
            path = os.path.join(self.temp_dir, name)
            with open(path, "w") as f:
                f.write("dummy")
            files.append(FileInfo(path, name))
        return files


class TestMainWindowRecentListUnwritable(MainWindowTestCase):
    """最近清單寫不進去時，專案檔讀寫成功就算成功：不跳錯誤提示框，只在狀態列提示"""

    def preferences_file_service(self) -> FileService:
        return FailingWrites(self.preferences_path)

    def test_project_still_opens_without_error_box(self):
        path = os.path.join(self.temp_dir, "opened.llproj")
        ProjectService().save_project(Project(groups=[Group(name="g", score_label="總譜")]), path)
        with answering_prompts() as shown, opening(path):
            self.trigger_menu(t("menu.file.open"))
        self.assertEqual(shown, [])
        self.assertEqual([g.name for g in self.window.project.groups], ["g"])
        self.assertIn("opened.llproj", self.window.windowTitle())
        self.assertTrue(self.status_shows(t("status.recent_failed")))

    def test_close_proceeds_after_choosing_save(self):
        self.window.show()
        self.click_button(t("group.add"))
        path = os.path.join(self.temp_dir, "saved.llproj")
        with answering_prompts(t("dialog.save_btn")) as shown, saving_as(path):
            closed = self.window.close()
        self.assertTrue(closed)
        self.assertEqual(shown, [(t("dialog.close"), t("dialog.close.message"))])
        self.assertEqual(len(ProjectService().load_project(path).groups), 1)
        self.assertTrue(self.status_shows(t("status.recent_failed")))


class TestMainWindowOpensWorkspaceFolder(MainWindowTestCase):
    """開啟工作區資料夾經外部開啟；資料夾還不存在或外部程式啟動不了時提示"""

    def setUp(self):
        super().setUp()
        self.workspace_dir = self.window.workspace_service.workspace_dir
        os.makedirs(self.workspace_dir, exist_ok=True)

    def test_opens_workspace_folder_with_external_opener(self):
        with answering_prompts() as shown:
            self.trigger_menu(t("menu.tools.open_workspace"))
        self.assertEqual(shown, [])
        self.assertEqual(self.opener.opened, [self.workspace_dir])

    def test_tells_user_when_folder_cannot_be_opened(self):
        self.opener.opens = False
        with answering_prompts() as shown:
            self.trigger_menu(t("menu.tools.open_workspace"))
        self.assertEqual(shown, [(t("dialog.error"), t("dialog.error.external_open_failed", target=self.workspace_dir))])


class TestMainWindowOpensLogFolder(MainWindowTestCase):
    """「說明」選單的「開啟紀錄檔資料夾」經外部開啟打開紀錄檔資料夾；還沒有紀錄檔或開不了時提示"""

    def test_opens_the_log_folder_with_external_opener(self):
        os.makedirs(self.log_dir)
        with answering_prompts() as shown:
            self.trigger_menu(t("menu.help.open_log_folder"))
        self.assertEqual(shown, [])
        self.assertEqual(self.opener.opened, [self.log_dir])

    def test_says_there_are_no_logs_yet_when_the_folder_is_missing(self):
        with answering_prompts() as shown:
            self.trigger_menu(t("menu.help.open_log_folder"))
        self.assertEqual(shown, [(t("dialog.info"), t("log.folder_missing"))])
        self.assertEqual(self.opener.opened, [])

    def test_tells_user_when_folder_cannot_be_opened(self):
        os.makedirs(self.log_dir)
        self.opener.opens = False
        with answering_prompts() as shown:
            self.trigger_menu(t("menu.help.open_log_folder"))
        self.assertEqual(shown, [(t("dialog.error"), t("dialog.error.external_open_failed", target=self.log_dir))])


class TestMainWindowRecentMenu(MainWindowTestCase):
    """最近專案選單"""

    def test_missing_project_is_gone_from_menu_after_trying_to_open_it(self):
        path = self.open_from_menu(Project())
        self.assertEqual(self.recent_menu_paths(), [path])
        os.remove(path)
        action = next(a for a in self.recent_menu().actions() if a.toolTip() == path)
        with answering_prompts() as shown:
            action.trigger()
        self.assertEqual(shown, [(t("dialog.error"), t("dialog.error.file_not_found", path=path))])
        self.assertEqual(self.recent_menu_paths(), [])


class TestMainWindowUnsavedPrompt(MainWindowTestCase):
    """有未存檔的修改時，「是否儲存」選了儲存卻沒存成，就中止原本的操作"""

    def setUp(self):
        super().setUp()
        self.window.show()
        self.click_button(t("group.add"))

    def test_close_is_blocked_when_save_as_is_cancelled(self):
        with answering_prompts(t("dialog.save_btn")), saving_as(""):
            closed = self.window.close()
        self.assertFalse(closed)
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.is_marked_unsaved())

    def test_new_project_is_blocked_when_save_as_is_cancelled(self):
        with answering_prompts(t("dialog.save_btn")), saving_as(""):
            self.trigger_menu(t("menu.file.new"))
        self.assertEqual(len(self.window.project.groups), 1)
        self.assertTrue(self.is_marked_unsaved())

    def test_close_is_blocked_when_project_file_cannot_be_written(self):
        path = os.path.join(self.temp_dir, "saved.llproj")
        real_write = self.window.file_service.write_json_atomic

        def failing_write(target, data):
            if target == path:
                raise OSError("disk full")
            real_write(target, data)

        self.window.file_service.write_json_atomic = failing_write
        with answering_prompts(t("dialog.save_btn")) as shown, saving_as(path):
            closed = self.window.close()
        self.assertFalse(closed)
        self.assertTrue(self.window.isVisible())
        self.assertTrue(self.is_marked_unsaved())
        self.assertIn((t("dialog.error"), t("dialog.error.save_failed", error="disk full")), shown)

    def test_close_proceeds_after_saving(self):
        path = os.path.join(self.temp_dir, "saved.llproj")
        with answering_prompts(t("dialog.save_btn")), saving_as(path):
            closed = self.window.close()
        self.assertTrue(closed)
        self.assertFalse(self.window.isVisible())
        self.assertEqual(len(ProjectService().load_project(path).groups), 1)

    def test_close_proceeds_without_saving_when_discarding(self):
        with answering_prompts(t("dialog.discard_btn")), saving_as(""):
            closed = self.window.close()
        self.assertTrue(closed)
        self.assertFalse(self.window.isVisible())


class TestMainWindowSaveAddsExtension(MainWindowTestCase):
    """另存專案時沒打副檔名就補上 .llproj；補上後的檔名已有檔案時，確認取代才寫入"""

    def test_project_saved_without_extension_gets_it(self):
        typed = os.path.join(self.temp_dir, "winter")
        with answering_prompts(), saving_as(typed):
            self.trigger_menu(t("menu.file.save"))
        self.assertTrue(os.path.isfile(typed + ".llproj"))
        self.assertFalse(os.path.exists(typed))
        self.assertIn("winter.llproj", self.window.windowTitle())

    def test_extension_typed_in_another_case_is_kept(self):
        typed = os.path.join(self.temp_dir, "winter.LLPROJ")
        with answering_prompts(), saving_as(typed):
            self.trigger_menu(t("menu.file.save"))
        self.assertEqual(os.listdir(self.temp_dir).count("winter.LLPROJ"), 1)
        self.assertNotIn("winter.LLPROJ.llproj", os.listdir(self.temp_dir))

    def test_existing_file_under_the_completed_name_is_kept_when_not_confirmed(self):
        typed = os.path.join(self.temp_dir, "winter")
        with open(typed + ".llproj", "w", encoding="utf-8") as f:
            f.write("old")
        with answering_prompts(QMessageBox.No) as shown, saving_as(typed):
            self.trigger_menu(t("menu.file.save"))
        self.assertEqual(shown, [(t("dialog.warning"), t("filedialog.replace", name="winter.llproj"))])
        with open(typed + ".llproj", encoding="utf-8") as f:
            self.assertEqual(f.read(), "old")

    def test_existing_file_under_the_completed_name_is_replaced_when_confirmed(self):
        typed = os.path.join(self.temp_dir, "winter")
        with open(typed + ".llproj", "w", encoding="utf-8") as f:
            f.write("old")
        self.click_button(t("group.add"))
        with answering_prompts(QMessageBox.Yes), saving_as(typed):
            self.trigger_menu(t("menu.file.save"))
        self.assertEqual(len(ProjectService().load_project(typed + ".llproj").groups), 1)


class TestMainWindowMarksUserEdits(MainWindowTestCase):
    """標題的未存檔標記跟著專案內容：與上次存檔或開啟時不同才標記，改回原樣就消失"""

    def _switch_language_back(self):
        set_locale("zh_TW")
        self.preferences.set("language", "zh_TW")
        self.preferences.save()

    def _open_project_with_group(self) -> Group:
        """從選單開啟一份含一個群組、大模板與預設值不同的專案，回傳存進專案檔的群組"""
        group = Group(
            name="第一樂章", piece_name="貝多芬第五號交響曲", movement_number="1",
            movement_name="Allegro con brio", composer="Beethoven", genre="交響曲",
        )
        self.open_from_menu(Project(master_template="{序號} {樂器} - {曲名}.pdf", groups=[group]))
        return group

    def test_typing_in_master_template_marks_unsaved(self):
        QTest.keyClicks(field_in(self.window, self.window.project.master_template), "x")
        self.assertTrue(self.is_marked_unsaved())

    def test_typing_in_group_piece_name_marks_unsaved(self):
        group = self._open_project_with_group()
        QTest.keyClicks(field_in(self.window, group.piece_name), "x")
        self.assertTrue(self.is_marked_unsaved())

    def test_opening_a_project_does_not_mark_unsaved(self):
        self._open_project_with_group()
        self.assertFalse(self.is_marked_unsaved())

    def test_opening_a_project_without_groups_does_not_mark_unsaved(self):
        self.open_from_menu(Project())
        self.assertFalse(self.is_marked_unsaved())

    def test_opening_a_project_with_an_unassigned_score_does_not_mark_unsaved(self):
        """群組裡有像總譜的檔案但沒指定總譜（例如存檔前清掉了），開啟時不猜總譜"""
        self.open_from_menu(Project(groups=[Group(name="g", files=self.create_files("Full Score.pdf"))]))
        self.assertFalse(self.is_marked_unsaved())

    def test_opening_an_old_project_with_blank_score_labels_then_closing_does_not_ask_to_save(self):
        path = os.path.join(self.temp_dir, "old.llproj")
        old = Project(groups=[Group(name="g", score_label="", files=self.create_files("Flute.pdf"))])
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": "1.1.0-alpha", **old.to_data()}, f)
        with answering_prompts(), opening(path):
            self.trigger_menu(t("menu.file.open"))
        self.assertEqual(self.window.project.groups[0].score_label, t("group.score_label"))
        self.assertFalse(self.is_marked_unsaved())
        with answering_prompts() as shown:
            closed = self.window.close()
        self.assertTrue(closed)
        self.assertEqual(shown, [])

    def test_typing_in_master_template_then_deleting_it_clears_the_mark(self):
        entry = field_in(self.window, self.window.project.master_template)
        QTest.keyClicks(entry, "x")
        QTest.keyClick(entry, Qt.Key_Backspace)
        self.assertFalse(self.is_marked_unsaved())

    def test_switching_language_that_rewrites_the_master_template_marks_unsaved(self):
        self.addCleanup(self._switch_language_back)
        self.trigger_menu(t("menu.view.language.en"))
        self.assertTrue(self.is_marked_unsaved())

    def test_switching_tabs_does_not_mark_unsaved(self):
        self.open_from_menu(Project(groups=[
            Group(name="a", instruments=["Flute"], score_label="總譜"), Group(name="b", score_label="總譜"),
        ]))
        tabs = self.window.findChild(QTabWidget)
        for index in (0, 2, 1):
            tabs.setCurrentIndex(index)
        self.assertFalse(self.is_marked_unsaved())

    def test_deleting_a_group_moves_its_files_to_ungrouped_and_marks_unsaved(self):
        path = self.open_from_menu(Project(groups=[
            Group(name="g", files=self.create_files("Flute.pdf", "Oboe.pdf"), score_label="總譜"),
        ]))
        with answering_prompts(QMessageBox.Yes):
            self.click_button(t("group.delete"))
        self.assertTrue(self.is_marked_unsaved())
        saved = self.save_from_menu(path)
        self.assertEqual(saved.groups, [])
        self.assertEqual([f.display_name for f in saved.ungrouped_files], ["Flute.pdf", "Oboe.pdf"])

    def test_new_group_keeps_the_score_label_of_the_language_it_was_created_in(self):
        self.addCleanup(self._switch_language_back)
        self.click_button(t("group.add"))
        self.trigger_menu(t("menu.view.language.en"))
        path = os.path.join(self.temp_dir, "saved.llproj")
        self.assertEqual(self.save_from_menu(path).groups[0].score_label, "總譜")

    def test_changing_output_settings_in_preview_marks_unsaved(self):
        def toggle_subfolders(dialog):
            button_in(dialog, QCheckBox, t("panel.subfolder")).click()

        with previewing(toggle_subfolders):
            self.click_button(t("panel.preview_rename"))
        self.assertTrue(self.is_marked_unsaved())

    def test_closing_preview_without_changing_settings_does_not_mark_unsaved(self):
        def touch_without_changing(dialog):
            button_in(dialog, QRadioButton, t("panel.parts_mode_root")).click()
            QTest.keyClick(field_in(dialog, self.window.project.subfolder_template), Qt.Key_Return)

        with previewing(touch_without_changing):
            self.click_button(t("panel.preview_rename"))
        self.assertFalse(self.is_marked_unsaved())


class TestMainWindowCloseComparesWithProjectFile(MainWindowTestCase):
    """關閉前再把目前內容和磁碟上的專案檔比對一次，不同就照常詢問是否儲存"""

    def test_close_asks_to_save_when_the_project_file_was_changed_elsewhere(self):
        path = self.open_from_menu(Project(groups=[Group(name="g", score_label="總譜")]))
        ProjectService().save_project(Project(groups=[Group(name="elsewhere", score_label="總譜")]), path)
        with answering_prompts(t("dialog.discard_btn")) as shown:
            closed = self.window.close()
        self.assertTrue(closed)
        self.assertEqual(shown, [(t("dialog.close"), t("dialog.close.message"))])

    def test_close_asks_to_save_when_the_project_file_is_gone(self):
        path = self.open_from_menu(Project(groups=[Group(name="g", score_label="總譜")]))
        os.remove(path)
        with answering_prompts(t("dialog.discard_btn")) as shown:
            self.window.close()
        self.assertEqual(shown, [(t("dialog.close"), t("dialog.close.message"))])


class TestMainWindowGuessesOnlyWhenFilesEnterGroup(MainWindowTestCase):
    """總譜與曲名只在檔案進入群組時自動偵測；使用者清掉的不會在重建分頁時被設回"""

    def test_cleared_score_is_not_set_back_when_a_group_is_added(self):
        score, part = self.create_files("Full Score.pdf", "Flute.pdf")
        path = self.open_from_menu(Project(groups=[Group(name="g", files=[part], score_file=score, score_label="總譜")]))
        self.click_button(t("group.score_file.clear"))
        self.click_button(t("group.add"))
        saved = self.save_from_menu(path).groups[0]
        self.assertIsNone(saved.score_file)
        self.assertEqual([f.display_name for f in saved.files], ["Full Score.pdf", "Flute.pdf"])

    def test_cleared_piece_name_is_not_set_back_when_a_group_is_added(self):
        files = self.create_files("Brahms Symphony - Flute.pdf", "Brahms Symphony - Oboe.pdf")
        path = self.open_from_menu(Project(groups=[
            Group(name="g", files=files, piece_name="Brahms Symphony", score_label="總譜"),
        ]))
        entry = field_in(self.window, "Brahms Symphony")
        entry.selectAll()
        QTest.keyClick(entry, Qt.Key_Backspace)
        self.click_button(t("group.add"))
        self.assertEqual(self.save_from_menu(path).groups[0].piece_name, "")

    def test_moving_ungrouped_files_into_a_new_group_guesses_score_and_piece_name(self):
        files = self.create_files("Full Score.pdf", "Brahms Symphony - Flute.pdf", "Brahms Symphony - Oboe.pdf")
        path = self.open_from_menu(Project(ungrouped_files=files))
        self.window.findChild(QTabWidget).setCurrentIndex(0)
        button_in(self.window, QCheckBox, t("ungrouped.select_all")).click()
        self.click_button(t("ungrouped.new_group_from_selected"))
        saved = self.save_from_menu(path).groups[0]
        self.assertEqual(saved.score_file.display_name, "Full Score.pdf")
        self.assertEqual(saved.piece_name, "Brahms Symphony")
        self.assertEqual(saved.score_label, "總譜")


class TestMainWindowKeepsTypedInput(MainWindowTestCase):
    """使用者在群組分頁輸入的內容，經過重建分頁的操作後仍在，存檔也存得進去"""

    def _saved_group(self, path: str) -> Group:
        """從選單儲存後，讀回專案檔裡的第一個群組"""
        with answering_prompts():
            self.trigger_menu(t("menu.file.save"))
        return ProjectService().load_project(path).groups[0]

    def test_typed_piece_name_survives_adding_a_group(self):
        path = self.open_from_menu(Project(groups=[Group(name="第一樂章", piece_name="命運")]))
        QTest.keyClicks(field_in(self.window, "命運"), " No.5")
        self.click_button(t("group.add"))
        self.assertEqual(self._saved_group(path).piece_name, "命運 No.5")

    def test_turning_on_small_template_survives_adding_a_group(self):
        path = self.open_from_menu(Project(groups=[Group(name="第一樂章", small_template="{曲名}.pdf")]))
        button_in(self.window, QCheckBox, t("group.use_small_template")).click()
        self.click_button(t("group.add"))
        self.assertTrue(self._saved_group(path).use_small_template)

    def test_auto_detected_piece_name_survives_adding_a_group(self):
        files = []
        for name in ("Brahms Symphony - Flute.pdf", "Brahms Symphony - Oboe.pdf"):
            file_path = os.path.join(self.temp_dir, name)
            with open(file_path, "w") as f:
                f.write("dummy")
            files.append(FileInfo(file_path, name))
        path = self.open_from_menu(Project(groups=[Group(name="第一樂章", piece_name="暫名", files=files)]))
        self.click_button(t("group.auto_detect"))
        self.click_button(t("group.add"))
        self.assertEqual(self._saved_group(path).piece_name, "Brahms Symphony")


class TestMainWindowInstrumentListFollowsGroup(MainWindowTestCase):
    """樂器表跟著目前的群組分頁；目前分頁不是群組時清空並停用，輸入的樂器才不會沒有地方寫入"""

    def _instrument_entry(self) -> QLineEdit:
        """樂器表的輸入欄"""
        return next(
            e for e in self.window.findChildren(QLineEdit)
            if e.placeholderText() == t("instrument.placeholder")
        )

    def _shown_instruments(self) -> List[str]:
        """樂器表目前列出的樂器"""
        listing = self._instrument_entry().parentWidget().findChild(QListWidget)
        return [listing.item(i).text() for i in range(listing.count())]

    def test_instrument_list_is_disabled_before_any_group_exists(self):
        self.assertFalse(self._instrument_entry().isEnabled())

    def test_instrument_list_is_cleared_and_disabled_on_ungrouped_tab(self):
        self.open_from_menu(Project(groups=[Group(name="g", instruments=["Flute", "Oboe"])]))
        self.window.findChild(QTabWidget).setCurrentIndex(0)
        self.assertFalse(self._instrument_entry().isEnabled())
        self.assertEqual(self._shown_instruments(), [])

    def test_exported_list_gets_the_extension_and_platform_newlines(self):
        """匯出編制表：沒打副檔名就補上 .txt，每行以這個平台的換行結尾、不會多出空行"""
        self.click_button(t("group.add"))
        for name in ("Flute", "Oboe"):
            QTest.keyClicks(self._instrument_entry(), name)
            QTest.keyClick(self._instrument_entry(), Qt.Key_Return)
        typed = os.path.join(self.temp_dir, "ensemble")
        with answering_prompts(), saving_as(typed):
            self.click_button(t("instrument.export"))
        with open(typed + ".txt", "rb") as f:
            lines = f.read().decode("utf-8").split(os.linesep)
        self.assertEqual([line for line in lines if "\r" in line or "\n" in line], [])
        self.assertIn("  Flute                          x 1", lines)
        self.assertEqual(lines[-1], "2 parts / 2 people")

    def test_instruments_entered_after_adding_a_group_are_saved(self):
        self.click_button(t("group.add"))
        QTest.keyClicks(self._instrument_entry(), "Flute")
        QTest.keyClick(self._instrument_entry(), Qt.Key_Return)
        path = os.path.join(self.temp_dir, "saved.llproj")
        with answering_prompts(), saving_as(path):
            self.trigger_menu(t("menu.file.save"))
        self.assertEqual(ProjectService().load_project(path).groups[0].instruments, ["Flute"])


class TestMainWindowPreviewNaming(MainWindowTestCase):
    """預覽的命名：聲部組名稱第一次用到時依當時的介面語言定下；資料夾名稱為 .. 時擋下"""

    def _switch_language(self, lang_code: str):
        self.trigger_menu(t(f"menu.view.language.{lang_code}"))

    def _open_group(self, **project_fields) -> None:
        """從選單開啟一份含一個群組（分譜 Flute、Horn）的專案"""
        group = Group(name="g", files=self.create_files("fl.pdf", "hn.pdf"), instruments=["Flute", "Horn"])
        self.open_from_menu(Project(master_template="{樂器}.pdf", groups=[group], **project_fields))

    def _preview(self, operate=lambda dialog: None) -> List[str]:
        """開啟預覽、操作後關閉，回傳預覽列出的新路徑"""
        shown = []

        def operate_and_read(dialog):
            operate(dialog)
            _settle()
            rows = [label.text() for label in dialog.findChildren(QLabel) if "\u2192 " in label.text()]
            shown.extend(row.split("\u2192 ", 1)[1] for row in rows)

        with previewing(operate_and_read):
            self.click_button(t("panel.preview_rename"))
        return shown

    def test_section_folders_named_in_english_stay_after_switching_to_chinese(self):
        self.addCleanup(set_locale, get_locale())
        self._open_group()
        self._switch_language("en")
        in_english = self._preview(
            lambda dialog: button_in(dialog, QRadioButton, t("panel.parts_mode_section")).click(),
        )
        self._switch_language("zh_TW")
        in_chinese = self._preview()
        expected = [
            os.path.join(self.temp_dir, "Woodwinds", "Flute.pdf"), os.path.join(self.temp_dir, "Brass", "Horn.pdf"),
        ]
        self.assertEqual(in_english, expected)
        self.assertEqual(in_chinese, expected)

    def test_parts_folder_named_dot_dot_blocks_the_rename(self):
        self._open_group(parts_output_mode=PartsOutputMode.PARTS, parts_subfolder_name="..")
        seen = {}

        def read_state(dialog):
            _settle()
            seen["warnings"] = [label.text() for label in dialog.findChildren(QLabel) if label.isVisibleTo(dialog)]
            seen["enabled"] = button_in(dialog, QPushButton, t("preview.execute")).isEnabled()

        with previewing(read_state):
            self.click_button(t("panel.preview_rename"))
        self.assertIn(t("preview.unsafe_folder_warning", name=".."), seen["warnings"])
        self.assertFalse(seen["enabled"])


class TestMainWindowPreviewVerdict(MainWindowTestCase):
    """預覽顯示搬移歷程的預檢判定，按下執行用的就是同一份判定"""

    def _open_group(self, voices: List[str]) -> None:
        """從選單開啟一份專案：一個群組，分譜依序為 p1.pdf、p2.pdf…，命名格式為 {樂器}.pdf"""
        files = self.create_files(*(f"p{i}.pdf" for i in range(1, len(voices) + 1)))
        group = Group(name="g", files=files, instruments=voices, score_label="總譜")
        self.open_from_menu(Project(master_template="{樂器}.pdf", groups=[group]))

    def _pdf_names(self) -> List[str]:
        return sorted(n for n in os.listdir(self.temp_dir) if n.endswith(".pdf"))

    def test_continue_with_suffix_really_renames(self):
        self._open_group(["Same", "Same"])
        with answering_prompts() as shown, executing_preview() as pressed:
            self.click_button(t("panel.preview_rename"))
        self.assertEqual(pressed, [t("preview.execute_with_suffix")])
        self.assertEqual(self._pdf_names(), ["Same (1).pdf", "Same.pdf"])
        self.assertEqual(shown, [(t("dialog.complete"), t("dialog.complete.renamed", count=2))])

    def test_blocked_preview_disables_execute_and_shows_the_reason(self):
        self._open_group(["Same", "Same", "Same (1)"])
        seen = {}

        def read_state(dialog):
            _settle()
            seen["warnings"] = [label.text() for label in dialog.findChildren(QLabel) if label.isVisibleTo(dialog)]
            seen["enabled"] = button_in(dialog, QPushButton, t("preview.execute")).isEnabled()

        with previewing(read_state):
            self.click_button(t("panel.preview_rename"))
        blocked = [os.path.join(self.temp_dir, name) for name in ("p2.pdf", "p3.pdf")]
        self.assertIn(t("preview.duplicate_target_warning", files="\n".join(blocked)), seen["warnings"])
        self.assertFalse(seen["enabled"])
        self.assertEqual(self._pdf_names(), ["p1.pdf", "p2.pdf", "p3.pdf"])

    def test_reminders_are_shown_and_execute_stays_enabled(self):
        files = self.create_files("p1.pdf", "p2.pdf")
        group = Group(name="g", files=files, instruments=["Flute"], score_label="總譜")
        self.open_from_menu(Project(master_template="{樂器}{Foo}.pdf", groups=[group]))
        seen = {}

        def read_state(dialog):
            _settle()
            seen["notes"] = [label.text() for label in dialog.findChildren(QLabel) if label.isVisibleTo(dialog)]
            seen["enabled"] = button_in(dialog, QPushButton, t("preview.execute")).isEnabled()

        with previewing(read_state):
            self.click_button(t("panel.preview_rename"))
        expected = "\n\n".join([
            t("preview.extra_files_warning", files=files[1].original_path),
            t("preview.unknown_variables_warning", names="{Foo}"),
        ])
        self.assertIn(expected, seen["notes"])
        self.assertTrue(seen["enabled"])


class _Crash(BaseException):
    """模擬程式被強制結束：不是 Exception，所以搬移歷程不會攔下來回滾"""


class MoveHistoryWindowTestCase(MainWindowTestCase):
    """主視窗與搬移歷程接線測試的共用骨架"""

    def _path(self, name: str) -> str:
        return os.path.join(self.temp_dir, name)

    def _open_group_of(self, *paths: str) -> str:
        """從選單開啟一份專案，群組引用指定的檔案，回傳專案檔路徑"""
        files = [FileInfo(p, os.path.basename(p)) for p in paths]
        return self.open_from_menu(Project(groups=[Group(name="g", files=files, score_label="總譜")]))

    def _shown_file_names(self) -> List[str]:
        """目前分頁的分譜清單顯示的文字"""
        _settle()
        listing = self.window.findChild(QTabWidget).currentWidget().findChild(QListWidget)
        return [listing.item(i).text() for i in range(listing.count())]

    def _fail_moves_when(self, predicate):
        """搬移歷程之後搬移檔案時，predicate(來源, 目標) 成立的那幾次拋出 OSError"""
        real_rename = FileService.rename_file

        def flaky_rename(old_path, new_path):
            if predicate(old_path, new_path):
                raise OSError("simulated")
            real_rename(self.history_files, old_path, new_path)

        self.history_files.rename_file = flaky_rename

    def _crash_writing_into(self, run, directory: str):
        """執行 run，第一次往 directory 寫入 JSON 時當機，之後寫入恢復正常"""
        real_write = FileService.write_json_atomic

        def write(path, data):
            if os.path.dirname(path) == directory:
                raise _Crash()
            real_write(self.history_files, path, data)

        self.history_files.write_json_atomic = write
        with self.assertRaises(_Crash):
            run()
        del self.history_files.write_json_atomic


class TestMainWindowMoveHistory(MoveHistoryWindowTestCase):
    """復原、重做與中斷還原：結果交給專案套用，訊息列出略過與殘留"""

    def test_undo_marks_unsaved_and_shows_the_original_name(self):
        a, = self.create_files("a.pdf")
        renamed = self._path("01-Flute.pdf")
        self.history.rename(RenameVerdict([RenameEntry(a.original_path, renamed)]))
        self._open_group_of(renamed)
        self.assertFalse(self.is_marked_unsaved())
        with answering_prompts(QMessageBox.Yes):
            self.trigger_menu(t("menu.edit.undo"))
        self.assertTrue(self.is_marked_unsaved())
        self.assertEqual(self._shown_file_names(), ["a.pdf"])

    def test_redo_marks_unsaved_and_shows_the_new_name(self):
        a, = self.create_files("a.pdf")
        self.history.rename(RenameVerdict([RenameEntry(a.original_path, self._path("01-Flute.pdf"))]))
        self.history.undo()
        self._open_group_of(a.original_path)
        with answering_prompts(QMessageBox.Yes):
            self.trigger_menu(t("menu.edit.redo"))
        self.assertTrue(self.is_marked_unsaved())
        self.assertEqual(self._shown_file_names(), ["01-Flute.pdf"])

    def test_undo_lists_the_skipped_files(self):
        a, b = self.create_files("a.pdf", "b.pdf")
        new_a, new_b = self._path("01-Flute.pdf"), self._path("02-Oboe.pdf")
        self.history.rename(RenameVerdict([RenameEntry(a.original_path, new_a), RenameEntry(b.original_path, new_b)]))
        self._open_group_of(new_a, new_b)
        os.remove(new_b)
        with answering_prompts(QMessageBox.Yes) as shown:
            self.trigger_menu(t("menu.edit.undo"))
        self.assertIn((t("dialog.info"), t("history.skipped", files=new_b)), shown)
        self.assertEqual(self._shown_file_names(), ["a.pdf", "02-Oboe.pdf"])

    def test_undo_warns_about_workspace_folders_whose_meta_cannot_be_written_back(self):
        workspace = WorkspaceService(self.history_files, os.path.join(self.temp_dir, "workspace"))
        source, = self.create_files("merged.pdf")
        folder = os.path.join(workspace.workspace_dir, "a1b2c3d4")
        workspace.write_meta(folder, source.original_path, "")
        part = os.path.join(folder, "flute.pdf")
        with open(part, "w") as f:
            f.write("part")
        renamed = self._path("01-Flute.pdf")
        self.history.rename(RenameVerdict([RenameEntry(part, renamed, output_directory=self.temp_dir)]))
        workspace.purge_empty_folders()
        self._open_group_of(renamed)
        real_write = FileService.write_json_atomic

        def failing_write(path, data):
            if os.path.basename(path) == "meta.json":
                raise OSError("locked")
            real_write(self.history_files, path, data)

        self.history_files.write_json_atomic = failing_write
        with answering_prompts(QMessageBox.Yes) as shown:
            self.trigger_menu(t("menu.edit.undo"))
        self.assertTrue(os.path.isfile(part))
        self.assertIn((t("dialog.warning"), t("history.meta_not_restored", folders=folder)), shown)
        self.assertEqual(self._shown_file_names(), ["flute.pdf"])

    def test_residual_record_is_described_by_the_operation_that_left_it(self):
        a, b = self.create_files("a.pdf", "b.pdf")
        new_a, new_b = self._path("01-Flute.pdf"), self._path("02-Oboe.pdf")
        self.history.rename(RenameVerdict([RenameEntry(a.original_path, new_a), RenameEntry(b.original_path, new_b)]))
        self._open_group_of(new_a, new_b)
        # 第二個檔搬回原位失敗觸發回滾；第一個檔又搬不回新位置，留在原位
        self._fail_moves_when(lambda old, new: old == new_b or new == new_a)
        with answering_prompts(QMessageBox.Yes):
            self.trigger_menu(t("menu.edit.undo"))
        self.assertEqual(self._shown_file_names(), ["a.pdf", "02-Oboe.pdf"])
        with answering_prompts(QMessageBox.No) as shown:
            self.trigger_menu(t("menu.edit.undo"))
        description = t("history.residual.undo", count=1)
        self.assertEqual(shown, [(t("dialog.confirm_undo"), t("dialog.confirm_undo.message", description=description))])

    def test_interrupted_rename_is_restored_from_the_prompt(self):
        a, b = self.create_files("a.pdf", "b.pdf")
        calls = []
        real_rename = FileService.rename_file

        def crash_on_second_move(old_path, new_path):
            calls.append(old_path)
            if len(calls) == 2:
                raise _Crash()
            real_rename(self.history_files, old_path, new_path)

        self.history_files.rename_file = crash_on_second_move
        with self.assertRaises(_Crash):
            self.history.rename(RenameVerdict([
                RenameEntry(a.original_path, self._path("01.pdf")), RenameEntry(b.original_path, self._path("02.pdf")),
            ]))
        del self.history_files.rename_file
        with answering_prompts(t("dialog.pending_move.restore")) as shown:
            self.assertTrue(self.window.prompt_pending_recovery())
        self.assertEqual(shown[-1], (t("dialog.pending_move.title"), t("dialog.pending_move.done", count=1)))
        self.assertEqual(sorted(n for n in os.listdir(self.temp_dir) if n.endswith(".pdf")), ["a.pdf", "b.pdf"])

    def test_prompt_after_an_interrupted_undo_says_undo_did_not_finish(self):
        a, b = self.create_files("a.pdf", "b.pdf")
        new_a, new_b = self._path("01-Flute.pdf"), self._path("02-Oboe.pdf")
        self.history.rename(RenameVerdict([RenameEntry(a.original_path, new_a), RenameEntry(b.original_path, new_b)]))
        real_rename = FileService.rename_file

        def crash_on_second_move(old_path, new_path):
            if old_path == new_b:
                raise _Crash()
            real_rename(self.history_files, old_path, new_path)

        self.history_files.rename_file = crash_on_second_move
        with self.assertRaises(_Crash):
            self.history.undo()
        del self.history_files.rename_file
        with answering_prompts(t("dialog.pending_move.later")) as shown:
            self.assertFalse(self.window.prompt_pending_recovery())
        self.assertEqual(shown, [(t("dialog.pending_move.undo.title"), t("dialog.pending_move.undo.message", count=1))])

    def test_keeping_a_finished_undo_updates_project_paths_and_marks_unsaved(self):
        a, = self.create_files("a.pdf")
        renamed = self._path("01-Flute.pdf")
        self.history.rename(RenameVerdict([RenameEntry(a.original_path, renamed)]))
        self._open_group_of(renamed)
        self._crash_writing_into(self.history.undo, os.path.join(self.temp_dir, "redo"))
        with answering_prompts(t("dialog.pending_move.keep")) as shown:
            self.assertTrue(self.window.prompt_pending_recovery())
        self.assertEqual(shown, [
            (t("dialog.pending_move.undo.title"), t("dialog.pending_move.undo.finished_message", count=1)),
        ])
        self.assertTrue(self.is_marked_unsaved())
        self.assertEqual(self._shown_file_names(), ["a.pdf"])


class TestMainWindowKeepsResultAtStartup(MoveHistoryWindowTestCase):
    """啟動時（還沒開啟任何專案）在中斷提示選「保留結果」：這批搬移所屬的專案反映檔案的實際位置"""

    def _restart(self) -> MainWindow:
        """模擬重新啟動：同一份使用者資料的新主視窗，還沒開啟任何專案"""
        window = MainWindow(self.preferences, history=self.history)

        def close():
            with answering_prompts(t("dialog.discard_btn")):
                window.close()

        self.addCleanup(close)
        return window

    def _fail_writes_into(self, directory: str):
        """之後往 directory 寫入 JSON 一律失敗（OSError）"""
        real_write = FileService.write_json_atomic

        def write(path, data):
            if os.path.dirname(path) == directory:
                raise OSError("disk full")
            real_write(self.history_files, path, data)

        self.history_files.write_json_atomic = write

    def _rename_then_crash(self, a: FileInfo, project_path: str) -> str:
        """把 a 改名，寫復原紀錄時當機（已整批搬完），回傳新路徑"""
        renamed = self._path("01-Flute.pdf")
        verdict = RenameVerdict([RenameEntry(a.original_path, renamed)])
        self._crash_writing_into(
            lambda: self.history.rename(verdict, project_path=project_path), os.path.join(self.temp_dir, "undo"),
        )
        return renamed

    def _group_file_paths(self, window: MainWindow) -> List[str]:
        return [f.original_path for g in window.project.groups for f in g.files]

    def test_rename_from_the_preview_kept_at_startup_opens_its_project_with_the_new_paths(self):
        a, = self.create_files("a.pdf")
        path = self.open_from_menu(Project(
            master_template="{Number}-{Instrument}.pdf",
            groups=[Group(name="g", files=[a], instruments=["Flute"], score_label="Score")],
        ))
        self._fail_writes_into(os.path.join(self.temp_dir, "undo"))
        with executing_preview(), answering_prompts() as shown:
            self.click_button(t("panel.preview_rename"))
        del self.history_files.write_json_atomic
        self.assertEqual(shown, [(t("dialog.warning"), t("history.record_not_saved", error="disk full"))])
        window = self._restart()
        with answering_prompts(t("dialog.pending_move.keep")) as shown:
            self.assertTrue(window.prompt_pending_recovery())
        self.assertEqual(shown, [(t("dialog.pending_move.title"), t("dialog.pending_move.finished_message", count=1))])
        self.assertEqual(self._group_file_paths(window), [self._path("01-Flute.pdf")])
        self.assertIn(os.path.basename(path), window.windowTitle())
        self.assertTrue(window.windowTitle().endswith(" *"))
        self.assertIsNone(self.history.pending())

    def test_modified_project_is_offered_for_saving_before_the_batch_project_opens(self):
        a, = self.create_files("a.pdf")
        path = os.path.join(self.temp_dir, "batch.llproj")
        ProjectService().save_project(Project(groups=[Group(name="g", files=[a], score_label="總譜")]), path)
        renamed = self._rename_then_crash(a, path)
        window = self._restart()
        window.project.add_group("other", score_label="總譜")
        with answering_prompts(t("dialog.pending_move.keep"), t("dialog.cancel_btn")) as shown:
            self.assertFalse(window.prompt_pending_recovery())
        self.assertEqual(shown[-1], (t("dialog.unsaved"), t("dialog.unsaved.message")))
        self.assertEqual([g.name for g in window.project.groups], ["other"])
        self.assertTrue(self.history.pending().complete)
        with answering_prompts(t("dialog.pending_move.keep"), t("dialog.discard_btn")):
            self.assertTrue(window.prompt_pending_recovery())
        self.assertEqual(self._group_file_paths(window), [renamed])
        self.assertIn("batch.llproj", window.windowTitle())

    def test_swap_kept_later_in_the_same_session_is_not_applied_to_the_project_twice(self):
        flute, oboe = self.create_files("Flute.pdf", "Oboe.pdf")
        self.open_from_menu(Project(
            master_template="{Instrument}.pdf",
            groups=[Group(name="g", files=[flute, oboe], instruments=["Oboe", "Flute"], score_label="Score")],
        ))
        self._fail_writes_into(os.path.join(self.temp_dir, "undo"))
        with executing_preview(), answering_prompts():
            self.click_button(t("panel.preview_rename"))
        del self.history_files.write_json_atomic
        swapped = [self._path("Oboe.pdf"), self._path("Flute.pdf")]
        self.assertEqual(self._group_file_paths(self.window), swapped)
        with answering_prompts(t("dialog.pending_move.keep")):
            self.assertTrue(self.window.prompt_pending_recovery())
        self.assertEqual(self._group_file_paths(self.window), swapped)

    def test_batch_of_a_project_never_saved_says_its_paths_were_not_updated(self):
        a, = self.create_files("a.pdf")
        self._rename_then_crash(a, "")
        window = self._restart()
        with answering_prompts(t("dialog.pending_move.keep")) as shown:
            self.assertTrue(window.prompt_pending_recovery())
        self.assertEqual(shown[-1], (t("dialog.info"), t("dialog.pending_move.project_unsaved")))
        self.assertFalse(window.windowTitle().endswith(" *"))


class TestMainWindowAbout(MainWindowTestCase):
    """「說明」選單的「關於泠靈小工具」：版號、GitHub 網址與授權；網址經外部開啟"""

    REPOSITORY_URL = "https://github.com/Suikann/ling-ling-suite"

    def open_about(self) -> QDialog:
        """從選單開啟「關於」視窗，回傳它（替換 exec，不會卡住測試）"""
        opened = []

        def fake_exec(dialog):
            """取代 exec：記下開啟的對話框並操作它，不真的進入事件迴圈"""
            opened.append(dialog)
            return QDialog.Accepted

        with mock.patch.object(QDialog, "exec", fake_exec):
            self.trigger_menu(t("menu.help.about"))
        self.assertEqual(len(opened), 1)
        return opened[0]

    def texts_in(self, dialog: QDialog) -> List[str]:
        """視窗裡各標籤的文字"""
        return [label.text() for label in dialog.findChildren(QLabel)]

    def test_source_run_shows_development_build(self):
        texts = self.texts_in(self.open_about())
        self.assertIn(t("about.version", version=t("about.dev_version")), texts)

    def test_packaged_build_shows_version_from_resource_dir(self):
        resource_dir = os.path.join(self.temp_dir, "_MEI12345")
        os.makedirs(resource_dir)
        with open(os.path.join(resource_dir, "version.txt"), "w") as f:
            f.write("v1.2.0\n")
        with answering_prompts(t("dialog.discard_btn")):
            self.window.close()
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "_MEIPASS", resource_dir, create=True):
            self.window = MainWindow(
                self.preferences, history=self.history, opener=self.opener, auth_service=self.auth,
                compressor=self.compressor(),
            )
            texts = self.texts_in(self.open_about())
        self.assertIn(t("about.version", version="v1.2.0"), texts)

    def test_shows_license(self):
        self.assertIn(t("about.license"), self.texts_in(self.open_about()))

    def test_repository_link_opens_with_external_opener(self):
        dialog = self.open_about()
        link = next(label for label in dialog.findChildren(QLabel) if self.REPOSITORY_URL in label.text())
        link.linkActivated.emit(self.REPOSITORY_URL)
        self.assertEqual(self.opener.opened, [self.REPOSITORY_URL])

    def test_link_that_cannot_be_opened_says_so(self):
        self.opener.opens = False
        dialog = self.open_about()
        link = next(label for label in dialog.findChildren(QLabel) if self.REPOSITORY_URL in label.text())
        with answering_prompts() as shown:
            link.linkActivated.emit(self.REPOSITORY_URL)
        self.assertEqual(
            shown, [(t("dialog.error"), t("dialog.error.external_open_failed", target=self.REPOSITORY_URL))],
        )


class TestPrepareApplication(unittest.TestCase):
    """啟動前的設定：補上輸入法環境變數，設定應用程式名稱與 desktop file name"""

    def test_sets_input_method_modules_and_application_identity(self):
        with mock.patch.dict(os.environ, {"QT_IM_MODULE": "fcitx"}, clear=True), \
                mock.patch.object(sys, "platform", "linux"):
            prepare_application()
            self.assertEqual(os.environ.get("QT_IM_MODULES"), "wayland;fcitx;ibus")
        self.assertEqual(QGuiApplication.desktopFileName(), "lingling-suite")
        self.assertEqual(QGuiApplication.applicationName(), "lingling-suite")

    def test_keeps_input_method_modules_set_by_user(self):
        with mock.patch.dict(os.environ, {"QT_IM_MODULES": "ibus"}, clear=True), \
                mock.patch.object(sys, "platform", "linux"):
            prepare_application()
            self.assertEqual(os.environ.get("QT_IM_MODULES"), "ibus")


class TestMainWindowGoogleLogin(MainWindowTestCase):
    """啟動時只刪舊的明文權杖檔、不碰安全儲存；登入沒存進安全儲存時當下提示"""

    def test_startup_deletes_legacy_token_without_touching_secure_storage(self):
        with open(self.legacy_token, "w", encoding="utf-8") as f:
            f.write('{"refresh_token": "legacy"}')
        window = MainWindow(self.preferences, history=self.history, auth_service=self.auth)
        self.addCleanup(window.deleteLater)
        self.assertFalse(os.path.exists(self.legacy_token))
        self.assertEqual(self.keyring.calls, [])

    def test_login_that_cannot_be_saved_warns_to_sign_in_again_next_time(self):
        self.keyring.failing_writes = True
        with answering_prompts() as shown:
            self.auth.authenticate()
        self.assertIn((t("catalog.auth.title"), t("catalog.auth.reason.not_saved")), shown)

    def test_login_in_background_that_cannot_be_saved_warns_on_main_thread(self):
        self.keyring.failing_writes = True
        warned_on = []
        record = lambda *args, **kwargs: warned_on.append(threading.current_thread())
        with mock.patch.object(QMessageBox, "warning", side_effect=record):
            login = threading.Thread(target=self.auth.authenticate)
            login.start()
            login.join(10)
            QApplication.sendPostedEvents()
        self.assertEqual(warned_on, [threading.main_thread()])


class TestMainWindowOpenCatalog(MainWindowTestCase):
    """開譜庫：安全儲存沒解鎖時，一次操作最多跳一次解鎖視窗；下一次需要時再跳"""

    def setUp(self):
        super().setUp()
        self.auth.authenticate()
        self.auth = GoogleAuthService(
            storage=SecureStorage("test-google", backend=self.keyring),
            request=FakeTokenEndpoint(),
            opener=CallbackBrowser(),
            client_secrets_path=os.path.join(self.temp_dir, "client_secret.json"),
            legacy_token_path=self.legacy_token,
        )
        self.window.close()
        self.preferences.set("catalog_root_folder_id", "folder-id")
        self.window = MainWindow(self.preferences, history=self.history, opener=self.opener, auth_service=self.auth)
        self.keyring.locked = True
        self.keyring.calls.clear()

    def open_catalog(self) -> List[str]:
        """從選單開譜庫；設定對話框一開就按取消，回傳過程中跳出的解鎖視窗（讀安全儲存的次數）"""
        from ui.catalog_settings_dialog import CatalogSettingsDialog
        with mock.patch.object(CatalogSettingsDialog, "exec", return_value=QDialog.Rejected):
            self.trigger_menu(t("menu.catalog.open"))
        return [call for call in self.keyring.calls if call == "get"]

    def test_cancelled_unlock_is_asked_only_once_per_open(self):
        self.assertEqual(len(self.open_catalog()), 1)

    def test_next_open_asks_to_unlock_again(self):
        self.open_catalog()
        self.keyring.calls.clear()
        self.assertEqual(len(self.open_catalog()), 1)


def wait_until(condition: Callable[[], bool], timeout: float = 20) -> None:
    """處理事件直到 condition 成立（背景工作的結果經事件迴圈回到主執行緒）；逾時判定失敗"""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("等待逾時")
        QApplication.processEvents()
        time.sleep(0.01)


class GatedCompressor(CompressionService):
    """測試放行（或要求取消）才開始壓縮的壓縮服務，讓測試在壓縮進行中操作主視窗"""

    def __init__(self, *args, **kwargs):
        """建立壓縮服務，閘門先關著"""
        super().__init__(*args, **kwargs)
        self.gate = threading.Event()
        self.results = []

    def compress(self, source, should_cancel=lambda: False, on_progress=lambda progress: None):
        """等測試放行（或要求取消、逾時）才照常壓縮，記下結果"""
        deadline = time.monotonic() + 20
        while not self.gate.wait(0.01) and not should_cancel() and time.monotonic() < deadline:
            pass
        result = super().compress(source, should_cancel, on_progress)
        self.results.append(result)
        return result


class MainWindowCompressTestCase(MainWindowTestCase):
    """壓縮 PDF 測試的共用骨架：原檔在臨時目錄的 Symphony 資料夾，另存到旁邊的「Symphony（壓縮）」"""

    size_limit = 10 ** 9

    def compressor(self) -> CompressionService:
        """會等測試放行的壓縮服務"""
        self.scratch_dir = os.path.join(self.temp_dir, "temp")
        self.gated = GatedCompressor(FileService(), self.scratch_dir, size_limit=self.size_limit)
        return self.gated

    def setUp(self):
        super().setUp()
        self.addCleanup(self.gated.gate.set)
        parts = os.path.join(self.temp_dir, "Symphony")
        os.makedirs(parts)
        self.source = scanned_pdf(os.path.join(parts, "Flute.pdf"), dpi=300)
        self.target = os.path.join(self.temp_dir, "Symphony（壓縮）", "Flute.pdf")

    def start_compressing(self, *targets: str) -> mock.MagicMock:
        """從工具選單只選一份原檔，另存對話框依序回答 targets，回傳另存對話框的替身"""
        answers = [(target, "") for target in targets]
        with mock.patch.object(QFileDialog, "getOpenFileNames", return_value=([self.source], "")), \
                mock.patch.object(QFileDialog, "getSaveFileName", side_effect=answers) as save:
            self.trigger_menu(t("menu.tools.compress_pdf"))
        return save

    def finish(self, operate: Callable[[QDialog], None] = keep_every_compressed_file) -> List[QDialog]:
        """放行壓縮並等結果回到主視窗；比較畫面開啟時以 operate 操作它（預設每份都留下再輸出）

        Returns:
            開啟過的比較畫面
        """
        with comparing(operate) as opened:
            self.gated.gate.set()
            wait_until(lambda: self.compress_action().isEnabled())
        return opened

    def action(self, text: str) -> QAction:
        """主視窗裡文字相符的選單項目"""
        return next(a for a in self.window.findChildren(QAction) if a.text() == text)

    def compress_action(self) -> QAction:
        """工具選單的「壓縮 PDF」"""
        return self.action(t("menu.tools.compress_pdf"))

    def file_operations_enabled(self) -> List[bool]:
        """重新命名、分割、旋轉、復原、重做是否可用"""
        rename = button_in(self.window, QPushButton, t("panel.preview_rename"))
        actions = [self.action(t(key)) for key in (
            "menu.tools.split_pdf", "menu.tools.rotate_pdf", "menu.edit.undo", "menu.edit.redo",
        )]
        return [rename.isEnabled()] + [a.isEnabled() for a in actions]

    def scratch_files(self) -> List[str]:
        """暫存資料夾裡留下的檔案"""
        return [name for _, _, names in os.walk(self.scratch_dir) for name in names]


class TestMainWindowCompressPdf(MainWindowCompressTestCase):
    """工具選單的壓縮 PDF：選檔、另存位置、背景執行時鎖住會動到檔案的操作"""

    def test_file_operations_are_locked_while_compressing_and_unlocked_after(self):
        self.start_compressing(self.target)
        self.assertEqual(self.file_operations_enabled(), [False] * 5)
        self.assertFalse(self.compress_action().isEnabled())
        with answering_prompts():
            self.finish()
        self.assertEqual(self.file_operations_enabled(), [True] * 5)

    def test_file_operations_stay_locked_after_switching_language_while_compressing(self):
        self.addCleanup(set_locale, get_locale())
        self.start_compressing(self.target)
        self.trigger_menu(t("menu.view.language.en"))
        self.assertEqual(self.file_operations_enabled(), [False] * 5)
        with answering_prompts():
            self.finish()
        self.assertEqual(self.file_operations_enabled(), [True] * 5)

    def test_suggests_the_folder_beside_the_original_and_saves_the_result_there(self):
        save = self.start_compressing(self.target)
        with answering_prompts() as shown:
            self.finish()
        self.assertEqual(save.call_args.args[2], self.target)
        self.assertEqual(self.gated.results[0].status, CompressionStatus.SMALL_ENOUGH)
        self.assertEqual(file_digest(self.target), file_digest(self.source))
        self.assertEqual(len(shown), 1)
        self.assertIn(self.target, shown[0][1])

    def test_target_typed_without_extension_is_saved_with_pdf_added(self):
        typed = os.path.join(self.temp_dir, "out", "Flute small")
        self.start_compressing(typed)
        with answering_prompts():
            self.finish()
        self.assertEqual(file_digest(typed + ".pdf"), file_digest(self.source))

    def test_choosing_the_original_file_is_refused_and_asked_again(self):
        digest = file_digest(self.source)
        with answering_prompts() as shown:
            save = self.start_compressing(self.source, "")
        self.assertEqual(save.call_count, 2)
        self.assertEqual(shown, [(t("dialog.warning"), t("compress.target_blocked"))])
        self.assertTrue(self.compress_action().isEnabled())
        self.assertEqual(file_digest(self.source), digest)

    def test_another_name_in_the_original_folder_is_saved_there(self):
        beside = os.path.join(os.path.dirname(self.source), "Flute small.pdf")
        self.start_compressing(beside)
        with answering_prompts():
            self.finish()
        self.assertEqual(file_digest(beside), file_digest(self.source))

    def test_finished_compression_leaves_no_background_task_behind(self):
        for _ in range(2):
            self.start_compressing(self.target)
            with answering_prompts():
                self.finish()
        _settle()
        self.assertEqual(self.window.findChildren(BackgroundTask), [])

    def test_closing_while_compressing_asks_and_staying_keeps_it_running(self):
        self.start_compressing(self.target)
        with answering_prompts(QMessageBox.No) as shown:
            self.assertFalse(self.window.close())
        self.assertEqual(shown, [(t("dialog.close"), t("compress.close.message"))])
        self.assertFalse(self.compress_action().isEnabled())
        with answering_prompts():
            self.finish()
        self.assertTrue(os.path.isfile(self.target))


class TestMainWindowCompressPdfCancel(MainWindowCompressTestCase):
    """取消壓縮：在頁與頁之間停下，什麼都不輸出"""

    size_limit = 1000

    def test_cancel_button_stops_compressing_without_output(self):
        self.start_compressing(self.target)
        self.click_button(t("compress.cancel"))
        with answering_prompts():
            self.finish()
        self.assertTrue(all(r.status is CompressionStatus.CANCELLED for r in self.gated.results))
        self.assertFalse(os.path.exists(self.target))
        self.assertEqual(self.scratch_files(), [])
        self.assertTrue(self.status_shows(t("compress.cancelled")))

    def test_closing_and_choosing_to_cancel_stops_compressing_without_output(self):
        self.start_compressing(self.target)
        with answering_prompts(QMessageBox.Yes):
            self.assertTrue(self.window.close())
        self.assertTrue(all(r.status is CompressionStatus.CANCELLED for r in self.gated.results))
        self.assertFalse(os.path.exists(self.target))
        self.assertEqual(self.scratch_files(), [])


class MainWindowCompressBatchTestCase(MainWindowCompressTestCase):
    """一整批壓縮的共用骨架：Symphony 資料夾裡有 Flute.pdf 與 Strings/Violin.pdf，預設輸出到旁邊的「Symphony（壓縮）」"""

    def setUp(self):
        super().setUp()
        self.root = os.path.dirname(self.source)
        os.makedirs(os.path.join(self.root, "Strings"))
        self.violin = scanned_pdf(os.path.join(self.root, "Strings", "Violin.pdf"), dpi=300)
        self.output_dir = os.path.join(self.temp_dir, "Symphony（壓縮）")

    def start_folder(self, *answers, folders=()) -> list:
        """從工具選單選整個 Symphony 資料夾；提示框依序回答 answers，選輸出位置的對話框依序回答 folders

        Returns:
            跳出過的提示框（標題，訊息）
        """
        with mock.patch.object(QFileDialog, "getExistingDirectory", side_effect=[self.root, *folders]), \
                answering_prompts(*answers) as shown:
            self.trigger_menu(t("menu.tools.compress_folder"))
        return shown

    def output_files(self) -> List[str]:
        """預設輸出位置裡的檔案（相對路徑）"""
        return sorted(
            os.path.relpath(os.path.join(folder, name), self.output_dir)
            for folder, _, names in os.walk(self.output_dir) for name in names
        )


class TestMainWindowCompressBatch(MainWindowCompressBatchTestCase):
    """壓縮一整批：整個資料夾或多選檔，輸出照原本的子資料夾結構"""

    def test_folder_is_compressed_into_the_folder_beside_it_keeping_subfolders(self):
        shown = self.start_folder(t("compress.output.start"))
        self.assertEqual(shown, [(t("compress.title"), t("compress.output.message", path=self.output_dir))])
        with answering_prompts() as shown:
            self.finish()
        self.assertEqual(self.output_files(), ["Flute.pdf", os.path.join("Strings", "Violin.pdf")])
        self.assertEqual(file_digest(os.path.join(self.output_dir, "Strings", "Violin.pdf")), file_digest(self.violin))
        self.assertEqual(len(shown), 1)
        self.assertIn(os.path.join("Strings", "Violin.pdf"), shown[0][1])

    def test_multiselected_files_go_into_the_folder_beside_them(self):
        oboe = scanned_pdf(os.path.join(self.root, "Oboe.pdf"), dpi=300)
        with mock.patch.object(QFileDialog, "getOpenFileNames", return_value=([self.source, oboe], "")), \
                answering_prompts(t("compress.output.start")):
            self.trigger_menu(t("menu.tools.compress_pdf"))
        with answering_prompts():
            self.finish()
        self.assertEqual(self.output_files(), ["Flute.pdf", "Oboe.pdf"])

    def test_progress_shows_which_file_of_how_many(self):
        self.start_folder(t("compress.output.start"))
        wait_until(lambda: self.status_shows(t("compress.progress.starting", index=1, total=2, name="Flute.pdf")))
        with answering_prompts():
            self.finish()

    def test_output_inside_the_input_folder_is_refused(self):
        inside = os.path.join(self.root, "Strings")
        shown = self.start_folder(t("compress.output.change"), t("compress.output.cancel"), folders=[inside])
        self.assertIn((t("dialog.warning"), t("compress.output.blocked")), shown)
        self.assertTrue(self.compress_action().isEnabled())
        self.assertEqual(self.gated.results, [])

    def test_changed_output_location_is_used(self):
        elsewhere = os.path.join(self.temp_dir, "elsewhere")
        os.makedirs(elsewhere)
        self.start_folder(t("compress.output.change"), folders=[elsewhere])
        with answering_prompts():
            self.finish()
        self.assertTrue(os.path.isfile(os.path.join(elsewhere, "Strings", "Violin.pdf")))

    def test_files_that_would_be_overwritten_are_listed_and_confirmed_once(self):
        earlier = os.path.join(self.output_dir, "Strings", "Violin.pdf")
        os.makedirs(os.path.dirname(earlier))
        with open(earlier, "wb") as f:
            f.write(b"earlier output")
        shown = self.start_folder(t("compress.output.start"), QMessageBox.Yes)
        self.assertEqual(shown[1][1], t("compress.overwrite.message", names=os.path.join("Strings", "Violin.pdf")))
        with answering_prompts():
            self.finish()
        self.assertEqual(file_digest(earlier), file_digest(self.violin))

    def test_declining_to_overwrite_starts_nothing(self):
        earlier = os.path.join(self.output_dir, "Flute.pdf")
        os.makedirs(self.output_dir)
        with open(earlier, "wb") as f:
            f.write(b"earlier output")
        self.start_folder(t("compress.output.start"), QMessageBox.No)
        self.assertTrue(self.compress_action().isEnabled())
        self.assertEqual(self.output_files(), ["Flute.pdf"])

    def test_folder_without_pdfs_is_reported(self):
        empty = os.path.join(self.temp_dir, "Empty")
        os.makedirs(empty)
        with mock.patch.object(QFileDialog, "getExistingDirectory", return_value=empty), \
                answering_prompts() as shown:
            self.trigger_menu(t("menu.tools.compress_folder"))
        self.assertEqual(shown, [(t("dialog.info"), t("compress.no_pdfs"))])


class TestMainWindowCompressCompare(MainWindowCompressBatchTestCase):
    """比較畫面：壓完先逐份確認，確認後才一次輸出；取消時什麼都不輸出"""

    size_limit = 100 * 1000

    def test_nothing_is_output_before_confirming_and_a_discarded_file_is_left_out(self):
        seen_before_confirming = []

        def keep_flute_discard_violin(dialog: QDialog):
            """記下確認前輸出位置有沒有東西，留下 Flute、不要 Violin 再輸出"""
            seen_before_confirming.append(os.path.exists(self.output_dir))
            files = dialog.findChild(QListWidget)
            self.assertEqual(
                [files.item(row).text().splitlines()[0] for row in range(files.count())],
                ["Flute.pdf", os.path.join("Strings", "Violin.pdf")],
            )
            files.setCurrentRow(0)
            button_in(dialog, QPushButton, t("compare.keep")).click()
            files.setCurrentRow(1)
            button_in(dialog, QPushButton, t("compare.drop")).click()
            button_in(dialog, QPushButton, t("compare.output")).click()

        self.start_folder(t("compress.output.start"))
        with answering_prompts():
            opened = self.finish(keep_flute_discard_violin)
        self.assertEqual(len(opened), 1)
        self.assertEqual(seen_before_confirming, [False])
        self.assertTrue(all(r.status is CompressionStatus.COMPRESSED for r in self.gated.results))
        self.assertEqual(self.output_files(), ["Flute.pdf"])
        self.assertEqual(self.scratch_files(), [])

    def test_cancelling_the_comparison_outputs_nothing_and_clears_the_compressed_files(self):
        self.start_folder(t("compress.output.start"))
        cancel = lambda dialog: button_in(dialog, QPushButton, t("compare.cancel")).click()
        with answering_prompts() as shown:
            self.finish(cancel)
        self.assertEqual(shown, [])
        self.assertFalse(os.path.exists(self.output_dir))
        self.assertEqual(self.scratch_files(), [])
        self.assertTrue(self.status_shows(t("compress.cancelled")))

    def test_discarding_every_file_outputs_nothing_and_says_so(self):
        def discard_all(dialog: QDialog):
            """兩份都選「不要這份」再輸出"""
            for _ in range(2):
                button_in(dialog, QPushButton, t("compare.drop")).click()
            button_in(dialog, QPushButton, t("compare.output")).click()

        self.start_folder(t("compress.output.start"))
        with answering_prompts() as shown:
            self.finish(discard_all)
        self.assertEqual(shown, [])
        self.assertFalse(os.path.exists(self.output_dir))
        self.assertEqual(self.scratch_files(), [])
        self.assertTrue(self.status_shows(t("compress.status.nothing")))

    def test_compare_view_opens_compressed_files_with_the_shared_opener(self):
        def open_first(dialog: QDialog):
            """選第一份並按「開啟」"""
            dialog.findChild(QListWidget).setCurrentRow(0)
            button_in(dialog, QPushButton, t("compare.open")).click()

        self.start_folder(t("compress.output.start"))
        with answering_prompts():
            self.finish(open_first)
        self.assertEqual(len(self.opener.opened), 1)
        self.assertEqual(os.path.basename(self.opener.opened[0]), "Flute.pdf")
        self.assertTrue(self.opener.opened[0].startswith(self.scratch_dir))


class TestMainWindowCompressBatchCancel(MainWindowCompressBatchTestCase):
    """取消整批：已壓好的也丟掉，輸出位置沒有任何新檔"""

    size_limit = 1000

    def test_cancelling_a_batch_delivers_nothing(self):
        self.start_folder(t("compress.output.start"))
        self.click_button(t("compress.cancel"))
        with answering_prompts():
            self.finish()
        self.assertFalse(os.path.exists(self.output_dir))
        self.assertEqual(self.scratch_files(), [])
        self.assertTrue(self.status_shows(t("compress.cancelled")))


class TestSingleInstanceLaunch(unittest.TestCase):
    """啟動：第一個程式執行中時，第二個只提示已在執行中，不開主視窗、不檢查進行中紀錄"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, True)
        self.preferences_path = os.path.join(temp_dir, "preferences.json")
        self.scratch_dir = os.path.join(temp_dir, "temp")
        self.leftover = os.path.join(self.scratch_dir, "0123abcd", "Flute.pdf")
        os.makedirs(os.path.dirname(self.leftover))
        with open(self.leftover, "wb") as f:
            f.write(b"left by a crash")
        self.lock = InstanceLock(os.path.join(temp_dir, "instance.lock"))
        self.addCleanup(self.lock.release)
        self.log_dir = os.path.join(temp_dir, "logs")
        os.makedirs(self.log_dir)
        self.expired_log = os.path.join(self.log_dir, "2000-01-01.log")
        self.todays_log = os.path.join(self.log_dir, datetime.date.today().isoformat() + ".log")
        for path in (self.expired_log, self.todays_log):
            with open(path, "w", encoding="utf-8") as f:
                f.write("x")

    def _main_windows(self) -> set:
        return {w for w in QApplication.topLevelWidgets() if isinstance(w, MainWindow)}

    def _launch(self):
        """啟動並處理完排定的事件，回傳（主視窗或 None，跳出過的提示框，新開的主視窗）"""
        before = self._main_windows()
        with answering_prompts() as shown:
            window = launch(PreferencesService(self.preferences_path), self.lock, self.scratch_dir, self.log_dir)
            QApplication.processEvents()
        if window is not None:
            self.addCleanup(window.close)
        return window, shown, self._main_windows() - before

    def test_second_instance_only_says_already_running(self):
        first = OtherInstance(self.lock.lock_path)
        self.addCleanup(first.kill)
        window, shown, opened = self._launch()
        self.assertIsNone(window)
        self.assertEqual(shown, [(t("app.title"), t("app.already_running"))])
        self.assertEqual(opened, set())

    def test_second_instance_leaves_the_temp_folder_of_the_first_alone(self):
        first = OtherInstance(self.lock.lock_path)
        self.addCleanup(first.kill)
        self._launch()
        self.assertTrue(os.path.isfile(self.leftover))

    def test_leftovers_in_the_temp_folder_are_cleared_at_startup(self):
        window, shown, opened = self._launch()
        self.assertIsNotNone(window)
        self.assertFalse(os.path.exists(self.leftover))

    def test_expired_logs_are_removed_at_startup(self):
        window, shown, opened = self._launch()
        self.assertIsNotNone(window)
        self.assertFalse(os.path.exists(self.expired_log))
        self.assertTrue(os.path.exists(self.todays_log))

    def test_second_instance_leaves_the_logs_alone(self):
        first = OtherInstance(self.lock.lock_path)
        self.addCleanup(first.kill)
        self._launch()
        self.assertTrue(os.path.exists(self.expired_log))

    def test_opens_main_window_after_first_instance_exits(self):
        OtherInstance(self.lock.lock_path).exit()
        window, shown, opened = self._launch()
        self.assertEqual(opened, {window})
        self.assertEqual(shown, [])

    def test_lock_that_cannot_be_created_shows_the_error_without_opening_the_window(self):
        not_a_directory = os.path.join(os.path.dirname(self.lock.lock_path), "not-a-directory")
        with open(not_a_directory, "w") as f:
            f.write("")
        self.lock = InstanceLock(os.path.join(not_a_directory, "instance.lock"))
        with mock.patch.object(MainWindow, "prompt_pending_recovery") as prompt:
            window, shown, opened = self._launch()
        self.assertIsNone(window)
        self.assertEqual(opened, set())
        prompt.assert_not_called()
        self.assertEqual(len(shown), 1)
        title, message = shown[0]
        self.assertEqual(title, t("app.title"))
        self.assertIn("not-a-directory", message)


if __name__ == '__main__':
    unittest.main()
