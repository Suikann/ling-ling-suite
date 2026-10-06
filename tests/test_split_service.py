# -*- coding: utf-8 -*-
"""
分割模組測試

從 SplitService 的介面驗證檢查與執行：在暫存目錄裡用真 PDF，工作區目錄由建構時注入，不需要 Qt。
合併譜第 i 頁（從 0 起算）的寬度是 100 + i，以頁寬辨認分譜含哪幾頁。
移到資源回收桶改為記下路徑後直接移除；寫分譜失敗以注入抽頁函式模擬。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from PyPDF2 import PdfReader, PdfWriter

from core.constants import WORKSPACE_FOLDER_HASH_LENGTH
from core.models import FileInfo, Project, SplitRecord, WorkspaceOwner
from services.file_service import FileService
from services.pdf_service import extract_pages
from services.project_service import ProjectService
from services.split_service import SplitRequest, SplitSegment, SplitService
from services.workspace_service import WorkspaceService
from path_spellings import spellings


def _make_pdf(path: str, pages: int) -> str:
    """建立 pages 頁的 PDF，第 i 頁寬 100 + i"""
    writer = PdfWriter()
    for i in range(pages):
        writer.add_blank_page(width=100 + i, height=100)
    with open(path, "wb") as f:
        writer.write(f)
    return path


def _page_numbers(path: str):
    """分譜含合併譜的哪幾頁（從 0 起算，由頁寬推得）"""
    return [int(float(page.mediabox.width)) - 100 for page in PdfReader(path).pages]


class _TrashRecordingFileService(FileService):
    """移到資源回收桶改為記下路徑後直接移除；可指定原子寫入會失敗的檔名"""

    def __init__(self):
        super().__init__()
        self.trashed = []
        self.unwritable = set()

    def delete_file(self, path):
        self.trashed.append(path)
        os.remove(path)

    def write_json_atomic(self, path, data):
        if os.path.basename(path) in self.unwritable:
            raise OSError(f"read-only: {path}")
        super().write_json_atomic(path, data)


def _failing_on_call(number: int):
    """第 number 次呼叫時寫出半個檔後拋出 OSError 的抽頁函式，其餘照常抽頁"""
    calls = []

    def extract(source, pages, output):
        calls.append(output)
        if len(calls) == number:
            with open(output, "wb") as f:
                f.write(b"%PDF-half")
            raise OSError("disk full")
        return extract_pages(source, pages, output)

    return extract


def _snapshot(folder: str):
    """資料夾裡每個項目的名稱與內容（子資料夾記為 None）"""
    snapshot = {}
    for name in sorted(os.listdir(folder)):
        path = os.path.join(folder, name)
        if os.path.isdir(path):
            snapshot[name] = None
        else:
            with open(path, "rb") as f:
                snapshot[name] = f.read()
    return snapshot


class SplitServiceTestCase(unittest.TestCase):
    """分割模組測試的共用骨架：合併譜放在 scores/，工作區在 data/workspace/"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp_dir, True)
        self.scores = os.path.join(self.temp_dir, "scores")
        os.makedirs(self.scores)
        self.file_service = _TrashRecordingFileService()
        self.workspace = WorkspaceService(self.file_service, os.path.join(self.temp_dir, "data", "workspace"))
        self.splitter = SplitService(self.file_service, self.workspace)
        self.source = _make_pdf(self.path("合併譜.pdf"), 4)

    def path(self, *parts: str) -> str:
        """scores/ 底下的路徑"""
        return os.path.join(self.scores, *parts)

    def request(self, *segments, **fields) -> SplitRequest:
        """分割 self.source 的請求；segments 為（第一頁、最後一頁、名稱）"""
        return SplitRequest(self.source, [SplitSegment(*s) for s in segments], **fields)


