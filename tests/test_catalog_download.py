# -*- coding: utf-8 -*-
"""
從譜庫下載的測試：專案的檔先存進假 Google、再清除本機那份，經下載服務放回原位；
另從版本資料夾下載到自選的資料夾。檢查放的位置、同名時不覆蓋、MD5 不符或中斷時不留檔，
以及清除後譜庫那份又重新存入過時下載最新的並交回新的指紋。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.catalog_download import folder_items
from core.catalog_errors import CatalogErrorKind
from core.locale import t
from services.activity_log import ActivityLog
from services.download_service import DownloadService
from services.file_service import FileService
from test_clear_local import ClearTestCase


def read(path: str) -> bytes:
    """檔案內容"""
    with open(path, "rb") as f:
        return f.read()


class DownloadTestCase(ClearTestCase):
    """專案的檔已存進譜庫並清除（情境見 DepositFixture），本機只剩只在譜庫的檔"""

    def setUp(self):
        super().setUp()
        self.downloads_temp = os.path.join(self.temp, "appdata-temp")
        self.downloader = DownloadService(FileService(), temp_dir=self.downloads_temp)

    def paths(self) -> list:
        """群組的總譜與分譜的路徑"""
        return [self.score.original_path] + [part.original_path for part in self.parts]

    def restore(self, should_cancel=lambda: False, sources=None):
        """把全部群組只在譜庫的檔下載回原位，回傳結果"""
        if sources is None:
            sources = self.downloader.restore_sources(self.project, [g.id for g in self.project.groups])
        return self.downloader.restore(sources, self.catalog, should_cancel, lambda progress: None)


class TestRestore(DownloadTestCase):
    """下載回原位：放回清除前的路徑與檔名，資料夾被刪了就重建"""

    def setUp(self):
        super().setUp()
        self.clear()

    def test_files_go_back_to_their_original_paths_and_deleted_folders_are_rebuilt(self):
        self.assertFalse(os.path.exists(self.out))
        outcome = self.restore()
        self.assertIsNone(outcome.error)
        self.assertEqual([read(path) for path in self.paths()], [b"score", "二胡 I".encode(), "琵琶".encode()])
        self.assertEqual(sorted(item.target for item in outcome.saved), sorted(self.paths()))
        self.assertEqual(self.downloader.restore_sources(self.project, [self.group.id]), [])

    def test_a_different_file_at_the_original_location_is_not_overwritten_and_is_listed(self):
        sources = self.downloader.restore_sources(self.project, [self.group.id])
        os.makedirs(os.path.dirname(self.parts[0].original_path))
        with open(self.parts[0].original_path, "wb") as f:
            f.write(b"someone else's file")
        outcome = self.restore(sources=sources)
        self.assertEqual(read(self.parts[0].original_path), b"someone else's file")
        self.assertEqual([item.target for item in outcome.occupied], [self.parts[0].original_path])
        self.assertEqual(outcome.not_done, [self.parts[0].original_path])
        self.assertEqual(read(self.parts[1].original_path), "琵琶".encode())

    def test_a_file_that_already_came_back_with_the_same_content_counts_as_restored(self):
        sources = self.downloader.restore_sources(self.project, [self.group.id])
        os.makedirs(os.path.dirname(self.parts[0].original_path))
        with open(self.parts[0].original_path, "wb") as f:
            f.write("二胡 I".encode())
        outcome = self.restore(sources=sources)
        self.assertEqual([item.target for item in outcome.skipped], [self.parts[0].original_path])
        self.assertEqual(outcome.occupied, [])

    def test_a_file_the_catalog_no_longer_has_is_listed_and_the_rest_come_back(self):
        self.google.drive.trash(self.registered_id("琵琶"))
        outcome = self.restore()
        self.assertEqual(outcome.unavailable, [self.parts[1].original_path])
        self.assertFalse(os.path.exists(self.parts[1].original_path))
        self.assertEqual(read(self.parts[0].original_path), "二胡 I".encode())


class TestRestorePlaceGone(DownloadTestCase):
    """一個群組連到的那處已不在譜庫：它的檔列為譜庫裡沒有，其他群組照常下載回來"""

    def test_the_other_groups_still_come_back(self):
        self.clear()
        gone = self.add_group_whose_place_goes()
        self.project.record_fingerprints({gone.score_file.original_path: "f" * 32})
        os.remove(gone.score_file.original_path)
        outcome = self.restore()
        self.assertIsNone(outcome.error)
        self.assertEqual(outcome.unavailable, [gone.score_file.original_path])
        self.assertEqual([read(path) for path in self.paths()], [b"score", "二胡 I".encode(), "琵琶".encode()])


class TestVerifiedDownload(DownloadTestCase):
    """先存成暫存檔、比對 Drive 的 MD5 一致才改成正式檔名；不一致、中斷或取消時不留半份檔案"""

    def setUp(self):
        super().setUp()
        self.clear()

    def leftovers(self) -> list:
        """輸出位置與暫存資料夾裡留下的所有檔"""
        found = []
        for root in (self.out, self.downloads_temp):
            for folder, _, names in os.walk(root):
                found += [os.path.join(folder, name) for name in names]
        return found

    def test_content_not_matching_the_md5_leaves_no_file(self):
        self.google.drive.corrupt_downloads(self.registered_id("二胡 I"))
        outcome = self.restore()
        self.assertEqual([item.target for item in outcome.mismatched], [self.parts[0].original_path])
        self.assertFalse(os.path.exists(self.parts[0].original_path))
        self.assertEqual(sorted(self.leftovers()), sorted([self.score.original_path, self.parts[1].original_path]))

    def test_an_interrupted_download_keeps_what_finished_leaves_no_half_file_and_pressing_again_completes(self):
        requests = self.google.requests
        self.restore(should_cancel=lambda: True)
        reading = self.google.requests - requests
        self.google.fail_at(self.google.requests + reading + 2, CatalogErrorKind.OFFLINE)
        outcome = self.restore()
        self.assertIs(outcome.error.kind, CatalogErrorKind.OFFLINE)
        self.assertEqual([item.target for item in outcome.saved], [self.score.original_path])
        self.assertEqual(self.leftovers(), [self.score.original_path])
        again = self.restore()
        self.assertIsNone(again.error)
        self.assertEqual([read(path) for path in self.paths()], [b"score", "二胡 I".encode(), "琵琶".encode()])

    def test_cancelling_stops_after_the_current_file_and_pressing_again_completes(self):
        asked = []

        def cancel_after_first():
            asked.append(True)
            return len(asked) > 1

        outcome = self.restore(should_cancel=cancel_after_first)
        self.assertTrue(outcome.cancelled)
        self.assertEqual([item.target for item in outcome.saved], [self.score.original_path])
        self.assertFalse(os.path.exists(self.parts[0].original_path))
        again = self.restore()
        self.assertEqual(sorted(item.target for item in again.saved), sorted(p.original_path for p in self.parts))


class TestRedepositedAfterClearing(DownloadTestCase):
    """清除之後譜庫那份又被重新存入過：下載最新的，交回新的指紋"""

    def test_the_latest_content_comes_back_and_the_fingerprint_follows_it(self):
        self.clear()
        pipa = self.registered_id("琵琶")
        self.google.drive.set_content(pipa, b"new pipa")
        outcome = self.restore()
        self.assertEqual(read(self.parts[1].original_path), b"new pipa")
        self.assertEqual(outcome.fingerprints[self.parts[1].original_path], self.google.drive.file(pipa).md5)
        self.project.record_fingerprints(outcome.fingerprints)
        self.assertEqual(self.parts[1].deposit_md5, self.google.drive.file(pipa).md5)
        self.assertTrue(self.project.is_modified())


class TestLog(DownloadTestCase):
    """下載的每份檔寫一筆紀錄檔"""

    def test_each_downloaded_file_is_logged(self):
        self.clear()
        log_dir = os.path.join(self.temp, "logs")
        downloader = DownloadService(FileService(), ActivityLog(FileService(), log_dir), self.downloads_temp)
        sources = downloader.restore_sources(self.project, [self.group.id])
        downloader.restore(sources, self.catalog, lambda: False, lambda progress: None)
        text = "".join(read(os.path.join(log_dir, name)).decode("utf-8") for name in os.listdir(log_dir))
        self.assertEqual(text.count(t("download.log.action")), 3)
        self.assertIn(self.parts[0].original_path, text)


class TestDownloadToFolder(DownloadTestCase):
    """一般下載：版本資料夾的檔下載到自選的資料夾；整個版本保留子資料夾，同名時絕不覆蓋"""

    def setUp(self):
        super().setUp()
        self.destination = os.path.join(self.temp, "下載")

    def pdfs(self):
        """版本頁讀到的版本資料夾裡全部的 PDF"""
        return self.catalog.edition_page("e1", "m1", self.project.orchestra_type).pdfs

    def download(self, keep_folders: bool, pdfs=None):
        """下載到 self.destination，回傳結果"""
        items = folder_items(self.pdfs() if pdfs is None else pdfs, self.destination, keep_folders, "春江花月夜")
        return self.downloader.download(items, self.catalog, lambda: False, lambda progress: None)

    def downloaded(self) -> dict:
        """自選的資料夾裡的檔：相對路徑（以 / 分隔）→ 內容"""
        found = {}
        for folder, _, names in os.walk(self.destination):
            for name in names:
                path = os.path.join(folder, name)
                found[os.path.relpath(path, self.destination).replace(os.sep, "/")] = read(path)
        return found

    def test_the_whole_edition_keeps_its_subfolders(self):
        outcome = self.download(keep_folders=True)
        self.assertIsNone(outcome.error)
        self.assertEqual(self.downloaded(), {
            "總譜.pdf": b"score", "分譜/二胡 I.pdf": "二胡 I".encode(), "分譜/琵琶.pdf": "琵琶".encode(),
        })

    def test_chosen_files_go_straight_into_the_folder(self):
        chosen = [pdf for pdf in self.pdfs() if pdf.path.startswith("分譜/")]
        self.download(keep_folders=False, pdfs=chosen)
        self.assertEqual(self.downloaded(), {"二胡 I.pdf": "二胡 I".encode(), "琵琶.pdf": "琵琶".encode()})

    def test_a_same_named_file_with_the_same_content_is_skipped(self):
        os.makedirs(self.destination)
        with open(os.path.join(self.destination, "總譜.pdf"), "wb") as f:
            f.write(b"score")
        outcome = self.download(keep_folders=True)
        self.assertEqual([os.path.basename(item.target) for item in outcome.skipped], ["總譜.pdf"])
        self.assertEqual(len(outcome.saved), 2)
        self.assertNotIn("總譜 (1).pdf", self.downloaded())

    def test_a_same_named_file_with_different_content_gets_a_suffix_and_downloading_again_adds_nothing(self):
        os.makedirs(self.destination)
        with open(os.path.join(self.destination, "總譜.pdf"), "wb") as f:
            f.write(b"my own score")
        self.download(keep_folders=True)
        again = self.download(keep_folders=True)
        found = self.downloaded()
        self.assertEqual(found["總譜.pdf"], b"my own score")
        self.assertEqual(found["總譜 (1).pdf"], b"score")
        self.assertNotIn("總譜 (2).pdf", found)
        self.assertEqual((len(again.saved), len(again.skipped)), (0, 3))

    def test_drive_folder_names_cannot_lead_outside_the_chosen_folder(self):
        sneaky = self.google.drive.add_folder("..", self.folder)
        self.google.drive.add_file("x.pdf", sneaky, b"x")
        self.download(keep_folders=True)
        self.assertEqual(self.downloaded()["_/x.pdf"], b"x")
        self.assertFalse(os.path.exists(os.path.join(self.temp, "x.pdf")))


if __name__ == "__main__":
    unittest.main()
