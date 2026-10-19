# -*- coding: utf-8 -*-
"""
HuaXia 發譜函式的介面測試

經 HuaXiaClient.publish 對假 HuaXia 測：假 HuaXia 的發譜函式照 docs/huaxia/LINGLING.md（HuaXia 依賴說明文件的複本）
實作，這裡釘住它與文件一致的行為，讓發譜（#122）可以照文件對它寫：一首曲目的寫入全成或全不成、重跑不重複建、
只撤這首曲目的登記並回傳實際撤掉的、不是幹部寫不進去、版本不符整個不做。
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from services.huaxia_client import (
    PUBLISH_INTERFACE_VERSION, HuaXiaClient, HuaXiaError, HuaXiaErrorKind, lingling_storage_path,
)
from test_publish_preflight import PrepareTestCase

# 泠靈先產生的新曲目編號
NEW_PIECE = "22222222-2222-2222-2222-222222222223"


def stored(piece_id: str, n: int) -> str:
    """曲目上第 n 份內容的泠靈 Storage 路徑（SHA-256 是假的）"""
    return lingling_storage_path(piece_id, f"{n:064x}")


class PublishTestCase(PrepareTestCase):
    """已登入的幹部 officer_amy、相符版本的發譜函式；本學期有一場還沒到的公演"""

    def setUp(self):
        super().setUp()
        self.client = self.auth.client
        now = datetime.datetime.now(datetime.timezone.utc)
        semester = self.huaxia.add_semester("115-1", is_current=True)
        self.concert = self.huaxia.add_event(semester, "期末音樂會", now + datetime.timedelta(days=30),
                                             now + datetime.timedelta(days=30, hours=3))

    def publish(self, **payload):
        """以 officer_amy 呼叫發譜函式（version 省略時為泠靈的介面版本）"""
        return self.client.publish(self.auth.access_token(), {"version": PUBLISH_INTERFACE_VERSION, **payload})

    def rows(self, table: str, **equals):
        """假 HuaXia 資料表裡欄位等於指定值的列"""
        return [row for row in self.huaxia.tables[table] if all(row.get(k) == v for k, v in equals.items())]


class TestNewPiece(PublishTestCase):
    """新建曲目：曲目、聲部、樂譜與場次一次寫進去，回傳新建的編號"""

    def test_a_new_piece_gets_its_parts_scores_and_concert(self):
        self.huaxia.put_object(stored(NEW_PIECE, 1))
        self.huaxia.put_object(stored(NEW_PIECE, 2))
        out = self.publish(
            piece_id=NEW_PIECE,
            new_piece={"title": "春江花月夜", "composer": "佚名", "duration_min": 7.5, "ensemble": "tutti"},
            create_parts=[
                {"name": "二胡 I", "needed": "6-8", "needed_min": 6, "section": "", "sort": 1},
                {"name": "琵琶", "needed": "2↑", "needed_min": 2, "section": "彈撥", "sort": 2},
            ],
            add_scores=[
                {"new_part": "二胡 I", "title": "二胡 I", "file_path": stored(NEW_PIECE, 1)},
                {"title": "總譜", "file_path": stored(NEW_PIECE, 2)},
            ],
            attach_events=[self.concert],
        )
        self.assertEqual(out["version"], PUBLISH_INTERFACE_VERSION)
        self.assertEqual(out["piece_id"], NEW_PIECE)
        self.assertTrue(out["piece_created"])
        self.assertEqual([p["name"] for p in out["created_parts"]], ["二胡 I", "琵琶"])
        erhu = out["created_parts"][0]["id"]
        self.assertEqual(
            [(s["part_id"], s["file_path"]) for s in out["added_scores"]],
            [(erhu, stored(NEW_PIECE, 1)), (None, stored(NEW_PIECE, 2))],
        )
        self.assertEqual(out["removed_scores"], [])
        self.assertEqual(out["attached_events"], [self.concert])
        self.assertEqual(self.rows("pieces", id=NEW_PIECE)[0]["title"], "春江花月夜")
        self.assertEqual(
            [(p["name"], p["needed"], p["needed_min"], p["section"]) for p in self.rows("piece_parts", piece_id=NEW_PIECE)],
            [("二胡 I", "6-8", 6, "拉弦"), ("琵琶", "2↑", 2, "彈撥")],
        )
        self.assertEqual(self.rows("event_pieces", piece_id=NEW_PIECE), [{"event_id": self.concert, "piece_id": NEW_PIECE}])

    def test_running_again_keeps_the_same_piece_and_skips_concerts_already_attached(self):
        new_piece = {"title": "春江花月夜"}
        self.publish(piece_id=NEW_PIECE, new_piece=new_piece, attach_events=[self.concert])
        out = self.publish(piece_id=NEW_PIECE, new_piece={"title": "改過的曲名"}, attach_events=[self.concert])
        self.assertFalse(out["piece_created"])
        self.assertEqual(out["attached_events"], [])
        self.assertEqual([p["title"] for p in self.rows("pieces", id=NEW_PIECE)], ["春江花月夜"])
        self.assertEqual(len(self.rows("event_pieces", piece_id=NEW_PIECE)), 1)


class TestExistingPiece(PublishTestCase):
    """現有曲目：改人數與聲部組、取代樂譜"""

    def setUp(self):
        super().setUp()
        self.erhu = self.huaxia.add_part(self.fenghuo, "二胡1", needed="6-8", needed_min=6, sort=1)
        self.old = self.huaxia.add_score(self.fenghuo, self.erhu, stored(self.fenghuo, 1), "二胡1")
        self.elsewhere = self.huaxia.add_score(self.moli, None, stored(self.moli, 1), "總譜")
        self.huaxia.put_object(stored(self.fenghuo, 2))

    def test_headcounts_change_and_only_this_pieces_registrations_are_removed(self):
        out = self.publish(
            piece_id=self.fenghuo, update_parts=[{"id": self.erhu, "needed": "4", "needed_min": 4}],
            remove_scores=[self.old, self.elsewhere, "99999999-9999-9999-9999-999999999999"],
            add_scores=[{"part_id": self.erhu, "title": "二胡 I", "file_path": stored(self.fenghuo, 2)}],
        )
        self.assertFalse(out["piece_created"])
        self.assertEqual(
            out["removed_scores"], [{"id": self.old, "part_id": self.erhu, "file_path": stored(self.fenghuo, 1)}],
        )
        part = self.rows("piece_parts", id=self.erhu)[0]
        self.assertEqual((part["needed"], part["needed_min"], part["section"]), ("4", 4, "拉弦"))
        self.assertEqual(len(self.rows("scores", id=self.elsewhere)), 1)
        self.assertEqual([s["file_path"] for s in self.rows("scores", part_id=self.erhu)], [stored(self.fenghuo, 2)])

    def test_an_empty_section_is_left_to_huaxias_guess(self):
        self.huaxia.tables["piece_parts"][0]["section"] = "吹管"
        self.publish(piece_id=self.fenghuo, update_parts=[{"id": self.erhu, "section": ""}])
        self.assertEqual(self.rows("piece_parts", id=self.erhu)[0]["section"], "拉弦")


class RejectionTestCase(PublishTestCase):
    """要被拒的切換內容的底稿：新建「將軍令」與它的嗩吶聲部、登記一份已傳到 Storage 的檔、掛上公演"""

    def setUp(self):
        super().setUp()
        self.moli_part = self.huaxia.add_part(self.moli, "笛子", sort=1)
        self.huaxia.put_object(stored(NEW_PIECE, 1))
        self.base = {
            "piece_id": NEW_PIECE, "new_piece": {"title": "將軍令"},
            "create_parts": [{"name": "嗩吶", "needed": "1", "needed_min": 1, "sort": 1}],
            "update_parts": [], "add_scores": [{"new_part": "嗩吶", "file_path": stored(NEW_PIECE, 1)}],
            "attach_events": [self.concert],
        }

    def snapshot(self):
        """會被發譜寫到的資料表現在的內容"""
        return {t: [dict(row) for row in self.huaxia.tables[t]] for t in ("pieces", "piece_parts", "scores", "event_pieces")}

    def assert_rejected(self, code: str, status: int, **change):
        """這樣改的切換內容整個被拒：錯誤代碼與狀態碼如預期，資料表沒有任何變化"""
        before = self.snapshot()
        with self.assertRaises(HuaXiaError) as caught:
            self.publish(**{**self.base, **change})
        self.assertEqual((caught.exception.code, caught.exception.status), (code, status))
        self.assertEqual(self.snapshot(), before)


class TestAllOrNothing(RejectionTestCase):
    """任何一步不成，整次呼叫都不留下寫入；錯誤代碼照依賴說明文件"""

    def test_a_file_not_yet_in_storage(self):
        self.assert_rejected("PT404", 404, add_scores=[
            *self.base["add_scores"], {"file_path": stored(NEW_PIECE, 9)},
        ])

    def test_a_path_that_is_not_linglings(self):
        self.assert_rejected("22023", 400, add_scores=[{"file_path": f"{NEW_PIECE}/full/123_x.pdf"}])
        self.assert_rejected("22023", 400, add_scores=[{"file_path": stored(self.moli, 1)}])

    def test_a_part_of_another_piece(self):
        self.assert_rejected("PT404", 404, update_parts=[{"id": self.moli_part, "needed": "9"}])
        self.assert_rejected("PT404", 404, add_scores=[{"part_id": self.moli_part, "file_path": stored(NEW_PIECE, 1)}])

    def test_a_concert_that_does_not_exist(self):
        self.assert_rejected("23503", 409, attach_events=[self.concert, "55555555-5555-5555-5555-555555555550"])

    def test_malformed_parts(self):
        self.assert_rejected("22023", 400, create_parts=[{"name": "嗩吶"}, {"name": "嗩吶"}])
        self.assert_rejected("22023", 400, add_scores=[{"new_part": "笙", "file_path": stored(NEW_PIECE, 1)}])
        self.assert_rejected("22023", 400, add_scores=[
            {"new_part": "嗩吶", "part_id": self.moli_part, "file_path": stored(NEW_PIECE, 1)},
        ])

    def test_an_existing_piece_that_is_gone(self):
        self.assert_rejected("PT404", 404, new_piece=None)

    def test_another_interface_version(self):
        self.assert_rejected("PT409", 409, version=PUBLISH_INTERFACE_VERSION + 1)
        self.huaxia.install_publish_function(PUBLISH_INTERFACE_VERSION + 1)
        self.assert_rejected("PT409", 409)


class TestNotAnOfficer(RejectionTestCase):
    """不是幹部：HuaXia 的 RLS 擋下寫入，什麼都沒寫進去；沒登入不能呼叫"""

    def setUp(self):
        super().setUp()
        self.token = self.auth.access_token()
        self.huaxia.remove_roles("officer_amy")
        self.huaxia.tables["user_roles"].append({"user_id": self.huaxia.tables["profiles"][0]["id"], "role": "member"})

    def publish(self, **payload):
        """以拿掉幹部身分的 officer_amy 呼叫發譜函式（沿用拿掉之前的 access token）"""
        return self.client.publish(self.token, {"version": PUBLISH_INTERFACE_VERSION, **payload})

    def test_creating_is_blocked_by_rls(self):
        self.assert_rejected("42501", 403)
        self.assert_rejected("42501", 403, piece_id=self.moli, new_piece=None, create_parts=[{"name": "高胡"}], add_scores=[])
        self.assert_rejected("42501", 403, piece_id=self.moli, new_piece=None, create_parts=[], add_scores=[])

    def test_changing_a_part_finds_nothing_it_may_change(self):
        self.assert_rejected(
            "PT404", 404, piece_id=self.moli, new_piece=None, create_parts=[], add_scores=[], attach_events=[],
            update_parts=[{"id": self.moli_part, "needed": "9"}],
        )

    def test_without_signing_in_the_function_cannot_be_called(self):
        before = self.snapshot()
        with self.assertRaises(HuaXiaError) as caught:
            HuaXiaClient(session=self.huaxia).publish("", {"version": PUBLISH_INTERFACE_VERSION, **self.base})
        self.assertEqual((caught.exception.code, caught.exception.status), ("42501", 401))
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(HuaXiaError) as caught:
            HuaXiaClient(session=self.huaxia).publish_interface_version("")
        self.assertEqual((caught.exception.code, caught.exception.status), ("42501", 401))


class TestStorage(PublishTestCase):
    """發譜的第 1 段與第 3 段：檔案傳到 Storage（不覆寫）、刪掉 Storage 的檔"""

    def test_an_upload_lands_at_its_path_and_a_second_one_is_already_there(self):
        token = self.auth.access_token()
        path = stored(self.moli, 1)
        self.assertTrue(self.client.upload(token, path, b"%PDF-1"))
        self.assertEqual(self.huaxia.objects[path], b"%PDF-1")
        self.assertFalse(self.client.upload(token, path, b"%PDF-1"))
        self.assertEqual(self.client.stored_files(token, f"lingling/{self.moli}"), {path: 6})

    def test_deleting_removes_only_the_given_files(self):
        token = self.auth.access_token()
        kept, gone = stored(self.moli, 1), stored(self.moli, 2)
        self.huaxia.put_object(kept)
        self.huaxia.put_object(gone)
        self.client.delete_files(token, [gone, stored(self.moli, 3)])
        self.assertEqual(sorted(self.huaxia.objects), [kept])

    def test_a_member_cannot_upload_or_delete(self):
        token = self.auth.access_token()
        path = stored(self.moli, 1)
        self.huaxia.put_object(path)
        self.huaxia.remove_roles("officer_amy")
        with self.assertRaises(HuaXiaError):
            self.client.upload(token, stored(self.moli, 2), b"%PDF")
        self.client.delete_files(token, [path])
        self.assertEqual(sorted(self.huaxia.objects), [path])

    def test_a_dropped_request_is_unreachable_and_later_ones_go_through(self):
        token = self.auth.access_token()
        self.huaxia.drop_requests("POST", "/storage/v1/object/scores/", skip=1)
        self.assertTrue(self.client.upload(token, stored(self.moli, 1), b"%PDF"))
        with self.assertRaises(HuaXiaError) as caught:
            self.client.upload(token, stored(self.moli, 2), b"%PDF")
        self.assertEqual(caught.exception.kind, HuaXiaErrorKind.UNREACHABLE)
        self.assertTrue(self.client.upload(token, stored(self.moli, 2), b"%PDF"))


if __name__ == '__main__':
    unittest.main()