class TestSplitIntoWorkspace(SplitServiceTestCase):
    """預設輸出到合併譜在工作區的子資料夾"""

    def test_each_segment_becomes_a_part_in_the_source_workspace_folder(self):
        check = self.splitter.check(self.request((0, 1, "Flute"), (2, 3, "Oboe")))
        self.assertFalse(check.blocked)
        result = self.splitter.execute(check)
        folder = self.splitter.output_folder(self.source)
        self.assertEqual(os.path.dirname(folder), self.workspace.workspace_dir)
        flute, oboe = os.path.join(folder, "Flute.pdf"), os.path.join(folder, "Oboe.pdf")
        self.assertEqual(result.parts, [FileInfo(flute, "Flute.pdf"), FileInfo(oboe, "Oboe.pdf")])
        self.assertEqual(result.voices, ["Flute", "Oboe"])
        self.assertEqual(result.replaced, [])
        self.assertEqual((_page_numbers(flute), _page_numbers(oboe)), ([0, 1], [2, 3]))
        self.assertEqual(_page_numbers(self.source), [0, 1, 2, 3])
        self.assertEqual(sorted(os.listdir(folder)), ["Flute.pdf", "Oboe.pdf", "meta.json"])
        self.assertEqual(result.record.created_directories, [folder])


class TestSplitPlan(SplitServiceTestCase):
    """分段到分譜：頁面去掉已刪的，檔名清理非法字元並補 .pdf，聲部名稱保留輸入"""

    def _planned(self, *segments, deleted_pages=()):
        """檢查結果要產生的分譜：（頁面、聲部名稱、檔名）"""
        check = self.splitter.check(self.request(*segments, deleted_pages=set(deleted_pages)))
        return [(e.pages, e.display_name, os.path.basename(e.output_path)) for e in check.plan]

    def test_deleted_pages_are_left_out(self):
        self.assertEqual(self._planned((0, 3, "Flute"), deleted_pages={1, 2}), [([0, 3], "Flute", "Flute.pdf")])

    def test_segment_with_every_page_deleted_is_skipped(self):
        self.assertEqual(
            self._planned((0, 0, "Flute"), (1, 2, "Oboe"), deleted_pages={0}), [([1, 2], "Oboe", "Oboe.pdf")],
        )

    def test_existing_pdf_extension_is_kept_whatever_its_case(self):
        self.assertEqual(self._planned((0, 0, "Flute.PDF")), [([0], "Flute.PDF", "Flute.PDF")])

    def test_blank_name_falls_back_to_part_for_the_file_only(self):
        self.assertEqual(self._planned((0, 0, "   ")), [([0], "", "Part.pdf")])

    def test_illegal_characters_are_replaced_in_the_file_name_only(self):
        self.assertEqual(self._planned((0, 0, " Violin I/II ")), [([0], "Violin I/II", "Violin I_II.pdf")])

    def test_nothing_to_split_when_every_page_is_deleted(self):
        check = self.splitter.check(self.request((0, 3, "Flute"), deleted_pages={0, 1, 2, 3}))
        self.assertEqual(check.plan, [])
        self.assertTrue(check.blocked)


class TestDuplicateSegmentNames(SplitServiceTestCase):
    """兩個分段會寫到同一個檔：檢查就擋下並指出是哪幾段，不寫出任何檔"""

    def assert_blocked(self, segments, file_name, numbers):
        check = self.splitter.check(self.request(*segments))
        self.assertTrue(check.blocked)
        self.assertEqual([(d.file_name, d.segments) for d in check.duplicates], [(file_name, numbers)])
        with self.assertRaises(ValueError):
            self.splitter.execute(check)
        self.assertFalse(os.path.exists(self.workspace.workspace_dir))

    def test_identical_names(self):
        self.assert_blocked([(0, 0, "Flute"), (1, 1, "Oboe"), (2, 3, "Flute")], "Flute.pdf", [1, 3])

    def test_names_identical_after_replacing_illegal_characters(self):
        self.assert_blocked([(0, 1, "A/B"), (2, 3, "A_B")], "A_B.pdf", [1, 2])

    def test_names_differing_only_in_case(self):
        self.assert_blocked([(0, 1, "Flute"), (2, 3, "FLUTE")], "Flute.pdf", [1, 2])

    def test_blank_names_falling_back_to_the_same_name(self):
        self.assert_blocked([(0, 0, ""), (1, 1, "Oboe"), (2, 3, "  ")], "Part.pdf", [1, 3])

    def test_numbers_count_segments_whose_pages_were_all_deleted(self):
        check = self.splitter.check(self.request(
            (0, 0, "Flute"), (1, 1, "Oboe"), (2, 3, "Flute"), deleted_pages={1},
        ))
        self.assertEqual([d.segments for d in check.duplicates], [[1, 3]])


