# -*- coding: utf-8 -*-
"""
譜庫服務的測試：對假 Google 驗證讀取的結果、寫入的共用規則與錯誤
"""
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.catalog_constants import (
    SHEET_COMPOSERS, SHEET_EDITIONS, SHEET_HEADERS, SHEET_MOVEMENTS, SHEET_PARTS, SHEET_PERFORMANCES, SHEET_PIECES,
    PublishingPlatform,
)
from core.catalog_errors import CatalogError, CatalogErrorKind
from fake_google_catalog import FakeGoogle
from services.catalog_service import CatalogService


def empty_catalog(google: FakeGoogle) -> None:
    """在假 Google 建出只有標題列的譜庫試算表"""
    for title, headers in SHEET_HEADERS.items():
        google.sheets.create_tab(title, [headers])


def add_row(google: FakeGoogle, sheet: str, **values: str) -> None:
    """依欄位名稱在工作表新增一列（不算請求）"""
    rows = google.sheets.rows(sheet)
    google.sheets.create_tab(sheet, rows + [[values.get(header, "") for header in SHEET_HEADERS[sheet]]])


class CatalogTestCase(unittest.TestCase):
    """建好假 Google 與注入它的譜庫服務；等待重試時不真的睡，只記下秒數"""

    def setUp(self):
        self.google = FakeGoogle()
        empty_catalog(self.google)
        self.waits = []
        self.catalog = CatalogService(self.google.sheets, self.google.drive, sleep=self.waits.append)


class TestOverview(CatalogTestCase):
    """譜庫視窗開啟時的總覽：曲目、作曲家、資料夾屬於哪首曲目、根資料夾內容"""

    def setUp(self):
        super().setUp()
        self.root = self.google.drive.add_folder("譜庫")
        self.folder = self.google.drive.add_folder("春江花月夜", self.root)
        self.google.drive.add_folder("未標記", self.root)
        self.google.drive.add_file("說明.pdf", self.root)
        self.google.drive.add_file("清單.xlsx", self.root, mime_type="application/vnd.ms-excel")
        add_row(self.google, SHEET_COMPOSERS, id="c1", name="彭修文")
        add_row(self.google, SHEET_PIECES, id="p1", composer_id="c1", title="春江花月夜")
        add_row(self.google, SHEET_EDITIONS, id="e1", piece_id="p1", drive_folder_id=self.folder)

    def test_reads_pieces_composers_and_which_folder_is_which_piece(self):
        overview = self.catalog.overview(self.root)
        self.assertEqual([p.title for p in overview.pieces], ["春江花月夜"])
        self.assertEqual([c.name for c in overview.composers], ["彭修文"])
        self.assertEqual(overview.folder_pieces[self.folder].id, "p1")
        self.assertEqual([f.name for f in overview.root.folders], ["春江花月夜", "未標記"])
        self.assertEqual([f.name for f in overview.root.pdfs], ["說明.pdf"])

    def test_request_count_does_not_grow_with_the_number_of_pieces(self):
        self.catalog.overview(self.root)
        few = self.google.requests
        for n in range(20):
            add_row(self.google, SHEET_PIECES, id=f"x{n}", title=f"曲{n}")
            add_row(self.google, SHEET_EDITIONS, id=f"y{n}", piece_id=f"x{n}")
        self.google.requests = 0
        self.catalog.overview(self.root)
        self.assertEqual(self.google.requests, few)

    def test_lists_every_page_of_a_large_folder(self):
        self.google.drive.page_size = 2
        overview = self.catalog.overview(self.root)
        self.assertEqual(len(overview.root.folders), 2)
        self.assertEqual(len(overview.root.pdfs), 1)

    def test_a_missing_root_folder_setting_is_not_found(self):
        with self.assertRaises(CatalogError) as raised:
            self.catalog.overview("")
        self.assertEqual(raised.exception.kind, CatalogErrorKind.NOT_FOUND)

    def test_a_failed_request_raises_its_kind_instead_of_an_empty_list(self):
        self.google.fail_at(1, CatalogErrorKind.OFFLINE)
        with self.assertRaises(CatalogError) as raised:
            self.catalog.overview(self.root)
        self.assertEqual(raised.exception.kind, CatalogErrorKind.OFFLINE)


