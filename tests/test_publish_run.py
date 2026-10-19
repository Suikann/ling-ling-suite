# -*- coding: utf-8 -*-
"""
三段式發譜的測試

對假 HuaXia、假 Google 與臨時目錄裡的檔測 PublishRunner：按下「發譜」時重查幹部身分與發譜函式、把要傳的檔複製成快照；
之後先寫「開始發譜」到譜庫的發譜紀錄（寫不進去就不發），再傳檔（先不登記）、每個群組呼叫一次發譜函式切換、
刪掉被取代的舊檔，最後補一列「發譜結果」，快照與壓縮檔在結束時刪掉。各段的中斷、失敗與重跑都收斂到同一個結果。
"""
import copy
import datetime
import hashlib
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.catalog_errors import CatalogErrorKind
from core.models import FileInfo, Headcount, HuaXiaLink, NewHuaXiaPiece, Project
from core.constants import CompressionStatus, HuaXiaEnsemble
from core.locale import t
from core.publish_log import PublishAction
from fake_google_catalog import FakeGoogle
from services.catalog_service import CatalogService
from services.activity_log import ActivityLog
from services.compression_service import CompressionResult, CompressionService
from services.file_service import FileService
from services.huaxia_client import PUBLISH_INTERFACE_VERSION, lingling_storage_path
from services.publish_runner import GroupFailure, PublishEvent, PublishRunner
from services.orphan_cleanup import clear_orphans
from services.publish_service import FunctionProblem, PublishPreview, check_groups
from ui.publish_run import progress_line
from test_catalog_service import empty_catalog
from test_publish_preflight import PrepareTestCase


def sha(content: bytes) -> str:
    """內容的 SHA-256（十六進位小寫）"""
    return hashlib.sha256(content).hexdigest()