class TestResplitInWorkspace(SplitServiceTestCase):
    """重新分割同一份合併譜：上次的分譜由這次的取代，舊的移到資源回收桶"""

    def setUp(self):
        super().setUp()
        self.mine = os.path.join(self.temp_dir, "mine.llproj")
        first = self.splitter.execute(self.splitter.check(
            self.request((0, 1, "Flute"), (2, 3, "Oboe"), project_path=self.mine),
        ))
        self.previous = [p.original_path for p in first.parts]
        self.folder = self.splitter.output_folder(self.source)

    def resplit(self, splitter=None, project_path=""):
        """以另一組分段重新分割（另一個尚未存檔的專案）"""
        splitter = splitter or self.splitter
        return splitter.execute(splitter.check(
            self.request((0, 0, "Flute"), (1, 3, "Horn"), project_path=project_path),
        ))

    def assert_untouched(self, before):
        """上次的分譜仍在原處、內容不變，沒有檔案進資源回收桶，所屬專案未被改寫，也沒有留下新分譜"""
        self.assertEqual(_snapshot(self.folder), before)
        self.assertEqual(self.file_service.trashed, [])
        self.assertEqual(self.workspace.read_meta(self.folder)["project_path"], self.mine)

    def test_check_reports_the_previous_parts(self):
        check = self.splitter.check(self.request((0, 0, "Flute"), (1, 3, "Horn")))
        self.assertFalse(check.blocked)
        self.assertEqual(check.previous_outputs, self.previous)
        self.assertEqual(check.replaced, self.previous)

    def test_new_parts_replace_the_previous_ones_which_go_to_the_recycle_bin(self):
        result = self.splitter.execute(self.splitter.check(self.request((0, 0, "Flute"), (1, 3, "Horn"))))
        flute, horn = os.path.join(self.folder, "Flute.pdf"), os.path.join(self.folder, "Horn.pdf")
        self.assertEqual([p.original_path for p in result.parts], [flute, horn])
        self.assertEqual(result.replaced, self.previous)
        self.assertEqual(sorted(os.path.basename(p) for p in self.file_service.trashed), ["Flute.pdf", "Oboe.pdf"])
        self.assertEqual((_page_numbers(flute), _page_numbers(horn)), ([0], [1, 2, 3]))
        self.assertEqual(sorted(os.listdir(self.folder)), ["Flute.pdf", "Horn.pdf", "meta.json"])

    def test_record_lists_the_new_parts_and_the_replaced_files(self):
        result = self.resplit()
        self.assertEqual(result.record, SplitRecord(
            source_path=self.source,
            created_files=[os.path.join(self.folder, "Flute.pdf"), os.path.join(self.folder, "Horn.pdf")],
            created_directories=[],
            replaced_files=self.previous,
        ))

    def test_failure_writing_the_second_part_rolls_the_whole_split_back(self):
        before = _snapshot(self.folder)
        with self.assertRaises(OSError):
            self.resplit(SplitService(self.file_service, self.workspace, extract=_failing_on_call(2)))
        self.assert_untouched(before)

    def test_failure_recording_the_new_owner_rolls_the_whole_split_back(self):
        before = _snapshot(self.folder)
        self.file_service.unwritable.add("meta.json")
        with self.assertRaises(OSError):
            self.resplit()
        self.assert_untouched(before)


