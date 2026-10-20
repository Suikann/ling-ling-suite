# -*- coding: utf-8 -*-
"""
版本頁的測試：經譜庫服務對假 Google 讀一處的編制與登記、列出版本資料夾裡沒登記的檔，再把指定的檔登記上去

狀態由分譜列有沒有登記 Drive 檔案推算；沒登記的檔是版本資料夾（含子資料夾）裡沒被任何分譜列登記的 PDF；
登記只在呼叫寫入時才送出，指定給已登記了檔的聲部時，原本那份丟進 Drive 垃圾桶。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.catalog_constants import SHEET_EDITIONS, SHEET_MOVEMENTS, SHEET_PARTS, SHEET_PIECES
from core.catalog_errors import CatalogErrorKind
from core.constants import OrchestraType
from fake_google_catalog import FakeGoogle
from services.catalog_service import CatalogService, PendingRegistration
from test_catalog_link import records
from test_catalog_service import add_row, empty_catalog


class VersionPageTestCase(unittest.TestCase):
    """假 Google 裡有一首分了樂章的曲目：版本 e1 指向資料夾「春江花月夜」，第一樂章 m1"""

    def setUp(self):
        self.google = FakeGoogle()
        empty_catalog(self.google)
        self.folder = self.google.drive.add_folder("春江花月夜")
        self.catalog = CatalogService(self.google.sheets, self.google.drive, sleep=lambda seconds: None)
        add_row(self.google, SHEET_PIECES, id="p1", title="春江花月夜", active_edition_id="e1")
        add_row(self.google, SHEET_EDITIONS, id="e1", piece_id="p1", drive_folder_id=self.folder)
        add_row(self.google, SHEET_MOVEMENTS, id="m1", piece_id="p1", movement_number="1", name="江樓鐘鼓")

    def part(self, voice: str, order: str, movement: str = "m1", **values: str) -> None:
        """在分譜工作表加一列"""
        add_row(
            self.google, SHEET_PARTS, id=f"pt-{voice}-{movement}", edition_id="e1", movement_id=movement,
            instrument_name=voice, sort_order=order, **values,
        )

    def page(self, movement: str = "m1"):
        """讀版本頁"""
        return self.catalog.edition_page("e1", movement, OrchestraType.CHINESE)


class TestStatus(VersionPageTestCase):
    """每個聲部的狀態由分譜列有沒有登記 Drive 檔案推算"""

    def test_a_voice_with_a_registered_file_is_present_and_one_without_is_missing(self):
        erhu = self.google.drive.add_file("二胡 I.pdf", self.folder)
        self.part("二胡 I", "1", drive_file_id=erhu, file_name="二胡 I.pdf", status="missing")
        self.part("琵琶", "2", status="present")
        page = self.page()
        self.assertEqual([voice for voice, _ in page.place.voices], ["二胡 I", "琵琶"])
        self.assertEqual(page.registration("二胡 I").file_name, "二胡 I.pdf")
        self.assertIsNone(page.registration("琵琶"))

    def test_the_score_row_is_shown_apart_from_the_voices(self):
        score = self.google.drive.add_file("總譜.pdf", self.folder)
        self.part("總譜", "0", is_score="TRUE", drive_file_id=score, file_name="總譜.pdf")
        self.part("琵琶", "1")
        page = self.page()
        self.assertEqual([voice for voice, _ in page.place.voices], ["琵琶"])
        self.assertEqual(page.score.file_id, score)


class TestUnregisteredFiles(VersionPageTestCase):
    """沒登記的檔：版本資料夾（含子資料夾）裡沒被任何分譜列登記的 PDF"""

    def test_lists_pdfs_in_the_folder_and_its_subfolders_that_no_part_row_registers(self):
        movement = self.google.drive.add_folder("江樓鐘鼓", self.folder)
        nested = self.google.drive.add_folder("拉弦", movement)
        self.google.drive.add_file("總譜.pdf", self.folder)
        self.google.drive.add_file("二胡 II.pdf", nested)
        registered = self.google.drive.add_file("二胡 I.pdf", nested)
        elsewhere = self.google.drive.add_file("琵琶.pdf", movement)
        thrown = self.google.drive.add_file("舊.pdf", self.folder)
        self.google.drive.trash(thrown)
        self.google.drive.add_file("說明.txt", self.folder, mime_type="text/plain")
        self.part("二胡 I", "1", drive_file_id=registered)
        self.part("琵琶", "1", movement="", drive_file_id=elsewhere)
        page = self.page()
        self.assertEqual(
            [pdf.path for pdf in page.unregistered], ["江樓鐘鼓/拉弦/二胡 II.pdf", "總譜.pdf"],
        )


class RegisterTestCase(VersionPageTestCase):
    """第一樂章有二胡 I（已登記舊檔）、琵琶（沒登記）；資料夾裡另有兩份沒登記的檔"""

    def setUp(self):
        super().setUp()
        self.old = self.google.drive.add_file("二胡 I 舊.pdf", self.folder)
        self.new = self.google.drive.add_file("二胡 I.pdf", self.folder)
        self.pipa = self.google.drive.add_file("琵琶.pdf", self.folder)
        self.part("二胡 I", "1", drive_file_id=self.old, file_name="二胡 I 舊.pdf")
        self.part("琵琶", "2")

    def row(self, voice: str) -> dict:
        """分譜工作表裡第一樂章這個聲部的一列"""
        return next(r for r in records(self.google, SHEET_PARTS) if r["instrument_name"] == voice)

    def file(self, file_id: str):
        """假 Drive 上的檔"""
        return self.google.drive.file(file_id)


class TestRegister(RegisterTestCase):
    """把沒登記的檔登記到聲部或總譜列；換掉原本登記的檔時舊的丟進 Drive 垃圾桶"""

    def test_registers_a_file_on_a_voice_without_one_in_one_write(self):
        self.catalog.register_files(PendingRegistration("e1", "m1", {"琵琶": self.file(self.pipa)}))
        self.assertEqual((self.row("琵琶")["drive_file_id"], self.row("琵琶")["file_name"]), (self.pipa, "琵琶.pdf"))
        self.assertEqual(len(self.google.sheets.writes), 1)
        self.assertEqual(self.page().registration("琵琶").file_id, self.pipa)

    def test_replacing_a_registered_file_throws_the_old_one_into_the_drive_trash(self):
        self.catalog.register_files(PendingRegistration("e1", "m1", {"二胡 I": self.file(self.new)}))
        self.assertEqual(self.row("二胡 I")["drive_file_id"], self.new)
        self.assertTrue(self.google.drive.is_trashed(self.old))
        self.assertFalse(self.google.drive.is_trashed(self.new))

    def test_the_score_row_is_added_when_the_place_has_none(self):
        score = self.google.drive.add_file("總譜.pdf", self.folder)
        self.catalog.register_files(PendingRegistration("e1", "m1", {}, score=self.file(score)))
        added = next(r for r in records(self.google, SHEET_PARTS) if r["is_score"] == "TRUE")
        self.assertEqual((added["edition_id"], added["movement_id"], added["drive_file_id"]), ("e1", "m1", score))
        self.assertEqual(self.page().score.file_id, score)
        self.assertEqual([voice for voice, _ in self.page().place.voices], ["二胡 I", "琵琶"])

    def test_sending_again_after_the_trash_failed_finishes_without_writing_twice(self):
        pending = PendingRegistration("e1", "m1", {"二胡 I": self.file(self.new)})
        self.google.fail_at(3, CatalogErrorKind.OFFLINE)
        with self.assertRaises(Exception):
            self.catalog.register_files(pending)
        self.assertEqual(self.row("二胡 I")["drive_file_id"], self.new)
        self.assertFalse(self.google.drive.is_trashed(self.old))
        self.catalog.register_files(pending)
        self.assertTrue(self.google.drive.is_trashed(self.old))
        self.assertEqual(len(self.google.sheets.writes), 1)

    def test_a_write_whose_response_was_lost_still_trashes_the_old_file_when_sent_again(self):
        pending = PendingRegistration("e1", "m1", {"二胡 I": self.file(self.new)})
        self.google.fail_at(2, CatalogErrorKind.OFFLINE, applied=True)
        with self.assertRaises(Exception):
            self.catalog.register_files(pending)
        self.catalog.register_files(pending)
        self.assertTrue(self.google.drive.is_trashed(self.old))
        self.assertEqual(self.row("二胡 I")["drive_file_id"], self.new)