class RunTestCase(PrepareTestCase):
    """臨時目錄裡已重新命名的「烽火操」（總譜、二胡 I、琵琶），對到假 HuaXia 上的「長城隨想－烽火操」

    HuaXia 上的那首有「二胡1」（人數 6）與「琵琶」兩個聲部，「二胡1」底下登記著泠靈上次發的舊檔。
    本學期有一場還沒到的公演；假 Google 上是只有標題列的譜庫。
    """

    def setUp(self):
        super().setUp()
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.out = os.path.join(self.temp_dir, "out")
        os.makedirs(self.out)
        self.scratch = os.path.join(self.temp_dir, "temp")
        self.project = Project(master_template="{曲名}-{序號}-{樂器}.pdf", output_directory=self.out)
        self.group = self.add_group("烽火操", ["二胡 I", "琵琶"], b"fenghuo")
        self.project.set_huaxia_link(self.group, HuaXiaLink(piece_id=self.fenghuo, piece_title="長城隨想－烽火操"))
        self.erhu = self.huaxia.add_part(self.fenghuo, "二胡1", needed="6", needed_min=6, sort=1)
        self.pipa = self.huaxia.add_part(self.fenghuo, "琵琶", needed="2", needed_min=2, sort=2)
        self.old = lingling_storage_path(self.fenghuo, sha(b"old erhu"))
        self.huaxia.put_object(self.old, b"old erhu")
        self.old_score = self.huaxia.add_score(self.fenghuo, self.erhu, self.old, title="二胡 I")
        now = datetime.datetime.now(datetime.timezone.utc)
        semester = self.huaxia.add_semester("115-1", is_current=True)
        self.concert = self.huaxia.add_event(
            semester, "期末音樂會", now + datetime.timedelta(days=30), now + datetime.timedelta(days=30, hours=3),
        )
        self.google = FakeGoogle()
        empty_catalog(self.google)
        self.catalog = CatalogService(self.google.sheets, self.google.drive, sleep=lambda seconds: None)
        self.compressor = CompressionService(FileService(), self.scratch)
        self.runner = PublishRunner(self.auth, FileService(), self.compressor, temp_dir=self.scratch)
        self.progress = []

    def add_group(self, name: str, voices, content: bytes):
        """加一個已重新命名、人數都填好的群組（總譜加每個聲部一份分譜），檔的內容是 content 加上檔名"""
        group = self.project.add_group(name, "總譜")
        self.project.update_group(group, piece_name=name)
        self.project.set_instruments(group, list(voices))
        names = [f"{name}-00-總譜.pdf"] + [f"{name}-{n:02d}-{voice}.pdf" for n, voice in enumerate(voices, start=1)]
        paths = [os.path.join(self.out, n) for n in names]
        for path in paths:
            with open(path, "wb") as f:
                f.write(content + os.path.basename(path).encode("utf-8"))
        self.project.add_files([FileInfo(path, os.path.basename(path)) for path in paths], group)
        for index in range(len(voices)):
            self.project.set_headcount(group, index, Headcount(6, 8) if index == 0 else Headcount(2))
        return group

    def path(self, group, voice: str) -> str:
        """群組裡這個聲部（空字串為總譜）的本機檔"""
        if not voice:
            return group.score_file.original_path
        return next(v.file.original_path for v in group.pairing() if v.voice == voice)

    def stored(self, group, voice: str, piece_id: str = "") -> str:
        """這份本機檔照現在的內容發出去時的 Storage 路徑"""
        with open(self.path(group, voice), "rb") as f:
            return lingling_storage_path(piece_id or group.huaxia_link.piece_id, sha(f.read()))

    def press_publish(self, events=(), group_ids=None):
        """重新按下發譜（group_ids 時只發這幾個群組）、走完預覽（勾 events 這幾場公演）後按「發譜」：交回這次的快照"""
        readiness = self.service.prepare()
        checks = check_groups(self.project, group_ids)
        preview = PublishPreview(self.project, checks, readiness, FileService(), 10 ** 9)
        for event in events:
            preview.choose_event(event, True)
        preview.assign_piece_ids()
        checked, snapshot = self.runner.start(preview.publish_plan())
        self.assertTrue(checked.ready)
        return snapshot

    def run_publish(self, snapshot, cancel_when=lambda progress: False, cancelled=None):
        """在這裡（代替背景執行緒）跑完這次發譜；cancel_when 對某次進度為真之後、或 cancelled 不是空的時就要求取消"""
        cancelled = [] if cancelled is None else cancelled

        def report(progress):
            self.progress.append(progress)
            if cancel_when(progress):
                cancelled.append(True)

        return self.runner.run(snapshot, lambda: self.catalog, lambda: bool(cancelled), report)

    def cancel_on_first_upload(self) -> list:
        """第一份檔傳到假 HuaXia 時就要求取消（那份照樣傳完）：回傳交給 run_publish 的 cancelled"""
        cancelled = []
        receive = self.huaxia.request

        def request(method, url, **kwargs):
            response = receive(method, url, **kwargs)
            if method == "POST" and "/storage/v1/object/scores/" in url:
                cancelled.append(True)
            return response

        self.huaxia.request = request
        return cancelled

    def scores(self, piece_id: str):
        """這首曲目登記的樂譜：（聲部編號，Storage 路徑）"""
        return sorted(
            (row["part_id"] or "", row["file_path"]) for row in self.huaxia.tables["scores"]
            if row["piece_id"] == piece_id
        )

    def log(self, action: PublishAction):
        """譜庫的發譜紀錄裡這種動作的列"""
        return [record for record in self.catalog.publish_log() if record.action is action]

    def leftovers(self):
        """暫存資料夾裡還留著的東西"""
        return os.listdir(self.scratch) if os.path.isdir(self.scratch) else []

    def compressed_preview(self) -> PublishPreview:
        """走完預覽、只發琵琶：大小上限 10 位元組，琵琶在暫存資料夾裡有壓縮好的一份（內容 small pipa）"""
        readiness = self.service.prepare()
        preview = PublishPreview(self.project, check_groups(self.project), readiness, FileService(), 10)
        output = os.path.join(self.scratch, "compressed-1", "pipa.pdf")
        os.makedirs(os.path.dirname(output))
        with open(output, "wb") as f:
            f.write(b"small pipa")
        for voice in ("二胡 I", ""):
            preview.drop(self.path(self.group, voice), True)
        source = self.path(self.group, "琵琶")
        preview.compressed[source] = CompressionResult(
            source, CompressionStatus.COMPRESSED, os.path.getsize(source), compressed_size=10, output_path=output,
        )
        return preview

    def uploads(self):
        """送到 Storage 的上傳請求的路徑（依送出的順序）"""
        prefix = "/storage/v1/object/scores/"
        return [
            r["path"][len(prefix):] for r in self.huaxia.requests
            if r["method"] == "POST" and r["path"].startswith(prefix)
        ]

    def tables(self):
        """HuaXia 的資料表現在的內容（比對有沒有變動用）"""
        return copy.deepcopy(self.huaxia.tables)