class TestPieceDetail(CatalogTestCase):
    """一首曲目的完整資訊：作曲家、版本、樂章、分譜、演出紀錄"""

    def setUp(self):
        super().setUp()
        add_row(self.google, SHEET_COMPOSERS, id="c1", name="彭修文")
        add_row(self.google, SHEET_PIECES, id="p1", composer_id="c1", title="春江花月夜")
        add_row(self.google, SHEET_PIECES, id="p2", title="別首")
        add_row(self.google, SHEET_EDITIONS, id="e1", piece_id="p1", publisher="人民音樂")
        add_row(self.google, SHEET_EDITIONS, id="e2", piece_id="p2")
        add_row(self.google, SHEET_MOVEMENTS, id="m1", piece_id="p1", movement_number="1")
        add_row(self.google, SHEET_PARTS, id="t1", edition_id="e1", instrument_name="二胡 I")
        add_row(self.google, SHEET_PARTS, id="t2", edition_id="e2", instrument_name="琵琶")
        add_row(self.google, SHEET_PERFORMANCES, id="r1", piece_id="p1", venue="中山堂")

    def test_reads_everything_about_the_piece_in_one_request(self):
        detail = self.catalog.piece_detail("p1")
        self.assertEqual(detail.piece.title, "春江花月夜")
        self.assertEqual(detail.composer.name, "彭修文")
        self.assertEqual([e.publisher for e in detail.editions], ["人民音樂"])
        self.assertEqual([m.id for m in detail.movements], ["m1"])
        self.assertEqual([p.instrument_name for p in detail.parts], ["二胡 I"])
        self.assertEqual([p.venue for p in detail.performances], ["中山堂"])
        self.assertEqual(self.google.requests, 1)

    def test_an_unknown_piece_is_none(self):
        self.assertIsNone(self.catalog.piece_detail("nope"))

    def test_reads_what_another_computer_just_changed(self):
        self.catalog.piece_detail("p1")
        self.google.before_request(2, lambda: self.google.sheets.set_cell(SHEET_PIECES, "C2", "春江花月夜（新）"))
        self.assertEqual(self.catalog.piece_detail("p1").piece.title, "春江花月夜（新）")


class TestEditionFiles(CatalogTestCase):
    """版本資料夾的檔：根目錄的 PDF 與每個子資料夾的 PDF"""

    def test_lists_pdfs_in_the_folder_and_each_subfolder(self):
        edition = self.google.drive.add_folder("春江花月夜")
        first = self.google.drive.add_folder("1 樂章", edition)
        self.google.drive.add_folder("2 樂章", edition)
        self.google.drive.add_file("總譜.pdf", edition)
        self.google.drive.add_file("二胡.pdf", first)
        trashed = self.google.drive.add_file("舊的.pdf", first)
        self.google.drive.trash(trashed)
        files = self.catalog.edition_files(edition)
        self.assertEqual([f.name for f in files.root_pdfs], ["總譜.pdf"])
        self.assertEqual(
            [(folder.name, [f.name for f in pdfs]) for folder, pdfs in files.subfolders],
            [("1 樂章", ["二胡.pdf"]), ("2 樂章", [])],
        )


class TestFindByName(CatalogTestCase):
    """以名稱搜尋 Drive：名稱含反斜線或單引號時照樣找得到"""

    def test_names_with_backslashes_and_quotes_are_found(self):
        folder = self.google.drive.add_folder("版本")
        for name in ["O'Neil.pdf", "a\\b.pdf", "c\\'d.pdf", "e'\\f.pdf"]:
            file_id = self.google.drive.add_file(name, folder)
            self.google.drive.add_file(name, self.google.drive.add_folder("別處"))
            with self.subTest(name=name):
                self.assertEqual([f.id for f in self.catalog.find_by_name(folder, name)], [file_id])


