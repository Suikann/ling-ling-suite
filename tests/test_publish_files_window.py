# -*- coding: utf-8 -*-
"""
發譜預覽「檔案」「公演」「確認」步的接線測試（offscreen）

「檔案」步列出每份要發的檔、要取代的與別人放的；超過大小上限的檔在背景壓縮，壓縮完成前「發譜」按不下去，
「比較」開共用的比較畫面，「不發這份」把檔從要發的拿掉；只在譜庫的檔跳過，旁邊是「從譜庫下載回來」。
「公演」步複選候選場次；「確認」步的合計與前幾步一致。主視窗接假 Google 與假 HuaXia，不測畫面。
"""
import datetime
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QCheckBox, QGroupBox, QLabel, QPushButton, QTableWidget, QWidget

from core.locale import t
from core.models import CatalogLink
from services.compression_service import CompressionService
from services.file_service import FileService
from services.huaxia_client import lingling_storage_path
from tests.synthetic_pdfs import scanned_pdf
from tests.test_main_window import GatedCompressor, button_in, comparing, wait_until
from tests.test_publish_window import PublishWindowTestCase, button, choose
from ui.compress_pdf import size_text
from ui.publish_dialog import PublishPreviewDialog

KB = 1000


def files_group(dialog: PublishPreviewDialog, name: str) -> QGroupBox:
    """「檔案」步裡這個群組的那一塊"""
    return next(box for box in dialog.findChildren(QGroupBox, "files_group") if box.title() == name)


def file_row(dialog: PublishPreviewDialog, group: str, voice: str) -> list:
    """「檔案」步裡這個群組這個聲部那一列的前五欄文字"""
    table = files_group(dialog, group).findChild(QTableWidget, "files_table")
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).text() == voice)
    return [table.item(row, column).text() for column in range(5)]


def compression_cell(dialog: PublishPreviewDialog, group: str, voice: str) -> QWidget:
    """「檔案」步裡這個群組這個聲部那一列的壓縮欄"""
    table = files_group(dialog, group).findChild(QTableWidget, "files_table")
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).text() == voice)
    return table.cellWidget(row, 5)


def confirm_text(dialog: PublishPreviewDialog, group: str, name: str) -> str:
    """「確認」步裡這個群組這一行的文字"""
    box = next(b for b in dialog.findChildren(QGroupBox, "confirm_group") if b.title() == group)
    return box.findChild(QLabel, name).text()


def go_to(dialog: PublishPreviewDialog, step: int):
    """「烽火操」挑現有的曲目，一路按下一步到第 step 步（從 0 起算）"""
    choose(dialog, "烽火操", "existing")
    for _ in range(step):
        button(dialog, "publish.next").click()


class FilesWindowTestCase(PublishWindowTestCase):
    """「烽火操」對到 HuaXia 的「長城隨想－烽火操」，上面有「二胡1」「琵琶」兩個聲部"""

    def setUp(self):
        super().setUp()
        self.erhu = self.huaxia.add_part(self.fenghuo, "二胡1", needed="6-8", needed_min=6, sort=1)
        self.pipa = self.huaxia.add_part(self.fenghuo, "琵琶", needed="2", needed_min=2, sort=2)
        self.out = os.path.join(self.temp_dir, "out")