class TestPublishing(RunTestCase):
    """一次跑完：檔傳上去、一首曲目一次切換、舊檔刪掉，前後各寫一列發譜紀錄"""

    def test_files_parts_and_concert_switch_and_the_old_file_is_deleted(self):
        outcome = self.run_publish(self.press_publish(events=[self.concert]))
        self.assertEqual(outcome.failed, [])
        erhu, pipa, score = (self.stored(self.group, v) for v in ("二胡 I", "琵琶", ""))
        self.assertEqual(self.scores(self.fenghuo), sorted([(self.erhu, erhu), (self.pipa, pipa), ("", score)]))
        self.assertNotIn(self.old, self.huaxia.objects)
        with open(self.path(self.group, "琵琶"), "rb") as f:
            self.assertEqual(self.huaxia.objects[pipa], f.read())
        part = next(row for row in self.huaxia.tables["piece_parts"] if row["id"] == self.erhu)
        self.assertEqual((part["needed"], part["needed_min"]), ("6-8", 6))
        self.assertIn({"event_id": self.concert, "piece_id": self.fenghuo}, self.huaxia.tables["event_pieces"])
        self.assertEqual(self.leftovers(), [])

    def test_the_publish_log_has_a_start_and_a_result_row_with_the_same_id(self):
        outcome = self.run_publish(self.press_publish(events=[self.concert]))
        [started], [result] = self.log(PublishAction.START), self.log(PublishAction.RESULT)
        self.assertEqual((started.publish_id, result.publish_id), (outcome.publish_id, outcome.publish_id))
        self.assertEqual((started.operator, started.group_id, started.piece_id), ("officer_amy", self.group.id, self.fenghuo))
        self.assertEqual(started.files_deleted, [self.old])
        self.assertEqual(started.parts_updated, [
            {"id": self.erhu, "name": "二胡1", "needed": ["6", "6-8"], "needed_min": [6, 6]},
        ])
        self.assertIsNone(result.failure)
        self.assertEqual(result.files_deleted, [self.old])
        self.assertEqual([s["id"] for s in result.scores_removed], [self.old_score])
        added = {row["id"] for row in self.huaxia.tables["scores"]}
        self.assertEqual({s["id"] for s in result.scores_added}, added)
        self.assertEqual([e["id"] for e in result.events], [self.concert])
        self.assertTrue(result.events[0]["ends_at"])