class TestRateLimit(CatalogTestCase):
    """遇到 429 以指數退避自動重試"""

    def test_retries_after_rate_limits_and_succeeds(self):
        add_row(self.google, SHEET_PIECES, id="p1", title="春江花月夜")
        self.google.fail_next(CatalogErrorKind.RATE_LIMITED, times=3)
        detail = self.catalog.piece_detail("p1")
        self.assertEqual(detail.piece.title, "春江花月夜")
        self.assertEqual(self.waits, [1.0, 2.0, 4.0])

    def test_gives_up_after_five_retries(self):
        self.google.fail_next(CatalogErrorKind.RATE_LIMITED, times=6)
        with self.assertRaises(CatalogError) as raised:
            self.catalog.piece_detail("p1")
        self.assertEqual(raised.exception.kind, CatalogErrorKind.RATE_LIMITED)
        self.assertEqual(self.waits, [1.0, 2.0, 4.0, 8.0, 16.0])

    def test_other_errors_are_not_retried(self):
        self.google.fail_next(CatalogErrorKind.NO_PERMISSION)
        with self.assertRaises(CatalogError):
            self.catalog.piece_detail("p1")
        self.assertEqual((self.google.requests, self.waits), (1, []))


class TestAddRows(CatalogTestCase):
    """新增列：ID 不撞號、重送不產生重複列、值以純文字寫入"""

    def ids_from(self, *ids: str) -> None:
        """讓譜庫服務依序抽到這些 ID"""
        sequence = iter(ids)
        self.catalog = CatalogService(
            self.google.sheets, self.google.drive, sleep=self.waits.append, new_id=lambda: next(sequence),
        )

    def piece_rows(self):
        """曲目工作表的資料列（不含標題列）"""
        return self.google.sheets.rows(SHEET_PIECES)[1:]

    def test_adds_rows_with_new_ids(self):
        rows = self.catalog.new_rows(SHEET_PIECES, [{"title": "甲"}, {"title": "乙"}])
        ids = self.catalog.add_rows(rows)
        self.assertEqual([(r[0], r[2]) for r in self.piece_rows()], list(zip(ids, ["甲", "乙"])))
        self.assertTrue(all(len(i) == 8 for i in ids))
        self.assertEqual(len(set(ids)), 2)

    def test_an_id_already_in_the_sheet_is_drawn_again(self):
        add_row(self.google, SHEET_PIECES, id="aaaaaaaa", title="舊的")
        self.ids_from("aaaaaaaa", "bbbbbbbb")
        ids = self.catalog.add_rows(self.catalog.new_rows(SHEET_PIECES, [{"title": "新的"}]))
        self.assertEqual(ids, ["bbbbbbbb"])
        self.assertEqual([r[0] for r in self.piece_rows()], ["aaaaaaaa", "bbbbbbbb"])

    def test_two_new_rows_never_share_an_id(self):
        self.ids_from("aaaaaaaa", "aaaaaaaa", "cccccccc")
        ids = self.catalog.add_rows(self.catalog.new_rows(SHEET_PIECES, [{"title": "甲"}, {"title": "乙"}]))
        self.assertEqual(ids, ["aaaaaaaa", "cccccccc"])

    def test_sending_again_after_a_lost_response_adds_no_duplicate_rows(self):
        rows = self.catalog.new_rows(SHEET_PIECES, [{"title": "甲"}, {"title": "乙"}])
        # 第 1 次請求讀 ID，第 2 次新增：Google 做完了，回應卻沒收到
        self.google.fail_at(2, CatalogErrorKind.OFFLINE, applied=True)
        with self.assertRaises(CatalogError):
            self.catalog.add_rows(rows)
        ids = self.catalog.add_rows(rows)
        self.assertEqual([r[2] for r in self.piece_rows()], ["甲", "乙"])
        self.assertEqual([r[0] for r in self.piece_rows()], ids)

    def test_a_retry_after_a_rate_limit_adds_no_duplicate_rows(self):
        rows = self.catalog.new_rows(SHEET_PIECES, [{"title": "甲"}])
        self.google.fail_at(2, CatalogErrorKind.RATE_LIMITED, applied=True)
        self.catalog.add_rows(rows)
        self.assertEqual([r[2] for r in self.piece_rows()], ["甲"])

    def test_a_resend_after_a_rejected_request_adds_the_rows(self):
        rows = self.catalog.new_rows(SHEET_PIECES, [{"title": "甲"}, {"title": "乙"}])
        self.google.fail_at(2, CatalogErrorKind.OFFLINE)
        with self.assertRaises(CatalogError):
            self.catalog.add_rows(rows)
        self.catalog.add_rows(rows)
        self.assertEqual([r[2] for r in self.piece_rows()], ["甲", "乙"])

    def test_values_starting_with_equals_are_written_as_text(self):
        self.catalog.add_rows(self.catalog.new_rows(SHEET_PIECES, [{"title": "=SUM(A1:A2)"}]))
        self.assertEqual(self.piece_rows()[0][2], "=SUM(A1:A2)")


