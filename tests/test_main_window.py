# -*- coding: utf-8 -*-
"""
主視窗測試（offscreen）

只測主視窗與服務層的接線，不測畫面；以 QT_QPA_PLATFORM=offscreen 執行，不需要顯示器。
提示框、檔案對話框與預覽對話框以替換的方式模擬使用者的操作，不會跳出真的對話框；文字欄位以 QTest 模擬鍵入（QTest 只能輸入 ASCII 字元，中文會讓 Qt 直接中止）。
"""
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import contextmanager
from typing import Callable, List
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QFileDialog, QLabel, QLineEdit, QListWidget, QMenu, QMessageBox,
    QPushButton, QRadioButton, QTabWidget, QWidget,
)

from core.locale import set_locale, t
from core.models import FileInfo, Group, Project
from services.file_service import FileService
from main import launch
from services.instance_lock import InstanceLock
from services.preferences_service import PreferencesService
from services.project_service import ProjectService
from ui.main_window import MainWindow
from ui.preview_dialog import PreviewDialog
from tests.failing_writes import FailingWrites
from tests.other_instance import OtherInstance


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
        self.window = MainWindow(self.preferences)

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
        self.open_from_menu(Project(groups=[Group(name="g", score_label="", files=self.create_files("Flute.pdf"))]))
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

    def test_instruments_entered_after_adding_a_group_are_saved(self):
        self.click_button(t("group.add"))
        QTest.keyClicks(self._instrument_entry(), "Flute")
        QTest.keyClick(self._instrument_entry(), Qt.Key_Return)
        path = os.path.join(self.temp_dir, "saved.llproj")
        with answering_prompts(), saving_as(path):
            self.trigger_menu(t("menu.file.save"))
        self.assertEqual(ProjectService().load_project(path).groups[0].instruments, ["Flute"])



class TestSingleInstanceLaunch(unittest.TestCase):
    """啟動：第一個程式執行中時，第二個只提示已在執行中，不開主視窗、不檢查進行中紀錄"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, True)
        self.preferences_path = os.path.join(temp_dir, "preferences.json")
        self.lock = InstanceLock(os.path.join(temp_dir, "instance.lock"))
        self.addCleanup(self.lock.release)

    def _main_windows(self) -> set:
        return {w for w in QApplication.topLevelWidgets() if isinstance(w, MainWindow)}

    def _launch(self):
        """啟動並處理完排定的事件，回傳（主視窗或 None，跳出過的提示框，新開的主視窗）"""
        before = self._main_windows()
        with answering_prompts() as shown:
            window = launch(PreferencesService(self.preferences_path), self.lock)
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

    def test_opens_main_window_after_first_instance_exits(self):
        OtherInstance(self.lock.lock_path).exit()
        window, shown, opened = self._launch()
        self.assertEqual(opened, {window})
        self.assertEqual(shown, [])


if __name__ == '__main__':
    unittest.main()