class TestNewPiece(RunTestCase):
    """新建曲目的編號在按下「發譜」時產生並寫進專案，重發沿用，不重複建曲目、聲部與樂譜"""

    def setUp(self):
        super().setUp()
        self.chunjiang = self.add_group("春江花月夜", ["簫"], b"chunjiang")
        new = NewHuaXiaPiece("春江花月夜", "", 7.5, HuaXiaEnsemble.SIZHU)
        self.project.set_huaxia_link(self.chunjiang, HuaXiaLink(piece_title="春江花月夜", new_piece=new))

    def test_the_new_piece_is_created_under_the_number_kept_in_the_project(self):
        self.run_publish(self.press_publish())
        piece_id = self.chunjiang.huaxia_link.piece_id
        self.assertTrue(piece_id)
        [piece] = [row for row in self.huaxia.tables["pieces"] if row["id"] == piece_id]
        self.assertEqual((piece["title"], piece["ensemble"], piece["duration_min"]), ("春江花月夜", "sizhu", 7.5))
        [xiao] = [row for row in self.huaxia.tables["piece_parts"] if row["piece_id"] == piece_id]
        self.assertEqual(self.scores(piece_id), sorted([
            (xiao["id"], self.stored(self.chunjiang, "簫")), ("", self.stored(self.chunjiang, "")),
        ]))
        [result] = [r for r in self.log(PublishAction.RESULT) if r.group_id == self.chunjiang.id]
        self.assertEqual(result.new_piece["title"], "春江花月夜")
        self.assertEqual([p["id"] for p in result.parts_created], [xiao["id"]])

    def test_publishing_again_after_an_interrupted_switch_keeps_the_piece_and_its_parts(self):
        self.huaxia.drop_requests("POST", "/rest/v1/rpc/lingling_publish", skip=1)
        first = self.run_publish(self.press_publish())
        self.assertEqual([g.group.group for g in first.failed], ["春江花月夜"])
        piece_id = self.chunjiang.huaxia_link.piece_id
        self.run_publish(self.press_publish())
        self.assertEqual(self.chunjiang.huaxia_link.piece_id, piece_id)
        self.run_publish(self.press_publish())
        self.assertEqual(len([row for row in self.huaxia.tables["pieces"] if row["title"] == "春江花月夜"]), 1)
        self.assertEqual(len([row for row in self.huaxia.tables["piece_parts"] if row["piece_id"] == piece_id]), 1)
        self.assertEqual(len(self.scores(piece_id)), 2)


class TestUploadInterrupted(RunTestCase):
    """第 1 段中斷：HuaXia 的資料表沒有任何變化；重發時已傳的檔不重傳，最後與一次跑完相同"""

    def setUp(self):
        super().setUp()
        # 一次只傳一份：傳完第一份就取消時，最多再傳完正在傳的那一份
        self.runner = PublishRunner(self.auth, FileService(), self.compressor, temp_dir=self.scratch, workers=1)

    def test_cancelling_while_uploading_changes_no_table(self):
        before = self.tables()
        outcome = self.run_publish(
            self.press_publish(events=[self.concert]),
            cancel_when=lambda progress: progress.event is PublishEvent.UPLOADED,
        )
        self.assertTrue(outcome.cancelled)
        self.assertEqual([g.failure for g in outcome.groups], [GroupFailure.CANCELLED])
        self.assertEqual(self.tables(), before)
        self.assertIn(self.old, self.huaxia.objects)
        [result] = self.log(PublishAction.RESULT)
        self.assertEqual(result.failure["stage"], "cancelled")
        self.assertEqual(self.leftovers(), [])

    def test_publishing_again_does_not_upload_what_is_there_and_ends_the_same(self):
        snapshot = self.press_publish(events=[self.concert])
        self.run_publish(snapshot, cancelled=self.cancel_on_first_upload())
        self.assertEqual(len(self.uploads()), 1)   # 傳第一份時取消：那份傳完，其餘不傳
        outcome = self.run_publish(self.press_publish(events=[self.concert]))
        self.assertEqual(outcome.failed, [])
        self.assertEqual(sorted(self.uploads()), sorted(set(self.uploads())))
        erhu, pipa, score = (self.stored(self.group, v) for v in ("二胡 I", "琵琶", ""))
        self.assertEqual(sorted(self.uploads()), sorted([erhu, pipa, score]))
        self.assertEqual(self.scores(self.fenghuo), sorted([(self.erhu, erhu), (self.pipa, pipa), ("", score)]))
        self.assertEqual(sorted(self.huaxia.objects), sorted([erhu, pipa, score]))

    def test_a_dropped_connection_while_uploading_changes_no_table(self):
        before = self.tables()
        self.huaxia.drop_requests("POST", "/storage/v1/object/scores/", skip=1, times=99)
        outcome = self.run_publish(self.press_publish())
        self.assertEqual([g.failure for g in outcome.groups], [GroupFailure.UPLOAD])
        self.assertEqual(self.tables(), before)



