# -*- coding: utf-8 -*-
"""
存入譜庫的測試：專案在臨時目錄的已重新命名檔案，經存入服務與譜庫服務存進假 Google，
檢查 Drive 上的檔、分譜工作表的登記、存入紀錄、請求次數與交回專案的指紋
"""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.catalog_constants import (
    SHEET_DEPOSIT_LOG, SHEET_EDITIONS, SHEET_HEADERS, SHEET_MOVEMENTS, SHEET_PARTS, SHEET_PIECES,
)
from core.catalog_errors import CatalogErrorKind
from core.constants import PROJECT_FILE_VERSION, PartsOutputMode
from core.deposit import DepositAction, DepositBlock
from core.models import FileInfo, Project, UndoMapping
from fake_google_catalog import FakeGoogle
from services.catalog_service import CatalogService
from services.deposit_service import DepositService
from services.file_service import FileService
from test_catalog_link import records
from test_catalog_service import add_row, empty_catalog


def md5(content: bytes) -> str:
    """內容的 MD5（十六進位）"""
    return hashlib.md5(content).hexdigest()


class DepositFixture:
    """假 Google 有「春江花月夜」版本 e1（Drive 資料夾）第一樂章 m1 的二胡 I、琵琶；
    專案的群組連到那裡，已依「{樂器}」重新命名到輸出位置下的「春江花月夜」子資料夾，分譜放在「分譜」資料夾。
    用的測試要先設好臨時目錄 self.temp"""

    VOICES = ("二胡 I", "琵琶")

    def build(self, voices) -> None:
        """建出假 Google 與專案：第一樂章有 voices 這些聲部，各有一份已重新命名的分譜（內容是聲部名稱）"""
        self.google = FakeGoogle()
        empty_catalog(self.google)
        self.folder = self.google.drive.add_folder("春江花月夜")
        add_row(self.google, SHEET_PIECES, id="p1", title="春江花月夜", active_edition_id="e1")
        add_row(self.google, SHEET_EDITIONS, id="e1", piece_id="p1", drive_folder_id=self.folder)
        add_row(self.google, SHEET_MOVEMENTS, id="m1", piece_id="p1", movement_number="1", name="江樓鐘鼓")
        for order, voice in enumerate(voices, start=1):
            add_row(
                self.google, SHEET_PARTS, id=f"pt{order}", edition_id="e1", movement_id="m1",
                instrument_name=voice, sort_order=str(order),
            )
        self.waits = []
        self.catalog = CatalogService(self.google.sheets, self.google.drive, sleep=self.waits.append)
        self.out = os.path.join(tempfile.mkdtemp(dir=self.temp), "輸出")
        self.service = DepositService(FileService())
        self.project = Project(master_template="{樂器}")
        self.project.set_output_settings(
            output_directory=self.out, use_subfolders=True, subfolder_template="{曲名}",
            parts_output_mode=PartsOutputMode.PARTS, parts_subfolder_name="分譜",
        )
        self.group = self.project.add_group("江樓鐘鼓", "總譜")
        self.project.update_group(self.group, piece_name="春江花月夜")
        self.project.link_group(self.group, self.catalog.read_place("e1", "m1", self.project.orchestra_type))
        self.score = self.put("春江花月夜/總譜.pdf", b"score")
        self.parts = [self.put(f"春江花月夜/分譜/{voice}.pdf", voice.encode()) for voice in voices]
        self.project.add_files([self.score] + self.parts, self.group)
        self.project.set_score(self.group, self.score)
        self.project.mark_saved()

    def put(self, relative: str, content: bytes) -> FileInfo:
        """在輸出位置下寫一個檔，回傳它的檔案資訊"""
        path = os.path.join(self.out, *relative.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
        return FileInfo(path, os.path.basename(path))

    def drive_path(self, file_id: str) -> str:
        """Drive 上的檔在版本資料夾下的路徑（資料夾以 / 分隔）"""
        names = []
        item = self.google.drive.file(file_id)
        while item.id != self.folder:
            names.insert(0, item.name)
            item = self.google.drive.file(item.parents[0])
        return "/".join(names)

    def registered(self) -> dict:
        """分譜工作表裡第一樂章各列登記的（Drive 路徑，檔名）；總譜列以「總譜」為鍵"""
        result = {}
        for record in records(self.google, SHEET_PARTS):
            if record["drive_file_id"]:
                key = "總譜" if record["is_score"] else record["instrument_name"]
                result[key] = (self.drive_path(record["drive_file_id"]), record["file_name"])
        return result


class DepositTestCase(DepositFixture, unittest.TestCase):
    """存入服務對假 Google 與臨時目錄（情境見 DepositFixture）"""

    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp)
        self.build(self.VOICES)

    def deposit(self, operator: str = "office@example.com", should_cancel=lambda: False):
        """預覽後存入，回傳（預覽，結果）"""
        plan = self.service.preview(self.project, self.catalog)
        return plan, self.service.deposit(plan, self.catalog, operator, should_cancel, lambda progress: None)


