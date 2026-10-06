# -*- coding: utf-8 -*-
"""
專案模型測試

純 Python，不需要 Qt：檔案走訪、依路徑套用結果、編輯操作與未存檔判定。
只有「欄位清單從存檔結果列舉」的那條測試會寫一次暫存專案檔。
"""
import dataclasses
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core.models import FileInfo, Group, Project, UndoMapping
from services.project_service import ProjectService
from path_spellings import spellings


def _path(*parts):
    """目前工作目錄之下的絕對路徑（不需實際存在）"""
    return os.path.join(os.getcwd(), "scores", *parts)


def _info(path):
    return FileInfo(path, os.path.basename(path))


class TestFileRefs(unittest.TestCase):
    """分譜＋總譜＋未分組的唯一走訪"""

    def test_lists_each_group_score_then_parts_then_ungrouped(self):
        first = Group(name="1", files=[_info(_path("Flute.pdf")), _info(_path("Oboe.pdf"))],
                      score_file=_info(_path("Score.pdf")))
        second = Group(name="2", files=[_info(_path("Horn.pdf"))])
        project = Project(groups=[first, second], ungrouped_files=[_info(_path("Loose.pdf"))])
        self.assertEqual(
            [(ref.group.name if ref.group else None, ref.file.display_name) for ref in project.file_refs()],
            [("1", "Score.pdf"), ("1", "Flute.pdf"), ("1", "Oboe.pdf"), ("2", "Horn.pdf"), (None, "Loose.pdf")],
        )


class TestRemovePaths(unittest.TestCase):
    """依路徑移除引用：同一條路徑的任何寫法都移除"""

    def test_removes_parts_score_and_ungrouped_written_in_any_spelling(self):
        part, score, loose, kept = _path("Flute.pdf"), _path("Score.pdf"), _path("Loose.pdf"), _path("Oboe.pdf")
        for label in spellings(part):
            with self.subTest(label):
                project = Project(
                    groups=[Group(files=[_info(part), _info(kept)], score_file=_info(score))],
                    ungrouped_files=[_info(loose)],
                )
                project.remove_paths([spellings(p)[label] for p in (part, score, loose)])
                self.assertEqual(project.all_file_paths(), [kept])
                self.assertIsNone(project.groups[0].score_file)


class TestReplacePaths(unittest.TestCase):
    """依路徑取代：重新命名、復原、重做的結果寫回專案，同一條路徑的任何寫法都跟著換"""

    def test_follows_moves_written_in_any_spelling(self):
        part, score, loose = _path("Flute.pdf"), _path("Score.pdf"), _path("Loose.pdf")
        moved = {part: _path("out", "01. Flute.pdf"), score: _path("out", "00. Score.pdf"),
                 loose: _path("out", "Loose.pdf")}
        for label in spellings(part):
            with self.subTest(label):
                project = Project(
                    groups=[Group(files=[_info(part)], score_file=_info(score))],
                    ungrouped_files=[_info(loose)],
                )
                project.replace_paths([UndoMapping(spellings(old)[label], new) for old, new in moved.items()])
                self.assertCountEqual(project.all_file_paths(), list(moved.values()))
                self.assertEqual(project.groups[0].files[0].display_name, "01. Flute.pdf")
                self.assertEqual(project.groups[0].score_file.display_name, "00. Score.pdf")


def _rich_group(name: str, parts: list) -> Group:
    """每個會存進專案檔的欄位都有非空值的群組"""
    return Group(
        name=name, files=[_info(_path(name, f"{p}.pdf")) for p in parts],
        instruments=list(parts), selected_instruments=list(range(len(parts))),
        piece_name="命運", movement_number="1", movement_name="Allegro", composer="Beethoven",
        genre="交響曲", score_file=_info(_path(name, "Score.pdf")), score_label="總譜",
        use_small_template=True, small_template="{樂器}.pdf",
    )


def _rich_project() -> Project:
    """每個會存進專案檔的欄位都有非空值的專案"""
    return Project(
        instruments=["Flute", "Oboe"], master_template="{序號}-{樂器}.pdf",
        groups=[_rich_group("1", ["Flute", "Oboe"]), _rich_group("2", ["Horn"])],
        ungrouped_files=[_info(_path("Loose.pdf")), _info(_path("Extra.pdf"))],
        use_subfolders=True, subfolder_template="{曲名}", use_parts_subfolder=True,
        parts_subfolder_name="Parts", parts_output_mode="parts", output_directory=_path("out"),
        instrument_headcounts={"Flute": 2}, instrument_sections={"Flute": "木管"},
    )