class TwoPiecesTestCase(RunTestCase):
    """另有一個群組「茉莉花」（總譜、笛子）對到 HuaXia 上的「茉莉花」，那首還沒有聲部"""

    def setUp(self):
        super().setUp()
        self.molihua = self.add_group("茉莉花", ["笛子"], b"molihua")
        self.project.set_huaxia_link(self.molihua, HuaXiaLink(piece_id=self.moli, piece_title="茉莉花"))

    def assert_published_fenghuo(self):
        """「烽火操」照常發成：新檔登記、舊檔刪掉"""
        erhu, pipa, score = (self.stored(self.group, v) for v in ("二胡 I", "琵琶", ""))
        self.assertEqual(self.scores(self.fenghuo), sorted([(self.erhu, erhu), (self.pipa, pipa), ("", score)]))
        self.assertNotIn(self.old, self.huaxia.objects)


class TestOneGroupFails(TwoPiecesTestCase):
    """一個群組的檔沒傳上去或切換失敗，只有它沒發成，其他群組照常"""

    def test_a_group_whose_file_did_not_upload_is_not_switched(self):
        self.huaxia.drop_requests("POST", f"/storage/v1/object/scores/lingling/{self.moli}", times=99)
        outcome = self.run_publish(self.press_publish())
        self.assertEqual([(g.group.group, g.failure) for g in outcome.failed], [("茉莉花", GroupFailure.UPLOAD)])
        self.assert_published_fenghuo()
        self.assertEqual(self.scores(self.moli), [])
        self.assertEqual([row for row in self.huaxia.tables["piece_parts"] if row["piece_id"] == self.moli], [])
        failed = next(r for r in self.log(PublishAction.RESULT) if r.group_id == self.molihua.id)
        self.assertEqual(failed.failure["stage"], "upload")
        self.assertEqual(failed.scores_added, [])

    def test_a_failed_switch_does_not_affect_the_other_group(self):
        self.huaxia.fail_switch(self.moli)
        outcome = self.run_publish(self.press_publish())
        [failed] = outcome.failed
        self.assertEqual((failed.group.group, failed.failure, failed.code), ("茉莉花", GroupFailure.SWITCH, "PT404"))
        self.assert_published_fenghuo()
        self.assertEqual(self.scores(self.moli), [])
        result = next(r for r in self.log(PublishAction.RESULT) if r.group_id == self.molihua.id)
        self.assertEqual((result.failure["stage"], result.failure["code"]), ("switch", "PT404"))
        self.assertEqual(sorted(result.files_uploaded), sorted(self.stored(self.molihua, v) for v in ("笛子", "")))

    def test_cancelling_while_switching_stops_after_the_current_group(self):
        outcome = self.run_publish(
            self.press_publish(), cancel_when=lambda progress: progress.event is PublishEvent.SWITCHED,
        )
        self.assertTrue(outcome.cancelled)
        self.assertEqual([g.failure for g in outcome.groups], [None, GroupFailure.CANCELLED])
        self.assertEqual(self.scores(self.moli), [])
        self.assertIn(self.old, self.huaxia.objects)
        self.assertEqual(outcome.orphans, [self.old])
        self.assertEqual(self.leftovers(), [])


class TestPublishingFailedGroupsAgain(TwoPiecesTestCase):
    """「重新發譜沒發成的群組」以同一套三段式只發那幾個群組，已發成的不重做"""

    def switched_pieces(self):
        """各次切換（發譜函式）的曲目編號，依呼叫的順序"""
        return [
            r["json"]["payload"]["piece_id"] for r in self.huaxia.requests
            if r["path"] == "/rest/v1/rpc/lingling_publish"
        ]

    def test_only_the_groups_that_failed_are_published_again(self):
        self.huaxia.drop_requests("POST", "/rest/v1/rpc/lingling_publish", skip=1)
        first = self.run_publish(self.press_publish())
        self.assertEqual([g.group.group for g in first.failed], ["茉莉花"])
        published = self.scores(self.fenghuo)
        switched = len(self.switched_pieces())
        outcome = self.run_publish(self.press_publish(group_ids=[g.group.group_id for g in first.failed]))
        self.assertEqual([(g.group.group, g.failure) for g in outcome.groups], [("茉莉花", None)])
        self.assertEqual(self.switched_pieces()[switched:], [self.moli])
        self.assertEqual(self.scores(self.fenghuo), published)
        self.assertEqual(len(self.scores(self.moli)), 2)
        self.assertEqual([r.group for r in self.log(PublishAction.START) if r.publish_id == outcome.publish_id], ["茉莉花"])


