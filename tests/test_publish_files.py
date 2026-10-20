# -*- coding: utf-8 -*-
"""
發譜預覽「檔案」「公演」步的規則測試

對專案模型、臨時目錄裡的檔與假 HuaXia 測：按下發譜時讀出可以挑的曲目上的樂譜與本學期還沒結束的公演；
每個聲部要取代的只是同一個聲部底下、泠靈發的那份（以泠靈的 Storage 路徑格式辨認，網頁上改了樂譜名稱也認得），
總譜是聲部空白、泠靈發的那份；別人放的檔只標出、不取代；只在譜庫與找不到的檔跳過。
"""
import datetime
import hashlib
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.models import CatalogLink, FileInfo, Headcount, HuaXiaLink, Project
from services.file_service import FileService
from services.huaxia_client import lingling_storage_path
from services.publish_service import (
    Fingerprints, group_publish, plan_deletions, plan_files, plan_voices, storage_slot,
)
from test_publish_preflight import PrepareTestCase


def fingerprint(n: int) -> str:
    """第 n 份內容的 SHA-256（十六進位，假的）"""
    return f"{n:064x}"


class FilesTestCase(PrepareTestCase):
    """臨時目錄裡已重新命名的「烽火操」（總譜、二胡 I、琵琶），對到假 HuaXia 上的「長城隨想－烽火操」

    HuaXia 上的那首有「二胡1」「琵琶」兩個聲部。
    """

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        out = os.path.join(self.temp_dir, "out")
        os.makedirs(out)
        self.project = Project(master_template="{序號}-{樂器}.pdf", output_directory=out)
        self.group = self.project.add_group("烽火操", "總譜")
        self.project.set_instruments(self.group, ["二胡 I", "琵琶"])
        names = ("00-總譜.pdf", "01-二胡 I.pdf", "02-琵琶.pdf")
        self.paths = [os.path.join(out, name) for name in names]
        for path, size in zip(self.paths, (300, 100, 200)):
            with open(path, "wb") as f:
                f.write(b"x" * size)
        self.project.add_files([FileInfo(path, os.path.basename(path)) for path in self.paths], self.group)
        self.project.set_headcount(self.group, 0, Headcount(6, 8))
        self.project.set_headcount(self.group, 1, Headcount(2))
        self.project.set_huaxia_link(self.group, HuaXiaLink(piece_id=self.fenghuo, piece_title="長城隨想－烽火操"))
        self.erhu = self.huaxia.add_part(self.fenghuo, "二胡1", needed="6-8", needed_min=6, sort=1)
        self.pipa = self.huaxia.add_part(self.fenghuo, "琵琶", needed="2", needed_min=2, sort=2)

    def plan(self):
        """重新按下發譜，算出這個群組的檔案"""
        readiness = self.service.prepare()
        voices = plan_voices(self.project, self.group, readiness.parts.get(self.fenghuo, []))
        return plan_files(self.group, voices, readiness.scores.get(self.fenghuo, []), FileService())

    def publish(self):
        """重新按下發譜，算出這個群組這次要寫進 HuaXia 的內容（含清舊檔要刪的 Storage 檔）"""
        readiness = self.service.prepare()
        voices = plan_voices(self.project, self.group, readiness.parts.get(self.fenghuo, []))
        files = plan_files(self.group, voices, readiness.scores.get(self.fenghuo, []), FileService())
        plan = group_publish(self.group, voices, files, {}, set(), [], 10 ** 9, Fingerprints(FileService()))
        return plan_deletions([plan])[0]

    def stored(self, index: int) -> str:
        """這個群組第 index 份本機檔（0 總譜、1 二胡 I、2 琵琶）照現在的內容發出去時的 Storage 路徑"""
        with open(self.paths[index], "rb") as f:
            content = f.read()
        return lingling_storage_path(
            self.fenghuo, hashlib.sha256(content).hexdigest(), storage_slot(("", "二胡 I", "琵琶")[index]),
        )

    def write(self, index: int, content: bytes) -> None:
        """改寫這個群組第 index 份本機檔的內容"""
        with open(self.paths[index], "wb") as f:
            f.write(content)

    def file(self, plan, path):
        """檔案計畫裡這份本機檔"""
        return next(f for f in plan.files if f.path == path)