class TestNewUpload(DepositTestCase):
    """Drive 上還沒有的檔：上傳到版本資料夾，子資料夾跟本機的輸出位置相同，並登記到聲部"""

    def test_uploads_score_and_parts_under_the_same_subfolders_and_registers_them(self):
        plan, outcome = self.deposit()
        self.assertEqual(
            [(item.source.voice, item.action) for item in plan.groups[0].items],
            [("", DepositAction.NEW), ("二胡 I", DepositAction.NEW), ("琵琶", DepositAction.NEW)],
        )
        self.assertIsNone(outcome.error)
        self.assertEqual(self.registered(), {
            "總譜": ("總譜.pdf", "總譜.pdf"),
            "二胡 I": ("分譜/二胡 I.pdf", "二胡 I.pdf"),
            "琵琶": ("分譜/琵琶.pdf", "琵琶.pdf"),
        })
        score_rows = [r for r in records(self.google, SHEET_PARTS) if r["is_score"]]
        self.assertEqual([(r["edition_id"], r["movement_id"], r["instrument_name"]) for r in score_rows], [("e1", "m1", "")])
        self.assertEqual(outcome.fingerprints, {
            self.score.original_path: md5(b"score"),
            self.parts[0].original_path: md5("二胡 I".encode()),
            self.parts[1].original_path: md5("琵琶".encode()),
        })


    def test_the_score_is_registered_on_the_existing_score_row(self):
        add_row(self.google, SHEET_PARTS, id="sc", edition_id="e1", movement_id="m1", is_score="TRUE")
        self.deposit()
        score_rows = [r for r in records(self.google, SHEET_PARTS) if r["is_score"]]
        self.assertEqual([r["id"] for r in score_rows], ["sc"])
        self.assertEqual(self.registered()["總譜"], ("總譜.pdf", "總譜.pdf"))