class TestCleaningUp(RunTestCase):
    """清舊檔只刪撤掉指著它的登記的群組都切換成功的；刪不掉的留成孤兒檔"""

    def test_a_failed_deletion_leaves_an_orphan_and_the_group_still_published(self):
        self.huaxia.drop_requests("DELETE", "/storage/v1/object/scores", times=99)
        outcome = self.run_publish(self.press_publish())
        self.assertEqual(outcome.failed, [])
        self.assertEqual(outcome.orphans, [self.old])
        self.assertIn(self.old, self.huaxia.objects)
        [result] = self.log(PublishAction.RESULT)
        self.assertEqual(result.files_deleted, [])
        self.assertIsNone(result.failure)

    def test_a_file_still_registered_by_a_group_that_failed_is_kept(self):
        # 「烽火操」第二樂章也對到同一首，它的琵琶底下登記著內容相同的舊檔
        second = self.add_group("烽火操二", ["琵琶"], b"second")
        self.project.set_huaxia_link(second, HuaXiaLink(piece_id=self.fenghuo, piece_title="長城隨想－烽火操"))
        self.project.set_instruments(self.group, ["二胡 I"])
        self.huaxia.add_score(self.fenghuo, self.pipa, self.old, title="琵琶")
        self.huaxia.drop_requests("POST", "/rest/v1/rpc/lingling_publish", skip=1)
        outcome = self.run_publish(self.press_publish())
        self.assertEqual([g.group.group for g in outcome.failed], ["烽火操二"])
        self.assertIn(self.old, self.huaxia.objects)
        self.assertIn((self.pipa, self.old), self.scores(self.fenghuo))


class TestOrphans(RunTestCase):
    """每次發譜開始前，清掉這次涉及的曲目底下泠靈路徑格式、但沒有登記的檔；只動泠靈自己的檔"""

    def leave_the_old_file_as_an_orphan(self):
        """發一次，第 3 段刪舊檔時斷線：舊檔沒有登記，留成孤兒檔"""
        self.huaxia.drop_requests("DELETE", "/storage/v1/object/scores")
        outcome = self.run_publish(self.press_publish())
        self.assertEqual(outcome.orphans, [self.old])
        self.assertNotIn(self.old, [path for _, path in self.scores(self.fenghuo)])

    def test_an_orphan_left_by_an_interrupted_deletion_is_cleared_before_the_next_publish(self):
        self.leave_the_old_file_as_an_orphan()
        outcome = self.run_publish(self.press_publish())
        self.assertNotIn(self.old, self.huaxia.objects)
        self.assertEqual([(c.piece_id, c.deleted) for c in outcome.cleanups], [(self.fenghuo, [self.old])])
        [cleared] = self.log(PublishAction.CLEAR_ORPHANS)
        self.assertEqual((cleared.publish_id, cleared.piece_id), (outcome.publish_id, self.fenghuo))
        self.assertEqual(cleared.files_deleted, [self.old])
        self.assertIn(
            t("publish.run.line.orphan_cleared", group="長城隨想－烽火操", name=self.old),
            [progress_line(progress) for progress in self.progress],
        )

    def test_files_not_in_lingling_format_and_registered_lingling_files_are_left_alone(self):
        # 網頁正在上傳、還沒登記的檔（路徑是時間戳），泠靈資料夾裡不是泠靈格式的檔，這次沒發的聲部登記著的泠靈檔
        uploading = f"{self.fenghuo}/{self.pipa}/1760000000000_bowing.pdf"
        stray = f"lingling/{self.fenghuo}/notes.pdf"
        registered = lingling_storage_path(self.fenghuo, sha(b"registered by the web"))
        for path in (uploading, stray, registered):
            self.huaxia.put_object(path)
        zhongruan = self.huaxia.add_part(self.fenghuo, "中阮", sort=3)
        self.huaxia.add_score(self.fenghuo, zhongruan, registered, title="中阮")
        outcome = self.run_publish(self.press_publish())
        self.assertEqual(outcome.failed, [])
        for path in (uploading, stray, registered):
            self.assertIn(path, self.huaxia.objects)
        self.assertEqual([c.deleted for c in outcome.cleanups], [[]])
        self.assertEqual(self.log(PublishAction.CLEAR_ORPHANS), [])

    def test_only_the_pieces_in_this_publish_are_cleared(self):
        elsewhere = lingling_storage_path(self.moli, sha(b"left on another piece"))
        self.huaxia.put_object(elsewhere)
        self.run_publish(self.press_publish())
        self.assertIn(elsewhere, self.huaxia.objects)

    def test_files_this_publish_sends_are_not_cleared_though_not_registered_yet(self):
        # 上次這首切換失敗：傳上去的新檔沒登記；這次發同樣的內容，清理不能刪掉它們，也不必重傳
        self.huaxia.drop_requests("POST", "/rest/v1/rpc/lingling_publish")
        self.assertEqual(len(self.run_publish(self.press_publish()).failed), 1)
        sent = sorted(self.uploads())
        outcome = self.run_publish(self.press_publish())
        self.assertEqual(outcome.failed, [])
        self.assertEqual([c.deleted for c in outcome.cleanups], [[]])
        self.assertEqual(sorted(self.uploads()), sent)
        erhu, pipa, score = (self.stored(self.group, v) for v in ("二胡 I", "琵琶", ""))
        self.assertEqual(self.scores(self.fenghuo), sorted([(self.erhu, erhu), (self.pipa, pipa), ("", score)]))

    def test_publishing_goes_on_when_the_orphans_cannot_be_listed(self):
        self.leave_the_old_file_as_an_orphan()
        self.huaxia.drop_requests("POST", "/storage/v1/object/list/scores")
        outcome = self.run_publish(self.press_publish())
        self.assertEqual(outcome.failed, [])
        self.assertIn(self.old, self.huaxia.objects)
        [cleanup] = outcome.cleanups
        self.assertTrue(cleanup.detail)
        [cleared] = self.log(PublishAction.CLEAR_ORPHANS)
        self.assertEqual((cleared.files_deleted, cleared.failure["detail"]), ([], cleanup.detail))