class TestFilesStep(FilesWindowTestCase):
    """「檔案」步列出每份檔要取代的與別人放的；只在譜庫的跳過"""

    def test_each_voice_shows_what_it_replaces_and_what_others_put(self):
        self.huaxia.add_score(
            self.fenghuo, self.erhu, lingling_storage_path(self.fenghuo, "1" * 64), title="二胡一（舊）",
        )
        self.huaxia.add_score(self.fenghuo, self.pipa, f"{self.fenghuo}/{self.pipa}/1_bow.pdf", title="弓法版")

        def operate(dialog):
            go_to(dialog, 2)
            erhu = file_row(dialog, "烽火操", "二胡 I")
            self.assertEqual(erhu[1:4], ["01-二胡 I.pdf", size_text(3), "二胡一（舊）"])
            self.assertEqual(erhu[4], "")
            pipa = file_row(dialog, "烽火操", "琵琶")
            self.assertEqual(pipa[3:], [t("publish.files.new"), t("publish.files.untouched", titles="弓法版")])
            self.assertEqual(file_row(dialog, "烽火操", t("publish.files.score"))[1], "00-總譜.pdf")
        self.publish(operate=operate)

    def test_a_catalog_only_file_is_skipped_with_a_download_button(self):
        group = self.window.project.groups[0]
        group.catalog_link = CatalogLink("e1")
        self.window.project.record_fingerprints({os.path.join(self.out, "02-琵琶.pdf"): "md5-pipa"})
        self.window.project.mark_saved()
        os.remove(os.path.join(self.out, "02-琵琶.pdf"))
        with mock.patch.object(self.window, "download_back") as download_back:
            def operate(dialog):
                go_to(dialog, 2)
                box = files_group(dialog, "烽火操")
                skipped = box.findChild(QLabel, "files_skipped").text()
                self.assertIn(t("publish.files.catalog_only", files="02-琵琶.pdf"), skipped)
                button_in(box, QPushButton, t("download.back")).click()
            self.publish(operate=operate)
        download_back.assert_called_once_with([group.id])


class CompressingTestCase(FilesWindowTestCase):
    """二胡 I 的分譜是超過大小上限（100 KB）的掃描檔；壓縮服務等測試放行才開始"""

    def compressor(self) -> CompressionService:
        """會等測試放行、大小上限 100 KB 的壓縮服務"""
        self.gated = GatedCompressor(FileService(), os.path.join(self.temp_dir, "temp"), size_limit=100 * KB)
        return self.gated

    def setUp(self):
        super().setUp()
        self.addCleanup(self.gated.gate.set)
        self.erhu_path = os.path.join(self.out, "01-二胡 I.pdf")
        scanned_pdf(self.erhu_path, dpi=300)
        self.erhu_size = os.path.getsize(self.erhu_path)

    def finish_compressing(self):
        """放行壓縮並等它做完"""
        self.gated.gate.set()
        wait_until(lambda: self.dialog.findChild(QLabel, "compression_status").isHidden())

    def state_of(self, voice: str) -> str:
        """目前開著的預覽裡這個聲部的壓縮狀態"""
        return compression_cell(self.dialog, "烽火操", voice).findChild(QLabel, "compression_state").text()