class TestRedeposit(DepositTestCase):
    """重新存入：聲部已登記的檔，內容不同就更新同一個 Drive 檔案（ID 不變、Drive 留著舊版本），相同就跳過"""

    def setUp(self):
        super().setUp()
        self.deposit()
        self.ids = {voice: self.registered_id(voice) for voice in self.VOICES}

    def registered_id(self, voice: str) -> str:
        """分譜工作表裡第一樂章這個聲部登記的 Drive 檔案 ID"""
        return next(r["drive_file_id"] for r in records(self.google, SHEET_PARTS) if r["instrument_name"] == voice)

    def relocate(self, file: FileInfo, relative: str) -> None:
        """把本機的檔搬到輸出位置下的另一個位置，專案跟著改路徑"""
        target = os.path.join(self.out, *relative.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        os.rename(file.original_path, target)
        self.project.replace_paths([UndoMapping(file.original_path, target)])
        self.project.mark_saved()

    def test_same_content_is_skipped_without_touching_drive(self):
        writes = self.google.drive_requests
        plan, outcome = self.deposit()
        self.assertEqual({item.action for item in plan.groups[0].items}, {DepositAction.SKIP})
        self.assertEqual(outcome.completed, 3)
        self.assertEqual(len(self.google.drive.revisions(self.ids["琵琶"])), 1)
        self.assertEqual(self.google.drive_requests - writes, 2)  # 只有預覽時列出版本資料夾與「分譜」資料夾

    def test_changed_content_updates_the_same_file_and_keeps_the_old_version(self):
        with open(self.parts[1].original_path, "wb") as f:
            f.write(b"new pipa")
        plan, outcome = self.deposit()
        self.assertEqual(
            [item.action for item in plan.groups[0].items],
            [DepositAction.SKIP, DepositAction.SKIP, DepositAction.UPDATE],
        )
        self.assertEqual(self.registered_id("琵琶"), self.ids["琵琶"])
        self.assertEqual(self.google.drive.content(self.ids["琵琶"]), b"new pipa")
        self.assertEqual(len(self.google.drive.revisions(self.ids["琵琶"])), 2)
        self.assertEqual(outcome.fingerprints[self.parts[1].original_path], md5(b"new pipa"))

    def test_renamed_locally_moves_the_same_file_to_the_new_name_and_folder(self):
        self.project.set_output_settings(parts_output_mode=PartsOutputMode.ROOT)
        self.project.set_master_template("{序號}_{樂器}")
        self.relocate(self.score, "春江花月夜/00_總譜.pdf")
        self.relocate(self.parts[0], "春江花月夜/01_二胡 I.pdf")
        self.relocate(self.parts[1], "春江花月夜/02_琵琶.pdf")
        with open(self.parts[1].original_path, "wb") as f:
            f.write(b"new pipa")
        plan, outcome = self.deposit()
        self.assertEqual(
            [item.action for item in plan.groups[0].items],
            [DepositAction.MOVE, DepositAction.MOVE, DepositAction.UPDATE],
        )
        self.assertIsNone(outcome.error)
        self.assertEqual(self.registered(), {
            "總譜": ("00_總譜.pdf", "00_總譜.pdf"),
            "二胡 I": ("01_二胡 I.pdf", "01_二胡 I.pdf"),
            "琵琶": ("02_琵琶.pdf", "02_琵琶.pdf"),
        })
        self.assertEqual({voice: self.registered_id(voice) for voice in self.VOICES}, self.ids)
        self.assertEqual(len(self.google.drive.revisions(self.ids["二胡 I"])), 1)
        self.assertEqual(len(self.google.drive.revisions(self.ids["琵琶"])), 2)


class TestSameNameUnregistered(DepositTestCase):
    """聲部沒登記、但目標資料夾已有同名檔：先比對 MD5，相同就直接登記，不同就更新它的內容後登記"""

    def setUp(self):
        super().setUp()
        self.parts_folder = self.google.drive.add_folder("分譜", self.folder)

    def test_same_content_is_registered_without_uploading(self):
        existing = self.google.drive.add_file("琵琶.pdf", self.parts_folder, "琵琶".encode())
        plan, outcome = self.deposit()
        self.assertEqual(plan.groups[0].items[2].action, DepositAction.REGISTER)
        self.assertEqual(self.registered()["琵琶"], ("分譜/琵琶.pdf", "琵琶.pdf"))
        self.assertEqual(len(self.google.drive.files_named("琵琶.pdf")), 1)
        self.assertEqual(len(self.google.drive.revisions(existing)), 1)

    def test_different_content_updates_that_file_then_registers_it(self):
        existing = self.google.drive.add_file("琵琶.pdf", self.parts_folder, b"old scan")
        plan, outcome = self.deposit()
        self.assertEqual(plan.groups[0].items[2].action, DepositAction.UPDATE)
        self.assertEqual(self.registered()["琵琶"], ("分譜/琵琶.pdf", "琵琶.pdf"))
        self.assertEqual(self.google.drive.content(existing), "琵琶".encode())
        self.assertEqual(len(self.google.drive.files_named("琵琶.pdf")), 1)

    def test_a_same_name_file_registered_to_another_row_is_left_alone(self):
        other = self.google.drive.add_file("琵琶.pdf", self.parts_folder, "琵琶".encode())
        add_row(self.google, SHEET_PARTS, id="other", edition_id="e1", movement_id="m2", instrument_name="琵琶",
                drive_file_id=other)
        plan, outcome = self.deposit()
        self.assertEqual(plan.groups[0].items[2].action, DepositAction.NEW)
        mine = next(r["drive_file_id"] for r in records(self.google, SHEET_PARTS) if r["id"] == "pt2")
        self.assertNotIn(mine, ("", other))
        self.assertEqual(len(self.google.drive.files_named("琵琶.pdf")), 2)


class TestInterrupted(DepositTestCase):
    """中斷後重按就補齊：已完成的保留並登記，沒有重複的檔與列"""

    def assert_complete_once(self):
        """每份檔在 Drive 上只有一份、都登記了，總譜列只有一列"""
        for name in ("總譜.pdf", "二胡 I.pdf", "琵琶.pdf"):
            self.assertEqual(len(self.google.drive.files_named(name)), 1, name)
        self.assertEqual(set(self.registered()), {"總譜", "二胡 I", "琵琶"})
        self.assertEqual(sum(1 for r in records(self.google, SHEET_PARTS) if r["is_score"]), 1)

    def test_stopping_at_the_second_file_keeps_the_first_and_pressing_again_completes_it(self):
        self.google.drive.fail_upload(2, CatalogErrorKind.OFFLINE)
        _, first = self.deposit()
        self.assertEqual(first.error.kind, CatalogErrorKind.OFFLINE)
        self.assertEqual((first.completed, first.total), (1, 3))
        self.assertEqual(set(self.registered()), {"總譜"})
        self.assertEqual(list(first.fingerprints), [self.score.original_path])
        plan, second = self.deposit()
        self.assertEqual(
            [item.action for item in plan.groups[0].items],
            [DepositAction.SKIP, DepositAction.NEW, DepositAction.NEW],
        )
        self.assertIsNone(second.error)
        self.assert_complete_once()
        self.assertEqual(len(records(self.google, SHEET_DEPOSIT_LOG)), 1 + 3)

    def test_an_upload_whose_response_was_lost_is_registered_on_the_next_press(self):
        self.google.drive.fail_upload(2, CatalogErrorKind.OFFLINE, applied=True)
        _, first = self.deposit()
        self.assertEqual(first.completed, 1)
        plan, second = self.deposit()
        self.assertEqual(plan.groups[0].items[1].action, DepositAction.REGISTER)
        self.assertIsNone(second.error)
        self.assert_complete_once()

    def test_an_upload_resent_after_a_lost_response_gets_conflict_and_keeps_one_copy(self):
        self.google.drive.fail_upload(2, CatalogErrorKind.RATE_LIMITED, applied=True)
        _, outcome = self.deposit()
        self.assertIsNone(outcome.error)
        self.assert_complete_once()

    def test_cancelling_stops_after_the_current_file_and_keeps_it(self):
        asked = []

        def cancel_after_first():
            asked.append(True)
            return len(asked) > 1

        _, outcome = self.deposit(should_cancel=cancel_after_first)
        self.assertTrue(outcome.cancelled)
        self.assertEqual(outcome.completed, 1)
        self.assertEqual(set(self.registered()), {"總譜"})


class TestRequests(DepositTestCase):
    """試算表的請求數不隨聲部數增加；遇到用量上限自動等一下再試"""

    def test_sheets_requests_are_the_same_for_3_and_30_voices(self):
        counts = []
        for voices in (("二胡 I", "二胡 II", "琵琶"), tuple(f"聲部{n}" for n in range(1, 31))):
            self.build(voices)
            before = self.google.sheets_requests
            _, outcome = self.deposit()
            self.assertEqual(outcome.completed, len(voices) + 1)
            counts.append(self.google.sheets_requests - before)
        self.assertEqual(counts[0], counts[1])

    def test_rate_limit_is_retried_after_waiting(self):
        self.google.drive.fail_upload(2, CatalogErrorKind.RATE_LIMITED)
        _, outcome = self.deposit()
        self.assertIsNone(outcome.error)
        self.assertEqual(outcome.completed, 3)
        self.assertEqual(self.waits, [1.0])


class TestDepositLog(DepositTestCase):
    """存入紀錄：每份檔一列，一次存入一個存入編號，只新增、不修改"""

    def test_each_file_gets_one_row_and_earlier_rows_stay(self):
        add_row(self.google, SHEET_DEPOSIT_LOG, id="old", deposit_id="d0", action="new", voice="笛")
        self.deposit(operator="a@example.com")
        with open(self.parts[1].original_path, "wb") as f:
            f.write(b"new pipa")
        self.deposit(operator="b@example.com")
        log = records(self.google, SHEET_DEPOSIT_LOG)
        self.assertEqual((log[0]["id"], log[0]["deposit_id"], log[0]["voice"]), ("old", "d0", "笛"))
        first, second = log[1:4], log[4:]
        self.assertEqual([r["action"] for r in first], ["new", "new", "new"])
        self.assertEqual([r["action"] for r in second], ["skip", "skip", "update"])
        self.assertEqual(len({r["deposit_id"] for r in first}), 1)
        self.assertEqual(len({r["deposit_id"] for r in second}), 1)
        self.assertEqual(len({first[0]["deposit_id"], second[0]["deposit_id"], "d0"}), 3)
        self.assertEqual(len({r["id"] for r in log}), 7)
        self.assertEqual(
            [(r["operator"], r["edition_id"], r["movement_id"], r["group"], r["voice"]) for r in first],
            [("a@example.com", "e1", "m1", "江樓鐘鼓", voice) for voice in ("總譜", "二胡 I", "琵琶")],
        )
        self.assertEqual(
            [r["local_path"] for r in second], [f.original_path for f in [self.score] + self.parts],
        )
        pipa = second[2]
        self.assertEqual(
            self.google.drive.revisions(pipa["drive_file_id"]), [pipa["revision_before"], pipa["revision_after"]],
        )
        self.assertEqual(second[0]["revision_before"], second[0]["revision_after"])

    def test_an_old_catalog_gets_the_deposit_log_sheet_with_its_header(self):
        self.google.sheets.delete_tab(SHEET_DEPOSIT_LOG)
        _, outcome = self.deposit()
        self.assertIsNone(outcome.error)
        self.assertEqual(self.google.sheets.rows(SHEET_DEPOSIT_LOG)[0], SHEET_HEADERS[SHEET_DEPOSIT_LOG])
        self.assertEqual(len(records(self.google, SHEET_DEPOSIT_LOG)), 3)


class TestNotDeposited(DepositTestCase):
    """不存入的群組與聲部：沒連到譜庫、還沒重新命名、有找不到的檔、版本沒有資料夾、譜庫沒有那個聲部"""

    def test_a_group_not_linked_is_not_deposited(self):
        other = self.project.add_group("茉莉花", "總譜", [self.put("茉莉花/總譜.pdf", b"x")])
        self.project.mark_saved()
        plan, outcome = self.deposit()
        self.assertEqual(plan.groups[1].sources.group_id, other.id)
        self.assertEqual((plan.groups[1].block, plan.groups[1].items), (DepositBlock.NOT_LINKED, []))
        self.assertEqual(outcome.total, 3)

    def test_a_group_not_renamed_yet_is_not_deposited(self):
        self.project.set_master_template("{序號}_{樂器}")
        plan, outcome = self.deposit()
        self.assertEqual(plan.groups[0].block, DepositBlock.NOT_RENAMED)
        self.assertEqual((outcome.total, self.google.drive.children(self.folder)), (0, []))

    def test_renamed_in_place_without_an_output_location_counts_as_renamed(self):
        self.project.set_output_settings(output_directory="")
        plan, _ = self.deposit()
        self.assertIsNone(plan.groups[0].block)

    def test_a_group_with_a_missing_file_is_not_deposited(self):
        os.remove(self.parts[0].original_path)
        plan, _ = self.deposit()
        self.assertEqual(plan.groups[0].block, DepositBlock.MISSING_FILES)

    def test_an_edition_without_a_drive_folder_is_not_deposited(self):
        self.google.sheets.set_cell(SHEET_EDITIONS, "G2", "")
        plan, _ = self.deposit()
        self.assertEqual(plan.groups[0].block, DepositBlock.NO_FOLDER)

    def test_a_voice_not_yet_in_the_catalog_is_left_out(self):
        self.project.add_instrument(self.group, "大阮")
        self.project.add_files([self.put("春江花月夜/分譜/大阮.pdf", b"daruan")], self.group)
        self.project.mark_saved()
        plan, outcome = self.deposit()
        self.assertEqual(plan.groups[0].items[3].action, DepositAction.UNLISTED)
        self.assertEqual(outcome.completed, 3)
        self.assertEqual(self.google.drive.files_named("大阮.pdf"), [])


class TestFingerprints(DepositTestCase):
    """存入後把每份檔的指紋（譜庫裡那份的 MD5）記進專案，一起存進專案檔"""

    def test_fingerprints_and_filed_voices_go_into_the_project(self):
        _, outcome = self.deposit()
        self.project.record_deposit(outcome.records)
        self.assertTrue(self.project.is_modified())
        self.assertEqual(
            [f.deposit_md5 for f in [self.group.score_file] + self.group.files],
            [md5(b"score"), md5("二胡 I".encode()), md5("琵琶".encode())],
        )
        self.assertEqual(self.group.catalog_link.snapshot.filed, ["二胡 I", "琵琶"])
        reopened = Project.from_data({"version": PROJECT_FILE_VERSION, **self.project.to_data()}, "總譜")
        self.assertEqual(reopened.groups[0].files[1].deposit_md5, md5("琵琶".encode()))

    def test_files_never_deposited_keep_no_fingerprint_in_the_project_file(self):
        self.assertNotIn("deposit_md5", self.project.to_data()["groups"][0]["files"][0])



class TestUndeposited(DepositTestCase):
    """未存入：本機檔案的 MD5 跟存入指紋不一致；只讀本機檔案，不碰譜庫"""

    def setUp(self):
        super().setUp()
        _, outcome = self.deposit()
        self.project.record_deposit(outcome.records)
        self.requests = self.google.requests

    def write(self, file: FileInfo, content: bytes) -> None:
        """改寫本機檔案的內容"""
        with open(file.original_path, "wb") as f:
            f.write(content)

    def undeposited(self) -> list:
        """回報未存入的檔案路徑"""
        return [ref.file.original_path for ref in self.service.undeposited(self.project)]

    def test_a_file_changed_after_archiving_is_undeposited(self):
        self.write(self.parts[1], b"new pipa")
        self.assertEqual(self.undeposited(), [self.parts[1].original_path])
        self.assertEqual(self.google.requests, self.requests)

    def test_changing_the_content_back_is_no_longer_undeposited(self):
        self.write(self.parts[1], b"new pipa")
        self.undeposited()
        self.write(self.parts[1], "琵琶".encode())
        self.assertEqual(self.undeposited(), [])

    def test_files_never_archived_or_missing_are_not_undeposited(self):
        extra = self.put("春江花月夜/分譜/揚琴.pdf", b"yangqin")
        self.project.add_files([extra], self.group)
        os.remove(self.parts[0].original_path)
        self.assertEqual(self.undeposited(), [])


if __name__ == "__main__":
    unittest.main()