class TestClearingOrphansDirectly(RunTestCase):
    """清孤兒檔可以單獨呼叫（收回前也清一次），清掉的檔寫進紀錄檔"""

    def test_clearing_one_piece_keeps_what_the_caller_still_uses_and_logs_what_it_deleted(self):
        orphan = lingling_storage_path(self.fenghuo, sha(b"orphan"))
        kept = lingling_storage_path(self.fenghuo, sha(b"about to be registered"))
        for path in (orphan, kept):
            self.huaxia.put_object(path)
        folder = os.path.join(self.temp_dir, "logs")
        [cleanup] = clear_orphans(
            self.auth, {self.fenghuo: "長城隨想－烽火操"}, keep=[kept], log=ActivityLog(FileService(), folder),
        )
        self.assertEqual(cleanup.deleted, [orphan])
        self.assertEqual(sorted(p for p in self.huaxia.objects if p.startswith("lingling/")), sorted([self.old, kept]))
        [name] = os.listdir(folder)
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            logged = f.read()
        self.assertIn(t("publish.log.orphan_cleared"), logged)
        self.assertIn(orphan, logged)
        self.assertNotIn(kept, logged)


class TestPublishLogFirst(RunTestCase):
    """「開始發譜」寫不進譜庫就不發譜；「發譜結果」寫不進去時 HuaXia 照樣變動、結果寫明"""

    def test_when_the_start_cannot_be_written_nothing_changes_on_huaxia(self):
        snapshot = self.press_publish()
        before, objects = self.tables(), dict(self.huaxia.objects)
        self.google.fail_next(CatalogErrorKind.OFFLINE, times=99)
        outcome = self.run_publish(snapshot)
        self.assertEqual(outcome.error.kind, CatalogErrorKind.OFFLINE)
        self.assertEqual(outcome.groups, [])
        self.assertEqual((self.tables(), self.huaxia.objects), (before, objects))
        self.assertEqual(self.uploads(), [])
        self.assertEqual(self.leftovers(), [])

    def test_when_the_result_cannot_be_written_huaxia_has_still_changed(self):
        snapshot = self.press_publish()
        # 「開始發譜」是第 1 到 3 個請求（列出工作表名稱、讀取、寫入），「發譜結果」的寫入是第 6 個
        self.google.fail_at(self.google.requests + 6, CatalogErrorKind.OFFLINE)
        outcome = self.run_publish(snapshot)
        self.assertEqual(outcome.log_error.kind, CatalogErrorKind.OFFLINE)
        self.assertEqual(outcome.failed, [])
        self.assertNotIn(self.old, self.huaxia.objects)
        self.assertEqual(len(self.log(PublishAction.START)), 1)
        self.assertEqual(self.log(PublishAction.RESULT), [])