class TestReplacing(FilesTestCase):
    """每個聲部只取代同一個聲部底下、泠靈發的那份"""

    def test_only_the_lingling_file_under_the_same_part_is_replaced(self):
        mine = self.huaxia.add_score(self.fenghuo, self.erhu, lingling_storage_path(self.fenghuo, fingerprint(1), "x"))
        pipa = self.huaxia.add_score(self.fenghuo, self.pipa, lingling_storage_path(self.fenghuo, fingerprint(2), "x"))
        erhu = self.file(self.plan(), self.paths[1])
        self.assertEqual([score.id for score in erhu.replaces], [mine])
        self.assertEqual([score.id for score in self.file(self.plan(), self.paths[2]).replaces], [pipa])

    def test_a_score_renamed_on_the_website_is_still_found(self):
        mine = self.huaxia.add_score(
            self.fenghuo, self.erhu, lingling_storage_path(self.fenghuo, fingerprint(1), "x"), title="01-二胡 I",
        )
        self.huaxia.rename_score(mine, "二胡一（弓法版）")
        self.assertEqual([score.id for score in self.file(self.plan(), self.paths[1]).replaces], [mine])

    def test_files_others_put_under_the_voice_are_marked_and_left_alone(self):
        web = self.huaxia.add_score(self.fenghuo, self.erhu, f"{self.fenghuo}/{self.erhu}/1760000000000_bow.pdf")
        erhu = self.file(self.plan(), self.paths[1])
        self.assertEqual(erhu.replaces, [])
        self.assertEqual([score.id for score in erhu.others], [web])

    def test_a_lingling_path_of_another_piece_is_not_replaced(self):
        moved = self.huaxia.add_score(self.fenghuo, self.erhu, lingling_storage_path(self.moli, fingerprint(1), "x"))
        erhu = self.file(self.plan(), self.paths[1])
        self.assertEqual(erhu.replaces, [])
        self.assertEqual([score.id for score in erhu.others], [moved])

    def test_the_full_score_replaces_the_lingling_score_without_a_voice(self):
        mine = self.huaxia.add_score(self.fenghuo, None, lingling_storage_path(self.fenghuo, fingerprint(3), "x"))
        web = self.huaxia.add_score(self.fenghuo, None, f"{self.fenghuo}/full/1760000000000_score.pdf")
        self.huaxia.add_score(self.fenghuo, None, lingling_storage_path(self.fenghuo, fingerprint(4), "x"), section="拉弦")
        self.huaxia.add_score(self.fenghuo, self.erhu, lingling_storage_path(self.fenghuo, fingerprint(5), "x"))
        score = self.file(self.plan(), self.paths[0])
        self.assertTrue(score.score)
        self.assertEqual([s.id for s in score.replaces], [mine])
        self.assertEqual([s.id for s in score.others], [web])

    def test_a_voice_created_on_huaxia_replaces_nothing(self):
        self.huaxia.remove_part(self.pipa)
        self.huaxia.add_score(self.fenghuo, self.erhu, lingling_storage_path(self.fenghuo, fingerprint(1), "x"))
        pipa = self.file(self.plan(), self.paths[2])
        self.assertIsNone(pipa.part)
        self.assertEqual((pipa.replaces, pipa.others), ([], []))


class TestNeverDeletingFilesInUse(FilesTestCase):
    """泠靈的 Storage 路徑只看曲目與內容：內容沒變的不重傳也不取代，清舊檔不刪還有登記或這次還要用的檔"""

    def test_an_unchanged_file_is_already_there(self):
        mine = self.huaxia.add_score(self.fenghuo, self.erhu, self.stored(1))
        erhu = self.file(self.plan(), self.paths[1])
        self.assertTrue(erhu.unchanged)
        self.assertEqual(erhu.replaces, [])
        plan = self.publish()
        self.assertNotIn(self.paths[1], [upload.file.path for upload in plan.uploads])
        self.assertEqual([file.path for file in plan.unchanged], [self.paths[1]])
        self.assertNotIn(mine, [score.id for score in plan.replaces])
        self.assertEqual(plan.deletes, [])

    def test_a_replaced_file_is_deleted_once_nothing_points_to_it(self):
        old = lingling_storage_path(self.fenghuo, fingerprint(1), "x")
        self.huaxia.add_score(self.fenghuo, self.erhu, old)
        self.huaxia.add_score(self.fenghuo, self.erhu, old)
        self.assertEqual(self.publish().deletes, [old])

    def test_a_file_this_publish_still_uses_is_not_deleted(self):
        shared = self.stored(2)
        erhu = self.huaxia.add_score(self.fenghuo, self.erhu, shared)
        self.huaxia.add_score(self.fenghuo, self.pipa, shared)
        plan = self.publish()
        self.assertEqual([score.id for score in plan.replaces], [erhu])
        self.assertTrue(self.file(self.plan(), self.paths[2]).unchanged)
        self.assertEqual(plan.deletes, [])