class TestOwnership(SplitServiceTestCase):
    """工作區子資料夾跨專案共用：上次的分譜屬於其他專案時，檢查點名該專案；成功後所屬專案改成目前專案"""

    def split(self, project_path="", *segments):
        segments = segments or ((0, 1, "Flute"), (2, 3, "Oboe"))
        return self.splitter.execute(self.splitter.check(self.request(*segments, project_path=project_path)))

    def owner_seen_by(self, project_path):
        return self.splitter.check(self.request((0, 3, "Flute"), project_path=project_path)).owner

    def project_file(self, name):
        path = os.path.join(self.temp_dir, name)
        ProjectService().save_project(Project(), path)
        return path

    def test_parts_of_another_project_name_that_project(self):
        theirs = self.project_file("theirs.llproj")
        self.split(theirs)
        self.assertEqual(self.owner_seen_by(os.path.join(self.temp_dir, "mine.llproj")), WorkspaceOwner(theirs, True))
        # 尚未存檔的專案也不是它
        self.assertEqual(self.owner_seen_by(""), WorkspaceOwner(theirs, True))

    def test_project_file_that_no_longer_exists_is_flagged(self):
        gone = os.path.join(self.temp_dir, "gone.llproj")
        self.split(gone)
        self.assertEqual(self.owner_seen_by(os.path.join(self.temp_dir, "mine.llproj")), WorkspaceOwner(gone, False))

    def test_own_parts_name_no_one_whatever_the_spelling_of_the_project_file(self):
        mine = os.path.join(os.getcwd(), "Winter.llproj")  # 只比對路徑，不讀寫
        self.split(mine)
        for label, spelled in spellings(mine).items():
            with self.subTest(label):
                self.assertIsNone(self.owner_seen_by(spelled))

    def test_parts_of_an_unsaved_project_name_no_one(self):
        self.split("")
        self.assertIsNone(self.owner_seen_by(os.path.join(self.temp_dir, "mine.llproj")))
        self.assertIsNone(self.owner_seen_by(""))

    def test_replacing_another_projects_parts_makes_them_the_current_projects(self):
        theirs = self.project_file("theirs.llproj")
        self.split(theirs)
        # 未存檔專案取代過一次後，再次重新分割取代的是自己上次的嘗試，不該再點名別人
        self.split("")
        self.assertIsNone(self.owner_seen_by(""))

    def test_no_owner_without_previous_parts(self):
        theirs = self.project_file("theirs.llproj")
        for part in self.split(theirs).parts:
            os.remove(part.original_path)
        self.assertIsNone(self.owner_seen_by(""))

    def test_meta_records_the_source_and_the_project_as_absolute_paths(self):
        # meta 會被別的 session 讀，相對路徑離開當下的工作目錄就沒有意義
        self.split("a.llproj")
        meta = self.workspace.read_meta(self.splitter.output_folder(self.source))
        self.assertEqual(meta["source_name"], "合併譜.pdf")
        self.assertEqual(meta["source_path"], os.path.abspath(self.source))
        self.assertEqual(meta["project_path"], os.path.join(os.getcwd(), "a.llproj"))
        self.assertIn("created_at", meta)


class TestSplitIntoChosenFolder(SplitServiceTestCase):
    """指定資料夾：已有同名檔案時需確認覆蓋，被覆蓋的檔移到資源回收桶並算成被取代的路徑"""

    def setUp(self):
        super().setUp()
        self.folder = os.path.join(self.temp_dir, "out")

    def split(self, *segments):
        return self.splitter.execute(self.splitter.check(self.request(*segments, folder=self.folder)))

    def test_new_folder_is_created_and_recorded(self):
        result = self.split((0, 1, "Flute"), (2, 3, "Oboe"))
        flute = os.path.join(self.folder, "Flute.pdf")
        self.assertEqual(result.parts[0], FileInfo(flute, "Flute.pdf"))
        self.assertEqual(_page_numbers(flute), [0, 1])
        self.assertEqual(result.created_directories, [self.folder])
        self.assertEqual(result.replaced, [])
        self.assertEqual(sorted(os.listdir(self.folder)), ["Flute.pdf", "Oboe.pdf"])

    def test_existing_file_with_the_same_name_needs_confirming_and_goes_to_the_recycle_bin(self):
        os.makedirs(self.folder)
        flute, notes = os.path.join(self.folder, "Flute.pdf"), os.path.join(self.folder, "notes.pdf")
        for path in (flute, notes):
            with open(path, "w") as f:
                f.write("mine")
        check = self.splitter.check(self.request((0, 1, "Flute"), (2, 3, "Oboe"), folder=self.folder))
        self.assertEqual((check.overwritten, check.previous_outputs, check.owner), ([flute], [], None))
        result = self.splitter.execute(check)
        self.assertEqual(result.replaced, [flute])
        self.assertEqual([os.path.basename(p) for p in self.file_service.trashed], ["Flute.pdf"])
        self.assertEqual(_page_numbers(flute), [0, 1])
        self.assertEqual(result.created_directories, [])
        self.assertEqual(sorted(os.listdir(self.folder)), ["Flute.pdf", "Oboe.pdf", "notes.pdf"])

    def test_resplit_into_the_same_folder_reports_the_previous_parts_as_replaced(self):
        first = self.split((0, 1, "Flute"), (2, 3, "Oboe"))
        second = self.split((0, 0, "Flute"), (1, 3, "Oboe"))
        self.assertEqual(second.replaced, [p.original_path for p in first.parts])
        self.assertEqual(second.parts, first.parts)

    def test_part_that_would_overwrite_the_source_is_blocked(self):
        before = _snapshot(self.scores)
        check = self.splitter.check(self.request((0, 1, "Flute"), (2, 3, " 合併譜 "), folder=self.scores))
        self.assertTrue(check.blocked)
        self.assertEqual(check.source_conflicts, ["合併譜.pdf"])
        with self.assertRaises(ValueError):
            self.splitter.execute(check)
        self.assertEqual(_snapshot(self.scores), before)