class TestSnapshot(RunTestCase):
    """發出去的是按下「發譜」那一刻的檔與編制；快照與壓縮檔在發譜結束時刪掉"""

    def test_changes_after_pressing_publish_do_not_reach_huaxia(self):
        with open(self.path(self.group, "琵琶"), "rb") as f:
            pressed = f.read()
        pipa = self.stored(self.group, "琵琶")
        snapshot = self.press_publish()
        with open(self.path(self.group, "琵琶"), "wb") as f:
            f.write(b"edited later")
        os.rename(self.path(self.group, "二胡 I"), os.path.join(self.temp_dir, "moved.pdf"))
        self.project.set_headcount(self.group, 0, Headcount(3))
        self.project.set_instruments(self.group, ["二胡 I", "琵琶", "中阮"])
        outcome = self.run_publish(snapshot)
        self.assertEqual(outcome.failed, [])
        self.assertEqual(self.huaxia.objects[pipa], pressed)
        part = next(row for row in self.huaxia.tables["piece_parts"] if row["id"] == self.erhu)
        self.assertEqual(part["needed"], "6-8")
        self.assertEqual(len([r for r in self.huaxia.tables["piece_parts"] if r["piece_id"] == self.fenghuo]), 2)

    def test_the_snapshot_is_a_copy_in_the_temporary_folder_removed_at_the_end(self):
        snapshot = self.press_publish()
        self.assertEqual(self.leftovers(), [os.path.basename(snapshot.folder)])
        self.assertEqual(len(os.listdir(snapshot.folder)), 3)
        self.run_publish(snapshot)
        self.assertEqual(self.leftovers(), [])

    def test_a_compressed_file_is_sent_and_then_discarded(self):
        checked, snapshot = self.runner.start(self.compressed_preview().publish_plan())
        outcome = self.run_publish(snapshot)
        self.assertEqual(outcome.failed, [])
        self.assertEqual(self.huaxia.objects[lingling_storage_path(self.fenghuo, sha(b"small pipa"))], b"small pipa")
        self.assertEqual(self.leftovers(), [])


class TestStartChecks(RunTestCase):
    """按下「發譜」時重查：不是幹部、發譜函式缺少或版本不符時不發，交出的壓縮檔也丟掉"""

    def assert_not_started(self, problem):
        """按下「發譜」時重查不過：沒有快照、沒有上傳，交出的壓縮檔丟掉、暫存資料夾是空的"""
        preview = self.compressed_preview()
        problem()
        checked, snapshot = self.runner.start(preview.publish_plan())
        self.assertFalse(checked.ready)
        self.assertIsNone(snapshot)
        self.assertEqual(self.uploads(), [])
        self.assertEqual(self.leftovers(), [])
        return checked

    def test_a_missing_function(self):
        checked = self.assert_not_started(lambda: self.huaxia.install_publish_function(None))
        self.assertEqual(checked.function_problem, FunctionProblem.MISSING)

    def test_another_version(self):
        checked = self.assert_not_started(lambda: self.huaxia.install_publish_function(PUBLISH_INTERFACE_VERSION + 1))
        self.assertEqual(checked.function_problem, FunctionProblem.VERSION_MISMATCH)

    def test_no_longer_an_officer(self):
        checked = self.assert_not_started(lambda: self.huaxia.remove_roles("officer_amy"))
        self.assertIsNotNone(checked.not_officer)


if __name__ == '__main__':
    unittest.main()