class TestOneStorageFilePerScore(FilesTestCase):
    """每筆樂譜登記各有自己的 Storage 檔：內容相同的兩份檔路徑不同，同一個位置重發同樣的內容路徑不變"""

    def test_two_voices_with_the_same_content_get_their_own_paths(self):
        self.write(1, b"same pdf")
        self.write(2, b"same pdf")
        self.write(0, b"same pdf")
        paths = [file.storage_path for file in self.plan().files]
        self.assertEqual(len(set(paths)), 3)

    def test_publishing_the_same_content_to_the_same_voice_again_keeps_its_path(self):
        first = self.file(self.plan(), self.paths[1]).storage_path
        self.huaxia.add_score(self.fenghuo, self.erhu, first)
        erhu = self.file(self.plan(), self.paths[1])
        self.assertEqual(erhu.storage_path, first)
        self.assertTrue(erhu.unchanged)

    def test_a_path_registered_under_another_part_is_not_reused(self):
        # 二胡 I 上次登記在網頁建的「二胡」，這次改對到「二胡1」：那筆登記還在，這次要另傳一份
        earlier = self.huaxia.add_part(self.fenghuo, "二胡", sort=3)
        before = self.stored(1)
        self.huaxia.add_score(self.fenghuo, earlier, before)
        erhu = self.file(self.plan(), self.paths[1])
        self.assertNotEqual(erhu.storage_path, before)
        self.assertFalse(erhu.unchanged)
        self.huaxia.add_score(self.fenghuo, self.erhu, erhu.storage_path)
        again = self.file(self.plan(), self.paths[1])
        self.assertEqual((again.storage_path, again.unchanged), (erhu.storage_path, True))


class TestSkipping(FilesTestCase):
    """本機找不到的檔跳過；群組記著譜庫裡有一份的是只在譜庫"""

    def test_a_catalog_only_file_is_skipped(self):
        self.group.catalog_link = CatalogLink("e1")
        self.project.record_fingerprints({self.paths[1]: "md5-erhu"})
        os.remove(self.paths[1])
        plan = self.plan()
        erhu = self.file(plan, self.paths[1])
        self.assertTrue(erhu.catalog_only)
        self.assertEqual(plan.skipped, [erhu])
        self.assertNotIn(erhu, plan.publishing)

    def test_a_missing_file_without_a_catalog_copy_is_skipped_too(self):
        os.remove(self.paths[2])
        pipa = self.file(self.plan(), self.paths[2])
        self.assertTrue(pipa.missing)
        self.assertFalse(pipa.catalog_only)

    def test_sizes_come_from_the_files(self):
        plan = self.plan()
        self.assertEqual([(f.voice, f.size) for f in plan.publishing], [("二胡 I", 100), ("琵琶", 200), ("", 300)])


class TestConcerts(PrepareTestCase):
    """「公演」步的候選：本學期、還沒結束、類型含公演的場次，依開始時間"""

    def setUp(self):
        super().setUp()
        self.now = datetime.datetime.now(datetime.timezone.utc)
        self.semester = self.huaxia.add_semester("115-1", is_current=True)

    def event(self, title, days, kinds=("concert",), semester=None):
        """days 天後開始、開始後兩小時結束的場次"""
        starts = self.now + datetime.timedelta(days=days)
        return self.huaxia.add_event(
            semester or self.semester, title, starts, starts + datetime.timedelta(hours=2), kinds=kinds,
        )

    def concerts(self):
        """重新按下發譜時讀到的候選場次的名稱"""
        return [event.title for event in self.service.prepare().concerts]

    def test_only_upcoming_concerts_of_this_semester_are_offered(self):
        self.event("期末公演", 60)
        self.event("校慶音樂會", 30, kinds=("dress", "concert"))
        self.event("期中音樂會", -3)
        self.event("大團練習", 7, kinds=("tutti",))
        self.event("上學期的公演", 90, semester=self.huaxia.add_semester("114-2"))
        self.assertEqual(self.concerts(), ["校慶音樂會", "期末公演"])

    def test_a_concert_still_going_on_is_offered(self):
        starts = self.now - datetime.timedelta(hours=1)
        self.huaxia.add_event(self.semester, "正在演", starts, starts + datetime.timedelta(hours=2))
        self.assertEqual(self.concerts(), ["正在演"])

    def test_the_concerts_each_piece_is_already_on_are_read(self):
        final = self.event("期末公演", 60)
        self.event("校慶音樂會", 30)
        past = self.event("期中音樂會", -3)
        for event in (final, past):
            self.huaxia.attach_piece(event, self.fenghuo)
        self.assertEqual(self.service.prepare().attached, {self.fenghuo: {final}})

    def test_no_current_semester_offers_nothing(self):
        self.event("期末公演", 60)
        self.huaxia.add_semester("115-2", is_current=True)
        self.assertEqual(self.concerts(), [])


class TestStorageListing(PrepareTestCase):
    """列出 Storage 裡泠靈發到一首曲目的檔（重發時內容相同的不必再傳）"""

    def test_lists_the_files_under_a_piece_with_their_sizes(self):
        for n in range(3):
            self.huaxia.put_object(lingling_storage_path(self.fenghuo, fingerprint(n), "x"), b"x" * (n + 1))
        self.huaxia.put_object(lingling_storage_path(self.moli, fingerprint(9), "x"))
        self.huaxia.put_object(f"{self.fenghuo}/full/1760000000000_score.pdf")
        client = self.auth.client
        folder = lingling_storage_path(self.fenghuo, fingerprint(0), "x").rsplit("/", 1)[0]
        listed = client.stored_files(self.auth.access_token(), folder, page_size=2)
        self.assertEqual(listed, {f"{folder}/{fingerprint(n)}-x.pdf": n + 1 for n in range(3)})


if __name__ == "__main__":
    unittest.main()
