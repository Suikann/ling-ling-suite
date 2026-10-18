# -*- coding: utf-8 -*-
"""
撤銷存入的測試：專案在臨時目錄的檔先存進假 Google，再挑那次存入撤銷，
檢查每份檔的下場（丟進垃圾桶、還原舊版本、無法還原、不動）、分譜工作表的登記、本機已清除時先下載回原位、
一起撤掉的編制，以及存入紀錄與編制寫入紀錄只新增
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.catalog_constants import (
    SHEET_DEPOSIT_LOG, SHEET_EDITIONS, SHEET_HEADERS, SHEET_INSTRUMENTATION_LOG, SHEET_PARTS, SHEET_PIECES,
)
from core.catalog_revert import RevertOutcome
from core.catalog_sync import WriteSource, place_writes
from core.locale import t
from core.deposit_revert import KeptBecause, LocalProject, RevertAction, RevertBlock, RevertChoice, deposits
from core.models import Headcount, Project
from services.activity_log import ActivityLog
from services.clear_local_service import ClearLocalService
from services.deposit_revert_service import DepositRevertService
from services.deposit_service import DepositService
from services.download_service import DownloadService
from services.file_service import FileService
from test_catalog_link import records
from test_catalog_service import add_row
from test_deposit import DepositFixture

OPERATOR = "office@example.com"


def read(path: str) -> bytes:
    """檔案內容"""
    with open(path, "rb") as f:
        return f.read()


class RevertTestCase(DepositFixture, unittest.TestCase):
    """撤銷服務對假 Google 與臨時目錄（情境見 DepositFixture）"""

    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp)
        self.build(self.VOICES)
        downloads = DownloadService(FileService(), temp_dir=os.path.join(self.temp, "appdata-temp"))
        self.reverter = DepositRevertService(FileService(), downloads)

    def archive(self) -> str:
        """把專案的檔存進譜庫、指紋交回專案，回傳這次的存入編號"""
        service = DepositService(FileService())
        plan = service.preview(self.project, self.catalog)
        outcome = service.deposit(plan, self.catalog, OPERATOR, lambda: False, lambda progress: None)
        self.assertIsNone(outcome.error)
        self.project.record_deposit(outcome.records)
        self.project.mark_saved()
        return deposits(self.catalog.deposit_log())[0].deposit_id

    def preview(self, deposit_id: str, catalog_only=(), local=None):
        """撤銷那次存入的預覽；預設開著的是這個專案"""
        local = local if local is not None else LocalProject.of(self.project, catalog_only)
        return self.reverter.preview(deposit_id, self.catalog, self.project.orchestra_type, local)

    def revert(self, deposit_id: str, instrumentation: bool = False, names=None, catalog_only=(), local=None):
        """預覽後撤銷那次存入的群組（預設全部），回傳（預覽，結果）"""
        return self.revert_preview(self.preview(deposit_id, catalog_only, local), instrumentation, names)

    def revert_preview(self, preview, instrumentation: bool = False, names=None):
        """照預覽撤銷群組（預設全部），回傳（預覽，結果）"""
        choices = [
            RevertChoice(group, instrumentation) for group in preview.groups if names is None or group.name in names
        ]
        outcome = self.reverter.revert(
            preview, choices, self.catalog, OPERATOR, self.project.orchestra_type, lambda: False, lambda p: None,
        )
        return preview, outcome

    @staticmethod
    def outcomes(group) -> dict:
        """群組每份檔的下場：檔名 → （下場，不動的原因）"""
        return {os.path.basename(f.entry.local_path): (f.action, f.kept) for f in group.files}

    def registered_ids(self) -> dict:
        """分譜工作表各列登記的 Drive 檔案 ID：聲部（總譜列為「總譜」）→ ID"""
        return {
            ("總譜" if r["is_score"] else r["instrument_name"]): r["drive_file_id"]
            for r in records(self.google, SHEET_PARTS) if r["edition_id"] == "e1"
        }

    def write_local(self, file, content: bytes) -> None:
        """改寫本機的檔"""
        with open(file.original_path, "wb") as f:
            f.write(content)


class TestNewUploads(RevertTestCase):
    """那次新上傳的：檔丟進 Drive 垃圾桶，分譜列的登記移除"""

    def test_new_files_go_to_the_trash_and_their_registration_is_removed(self):
        deposit_id = self.archive()
        ids = self.registered_ids()
        preview, outcome = self.revert(deposit_id)
        self.assertEqual(self.outcomes(preview.groups[0]), {
            "總譜.pdf": (RevertAction.TRASH, None),
            "二胡 I.pdf": (RevertAction.TRASH, None),
            "琵琶.pdf": (RevertAction.TRASH, None),
        })
        self.assertIsNone(outcome.error)
        self.assertEqual(outcome.completed, 3)
        for voice in ("總譜", "二胡 I", "琵琶"):
            self.assertTrue(self.google.drive.is_trashed(ids[voice]))
        self.assertEqual(self.registered_ids(), {"總譜": "", "二胡 I": "", "琵琶": ""})
        self.assertEqual(sorted(outcome.reverted_paths), sorted(
            [self.score.original_path] + [part.original_path for part in self.parts],
        ))

    def test_each_reverted_file_is_logged(self):
        deposit_id = self.archive()
        log_dir = os.path.join(self.temp, "logs")
        downloads = DownloadService(FileService(), temp_dir=os.path.join(self.temp, "appdata-temp"))
        self.reverter = DepositRevertService(FileService(), downloads, ActivityLog(FileService(), log_dir))
        self.revert(deposit_id)
        text = "".join(read(os.path.join(log_dir, name)).decode("utf-8") for name in os.listdir(log_dir))
        self.assertEqual(text.count(t("revert.log.action", action=t("revert.action.trash"))), 3)
        self.assertIn(self.parts[0].original_path, text)

    def test_reverting_again_finds_nothing_more_to_do(self):
        deposit_id = self.archive()
        self.revert(deposit_id)
        preview, outcome = self.revert(deposit_id)
        self.assertEqual(
            {action for action, _ in self.outcomes(preview.groups[0]).values()}, {RevertAction.KEEP},
        )
        self.assertEqual(
            {kept for _, kept in self.outcomes(preview.groups[0]).values()}, {KeptBecause.REVERTED},
        )
        self.assertEqual(outcome.completed, 0)


class TestProjectFingerprints(RevertTestCase):
    """撤掉的檔譜庫不再有這份內容：專案拿掉它們的存入指紋，不再算在譜庫裡有一份"""

    def test_reverted_files_lose_their_fingerprint(self):
        deposit_id = self.archive()
        _, outcome = self.revert(deposit_id)
        self.project.forget_deposits(outcome.reverted_paths)
        self.assertTrue(self.project.is_modified())
        for file in [self.score] + self.parts:
            self.assertEqual(file.deposit_md5, "")
            self.assertFalse(self.group.has_catalog_copy(file))


class TestRegisteredExisting(RevertTestCase):
    """那次登記的是 Drive 上原有、內容相同的同名檔：只移除登記，檔留在 Drive 上"""

    def test_only_the_registration_is_removed(self):
        parts_folder = self.google.drive.add_folder("分譜", self.folder)
        existing = self.google.drive.add_file("琵琶.pdf", parts_folder, "琵琶".encode())
        deposit_id = self.archive()
        self.assertEqual(self.registered_ids()["琵琶"], existing)
        preview, outcome = self.revert(deposit_id)
        self.assertEqual(self.outcomes(preview.groups[0])["琵琶.pdf"], (RevertAction.UNREGISTER, None))
        self.assertIsNone(outcome.error)
        self.assertEqual(self.registered_ids()["琵琶"], "")
        self.assertFalse(self.google.drive.is_trashed(existing))


class TestUpdatedContent(RevertTestCase):
    """那次更新內容的：Drive 還留著舊版本就還原（同一個檔），已被清掉的列為無法還原"""

    def setUp(self):
        super().setUp()
        self.archive()
        self.pipa = self.registered_ids()["琵琶"]
        self.old_revision = self.google.drive.revisions(self.pipa)[-1]
        self.write_local(self.parts[1], b"wrong pipa")
        self.deposit_id = self.archive()

    def test_the_overwritten_content_comes_back_into_the_same_file(self):
        preview, outcome = self.revert(self.deposit_id)
        self.assertEqual(self.outcomes(preview.groups[0]), {
            "總譜.pdf": (RevertAction.KEEP, KeptBecause.SKIPPED),
            "二胡 I.pdf": (RevertAction.KEEP, KeptBecause.SKIPPED),
            "琵琶.pdf": (RevertAction.RESTORE, None),
        })
        self.assertIsNone(outcome.error)
        self.assertEqual(self.google.drive.content(self.pipa), "琵琶".encode())
        self.assertEqual(self.registered_ids()["琵琶"], self.pipa)
        self.assertFalse(self.google.drive.is_trashed(self.pipa))
        # 還原後舊版本的內容已是目前的內容，不再永久保留
        self.assertEqual(self.google.drive.kept_revisions(self.pipa), [])

    def test_an_old_version_drive_already_purged_cannot_be_restored(self):
        self.google.drive.purge_revision(self.pipa, self.old_revision)
        preview, outcome = self.revert(self.deposit_id)
        self.assertEqual(self.outcomes(preview.groups[0])["琵琶.pdf"], (RevertAction.LOST, None))
        self.assertEqual(outcome.completed, 0)
        self.assertEqual(self.google.drive.content(self.pipa), b"wrong pipa")


class TestChangedAfterwards(RevertTestCase):
    """之後又重新存入或改過的不動，並列出來"""

    def test_a_file_changed_on_drive_afterwards_is_kept(self):
        deposit_id = self.archive()
        pipa = self.registered_ids()["琵琶"]
        self.google.drive.set_content(pipa, b"edited on the web")
        preview, outcome = self.revert(deposit_id)
        self.assertEqual(self.outcomes(preview.groups[0])["琵琶.pdf"], (RevertAction.KEEP, KeptBecause.CHANGED))
        self.assertFalse(self.google.drive.is_trashed(pipa))
        self.assertEqual(self.registered_ids()["琵琶"], pipa)
        self.assertEqual(outcome.completed, 2)

    def test_a_file_deposited_again_afterwards_is_kept_and_names_that_deposit(self):
        first = self.archive()
        self.write_local(self.parts[1], b"new pipa")
        second = self.archive()
        preview, outcome = self.revert(first)
        pipa = next(f for f in preview.groups[0].files if f.entry.voice == "琵琶")
        self.assertEqual((pipa.action, pipa.kept, pipa.later.deposit_id), (RevertAction.KEEP, KeptBecause.REDEPOSITED, second))
        self.assertEqual(outcome.completed, 0)
        self.assertEqual(self.google.drive.content(self.registered_ids()["琵琶"]), b"new pipa")


class TestOnlyCopy(RevertTestCase):
    """撤銷不毀掉唯一的一份：要丟進垃圾桶或還原的檔，存入時的專案要開著、本機有內容相同的一份（或已清除、先下載回來）"""

    def test_a_group_whose_project_is_not_open_is_refused_and_drive_is_untouched(self):
        deposit_id = self.archive()
        ids = self.registered_ids()
        preview, outcome = self.revert(deposit_id, local=LocalProject.of(Project(), ()))
        self.assertEqual(preview.groups[0].block, RevertBlock.NOT_OPEN)
        self.assertEqual([(group.name, block) for group, block in outcome.refused], [("江樓鐘鼓", RevertBlock.NOT_OPEN)])
        self.assertEqual(outcome.completed, 0)
        self.assertFalse(any(self.google.drive.is_trashed(file_id) for file_id in ids.values()))
        self.assertEqual(self.registered_ids(), ids)

    def test_a_group_whose_local_file_changed_is_refused(self):
        deposit_id = self.archive()
        self.write_local(self.parts[1], b"edited locally")
        preview, outcome = self.revert(deposit_id)
        group = preview.groups[0]
        self.assertEqual(group.block, RevertBlock.NO_LOCAL_COPY)
        self.assertEqual([os.path.basename(f.entry.local_path) for f in group.uncovered], ["琵琶.pdf"])
        self.assertEqual(outcome.completed, 0)
        self.assertFalse(self.google.drive.is_trashed(self.registered_ids()["琵琶"]))

    def test_a_missing_local_file_that_is_not_only_in_the_catalog_is_refused(self):
        deposit_id = self.archive()
        os.remove(self.parts[1].original_path)
        preview, outcome = self.revert(deposit_id)
        self.assertEqual(preview.groups[0].block, RevertBlock.NO_LOCAL_COPY)
        self.assertEqual(outcome.completed, 0)

    def test_a_local_file_changed_after_the_preview_stops_the_group(self):
        deposit_id = self.archive()
        preview = self.preview(deposit_id)
        self.assertIsNone(preview.groups[0].block)
        self.write_local(self.parts[1], b"edited after the preview")
        _, outcome = self.revert_preview(preview)
        self.assertEqual([block for _, block in outcome.refused], [RevertBlock.NO_LOCAL_COPY])
        self.assertEqual(outcome.completed, 0)
        self.assertFalse(self.google.drive.is_trashed(self.registered_ids()["琵琶"]))

    def test_a_renamed_group_is_still_the_group_that_made_the_deposit(self):
        deposit_id = self.archive()
        self.project.update_group(self.group, name="改過名字的群組")
        preview, outcome = self.revert(deposit_id)
        self.assertIsNone(preview.groups[0].block)
        self.assertEqual(outcome.completed, 3)

    def test_rows_written_before_group_ids_are_matched_by_group_name(self):
        deposit_id = self.archive()
        column = SHEET_HEADERS[SHEET_DEPOSIT_LOG].index("group_id")
        for number in range(2, len(self.google.sheets.rows(SHEET_DEPOSIT_LOG)) + 1):
            self.google.sheets.set_cell(SHEET_DEPOSIT_LOG, f"{chr(ord('A') + column)}{number}", "")
        preview, outcome = self.revert(deposit_id)
        self.assertIsNone(preview.groups[0].block)
        self.assertEqual(outcome.completed, 3)

    def test_removing_only_registrations_needs_no_local_copy(self):
        parts_folder = self.google.drive.add_folder("分譜", self.folder)
        self.google.drive.add_file("琵琶.pdf", parts_folder, "琵琶".encode())
        self.google.drive.add_file("二胡 I.pdf", parts_folder, "二胡 I".encode())
        self.google.drive.add_file("總譜.pdf", self.folder, b"score")
        deposit_id = self.archive()
        preview, outcome = self.revert(deposit_id, local=LocalProject.of(Project(), ()))
        self.assertIsNone(preview.groups[0].block)
        self.assertEqual(outcome.completed, 3)


class TestRecheckedBeforeActing(RevertTestCase):
    """動 Drive 之前一刻重讀：預覽之後在 Drive 上又改過的檔不動，列出來"""

    def test_a_file_changed_on_drive_after_the_preview_is_kept(self):
        deposit_id = self.archive()
        preview = self.preview(deposit_id)
        pipa = self.registered_ids()["琵琶"]
        self.google.drive.set_content(pipa, b"edited on the web after the preview")
        _, outcome = self.revert_preview(preview)
        self.assertEqual([os.path.basename(f.entry.local_path) for f in outcome.changed], ["琵琶.pdf"])
        self.assertFalse(self.google.drive.is_trashed(pipa))
        self.assertEqual(self.registered_ids()["琵琶"], pipa)
        self.assertEqual(outcome.completed, 2)


class TestRegistrationsElsewhere(RevertTestCase):
    """移除登記只動那次存入的那處、那個聲部的列；同一個檔另外登記在別處的不動"""

    def test_a_registration_of_the_same_file_at_another_place_stays(self):
        deposit_id = self.archive()
        pipa = self.registered_ids()["琵琶"]
        add_row(
            self.google, SHEET_PARTS, id="pt8", edition_id="e1", movement_id="", instrument_name="琵琶",
            sort_order="1", drive_file_id=pipa, file_name="琵琶.pdf",
        )
        _, outcome = self.revert(deposit_id)
        self.assertEqual(outcome.completed, 3)
        rows = {r["id"]: r["drive_file_id"] for r in records(self.google, SHEET_PARTS)}
        self.assertEqual(rows["pt8"], pipa)
        self.assertEqual(rows["pt2"], "")


class ClearedTestCase(RevertTestCase):
    """第二個群組「將軍令」連到另一首曲目；兩個群組一起存入後，本機檔案都清除了"""

    def setUp(self):
        super().setUp()
        self.second = self.add_second_group()
        self.deposit_id = self.archive()
        ClearLocalService(FileService(), home=os.path.join(self.temp, "home")).clear(
            ClearLocalService(FileService()).preview(self.project, self.catalog), [g.id for g in self.project.groups],
            lambda: False, lambda progress: None,
        )
        self.cleared = [f.original_path for g in self.project.groups for f in [g.score_file] + list(g.files)]
        self.assertFalse(any(os.path.exists(path) for path in self.cleared))

    def add_second_group(self):
        """第二個群組「將軍令」連到另一首曲目的整首（笛子），已重新命名"""
        folder = self.google.drive.add_folder("將軍令")
        add_row(self.google, SHEET_PIECES, id="p2", title="將軍令", active_edition_id="e2")
        add_row(self.google, SHEET_EDITIONS, id="e2", piece_id="p2", drive_folder_id=folder)
        add_row(self.google, SHEET_PARTS, id="pt9", edition_id="e2", movement_id="", instrument_name="笛子", sort_order="1")
        group = self.project.add_group("將軍令", "總譜")
        self.project.update_group(group, piece_name="將軍令")
        self.project.link_group(group, self.catalog.read_place("e2", "", self.project.orchestra_type))
        score = self.put("將軍令/總譜.pdf", b"general score")
        part = self.put("將軍令/分譜/笛子.pdf", b"dizi")
        self.project.add_files([score, part], group)
        self.project.set_score(group, score)
        self.project.mark_saved()
        return group


class TestClearedLocally(ClearedTestCase):
    """本機已清除的群組先下載回原位並核對；下載失敗的群組不撤銷，其他群組照常"""

    def test_cleared_files_come_back_before_the_drive_copies_are_removed(self):
        preview, outcome = self.revert(self.deposit_id, catalog_only=self.cleared)
        self.assertEqual([len(group.downloads()) for group in preview.groups], [3, 2])
        self.assertIsNone(outcome.error)
        self.assertEqual(outcome.skipped, [])
        self.assertEqual(read(self.parts[1].original_path), "琵琶".encode())
        self.assertEqual(read(self.second.files[0].original_path), b"dizi")
        self.assertEqual(outcome.completed, 5)
        self.assertIn(self.parts[1].original_path, outcome.fingerprints)

    def test_a_group_whose_download_fails_is_not_reverted_and_the_others_are(self):
        pipa = self.registered_ids()["琵琶"]
        self.google.drive.corrupt_downloads(pipa)
        preview, outcome = self.revert(self.deposit_id, catalog_only=self.cleared)
        self.assertEqual([group.name for group, _ in outcome.skipped], ["江樓鐘鼓"])
        self.assertEqual(outcome.skipped[0][1].not_done, [self.parts[1].original_path])
        self.assertFalse(self.google.drive.is_trashed(pipa))
        self.assertEqual(self.registered_ids()["琵琶"], pipa)
        self.assertEqual(sorted(os.path.basename(p) for p in outcome.reverted_paths), ["笛子.pdf", "總譜.pdf"])
        self.assertTrue(os.path.exists(self.second.files[0].original_path))


class TestInstrumentation(RevertTestCase):
    """勾了「一起撤掉編制」：那次存入之前這個群組寫進那處、之後沒被改過的編制撤掉；兩張紀錄表都只新增"""

    def setUp(self):
        super().setUp()
        self.project.add_instrument(self.group, "笛子")
        self.project.set_headcount(self.group, 1, Headcount(3))
        self.project.set_headcount(self.group, 0, Headcount(6))
        self.project.add_files([self.put("春江花月夜/分譜/笛子.pdf", b"dizi")], self.group)
        outcome = self.catalog.sync_instrumentation(
            place_writes(self.project), self.project.orchestra_type, OPERATOR, WriteSource.SAVE, {},
        )
        self.assertTrue(outcome.written)
        self.project.mark_catalog_synced(outcome)
        self.project.mark_saved()
        self.deposit_id = self.archive()
        self.other_writes("二胡 I", Headcount(8))

    def other_writes(self, voice: str, headcount: Headcount) -> None:
        """別的專案的「別的群組」也連到第一樂章，改了一個聲部的人數並存檔寫回"""
        project = Project()
        group = project.add_group("別的群組", "總譜")
        project.link_group(group, self.catalog.read_place("e1", "m1", project.orchestra_type))
        project.set_headcount(group, group.instruments.index(voice), headcount)
        outcome = self.catalog.sync_instrumentation(
            place_writes(project), project.orchestra_type, "other@example.com", WriteSource.SAVE, {},
        )
        self.assertTrue(outcome.written)

    def voices(self) -> dict:
        """分譜工作表裡第一樂章的聲部與最少人數"""
        return {
            r["instrument_name"]: r["headcount_min"] for r in records(self.google, SHEET_PARTS)
            if r["movement_id"] == "m1" and r["instrument_name"]
        }

    def test_untouched_writes_are_reverted_and_later_changes_kept(self):
        preview = self.preview(self.deposit_id)
        rows = {row.voice: row.outcome for row in preview.groups[0].instrumentation}
        self.assertEqual(rows, {"二胡 I": RevertOutcome.KEEP, "琵琶": RevertOutcome.RESTORE, "笛子": RevertOutcome.REMOVE})
        _, outcome = self.revert(self.deposit_id, instrumentation=True)
        self.assertIsNone(outcome.error)
        self.assertEqual(self.voices(), {"二胡 I": "8", "琵琶": ""})
        undo = [r for r in records(self.google, SHEET_INSTRUMENTATION_LOG) if r["source"] == WriteSource.UNDO.value]
        self.assertEqual({(r["voice"], r["field"]) for r in undo}, {("笛子", "voice"), ("琵琶", "headcount")})

    def test_without_the_box_the_instrumentation_stays(self):
        self.revert(self.deposit_id)
        self.assertEqual(self.voices(), {"二胡 I": "8", "琵琶": "3", "笛子": ""})

    def test_both_logs_only_gain_new_rows(self):
        deposit_log = self.google.sheets.rows(SHEET_DEPOSIT_LOG)
        instrumentation_log = self.google.sheets.rows(SHEET_INSTRUMENTATION_LOG)
        _, outcome = self.revert(self.deposit_id, instrumentation=True)
        after = self.google.sheets.rows(SHEET_DEPOSIT_LOG)
        self.assertEqual(after[1:len(deposit_log)], deposit_log[1:])
        added = records(self.google, SHEET_DEPOSIT_LOG)[len(deposit_log) - 1:]
        self.assertEqual(len(added), outcome.completed)
        self.assertEqual({(r["reverts"], r["action"], r["operator"]) for r in added}, {
            (self.deposit_id, RevertAction.TRASH.value, OPERATOR),
        })
        self.assertEqual(len({r["deposit_id"] for r in added}), 1)
        self.assertNotEqual(added[0]["deposit_id"], self.deposit_id)
        new_instrumentation = self.google.sheets.rows(SHEET_INSTRUMENTATION_LOG)
        self.assertEqual(new_instrumentation[:len(instrumentation_log)], instrumentation_log)
        self.assertEqual(len(new_instrumentation) - len(instrumentation_log), 2)


if __name__ == "__main__":
    unittest.main()
