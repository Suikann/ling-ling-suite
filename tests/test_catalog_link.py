# -*- coding: utf-8 -*-
"""
群組連到譜庫的測試：對假 Google 讀一處的編制、新建曲目與版本，再交給專案連線，檢查群組與待寫入的改動
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.catalog_constants import (
    DRIVE_FOLDER_MIME,
    SHEET_COMPOSERS, SHEET_DURATIONS, SHEET_EDITIONS, SHEET_HEADERS, SHEET_MOVEMENTS, SHEET_PARTS, SHEET_PIECES,
    SHEET_SECTIONS,
)
from core.catalog_errors import CatalogError, CatalogErrorKind
from core.constants import OrchestraType
from core.models import CatalogChange, CatalogChangeKind, DuplicateVoicesError, FileInfo, Headcount, Project
from fake_google_catalog import FakeGoogle
from services.catalog_service import CatalogService, PendingPlace
from test_catalog_service import add_row, empty_catalog


class LinkTestCase(unittest.TestCase):
    """假 Google 裡有一首分了樂章的曲目：版本 e1、第一樂章 m1"""

    def setUp(self):
        self.google = FakeGoogle()
        empty_catalog(self.google)
        self.root = self.google.drive.add_folder("譜庫")
        self.catalog = CatalogService(self.google.sheets, self.google.drive, sleep=lambda seconds: None)
        add_row(self.google, SHEET_PIECES, id="p1", title="春江花月夜", active_edition_id="e1")
        add_row(self.google, SHEET_EDITIONS, id="e1", piece_id="p1", edition_label="彭修文編")
        add_row(self.google, SHEET_MOVEMENTS, id="m1", piece_id="p1", movement_number="1", name="江樓鐘鼓")

    def part(self, voice: str, order: str, movement: str = "m1", **values: str) -> None:
        """在分譜工作表加一個聲部"""
        add_row(
            self.google, SHEET_PARTS, id=f"pt-{voice}-{movement}", edition_id="e1", movement_id=movement,
            instrument_name=voice, sort_order=order, **values,
        )


class TestLinkTargets(LinkTestCase):
    """連線對話框挑選的曲目、版本與樂章，一次請求讀出"""

    def test_reads_pieces_editions_and_movements_in_one_request(self):
        add_row(self.google, SHEET_MOVEMENTS, id="m0", piece_id="p1", movement_number="0", name="引子", sort_order="0")
        targets = self.catalog.link_targets()
        self.assertEqual([p.title for p in targets.pieces], ["春江花月夜"])
        self.assertEqual([e.id for e in targets.editions_of("p1")], ["e1"])
        self.assertEqual([m.id for m in targets.movements_of("p1")], ["m0", "m1"])
        self.assertEqual(self.google.requests, 1)


class TestReadPlace(LinkTestCase):
    """讀一處（版本的整首或一個樂章）的編制、時長與社團的聲部組對照"""

    def test_reads_voices_in_order_with_headcounts_duration_and_club_sections(self):
        self.part("二胡 II", "2", headcount_min="4", headcount_max="6")
        self.part("二胡 I", "1", headcount_min="6", headcount_open_ended="TRUE")
        self.part("總譜", "0", is_score="TRUE")
        self.part("琵琶", "1", movement="")
        add_row(self.google, SHEET_DURATIONS, id="d1", edition_id="e1", movement_id="m1", seconds="425")
        add_row(self.google, SHEET_SECTIONS, id="s1", orchestra_type="chinese", instrument="二胡", section="拉弦")
        add_row(self.google, SHEET_SECTIONS, id="s2", orchestra_type="western", instrument="Flute", section="木管")
        place = self.catalog.read_place("e1", "m1", OrchestraType.CHINESE)
        self.assertEqual(place.voices, [("二胡 I", Headcount(6, None, True)), ("二胡 II", Headcount(4, 6))])
        self.assertEqual(place.duration_seconds, 425)
        self.assertEqual(place.sections, {"二胡": "拉弦"})
        self.assertEqual((place.piece_title, place.edition_label, place.movement_name), ("春江花月夜", "彭修文編", "江樓鐘鼓"))

    def test_whole_piece_is_its_own_place(self):
        self.part("二胡 I", "1")
        self.part("琵琶", "1", movement="")
        place = self.catalog.read_place("e1", "", OrchestraType.CHINESE)
        self.assertEqual([voice for voice, _ in place.voices], ["琵琶"])
        self.assertEqual(place.movement_name, "")

    def test_missing_columns_and_sheets_read_as_empty(self):
        old_parts = SHEET_HEADERS[SHEET_PARTS][:9]
        self.google.sheets.create_tab(SHEET_PARTS, [old_parts, ["pt1", "e1", "m1", "二胡 I", "1"]])
        tabs = {title: self.google.sheets.rows(title) for title in self.google.sheets.tabs()}
        google = FakeGoogle()
        for title, rows in tabs.items():
            if title not in (SHEET_DURATIONS, SHEET_SECTIONS):
                google.sheets.create_tab(title, rows)
        catalog = CatalogService(google.sheets, google.drive, sleep=lambda seconds: None)
        place = catalog.read_place("e1", "m1", OrchestraType.CHINESE)
        self.assertEqual(place.voices, [("二胡 I", Headcount())])
        self.assertIsNone(place.duration_seconds)
        self.assertEqual(place.sections, {})

    def test_reads_with_two_requests(self):
        for n in range(30):
            self.part(f"聲部{n}", str(n))
        self.catalog.read_place("e1", "m1", OrchestraType.CHINESE)
        self.assertEqual(self.google.requests, 2)


def records(google: FakeGoogle, sheet: str) -> list:
    """工作表的每一列（不含標題列）轉成欄位名稱對值"""
    headers = SHEET_HEADERS[sheet]
    return [
        {h: (row[i] if i < len(row) else "") for i, h in enumerate(headers)} for row in google.sheets.rows(sheet)[1:]
    ]


class TestCreatePlace(LinkTestCase):
    """譜庫裡找不到時，用群組的曲名、作曲家、樂章編號與名稱新建；版本連同 Drive 資料夾"""

    def test_creates_piece_composer_edition_with_folder_and_movement(self):
        pending = PendingPlace(
            title="將軍令", composer="彭修文", root_folder_id=self.root, new_movement=True,
            movement_number="2", movement_name="點將",
        )
        place = self.catalog.create_place(pending, OrchestraType.CHINESE)
        piece = next(r for r in records(self.google, SHEET_PIECES) if r["title"] == "將軍令")
        composer = next(r for r in records(self.google, SHEET_COMPOSERS) if r["id"] == piece["composer_id"])
        self.assertEqual(composer["name"], "彭修文")
        edition = next(r for r in records(self.google, SHEET_EDITIONS) if r["piece_id"] == piece["id"])
        self.assertEqual(piece["active_edition_id"], edition["id"])
        folder = self.google.drive.file(edition["drive_folder_id"])
        self.assertEqual((folder.name, folder.mime_type, folder.parents), ("將軍令", DRIVE_FOLDER_MIME, (self.root,)))
        movement = next(r for r in records(self.google, SHEET_MOVEMENTS) if r["piece_id"] == piece["id"])
        self.assertEqual((movement["movement_number"], movement["name"]), ("2", "點將"))
        self.assertEqual((place.edition_id, place.movement_id), (edition["id"], movement["id"]))
        self.assertEqual((place.piece_title, place.movement_name, place.voices), ("將軍令", "點將", []))

    def test_reuses_a_composer_with_the_same_name_and_links_the_whole_piece(self):
        add_row(self.google, SHEET_COMPOSERS, id="c1", name="彭修文")
        place = self.catalog.create_place(
            PendingPlace(title="將軍令", composer="彭修文", root_folder_id=self.root), OrchestraType.CHINESE,
        )
        self.assertEqual(len(records(self.google, SHEET_COMPOSERS)), 1)
        piece = next(r for r in records(self.google, SHEET_PIECES) if r["title"] == "將軍令")
        self.assertEqual(piece["composer_id"], "c1")
        self.assertEqual(place.movement_id, "")
        self.assertEqual(len(records(self.google, SHEET_MOVEMENTS)), 1)

    def test_new_edition_of_an_existing_piece_keeps_its_active_edition(self):
        place = self.catalog.create_place(
            PendingPlace(title="春江花月夜", piece_id="p1", root_folder_id=self.root, movement_id="m1"),
            OrchestraType.CHINESE,
        )
        self.assertNotEqual(place.edition_id, "e1")
        self.assertEqual(place.movement_id, "m1")
        self.assertEqual(len(records(self.google, SHEET_PIECES)), 1)
        self.assertEqual(records(self.google, SHEET_PIECES)[0]["active_edition_id"], "e1")

    def test_new_movement_of_an_existing_edition_creates_only_the_movement(self):
        place = self.catalog.create_place(
            PendingPlace(title="春江花月夜", piece_id="p1", edition_id="e1", new_movement=True, movement_name="尾聲"),
            OrchestraType.CHINESE,
        )
        self.assertEqual(place.edition_id, "e1")
        self.assertEqual(len(records(self.google, SHEET_EDITIONS)), 1)
        movement = next(r for r in records(self.google, SHEET_MOVEMENTS) if r["id"] == place.movement_id)
        self.assertEqual((movement["movement_number"], movement["name"]), ("2", "尾聲"))

    def test_sending_again_after_a_failure_creates_nothing_twice(self):
        pending = PendingPlace(
            title="將軍令", composer="彭修文", root_folder_id=self.root, new_movement=True, movement_number="1",
        )
        for number in range(1, 30):
            self.google.fail_at(self.google.requests + number, CatalogErrorKind.OFFLINE)
            try:
                self.catalog.create_place(pending, OrchestraType.CHINESE)
                break
            except CatalogError:
                continue
        self.assertEqual(len(records(self.google, SHEET_PIECES)), 2)
        self.assertEqual(len(records(self.google, SHEET_COMPOSERS)), 1)
        self.assertEqual(len(records(self.google, SHEET_EDITIONS)), 2)
        self.assertEqual(len(records(self.google, SHEET_MOVEMENTS)), 2)
        self.assertEqual(len(self.google.drive.children(self.root)), 1)

    def test_without_a_root_folder_nothing_is_created(self):
        with self.assertRaises(CatalogError) as caught:
            self.catalog.create_place(PendingPlace(title="將軍令"), OrchestraType.CHINESE)
        self.assertEqual(caught.exception.kind, CatalogErrorKind.NOT_FOUND)
        self.assertEqual(len(records(self.google, SHEET_PIECES)), 1)


def part_file(name: str) -> FileInfo:
    """群組裡的一份分譜（只用到檔名）"""
    return FileInfo(f"/scores/{name}", name)


class ProjectLinkTestCase(LinkTestCase):
    """專案裡有一個「春江花月夜」第一樂章的群組；譜庫的第一樂章有二胡 I、二胡 II、琵琶、揚琴"""

    def setUp(self):
        super().setUp()
        self.project = Project()
        self.group = self.project.add_group("江樓鐘鼓", "總譜")
        self.project.update_group(self.group, piece_name="春江花月夜", movement_number="1", movement_name="江樓鐘鼓")

    def fill_catalog(self):
        """譜庫的第一樂章：四個聲部、時長 7:05"""
        self.part("二胡 I", "1", headcount_min="6", headcount_open_ended="TRUE")
        self.part("二胡 II", "2", headcount_min="4", headcount_max="6")
        self.part("琵琶", "3", headcount_min="2")
        self.part("揚琴", "4", headcount_min="1")
        add_row(self.google, SHEET_DURATIONS, id="d1", edition_id="e1", movement_id="m1", seconds="425")
        for n, (instrument, section) in enumerate((("二胡", "拉弦"), ("琵琶", "彈撥"), ("揚琴", "彈撥"))):
            add_row(
                self.google, SHEET_SECTIONS, id=f"s{n}", orchestra_type="chinese", instrument=instrument,
                section=section,
            )

    def link(self):
        """讀第一樂章並把群組連過去"""
        return self.project.link_group(self.group, self.catalog.read_place("e1", "m1", self.project.orchestra_type))


class TestLinkToFilledPlace(ProjectLinkTestCase):
    """譜庫那處已有編制、群組還沒有樂器表：編制與時長從譜庫帶入"""

    def setUp(self):
        super().setUp()
        self.fill_catalog()

    def test_group_without_files_takes_the_catalog_instrumentation_and_duration(self):
        result = self.link()
        self.assertFalse(result.needs_merge)
        self.assertEqual(self.group.instruments, ["二胡 I", "二胡 II", "琵琶", "揚琴"])
        self.assertEqual(self.group.headcounts, [Headcount(6, None, True), Headcount(4, 6), Headcount(2), Headcount(1)])
        self.assertEqual(self.group.present, [True, True, True, True])
        self.assertEqual(self.group.duration_seconds, 425)
        link = self.group.catalog_link
        self.assertEqual((link.edition_id, link.movement_id), ("e1", "m1"))
        self.assertEqual((link.piece_title, link.edition_label, link.movement_name), ("春江花月夜", "彭修文編", "江樓鐘鼓"))
        self.assertEqual(self.project.catalog_changes(self.group), [])

    def test_files_go_to_the_voices_named_in_their_file_names_and_the_rest_are_guessed_by_position(self):
        files = [part_file(n) for n in ("春江_琵琶.pdf", "春江_二胡1.pdf", "春江_中阮.pdf")]
        self.project.add_files(files, self.group)
        result = self.link()
        self.assertEqual(self.group.instruments, ["二胡 I", "二胡 II", "琵琶", "揚琴"])
        self.assertEqual(
            [(pair.voice, pair.file.display_name if pair.file else None) for pair in self.group.pairing()],
            [("二胡 I", "春江_二胡1.pdf"), ("二胡 II", "春江_中阮.pdf"), ("琵琶", "春江_琵琶.pdf")],
        )
        self.assertEqual(result.guessed, ["二胡 II"])
        self.assertEqual(self.group.present, [True, True, True, False])

    def test_files_beyond_the_catalog_voices_move_to_ungrouped(self):
        names = ("曲_二胡1.pdf", "曲_二胡2.pdf", "曲_琵琶.pdf", "曲_揚琴.pdf", "曲_排鼓.pdf")
        files = [part_file(n) for n in names]
        self.project.add_files(files, self.group)
        result = self.link()
        self.assertEqual([f.display_name for f in result.moved], ["曲_排鼓.pdf"])
        self.assertEqual([f.display_name for f in self.project.ungrouped_files], ["曲_排鼓.pdf"])
        self.assertEqual(result.guessed, [])

    def test_group_that_already_has_an_instrument_list_waits_for_the_merge_table(self):
        self.project.set_instruments(self.group, ["笛子", "笙"])
        result = self.link()
        self.assertTrue(result.needs_merge)
        self.assertEqual(self.group.instruments, ["笛子", "笙"])
        self.assertIsNone(self.group.catalog_link)


class TestLinkToEmptyPlace(ProjectLinkTestCase):
    """譜庫那處還沒有任何聲部：不問，群組的編制留著，記成下次存檔要寫進去的改動"""

    def test_keeps_the_group_instrumentation_and_records_it_as_pending(self):
        self.project.set_instruments(self.group, ["二胡 I", "琵琶"])
        self.project.set_headcount(self.group, 0, Headcount(6))
        self.project.set_duration(self.group, 300)
        result = self.link()
        self.assertFalse(result.needs_merge)
        self.assertEqual(self.group.instruments, ["二胡 I", "琵琶"])
        self.assertEqual(self.group.duration_seconds, 300)
        changes = self.project.catalog_changes(self.group)
        self.assertIn(CatalogChange(CatalogChangeKind.VOICES, "", (), ("二胡 I", "琵琶")), changes)
        self.assertIn(CatalogChange(CatalogChangeKind.HEADCOUNT, "二胡 I", None, Headcount(6)), changes)
        self.assertIn(CatalogChange(CatalogChangeKind.DURATION, "", None, 300), changes)
        self.assertNotIn("琵琶", [c.voice for c in changes if c.kind is CatalogChangeKind.HEADCOUNT])

    def test_catalog_duration_wins_even_when_the_place_has_no_voices(self):
        add_row(self.google, SHEET_DURATIONS, id="d1", edition_id="e1", movement_id="m1", seconds="425")
        self.project.set_duration(self.group, 300)
        self.link()
        self.assertEqual(self.group.duration_seconds, 425)
        self.assertNotIn(CatalogChangeKind.DURATION, [c.kind for c in self.project.catalog_changes(self.group)])


class TestSectionsOnLink(ProjectLinkTestCase):
    """聲部組由社團對照決定；社團還沒有的樂器以專案的值記成改動"""

    def test_club_value_wins_and_missing_instruments_become_changes(self):
        add_row(self.google, SHEET_SECTIONS, id="s1", orchestra_type="chinese", instrument="二胡", section="拉弦")
        self.project.set_instruments(self.group, ["二胡 I", "二胡 II", "琵琶"])
        self.project.set_section("二胡 I", "低音")
        self.link()
        self.assertEqual(self.project.section_of("二胡 II"), "拉弦")
        changes = [c for c in self.project.catalog_changes(self.group) if c.kind is CatalogChangeKind.SECTION]
        self.assertEqual(changes, [CatalogChange(CatalogChangeKind.SECTION, "琵琶", "", "彈撥")])

    def test_changing_a_linked_section_afterwards_is_a_change(self):
        add_row(self.google, SHEET_SECTIONS, id="s1", orchestra_type="chinese", instrument="二胡", section="拉弦")
        self.project.set_instruments(self.group, ["二胡 I"])
        self.link()
        self.project.set_section("二胡 I", "低音")
        self.assertIn(
            CatalogChange(CatalogChangeKind.SECTION, "二胡 I", "拉弦", "低音"), self.project.catalog_changes(self.group),
        )


class TestDuplicateVoicesBlockLinking(ProjectLinkTestCase):
    """舊專案留下重名聲部的群組不能連，寫出是哪幾列"""

    def test_refuses_and_names_the_rows(self):
        self.project.set_instruments(self.group, ["二胡1", "琵琶", "二胡 I"])
        place = self.catalog.read_place("e1", "m1", self.project.orchestra_type)
        with self.assertRaises(DuplicateVoicesError) as caught:
            self.project.link_group(self.group, place)
        self.assertEqual(caught.exception.rows, [0, 2])
        self.assertIsNone(self.group.catalog_link)
        self.assertEqual(self.group.instruments, ["二胡1", "琵琶", "二胡 I"])


if __name__ == "__main__":
    unittest.main()