def _saved_fields(owner, data: dict, label: str):
    """依專案檔內容列出（欄位名稱、所屬物件、屬性名），遇到檔案資訊或群組就往下列

    Args:
        owner: 這層資料對應的模型物件（專案、群組或檔案資訊）
        data: 專案檔中這層的內容
        label: 這層的顯示名稱
    """
    for key, value in data.items():
        yield f"{label}.{key}", owner, key
        model = getattr(owner, key)
        if dataclasses.is_dataclass(model):
            yield from _saved_fields(model, value, f"{label}.{key}")
        elif isinstance(model, list) and model and dataclasses.is_dataclass(model[0]):
            for i, (item, item_data) in enumerate(zip(model, value)):
                yield from _saved_fields(item, item_data, f"{label}.{key}[{i}]")


def _changed(value):
    """與原值不同的新值（不改動原值本身）"""
    if isinstance(value, bool):
        return not value
    if isinstance(value, str):
        return value + "x"
    if isinstance(value, list):
        return value[:-1]
    if isinstance(value, dict):
        return {**value, "x": 0}
    if dataclasses.is_dataclass(value):
        return None
    raise AssertionError(f"沒有為 {type(value).__name__} 準備改值方式")


class TestUnsavedIsDifferenceFromSavedContent(unittest.TestCase):
    """未存檔＝專案目前的內容（專案檔會存的內容）與上次存檔或開啟時不同"""

    def test_every_saved_field_is_unsaved_when_changed_and_saved_when_changed_back(self):
        """欄位清單從存檔結果列舉，日後新增的欄位自動涵蓋"""
        project = _rich_project()
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, True)
        path = os.path.join(temp_dir, "p.llproj")
        ProjectService().save_project(project, path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        del data["version"]
        fields = list(_saved_fields(project, data, "專案"))
        self.assertGreater(len(fields), 30)
        self.assertFalse(project.is_modified())
        for label, owner, name in fields:
            with self.subTest(label):
                original = getattr(owner, name)
                setattr(owner, name, _changed(original))
                self.assertTrue(project.is_modified())
                setattr(owner, name, original)
                self.assertFalse(project.is_modified())

    def test_check_takes_under_ten_milliseconds_for_two_hundred_groups_of_thirty_parts(self):
        groups = [_rich_group(f"g{i}", [f"Part {n}" for n in range(30)]) for i in range(200)]
        project = Project(groups=groups)
        project.groups[-1].files[-1].display_name = "changed.pdf"
        timings = []
        for _ in range(5):
            start = time.perf_counter()
            modified = project.is_modified()
            timings.append(time.perf_counter() - start)
        self.assertTrue(modified)
        self.assertLess(sorted(timings)[len(timings) // 2], 0.010)


class TestOpenedProjectIsSaved(unittest.TestCase):
    """由專案檔內容還原的專案判為已存檔；舊格式的遷移在拍快照之前完成"""

    def test_blank_score_labels_of_old_projects_are_filled_without_marking_unsaved(self):
        data = _rich_project().to_data()
        data["groups"][0]["score_label"] = ""
        project = Project.from_data(data, score_label="Full Score")
        self.assertEqual([g.score_label for g in project.groups], ["Full Score", "總譜"])
        self.assertFalse(project.is_modified())

    def test_old_selection_and_parts_folder_flag_are_migrated_without_marking_unsaved(self):
        data = {
            "instruments": ["Flute", "Oboe", "Horn"], "use_parts_subfolder": True,
            "groups": [{"name": "g", "score_label": "總譜", "selected_instruments": [0, 2]}],
        }
        project = Project.from_data(data, score_label="總譜")
        self.assertEqual(project.groups[0].instruments, ["Flute", "Horn"])
        self.assertEqual(project.groups[0].selected_instruments, [0, 1])
        self.assertEqual(project.parts_output_mode, "parts")
        self.assertFalse(project.is_modified())

    def test_round_trip_keeps_every_saved_field(self):
        project = _rich_project()
        self.assertEqual(Project.from_data(project.to_data(), score_label="").to_data(), project.to_data())


if __name__ == '__main__':
    unittest.main()