class TestWorkspaceFolder(SplitServiceTestCase):
    """每份合併譜對應工作區內一個子資料夾，以來源路徑為鍵"""

    def test_folder_name_is_a_fixed_length_hash_inside_the_workspace(self):
        folder = self.splitter.output_folder(self.source)
        self.assertEqual(os.path.dirname(folder), self.workspace.workspace_dir)
        self.assertEqual(len(os.path.basename(folder)), WORKSPACE_FOLDER_HASH_LENGTH)
        self.assertEqual(self.splitter.output_folder(self.source), folder)

    def test_different_sources_get_different_folders_even_with_the_same_file_name(self):
        os.makedirs(self.path("another"))
        twin = _make_pdf(self.path("another", "合併譜.pdf"), 1)
        other = _make_pdf(self.path("other.pdf"), 1)
        folders = {self.splitter.output_folder(p) for p in (self.source, twin, other)}
        self.assertEqual(len(folders), 3)

    def test_every_spelling_of_a_source_maps_to_the_same_folder(self):
        source = os.path.join(os.getcwd(), "scores", "Winter Score.pdf")  # 只比對路徑，不讀寫
        folder = self.splitter.output_folder(source)
        for label, spelled in spellings(source).items():
            with self.subTest(label):
                self.assertEqual(self.splitter.output_folder(spelled), folder)

    def test_folder_split_before_the_upgrade_is_found_by_its_recorded_source(self):
        # 升級前的雜湊保留大小寫：Linux／macOS 上來源路徑有大寫字母時，舊子資料夾的名稱和現在算出的不同
        legacy = os.path.join(self.workspace.workspace_dir, "1e9ac7d0")
        self.workspace.write_meta(legacy, self.source, "")
        old_part = os.path.join(legacy, "Flute.pdf")
        _make_pdf(old_part, 1)
        self.assertEqual(self.splitter.output_folder(self.source), legacy)
        check = self.splitter.check(self.request((0, 1, "Flute"), (2, 3, "Oboe")))
        self.assertEqual(check.previous_outputs, [old_part])
        result = self.splitter.execute(check)
        self.assertEqual(result.replaced, [old_part])
        self.assertEqual([p.original_path for p in result.parts], [old_part, os.path.join(legacy, "Oboe.pdf")])
        self.assertEqual(_page_numbers(old_part), [0, 1])
        self.assertEqual(os.listdir(self.workspace.workspace_dir), ["1e9ac7d0"])


class TestFailedFirstSplit(SplitServiceTestCase):
    """第一次分割就失敗：不留下工作區子資料夾"""

    def test_nothing_is_left_in_the_workspace(self):
        splitter = SplitService(self.file_service, self.workspace, extract=_failing_on_call(2))
        with self.assertRaises(OSError):
            splitter.execute(splitter.check(self.request((0, 1, "Flute"), (2, 3, "Oboe"))))
        self.assertFalse(os.path.exists(self.splitter.output_folder(self.source)))


if __name__ == '__main__':
    unittest.main()