class TestWriteCells(CatalogTestCase):
    """一次寫入多格：一個請求、值以純文字寫入、429 後自動重試"""

    def test_writes_all_cells_in_one_request(self):
        add_row(self.google, SHEET_PIECES, id="p1", title="舊", genre="國樂")
        self.google.fail_next(CatalogErrorKind.RATE_LIMITED)
        self.catalog.write_cells([("'曲目'!C2", [["=新曲名"]]), ("'曲目'!G2", [["絲竹"]])])
        self.assertEqual(self.google.sheets.rows(SHEET_PIECES)[1][2], "=新曲名")
        self.assertEqual(self.google.sheets.rows(SHEET_PIECES)[1][6], "絲竹")
        self.assertEqual(self.google.requests, 2)


class TestPublishingPlatform(CatalogTestCase):
    """社團設定的「發譜平台」：讀到「華夏國樂社」為 HuaXia；沒有這一列或沒有這張工作表為沒有"""

    def club_settings(self, *rows):
        """社團設定工作表寫成標題列加 rows"""
        self.google.sheets.create_tab("社團設定", [["鍵", "值"], *rows])

    def test_huaxia_when_the_row_says_huaxia(self):
        self.club_settings(["發譜平台", "華夏國樂社"])
        self.assertIs(self.catalog.publishing_platform(), PublishingPlatform.HUAXIA)

    def test_surrounding_spaces_are_ignored(self):
        self.club_settings(["其他", "x"], [" 發譜平台 ", " 華夏國樂社 "])
        self.assertIs(self.catalog.publishing_platform(), PublishingPlatform.HUAXIA)

    def test_none_without_the_row(self):
        self.club_settings(["其他", "華夏國樂社"])
        self.assertIsNone(self.catalog.publishing_platform())

    def test_none_for_a_platform_it_does_not_know(self):
        self.club_settings(["發譜平台", "別的平台"])
        self.assertIsNone(self.catalog.publishing_platform())

    def test_none_without_the_sheet(self):
        google = FakeGoogle()
        google.sheets.create_tab("曲目", [SHEET_HEADERS[SHEET_PIECES]])
        catalog = CatalogService(google.sheets, google.drive, sleep=self.waits.append)
        self.assertIsNone(catalog.publishing_platform())

    def test_unreadable_raises_with_the_kind(self):
        self.club_settings(["發譜平台", "華夏國樂社"])
        self.google.fail_at(2, CatalogErrorKind.OFFLINE)
        with self.assertRaises(CatalogError) as raised:
            self.catalog.publishing_platform()
        self.assertEqual(raised.exception.kind, CatalogErrorKind.OFFLINE)


class TestWithoutGoogleClient(unittest.TestCase):
    """譜庫服務、關口與錯誤類別不載入 Google 程式庫（只有通事會，而且是用到時才載入）"""

    def test_service_modules_load_no_google_library(self):
        src = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
        code = (
            f"import sys; sys.path.insert(0, {src!r}); "
            "import services.catalog_service, services.catalog_ports, core.catalog_errors, services.google_catalog; "
            "print(sorted(m for m in sys.modules if m.split('.')[0] in ('google', 'googleapiclient', 'httplib2')))"
        )
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "[]")


if __name__ == '__main__':
    unittest.main()