class TestCompression(CompressingTestCase):
    """超過上限的檔在背景壓縮；壓縮完成前「發譜」按不下去"""

    def test_publish_waits_for_compression(self):
        def operate(dialog):
            self.dialog = dialog
            go_to(dialog, 4)
            publish = button(dialog, "publish.publish")
            self.assertFalse(publish.isHidden())
            self.assertFalse(publish.isEnabled())
            self.finish_compressing()
            self.assertTrue(publish.isEnabled())
        self.publish(operate=operate)

    def test_compare_opens_the_shared_compare_screen_and_dropping_there_drops_the_file(self):
        def drop(compare):
            button_in(compare, QPushButton, t("compare.drop")).click()
            button_in(compare, QPushButton, t("publish.files.compare_done")).click()

        def operate(dialog):
            self.dialog = dialog
            go_to(dialog, 2)
            self.finish_compressing()
            with comparing(drop) as opened:
                button_in(compression_cell(dialog, "烽火操", "二胡 I"), QPushButton, t("publish.files.compare")).click()
            self.assertEqual(len(opened), 1)
            plan = next(p for p in dialog.publish_plan() if p.group.name == "烽火操")
            self.assertNotIn(self.erhu_path, [upload.file.path for upload in plan.uploads])
        self.publish(operate=operate)

    def test_open_shows_the_compressed_file(self):
        def operate(dialog):
            self.dialog = dialog
            go_to(dialog, 2)
            self.finish_compressing()
            button_in(compression_cell(dialog, "烽火操", "二胡 I"), QPushButton, t("publish.files.open")).click()
        self.publish(operate=operate)
        self.assertEqual(self.opener.opened, [self.gated.results[0].output_path])

    def test_dropping_a_file_takes_it_out_of_the_confirmation(self):
        def operate(dialog):
            self.dialog = dialog
            go_to(dialog, 2)
            self.finish_compressing()
            compressed = self.gated.results[0].compressed_size
            dialog_back = button(dialog, "publish.back")
            button(dialog, "publish.next").click()
            button(dialog, "publish.next").click()
            self.assertEqual(
                confirm_text(dialog, "烽火操", "confirm_files"),
                t("publish.confirm.files", count=3, size=size_text(compressed + 6), replaced=0),
            )
            dialog_back.click()
            dialog_back.click()
            button_in(compression_cell(dialog, "烽火操", "二胡 I"), QPushButton, t("publish.files.drop")).click()
            button(dialog, "publish.next").click()
            button(dialog, "publish.next").click()
            self.assertEqual(
                confirm_text(dialog, "烽火操", "confirm_files"),
                t("publish.confirm.files", count=2, size=size_text(6), replaced=0),
            )
            self.assertTrue(button(dialog, "publish.publish").isEnabled())
        self.publish(operate=operate)

    def test_closing_the_preview_discards_the_compressed_files(self):
        def operate(dialog):
            self.dialog = dialog
            go_to(dialog, 2)
            self.finish_compressing()
        self.publish(operate=operate)
        self.assertFalse(os.path.exists(self.gated.results[0].output_path))


class TestEventsAndConfirm(FilesWindowTestCase):
    """「公演」步複選候選場次，「確認」步列出要寫進 HuaXia 的變動與合計"""

    def setUp(self):
        super().setUp()
        now = datetime.datetime.now(datetime.timezone.utc)
        semester = self.huaxia.add_semester("115-1", is_current=True)
        for title, days in (("期末公演", 60), ("校慶音樂會", 30)):
            starts = now + datetime.timedelta(days=days)
            self.huaxia.add_event(semester, title, starts, starts + datetime.timedelta(hours=2), location="中正堂")
        self.huaxia.add_score(self.fenghuo, self.erhu, lingling_storage_path(self.fenghuo, "1" * 64))

    def test_concerts_are_offered_with_the_note_and_chosen_ones_are_confirmed(self):
        def operate(dialog):
            go_to(dialog, 3)
            concerts = dialog.findChildren(QCheckBox, "concert")
            self.assertEqual([c.text().split("　")[0] for c in concerts], ["校慶音樂會", "期末公演"])
            self.assertEqual(
                [n.text() for n in dialog.findChildren(QLabel, "concert_note")], [t("publish.events.note")] * 2,
            )
            concerts[1].click()
            button(dialog, "publish.next").click()
            self.assertEqual(
                dialog.findChild(QLabel, "confirm_events").text(), t("publish.confirm.events", events="期末公演"),
            )
            self.assertEqual([e.title for e in dialog.publish_plan()[0].events], ["期末公演"])
        self.publish(operate=operate)

    def test_the_confirmation_adds_up_the_files(self):
        def operate(dialog):
            go_to(dialog, 4)
            self.assertEqual(
                confirm_text(dialog, "烽火操", "confirm_files"),
                t("publish.confirm.files", count=3, size=size_text(9), replaced=1),
            )
            self.assertEqual(
                dialog.findChild(QLabel, "confirm_total").text(),
                t("publish.confirm.total", groups=1, count=3, size=size_text(9)),
            )
            self.assertTrue(button(dialog, "publish.publish").isEnabled())
        self.publish(operate=operate)


if __name__ == "__main__":
    unittest.main()
