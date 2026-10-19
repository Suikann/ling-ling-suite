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
import subprocess
import sys
import tempfile
import time
import unittest
from enum import Enum

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core.constants import PROJECT_FILE_VERSION, HuaXiaEnsemble, OrchestraType
from core.models import (
    CatalogLink, CatalogRevert, CatalogSnapshot, DuplicateVoiceError, FileInfo, Group, Headcount, HuaXiaLink, NewHuaXiaPiece, Project, SplitPlacement, SplitResult, UndoMapping,
    format_duration, sum_headcounts,
)
from services.project_service import ProjectService
from path_spellings import spellings


def _path(*parts):
    """目前工作目錄之下的絕對路徑（不需實際存在）"""
    return os.path.join(os.getcwd(), "scores", *parts)


def _info(path):
    return FileInfo(path, os.path.basename(path))


class TestProjectModelIsPurePython(unittest.TestCase):
    """專案模型不依賴 Qt：只匯入 core.models 的程式不會載入 PySide6"""

    def test_importing_the_model_does_not_load_qt(self):
        src = os.path.join(os.path.dirname(__file__), '..', 'src')
        code = f"import sys; sys.path.insert(0, {src!r}); import core.models; print('PySide6' in sys.modules)"
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "False")


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


def _infos(*names):
    return [_info(_path(n)) for n in names]


def _rich_group(name: str, parts: list) -> Group:
    """每個會存進專案檔的欄位都有非空值的群組"""
    return Group(
        name=name, files=[_info(_path(name, f"{p}.pdf")) for p in parts],
        instruments=list(parts), headcounts=[Headcount(2, 3)] * len(parts), duration_seconds=465,
        selected_instruments=list(range(len(parts))),
        piece_name="命運", movement_number="1", movement_name="Allegro", composer="Beethoven",
        genre="交響曲", score_file=_info(_path(name, "Score.pdf")), score_label="總譜",
        use_small_template=True, small_template="{樂器}.pdf",
        catalog_link=CatalogLink(
            edition_id="e1", movement_id="m1", piece_title="命運", edition_label="Bärenreiter", movement_name="Allegro",
            snapshot=CatalogSnapshot([(p, Headcount(2)) for p in parts], 470, {parts[0]: "木管"}, [parts[0]]),
            other_pieces={parts[0]: 3},
            reverts=[CatalogRevert("e0", "m0", [parts[0]], [(parts[0], Headcount(3), Headcount(2))])],
        ),
        huaxia_link=HuaXiaLink(
            piece_id="hx-1", piece_title="命運", new_piece=NewHuaXiaPiece("命運", "Beethoven", 7.5, HuaXiaEnsemble.SIZHU),
            part_ids={parts[0]: "part-1"},
        ),
    )


def _rich_project() -> Project:
    """每個會存進專案檔的欄位都有非空值的專案"""
    return Project(
        instruments=["Flute", "Oboe"], master_template="{序號}-{樂器}.pdf",
        groups=[_rich_group("1", ["Flute", "Oboe"]), _rich_group("2", ["Horn"])],
        ungrouped_files=[_info(_path("Loose.pdf")), _info(_path("Extra.pdf"))],
        use_subfolders=True, subfolder_template="{曲名}", use_parts_subfolder=True,
        parts_subfolder_name="Parts", parts_output_mode="parts", output_directory=_path("out"),
        instrument_sections={"Flute": "木管"},
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
    """與原值不同的新值（不改動原值本身）；資料類別能以預設值建立時換成預設值（必填的欄位），否則換成 None"""
    if isinstance(value, bool):
        return not value
    if isinstance(value, Enum):
        return next(member for member in type(value) if member is not value)
    if isinstance(value, int):
        return value + 1
    if isinstance(value, str):
        return value + "x"
    if isinstance(value, list):
        return value[:-1]
    if isinstance(value, dict):
        return {**value, "x": 0}
    if isinstance(value, tuple) and hasattr(value, "_fields"):
        return None
    if dataclasses.is_dataclass(value):
        try:
            return type(value)()
        except TypeError:
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
        data = {"version": PROJECT_FILE_VERSION, **project.to_data()}
        self.assertEqual(Project.from_data(data, score_label="").to_data(), project.to_data())


class TestMigrationOnlyForOldProjectFiles(unittest.TestCase):
    """舊格式的遷移只在開啟舊版程式寫的專案檔時做一次；存檔後再開啟，使用者清掉的值不會被補回"""

    def setUp(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, True)
        self.path = os.path.join(temp_dir, "p.llproj")
        self.service = ProjectService()

    def _reopen(self, project: Project) -> Project:
        self.service.save_project(project, self.path)
        return self.service.load_project(self.path)

    def _write_old_file(self, data: dict) -> None:
        """照舊版程式的寫法（version 是當時的應用程式版本）寫一份專案檔"""
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"version": "1.1.0-alpha", **data}, f)

    def test_cleared_score_label_stays_cleared(self):
        project = Project(groups=[Group(name="g", score_label="總譜")])
        project.update_group(project.groups[0], score_label="")
        self.assertEqual(self._reopen(project).groups[0].score_label, "")

    def test_cleared_voice_list_stays_cleared_even_with_an_old_project_level_list(self):
        project = Project(instruments=["Flute", "Oboe"], groups=[Group(name="g", instruments=["Flute", "Oboe"])])
        project.set_instruments(project.groups[0], [])
        self.assertEqual(self._reopen(project).groups[0].instruments, [])

    def test_voices_not_in_this_project_stay_unchecked_after_reopening(self):
        group = Group(name="g", instruments=["Flute", "Oboe", "Horn"])
        project = Project(groups=[group])
        project.set_present(group, 1, False)
        reopened = self._reopen(project)
        self.assertEqual(reopened.groups[0].present, [True, False, True])
        self.assertFalse(reopened.is_modified())

    def test_every_voice_of_an_older_file_is_in_this_project(self):
        self._write_old_file({"groups": [{"name": "a", "score_label": "總譜", "instruments": ["Flute", "Oboe"]}]})
        self.assertEqual(self.service.load_project(self.path).groups[0].present, [True, True])

    def test_file_written_by_the_old_version_still_migrates(self):
        self._write_old_file({
            "instruments": ["Flute", "Oboe", "Horn"],
            "groups": [
                {"name": "a", "score_label": "", "selected_instruments": [0, 2]},
                {"name": "b", "score_label": "Score", "instruments": ["Tuba"]},
            ],
        })
        project = self.service.load_project(self.path)
        self.assertEqual([g.score_label for g in project.groups], ["總譜", "Score"])
        self.assertEqual([g.instruments for g in project.groups], [["Flute", "Horn"], ["Tuba"]])
        self.assertFalse(project.is_modified())

    def test_old_file_matches_itself_after_migration(self):
        self._write_old_file({"groups": [{"name": "a", "score_label": ""}]})
        project = self.service.load_project(self.path)
        self.assertTrue(self.service.matches_file(project, self.path))


class TestChangedNotification(unittest.TestCase):
    """專案只有一個「已變更」通知；訂閱者收到時看得到最新的未存檔狀態"""

    def test_subscribers_hear_edits_and_saving(self):
        project = Project()
        original = project.master_template
        heard = []
        project.subscribe(lambda: heard.append(project.is_modified()))
        project.set_master_template("{樂器}.pdf")
        project.set_master_template(original)
        project.set_master_template("{曲名}.pdf")
        project.mark_saved()
        self.assertEqual(heard, [True, False, True, False])


class TestAutoDetectWhenFilesEnterGroup(unittest.TestCase):
    """總譜與曲名只在檔案進入群組時猜一次；群組已有總譜或曲名時不覆蓋，開啟專案時不猜"""

    PARTS = ("Brahms Symphony - Flute.pdf", "Brahms Symphony - Oboe.pdf")

    def _files(self):
        return _infos("Full Score.pdf", *self.PARTS)

    def assert_detected(self, group: Group):
        self.assertEqual(group.score_file.display_name, "Full Score.pdf")
        self.assertEqual([f.display_name for f in group.files], list(self.PARTS))
        self.assertEqual(group.piece_name, "Brahms Symphony")

    def test_importing_a_folder_as_groups(self):
        project = Project()
        project.add_groups([Group(name="Brahms", files=self._files())], score_label="總譜")
        self.assert_detected(project.groups[0])

    def test_creating_a_group_from_selected_ungrouped_files(self):
        files = self._files()
        project = Project(ungrouped_files=list(files))
        group = project.add_group("g", score_label="總譜", files=files)
        self.assert_detected(group)
        self.assertEqual(project.ungrouped_files, [])

    def test_moving_files_into_a_group(self):
        files = self._files()
        project = Project(ungrouped_files=list(files))
        group = project.add_group("g", score_label="總譜")
        project.move_to_group(files, group)
        self.assert_detected(group)
        self.assertEqual(project.ungrouped_files, [])

    def test_adding_files_to_a_group(self):
        project = Project()
        group = project.add_group("g", score_label="總譜")
        project.add_files(self._files(), group)
        self.assert_detected(group)

    def test_split_result_entering_a_group(self):
        project = Project()
        result = SplitResult(_path("合併譜.pdf"), self._files(), ["Score", "Flute", "Oboe"], replaced=[])
        project.apply_split(result, score_label="總譜")
        self.assert_detected(project.groups[0])

    def test_existing_score_and_piece_name_are_kept(self):
        score = _info(_path("Conductor.pdf"))
        project = Project(groups=[Group(name="g", score_file=score, piece_name="自訂曲名")])
        project.add_files(self._files(), project.groups[0])
        group = project.groups[0]
        self.assertIs(group.score_file, score)
        self.assertEqual(group.piece_name, "自訂曲名")
        self.assertEqual(len(group.files), 3)

    def test_opening_a_project_does_not_guess(self):
        data = Project(groups=[Group(name="g", files=self._files())]).to_data()
        group = Project.from_data(data, score_label="總譜").groups[0]
        self.assertIsNone(group.score_file)
        self.assertEqual(group.piece_name, "")

    def test_no_common_name_leaves_the_piece_name_empty_rather_than_the_group_name(self):
        project = Project()
        project.add_groups([Group(name="Brahms", files=_infos("Flute.pdf", "Oboe.pdf"))], score_label="總譜")
        self.assertEqual(project.groups[0].piece_name, "")

    def test_files_added_to_ungrouped_are_left_alone(self):
        project = Project()
        project.add_files(self._files())
        self.assertEqual(len(project.ungrouped_files), 3)

    def test_guessing_the_piece_name_on_request_replaces_it(self):
        project = Project(groups=[Group(name="g", piece_name="暫名", files=_infos(*self.PARTS))])
        self.assertEqual(project.guess_piece_name(project.groups[0]), "Brahms Symphony")
        self.assertEqual(project.groups[0].piece_name, "Brahms Symphony")

    def test_guessing_without_a_common_name_keeps_the_piece_name(self):
        project = Project(groups=[Group(name="g", piece_name="暫名", files=_infos("123.pdf", "456.pdf"))])
        self.assertEqual(project.guess_piece_name(project.groups[0]), "")
        self.assertEqual(project.groups[0].piece_name, "暫名")


class TestGroupEdits(unittest.TestCase):
    """群組與檔案的編輯操作：每次都發出「已變更」通知，並讓專案判為未存檔"""

    def setUp(self):
        self.parts = _infos("Flute.pdf", "Oboe.pdf", "Horn.pdf")
        self.score = _info(_path("Score.pdf"))
        self.group = Group(name="g", files=list(self.parts), score_file=self.score, score_label="總譜")
        self.project = Project(groups=[self.group], ungrouped_files=_infos("Loose.pdf"))
        self.heard = []
        self.project.subscribe(lambda: self.heard.append(self.project.is_modified()))

    def assert_changed(self):
        self.assertEqual(self.heard, [True])

    def names(self, files):
        return [f.display_name for f in files]

    def test_updating_group_fields(self):
        self.project.update_group(self.group, piece_name="命運", use_small_template=True)
        self.assertEqual((self.group.piece_name, self.group.use_small_template), ("命運", True))
        self.assert_changed()

    def test_updating_an_unknown_group_field_is_refused(self):
        with self.assertRaises(ValueError):
            self.project.update_group(self.group, files=[])

    def test_group_outside_any_project_follows_the_same_editing_rules(self):
        group = Group(name="drive")
        group.update(composer="Holst")
        self.assertEqual(group.composer, "Holst")
        with self.assertRaises(ValueError):
            group.update(instruments=["Flute"])

    def test_deleting_a_group_moves_its_score_and_parts_to_ungrouped(self):
        self.project.delete_group(self.group)
        self.assertEqual(self.project.groups, [])
        self.assertEqual(
            self.names(self.project.ungrouped_files), ["Loose.pdf", "Score.pdf", "Flute.pdf", "Oboe.pdf", "Horn.pdf"],
        )
        self.assert_changed()

    def test_moving_parts_back_to_ungrouped(self):
        self.project.move_to_ungrouped([self.parts[0], self.parts[2]])
        self.assertEqual(self.names(self.group.files), ["Oboe.pdf"])
        self.assertEqual(self.names(self.project.ungrouped_files), ["Loose.pdf", "Flute.pdf", "Horn.pdf"])
        self.assert_changed()

    def test_setting_the_score_puts_the_previous_score_back_among_parts(self):
        self.project.set_score(self.group, self.parts[1])
        self.assertIs(self.group.score_file, self.parts[1])
        self.assertEqual(self.names(self.group.files), ["Flute.pdf", "Horn.pdf", "Score.pdf"])
        self.assert_changed()

    def test_clearing_the_score_puts_it_back_among_parts(self):
        self.project.clear_score(self.group)
        self.assertIsNone(self.group.score_file)
        self.assertEqual(self.names(self.group.files), ["Flute.pdf", "Oboe.pdf", "Horn.pdf", "Score.pdf"])
        self.assert_changed()

    def test_reordering_parts(self):
        self.project.reorder_files(self.group, [self.parts[2], self.parts[0], self.parts[1]])
        self.assertEqual(self.names(self.group.files), ["Horn.pdf", "Flute.pdf", "Oboe.pdf"])
        self.assert_changed()

    def test_reordering_with_different_files_is_refused(self):
        with self.assertRaises(ValueError):
            self.project.reorder_files(self.group, self.parts[:2])

    def test_setting_instruments_keeps_the_old_all_selected_mirror(self):
        self.project.set_instruments(self.group, ["Flute", "Oboe"])
        self.assertEqual((self.group.instruments, self.group.selected_instruments), (["Flute", "Oboe"], [0, 1]))
        self.assert_changed()

    def test_linking_groups_as_movements(self):
        second = Group(name="Adagio", movement_name="")
        third = Group(name="Finale", movement_name="Allegro")
        self.project.groups.extend([second, third])
        self.project.link_movements([second, third], "命運")
        self.assertEqual(
            [(g.piece_name, g.movement_number, g.movement_name) for g in (second, third)],
            [("命運", "1", "Adagio"), ("命運", "2", "Allegro")],
        )
        self.assert_changed()


class TestInstrumentListWithoutDuplicateVoices(unittest.TestCase):
    """樂器表新增或改名：經「同一聲部」規則判為相同的兩列算重名，擋下且樂器表不變"""

    def setUp(self):
        self.group = Group(name="g", instruments=["二胡 I", "二胡 II", "Cello"])
        self.project = Project(groups=[self.group])
        self.heard = []
        self.project.subscribe(lambda: self.heard.append(self.project.is_modified()))

    def test_adding_a_new_voice_appends_it(self):
        self.project.add_instrument(self.group, "二胡 III")
        self.assertEqual(self.group.instruments, ["二胡 I", "二胡 II", "Cello", "二胡 III"])
        self.assertEqual(self.heard, [True])

    def test_adding_another_spelling_of_an_existing_voice_is_refused(self):
        with self.assertRaises(DuplicateVoiceError) as refused:
            self.project.add_instrument(self.group, "二胡1")
        self.assertEqual(refused.exception.existing, "二胡 I")
        self.assertEqual(self.group.instruments, ["二胡 I", "二胡 II", "Cello"])
        self.assertEqual(self.heard, [])

    def test_renaming_a_voice_replaces_it_in_place(self):
        self.project.rename_instrument(self.group, 2, "大提琴")
        self.assertEqual(self.group.instruments, ["二胡 I", "二胡 II", "大提琴"])
        self.assertEqual(self.heard, [True])

    def test_renaming_to_another_spelling_of_itself_is_allowed(self):
        self.project.rename_instrument(self.group, 0, "二胡1")
        self.assertEqual(self.group.instruments, ["二胡1", "二胡 II", "Cello"])

    def test_renaming_to_another_row_s_voice_is_refused(self):
        with self.assertRaises(DuplicateVoiceError) as refused:
            self.project.rename_instrument(self.group, 0, "二胡Ⅱ")
        self.assertEqual(refused.exception.existing, "二胡 II")
        self.assertEqual(self.group.instruments, ["二胡 I", "二胡 II", "Cello"])
        self.assertEqual(self.heard, [])


class TestDuplicateVoicesOfOldProjects(unittest.TestCase):
    """舊樂器表裡的重名聲部原樣保留：開檔、移動、取消勾選與改其他列都照常，不自動改名"""

    def setUp(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, temp_dir, True)
        path = os.path.join(temp_dir, "old.llproj")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"version": 2, "groups": [
                {"name": "g", "score_label": "總譜", "instruments": ["二胡 I", "二胡1", "中胡"]},
            ]}, f)
        self.project = ProjectService().load_project(path)
        self.group = self.project.groups[0]

    def test_opening_keeps_both_spellings(self):
        self.assertEqual(self.group.instruments, ["二胡 I", "二胡1", "中胡"])
        self.assertFalse(self.project.is_modified())

    def test_editing_other_rows_keeps_them(self):
        self.project.move_instruments(self.group, [2], 0)
        self.project.set_present(self.group, 1, False)
        self.project.rename_instrument(self.group, 0, "中胡 I")
        self.assertEqual(self.group.instruments, ["中胡 I", "二胡 I", "二胡1"])

    def test_renaming_into_another_duplicate_is_still_refused(self):
        with self.assertRaises(DuplicateVoiceError):
            self.project.rename_instrument(self.group, 2, "二胡一")


class TestVoicesCarryTheirParts(unittest.TestCase):
    """第 N 份分譜配本專案有的第 N 個聲部；拖聲部時分譜跟著走，單獨拖分譜只改配對，改名時分譜留在原位"""

    def setUp(self):
        self.group = Group(
            name="g", instruments=["Flute", "Oboe", "Horn"], files=_infos("fl.pdf", "ob.pdf", "hn.pdf"),
        )
        self.project = Project(groups=[self.group], ungrouped_files=_infos("Loose.pdf"))
        self.heard = []
        self.project.subscribe(lambda: self.heard.append(self.project.is_modified()))

    def pairs(self):
        """（聲部、配到的分譜檔名；沒有分譜時為 None），依本專案有的聲部的順序"""
        return [(p.voice, p.file.display_name if p.file else None) for p in self.group.pairing()]

    def test_moving_a_voice_takes_its_part_along(self):
        self.project.move_instruments(self.group, [2], 0)
        self.assertEqual(self.pairs(), [("Horn", "hn.pdf"), ("Flute", "fl.pdf"), ("Oboe", "ob.pdf")])
        self.assertEqual(self.heard, [True])

    def test_moving_several_voices_down_keeps_their_order_and_parts(self):
        self.project.move_instruments(self.group, [0, 1], 3)
        self.assertEqual(self.pairs(), [("Horn", "hn.pdf"), ("Flute", "fl.pdf"), ("Oboe", "ob.pdf")])

    def test_a_voice_without_a_part_moved_before_parts_stays_without_a_part(self):
        """沒有分譜的聲部插進中間，仍是本專案有、沒有分譜，後面的分譜不會錯位"""
        self.project.add_instrument(self.group, "Tuba")
        self.project.move_instruments(self.group, [3], 1)
        self.assertEqual(self.group.instruments, ["Flute", "Tuba", "Oboe", "Horn"])
        self.assertEqual(self.group.present, [True, True, True, True])
        self.assertEqual(
            self.pairs(), [("Flute", "fl.pdf"), ("Tuba", None), ("Oboe", "ob.pdf"), ("Horn", "hn.pdf")],
        )

    def test_voices_without_parts_stay_in_this_project_when_moved_among_themselves(self):
        self.project.add_instrument(self.group, "Tuba")
        self.project.add_instrument(self.group, "Harp")
        self.project.move_instruments(self.group, [4], 3)
        self.assertEqual(self.pairs()[3:], [("Harp", None), ("Tuba", None)])

    def test_dragging_a_part_alone_only_changes_the_pairing(self):
        fl, ob, hn = self.group.files
        self.project.reorder_files(self.group, [ob, fl, hn])
        self.assertEqual(self.group.instruments, ["Flute", "Oboe", "Horn"])
        self.assertEqual(self.pairs(), [("Flute", "ob.pdf"), ("Oboe", "fl.pdf"), ("Horn", "hn.pdf")])

    def test_renaming_a_voice_keeps_its_part(self):
        self.project.rename_instrument(self.group, 1, "Oboe d'amore")
        self.assertEqual(self.pairs()[1], ("Oboe d'amore", "ob.pdf"))

    def test_unchecking_a_voice_moves_its_part_to_ungrouped_and_reports_it(self):
        moved = self.project.set_present(self.group, 1, False)
        self.assertEqual([f.display_name for f in moved], ["ob.pdf"])
        self.assertEqual(self.pairs(), [("Flute", "fl.pdf"), ("Horn", "hn.pdf")])
        self.assertEqual([f.display_name for f in self.project.ungrouped_files], ["Loose.pdf", "ob.pdf"])
        self.assertEqual(self.heard, [True])

    def test_unchecking_a_voice_without_a_part_moves_nothing(self):
        self.project.add_instrument(self.group, "Tuba")
        self.assertEqual(self.project.set_present(self.group, 3, False), [])
        self.assertEqual(self.group.present, [True, True, True, False])
        self.assertEqual(len(self.project.ungrouped_files), 1)

    def test_rechecking_a_voice_leaves_it_without_a_part_and_the_others_keep_theirs(self):
        """重新勾選的聲部沒有分譜，不會接走後面聲部的分譜"""
        self.project.set_present(self.group, 1, False)
        self.assertEqual(self.project.set_present(self.group, 1, True), [])
        self.assertEqual(self.pairs(), [("Flute", "fl.pdf"), ("Oboe", None), ("Horn", "hn.pdf")])
        self.assertEqual(self.group.present, [True, True, True])

    def test_a_rechecked_voice_does_not_take_a_part_beyond_the_voices(self):
        self.project.add_files(_infos("extra.pdf"), self.group)
        self.project.set_present(self.group, 2, False)
        self.project.set_present(self.group, 2, True)
        self.assertEqual(self.pairs(), [("Flute", "fl.pdf"), ("Oboe", "ob.pdf"), ("Horn", None)])
        self.assertEqual([f.display_name for f in self.group.extra_files()], ["extra.pdf"])

    def test_moving_and_removing_other_voices_keep_a_voice_without_a_part_empty(self):
        self.project.set_present(self.group, 0, False)
        self.project.set_present(self.group, 0, True)
        self.project.move_instruments(self.group, [2], 0)
        self.project.remove_instruments(self.group, [2])
        self.project.rename_instrument(self.group, 1, "Piccolo")
        self.assertEqual(self.pairs(), [("Horn", "hn.pdf"), ("Piccolo", None)])

    def test_a_part_added_to_the_group_goes_to_a_voice_without_a_part(self):
        self.project.set_present(self.group, 1, False)
        self.project.set_present(self.group, 1, True)
        self.project.add_files(_infos("new.pdf"), self.group)
        self.assertEqual(self.pairs(), [("Flute", "fl.pdf"), ("Oboe", "new.pdf"), ("Horn", "hn.pdf")])
        self.project.move_to_group([self.project.ungrouped_files[0]], self.group)
        self.assertEqual([f.display_name for f in self.group.extra_files()], ["Loose.pdf"])

    def test_a_part_taken_out_of_the_group_leaves_its_voice_without_a_part(self):
        self.project.move_to_ungrouped([self.group.files[0]])
        self.assertEqual(self.pairs(), [("Flute", None), ("Oboe", "ob.pdf"), ("Horn", "hn.pdf")])
        self.project.set_score(self.group, self.group.files[0])
        self.assertEqual(self.pairs(), [("Flute", None), ("Oboe", None), ("Horn", "hn.pdf")])

    def test_clearing_the_score_does_not_move_any_voice_s_part(self):
        self.group.score_file = _info(_path("score.pdf"))
        self.project.clear_score(self.group)
        self.assertEqual(self.pairs(), [("Flute", "fl.pdf"), ("Oboe", "ob.pdf"), ("Horn", "hn.pdf")])
        self.assertEqual([f.display_name for f in self.group.extra_files()], ["score.pdf"])

    def test_a_cleared_score_goes_to_a_voice_without_a_part(self):
        self.project.set_score(self.group, self.group.files[1])
        self.project.clear_score(self.group)
        self.assertEqual(self.pairs(), [("Flute", "fl.pdf"), ("Oboe", "ob.pdf"), ("Horn", "hn.pdf")])

    def test_a_part_dragged_onto_a_voice_without_a_part_changes_only_that_pairing(self):
        self.project.add_instrument(self.group, "Tuba")
        self.project.move_instruments(self.group, [3], 1)
        self.project.pair_part(self.group, self.group.files[2], 1)
        self.assertEqual(
            self.pairs(), [("Flute", "fl.pdf"), ("Tuba", "hn.pdf"), ("Oboe", "ob.pdf"), ("Horn", None)],
        )

    def test_a_part_beyond_the_voices_dragged_onto_a_voice_without_a_part(self):
        self.project.add_files(_infos("extra.pdf"), self.group)
        self.project.set_present(self.group, 1, False)
        self.project.set_present(self.group, 1, True)
        extra = self.group.extra_files()[0]
        self.project.pair_part(self.group, extra, 1)
        self.assertEqual(self.pairs(), [("Flute", "fl.pdf"), ("Oboe", "extra.pdf"), ("Horn", "hn.pdf")])

    def test_pairing_a_part_with_a_voice_that_has_one_or_is_not_in_this_project_is_refused(self):
        self.project.set_present(self.group, 1, False)
        for index in (0, 1):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.project.pair_part(self.group, self.group.files[1], index)

    def test_a_new_voice_takes_the_first_part_beyond_the_voices(self):
        self.project.add_files(_infos("tb.pdf"), self.group)
        self.project.add_instrument(self.group, "Tuba")
        self.assertEqual(self.pairs()[-1], ("Tuba", "tb.pdf"))

    def test_a_new_voice_taking_a_part_beyond_the_voices_leaves_the_others_once(self):
        self.project.add_files(_infos("tb.pdf", "hp.pdf"), self.group)
        self.project.add_instrument(self.group, "Tuba")
        self.assertEqual(
            [f.display_name for f in self.group.files], ["fl.pdf", "ob.pdf", "hn.pdf", "tb.pdf", "hp.pdf"],
        )
        self.assertEqual([f.display_name for f in self.group.extra_files()], ["hp.pdf"])

    def test_a_voice_without_a_part_is_saved_and_opened_again(self):
        self.project.set_present(self.group, 1, False)
        self.project.set_present(self.group, 1, True)
        data = {**self.project.to_data(), "version": PROJECT_FILE_VERSION}
        opened = Project.from_data(data, score_label="總譜")
        self.assertEqual(
            [(p.voice, p.file.display_name if p.file else None) for p in opened.groups[0].pairing()],
            [("Flute", "fl.pdf"), ("Oboe", None), ("Horn", "hn.pdf")],
        )

    def test_a_project_saved_without_parts_marked_missing_opens_with_the_same_pairing(self):
        """這一欄以前的專案檔（含舊版）照位置配對，開啟時配對不變、不算修改"""
        self.project.set_present(self.group, 1, False)
        data = self.project.to_data()
        for group in data["groups"]:
            del group["vacant"]
        opened = Project.from_data({**data, "version": PROJECT_FILE_VERSION}, score_label="總譜")
        self.assertEqual(
            [(p.voice, p.file.display_name if p.file else None) for p in opened.groups[0].pairing()],
            [("Flute", "fl.pdf"), ("Horn", "hn.pdf")],
        )
        self.assertFalse(opened.is_modified())

    def test_renaming_keeps_whether_the_voice_is_in_this_project(self):
        self.project.set_present(self.group, 1, False)
        self.project.rename_instrument(self.group, 1, "Oboe d'amore")
        self.assertEqual(self.group.present, [True, False, True])

    def test_adding_a_voice_keeps_whether_the_others_are_in_this_project(self):
        self.project.set_present(self.group, 1, False)
        self.project.add_instrument(self.group, "Tuba")
        self.assertEqual(self.group.present, [True, False, True, True])

    def test_removing_voices_moves_their_parts_to_ungrouped_and_reports_them(self):
        moved = self.project.remove_instruments(self.group, [0, 2])
        self.assertEqual([f.display_name for f in moved], ["fl.pdf", "hn.pdf"])
        self.assertEqual(self.pairs(), [("Oboe", "ob.pdf")])
        self.assertEqual([f.display_name for f in self.project.ungrouped_files], ["Loose.pdf", "fl.pdf", "hn.pdf"])
        self.assertEqual(self.heard, [True])

    def test_parts_beyond_the_voices_stay_last(self):
        self.project.add_files(_infos("extra.pdf"), self.group)
        self.project.move_instruments(self.group, [0], 3)
        self.assertEqual([f.display_name for f in self.group.extra_files()], ["extra.pdf"])
        self.assertEqual(self.pairs()[-1], ("Flute", "fl.pdf"))

    def test_undoing_a_split_brings_back_a_voice_without_a_part(self):
        self.project.set_present(self.group, 1, False)
        self.project.set_present(self.group, 1, True)
        result = SplitResult(
            source_path=self.group.files[0].original_path, parts=_infos("vn.pdf"), voices=["Violin"], replaced=[],
        )
        placement = self.project.apply_split(result, score_label="總譜")
        self.project.revert_split(result.record(SplitPlacement.from_data(placement.to_data())))
        self.assertEqual(self.pairs(), [("Flute", "fl.pdf"), ("Oboe", None), ("Horn", "hn.pdf")])

    def undo_split(self, source, voices):
        """把群組裡的 source 分割成 voices 這幾份新分譜，再經紀錄檔的內容復原"""
        result = SplitResult(
            source_path=source.original_path, parts=_infos(*(f"{v}.pdf" for v in voices)), voices=list(voices),
            replaced=[],
        )
        placement = self.project.apply_split(result, score_label="總譜")
        self.project.revert_split(result.record(SplitPlacement.from_data(placement.to_data())))

    def test_undoing_a_split_of_a_part_after_a_voice_without_a_part_restores_every_pairing(self):
        self.project.set_present(self.group, 1, False)
        self.project.set_present(self.group, 1, True)
        self.undo_split(self.group.files[1], ["Violin", "Viola"])
        self.assertEqual(self.pairs(), [("Flute", "fl.pdf"), ("Oboe", None), ("Horn", "hn.pdf")])

    def test_undoing_a_split_that_kept_the_voice_list_restores_every_pairing(self):
        self.project.add_files(_infos("extra.pdf"), self.group)
        self.undo_split(self.group.files[1], ["Flute", "Oboe", "Horn"])
        self.assertEqual(self.pairs(), [("Flute", "fl.pdf"), ("Oboe", "ob.pdf"), ("Horn", "hn.pdf")])
        self.assertEqual([f.display_name for f in self.group.extra_files()], ["extra.pdf"])

    def test_replacing_the_whole_list_carries_each_part_with_its_voice_by_name(self):
        """整份換掉樂器表是清單變動：分譜依同一聲部的規則跟著聲部，不依位置錯配"""
        self.project.set_present(self.group, 1, False)
        self.project.set_instruments(self.group, ["Violin", "horn", "Oboe", "Flute"])
        self.assertEqual(self.group.instruments, ["Violin", "horn", "Oboe", "Flute"])
        self.assertEqual(self.group.present, [True, True, False, True])
        self.assertEqual(self.pairs(), [("Violin", None), ("horn", "hn.pdf"), ("Flute", "fl.pdf")])
        self.assertEqual(self.group.extra_files(), [])

    def test_parts_of_voices_left_out_of_the_new_list_stay_in_the_group_beyond_the_voices(self):
        self.project.set_instruments(self.group, ["Flute", "Violin"])
        self.assertEqual(self.pairs(), [("Flute", "fl.pdf"), ("Violin", None)])
        self.assertEqual([f.display_name for f in self.group.extra_files()], ["ob.pdf", "hn.pdf"])
        self.assertEqual([f.display_name for f in self.project.ungrouped_files], ["Loose.pdf"])

    def test_a_voice_without_a_part_keeps_none_when_the_whole_list_is_replaced(self):
        self.project.set_present(self.group, 0, False)
        self.project.set_present(self.group, 0, True)
        self.project.set_instruments(self.group, ["Horn", "Flute", "Oboe"])
        self.assertEqual(self.pairs(), [("Horn", "hn.pdf"), ("Flute", None), ("Oboe", "ob.pdf")])

    def test_voices_taken_from_the_file_names_pair_with_the_files_named_for_them(self):
        """從檔名擷取聲部：還沒配到聲部的分譜依檔名取出的聲部配給新的聲部，認不出的留在多出的分譜"""
        group = Group(name="g", files=_infos("江樓 - 琵琶.pdf", "江樓 - 二胡I.pdf", "江樓 - 中阮.pdf"))
        project = Project(groups=[group])
        project.set_instruments(group, ["二胡 I", "琵琶", "揚琴"])
        self.assertEqual(
            [(p.voice, p.file.display_name if p.file else None) for p in group.pairing()],
            [("二胡 I", "江樓 - 二胡I.pdf"), ("琵琶", "江樓 - 琵琶.pdf"), ("揚琴", None)],
        )
        self.assertEqual([f.display_name for f in group.extra_files()], ["江樓 - 中阮.pdf"])

    def test_a_list_for_a_group_without_parts_has_every_voice(self):
        group = Group(name="g")
        project = Project(groups=[group])
        project.set_instruments(group, ["Flute", "Oboe"])
        self.assertEqual(group.present, [True, True])
        self.assertEqual(group.vacant, [False, False])


class TestHeadcountRanges(unittest.TestCase):
    """每個群組各自記每個聲部的人數範圍：最少留空＝未填，最多留空＝等於最少，「以上」記成 n↑"""

    def setUp(self):
        self.group = Group(name="g", instruments=["二胡 I", "二胡 II", "大提琴"])
        self.other = Group(name="h", instruments=["二胡 I"])
        self.project = Project(groups=[self.group, self.other])
        self.heard = []
        self.project.subscribe(lambda: self.heard.append(self.project.is_modified()))

    def test_headcounts_start_unfilled(self):
        self.assertEqual(self.group.headcounts, [Headcount()] * 3)
        self.assertFalse(Headcount().filled)
        self.assertEqual(Headcount().text(), "")

    def test_a_range_a_single_count_and_an_open_ended_count_are_kept(self):
        self.project.set_headcount(self.group, 0, Headcount(6, 8))
        self.project.set_headcount(self.group, 1, Headcount(4))
        self.project.set_headcount(self.group, 2, Headcount(2, open_ended=True))
        self.assertEqual([h.text() for h in self.group.headcounts], ["6-8", "4", "2↑"])
        self.assertTrue(all(h.filled for h in self.group.headcounts))
        self.assertEqual(self.heard, [True, True, True])

    def test_each_group_keeps_its_own_headcounts(self):
        self.project.set_headcount(self.group, 0, Headcount(6, 8))
        self.assertEqual(self.other.headcounts, [Headcount()])

    def test_a_blank_maximum_equals_the_minimum(self):
        self.project.set_headcount(self.group, 0, Headcount(6, 6))
        self.project.set_headcount(self.group, 1, Headcount(6, 5))
        self.assertEqual(self.group.headcounts[:2], [Headcount(6), Headcount(6)])

    def test_open_ended_has_no_maximum(self):
        self.project.set_headcount(self.group, 0, Headcount(2, 4, open_ended=True))
        self.assertEqual(self.group.headcounts[0], Headcount(2, open_ended=True))

    def test_zero_is_blank(self):
        self.project.set_headcount(self.group, 0, Headcount(0, 0))
        self.assertEqual(self.group.headcounts[0], Headcount())
        self.assertFalse(self.group.headcounts[0].filled)

    def test_totals_add_the_minimums_and_maximums(self):
        self.assertEqual(sum_headcounts([Headcount(6, 8), Headcount(4), Headcount()]).text(), "10-12")
        self.assertEqual(sum_headcounts([Headcount(6, 8), Headcount(2, open_ended=True)]).text(), "8↑")
        self.assertEqual(sum_headcounts([Headcount()]).text(), "")

    def test_a_maximum_without_a_minimum_is_kept_but_unfilled(self):
        self.project.set_headcount(self.group, 0, Headcount(None, 8))
        self.assertEqual(self.group.headcounts[0], Headcount(None, 8))
        self.assertFalse(self.group.headcounts[0].filled)


class TestCopyInstrumentation(unittest.TestCase):
    """從其他群組複製編制：以同一聲部的規則逐聲部對應，對應不到的留空"""

    def setUp(self):
        self.source = Group(
            name="第一樂章", instruments=["二胡1", "大提琴", "琵琶", "二胡 1"],
            headcounts=[Headcount(6, 8), Headcount(4), Headcount(2, open_ended=True), Headcount(1)],
        )
        self.group = Group(
            name="第二樂章", instruments=["二胡 I", "二胡 II", "大提琴"], headcounts=[Headcount(3)] * 3,
        )
        self.project = Project(groups=[self.source, self.group])
        self.heard = []
        self.project.subscribe(lambda: self.heard.append(self.project.is_modified()))

    def test_headcounts_are_matched_by_voice_and_the_rest_left_blank(self):
        unmatched = self.project.copy_instrumentation(self.group, self.source)
        self.assertEqual([h.text() for h in self.group.headcounts], ["6-8", "", "4"])
        self.assertEqual(unmatched, ["二胡 II"])
        self.assertEqual([h.text() for h in self.source.headcounts], ["6-8", "4", "2↑", "1"])
        self.assertEqual(self.heard, [True])

    def test_sections_are_shared_by_instrument_already(self):
        self.project.set_section("二胡1", "低音")
        self.project.copy_instrumentation(self.group, self.source)
        self.assertEqual([self.project.section_of(v) for v in self.group.instruments], ["低音", "低音", "低音"])


class TestDuration(unittest.TestCase):
    """群組的時長以秒存、以分:秒呈現；0 與留空都是未填"""

    def setUp(self):
        self.group = Group(name="g")
        self.project = Project(groups=[self.group])
        self.heard = []
        self.project.subscribe(lambda: self.heard.append(self.project.is_modified()))

    def test_duration_is_stored_in_seconds_and_shown_as_minutes_and_seconds(self):
        self.project.set_duration(self.group, 7 * 60 + 5)
        self.assertEqual(self.group.duration_seconds, 425)
        self.assertEqual(format_duration(self.group.duration_seconds), "7:05")
        self.assertEqual(format_duration(75 * 60), "75:00")
        self.assertEqual(self.heard, [True])

    def test_zero_or_blank_is_unfilled(self):
        self.assertIsNone(self.group.duration_seconds)
        self.project.set_duration(self.group, 0)
        self.assertIsNone(self.group.duration_seconds)
        self.assertEqual(format_duration(None), "")


class TestHeadcountsFollowTheirVoices(unittest.TestCase):
    """人數跟著聲部：移動、取消勾選、改名都不換人；整份換掉樂器表時同一聲部沿用原本的人數"""

    def setUp(self):
        self.group = Group(
            name="g", instruments=["Flute", "Oboe", "Horn"], files=_infos("fl.pdf", "ob.pdf", "hn.pdf"),
            headcounts=[Headcount(1), Headcount(2), Headcount(4, 6)],
        )
        self.project = Project(groups=[self.group])

    def counts(self):
        """（聲部、人數記法），依樂器表順序"""
        return list(zip(self.group.instruments, [h.text() for h in self.group.headcounts]))

    def test_moving_a_voice_takes_its_headcount_along(self):
        self.project.move_instruments(self.group, [2], 0)
        self.assertEqual(self.counts(), [("Horn", "4-6"), ("Flute", "1"), ("Oboe", "2")])

    def test_unchecking_and_removing_keep_the_other_headcounts(self):
        self.project.set_present(self.group, 0, False)
        self.project.remove_instruments(self.group, [1])
        self.assertEqual(self.counts(), [("Flute", "1"), ("Horn", "4-6")])

    def test_renaming_keeps_the_headcount_and_a_new_voice_is_unfilled(self):
        self.project.rename_instrument(self.group, 1, "English Horn")
        self.project.add_instrument(self.group, "Tuba")
        self.assertEqual(self.counts(), [("Flute", "1"), ("English Horn", "2"), ("Horn", "4-6"), ("Tuba", "")])

    def test_duplicate_rows_of_an_old_project_keep_their_own_headcounts(self):
        self.project.set_instruments(self.group, ["二胡 I", "二胡1", "Horn"])
        self.project.set_headcount(self.group, 0, Headcount(3))
        self.project.set_headcount(self.group, 1, Headcount(5))
        self.project.move_instruments(self.group, [0], 3)
        self.project.set_present(self.group, 0, True)
        self.assertEqual(self.counts(), [("二胡1", "5"), ("Horn", "4-6"), ("二胡 I", "3")])

    def test_replacing_the_whole_list_keeps_the_headcounts_of_the_same_voices(self):
        self.project.set_instruments(self.group, ["horn", "Violin", "Flute"])
        self.assertEqual(self.counts(), [("horn", "4-6"), ("Violin", ""), ("Flute", "1")])

    def test_undoing_a_split_brings_the_headcounts_back(self):
        result = SplitResult(
            source_path=self.group.files[0].original_path, parts=_infos("vn.pdf"), voices=["Violin"], replaced=[],
        )
        placement = self.project.apply_split(result, score_label="總譜")
        self.assertEqual(self.counts(), [("Flute", "1"), ("Oboe", "2"), ("Horn", "4-6"), ("Violin", "")])
        self.project.revert_split(result.record(SplitPlacement.from_data(placement.to_data())))
        self.assertEqual(self.counts(), [("Flute", "1"), ("Oboe", "2"), ("Horn", "4-6")])


class TestApplySplit(unittest.TestCase):
    """套用分割結果：移除被取代的引用、加入新分譜、把合併譜移出群組；重新分割時由原群組接手"""

    SOURCE = _path("合併譜.pdf")

    def split(self, project, *names, folder="ws", replaced=()):
        """把分割結果（分譜為 folder 下的 names）套用到專案，回傳接手的群組"""
        parts = [_info(_path(folder, name + ".pdf")) for name in names]
        result = SplitResult(self.SOURCE, parts, list(names), replaced=list(replaced))
        placement = project.apply_split(result, score_label="總譜")
        return next(g for g in project.groups if g.id == placement.group_id)

    @staticmethod
    def layout(project):
        """各群組的（名稱、總譜、分譜）與未分組，皆以路徑表示"""
        groups = [
            (g.name, g.score_file.original_path if g.score_file else None, [f.original_path for f in g.files])
            for g in project.groups
        ]
        return groups, [f.original_path for f in project.ungrouped_files]

    def test_merged_score_among_parts_leaves_the_group_which_takes_the_new_parts(self):
        group = Group(name="g", files=[_info(_path("Loose.pdf")), _info(self.SOURCE)], score_label="總譜")
        project = Project(groups=[group])
        self.assertIs(self.split(project, "Flute", "Oboe"), group)
        self.assertEqual(self.layout(project), (
            [("g", None, [_path("ws", "Flute.pdf"), _path("ws", "Oboe.pdf"), _path("Loose.pdf")])], [],
        ))
        self.assertEqual(group.instruments, ["Flute", "Oboe"])

    def test_merged_score_set_as_the_group_score_leaves_the_group_too(self):
        group = Group(name="g", score_file=_info(self.SOURCE), score_label="總譜")
        project = Project(groups=[group])
        self.split(project, "Flute", "Oboe")
        self.assertEqual(self.layout(project), (
            [("g", None, [_path("ws", "Flute.pdf"), _path("ws", "Oboe.pdf")])], [],
        ))

    def test_merged_score_in_ungrouped_stays_there_and_a_group_named_after_it_takes_the_parts(self):
        project = Project(ungrouped_files=[_info(self.SOURCE)])
        group = self.split(project, "Flute", "Oboe")
        self.assertEqual(self.layout(project), (
            [("合併譜", None, [_path("ws", "Flute.pdf"), _path("ws", "Oboe.pdf")])], [self.SOURCE],
        ))
        self.assertEqual(group.score_label, "總譜")

    def test_resplit_into_the_workspace_hands_the_new_parts_to_the_group_of_the_replaced_ones(self):
        old = [_info(_path("ws", "Flute.pdf")), _info(_path("ws", "Oboe.pdf"))]
        held = Group(name="held", files=old, score_label="總譜")
        other = Group(name="other", files=[_info(_path("Horn.pdf")), _info(self.SOURCE)], score_label="總譜")
        project = Project(groups=[held, other])
        self.assertIs(self.split(project, "Flute", "Horn", replaced=[f.original_path for f in old]), held)
        self.assertEqual(self.layout(project), ([
            ("held", None, [_path("ws", "Flute.pdf"), _path("ws", "Horn.pdf")]),
            ("other", None, [_path("Horn.pdf")]),
        ], []))

    def test_resplit_into_a_chosen_folder_does_not_duplicate_the_parts_in_another_group(self):
        old = [_path("out", "Flute.pdf"), _path("out", "Oboe.pdf")]
        held = Group(name="held", files=[_info(p) for p in old], score_label="總譜")
        project = Project(groups=[held], ungrouped_files=[_info(self.SOURCE)])
        self.split(project, "Flute", "Oboe", folder="out", replaced=old)
        self.assertEqual(self.layout(project), ([("held", None, old)], [self.SOURCE]))

    def test_resplit_from_ungrouped_leaves_no_empty_group(self):
        old = [_path("ws", "Flute.pdf"), _path("ws", "Oboe.pdf")]
        project = Project(
            groups=[Group(name="合併譜", files=[_info(p) for p in old], score_label="總譜")],
            ungrouped_files=[_info(self.SOURCE)],
        )
        self.split(project, "Flute", "Horn", replaced=old)
        self.assertEqual(self.layout(project), (
            [("合併譜", None, [_path("ws", "Flute.pdf"), _path("ws", "Horn.pdf")])], [self.SOURCE],
        ))

    @staticmethod
    def pairs(group):
        """（聲部、配到的分譜檔名；沒有分譜時為 None），依本專案有的聲部的順序"""
        return [(p.voice, p.file.display_name if p.file else None) for p in group.pairing()]

    def test_a_score_segment_becomes_the_score_without_shifting_any_part(self):
        project = Project(ungrouped_files=[_info(self.SOURCE)])
        group = self.split(project, "總譜", "二胡", "琵琶")
        self.assertEqual(group.score_file.display_name, "總譜.pdf")
        self.assertEqual(group.instruments, ["二胡", "琵琶"])
        self.assertEqual(self.pairs(group), [("二胡", "二胡.pdf"), ("琵琶", "琵琶.pdf")])

    def test_new_parts_pair_with_their_own_voices_and_a_part_already_there_is_left_alone(self):
        group = Group(name="g", files=[_info(_path("笛.pdf")), _info(self.SOURCE)], score_label="總譜")
        project = Project(groups=[group])
        self.split(project, "二胡", "琵琶")
        self.assertEqual(self.pairs(group), [("二胡", "二胡.pdf"), ("琵琶", "琵琶.pdf")])
        self.assertEqual([f.display_name for f in group.extra_files()], ["笛.pdf"])

    def test_existing_voices_keep_their_parts_and_new_parts_go_to_their_voices(self):
        group = Group(
            name="g", instruments=["笛", "二胡 I", "笙"], files=[_info(_path("笛.pdf")), _info(self.SOURCE)],
            score_label="總譜",
        )
        project = Project(groups=[group])
        project.set_present(group, 2, False)
        self.split(project, "二胡1", "琵琶", "笙")
        self.assertEqual(group.instruments, ["笛", "二胡 I", "笙", "琵琶"])
        self.assertEqual(group.present, [True, True, True, True])
        self.assertEqual(
            self.pairs(group), [("笛", "笛.pdf"), ("二胡 I", "二胡1.pdf"), ("笙", "笙.pdf"), ("琵琶", "琵琶.pdf")],
        )

    def test_a_new_part_for_a_voice_that_already_has_one_goes_beyond_the_voices(self):
        group = Group(
            name="g", instruments=["笛"], files=[_info(_path("笛.pdf")), _info(self.SOURCE)], score_label="總譜",
        )
        project = Project(groups=[group])
        self.split(project, "笛", "琵琶")
        self.assertEqual(self.pairs(group), [("笛", "笛.pdf"), ("琵琶", "琵琶.pdf")])
        self.assertEqual([f.original_path for f in group.extra_files()], [_path("ws", "笛.pdf")])

    def test_replaced_refs_are_removed_wherever_they_are(self):
        old = _path("ws", "Flute.pdf")
        project = Project(groups=[Group(name="g", files=[_info(self.SOURCE)])], ungrouped_files=[_info(old)])
        self.split(project, "Flute", replaced=[old])
        self.assertEqual(self.layout(project), ([("g", None, [_path("ws", "Flute.pdf")])], []))

    def test_applying_marks_the_project_unsaved(self):
        project = Project(ungrouped_files=[_info(self.SOURCE)])
        heard = []
        project.subscribe(lambda: heard.append(project.is_modified()))
        self.split(project, "Flute")
        self.assertEqual(heard, [True])


class TestRevertSplit(unittest.TestCase):
    """退回分割：分割後專案又有變動時，只退回分割改掉的部分，不丟掉其他引用

    復原分割的完整流程（真 PDF、搬移歷程）在 test_split_service 的 TestUndoSplit。
    """

    SOURCE = _path("合併譜.pdf")

    def split(self, project, *names, replaced=()):
        """把分割結果套用到專案，回傳分割紀錄（帶安置方式）"""
        parts = [_info(_path("ws", name + ".pdf")) for name in names]
        result = SplitResult(self.SOURCE, parts, list(names), replaced=list(replaced))
        return result.record(project.apply_split(result, score_label="總譜"))

    def test_merged_score_keeps_its_order_among_the_parts_left_after_a_resplit(self):
        old = [_info(_path("ws", "Flute.pdf")), _info(_path("ws", "Oboe.pdf"))]
        horn = _info(_path("Horn.pdf"))
        group = Group(name="g", files=old + [_info(self.SOURCE), horn], score_label="總譜")
        project = Project(groups=[group])
        project.revert_split(self.split(project, "Flute", "Tuba", replaced=[f.original_path for f in old]))
        self.assertEqual([f.original_path for f in group.files], [self.SOURCE, horn.original_path])

    def test_merged_score_goes_to_ungrouped_when_its_group_was_deleted_after_the_split(self):
        group = Group(name="g", files=[_info(self.SOURCE)], score_label="總譜")
        project = Project(groups=[group])
        record = self.split(project, "Flute", "Oboe")
        project.delete_group(group)
        project.revert_split(record)
        self.assertEqual(project.groups, [])
        self.assertEqual([f.original_path for f in project.ungrouped_files], [self.SOURCE])

    def test_files_put_into_the_created_group_after_the_split_go_to_ungrouped(self):
        horn = _info(_path("Horn.pdf"))
        project = Project(ungrouped_files=[_info(self.SOURCE), horn])
        record = self.split(project, "Flute")
        project.move_to_group([horn], project.groups[0])
        project.revert_split(record)
        self.assertEqual(project.groups, [])
        self.assertEqual([f.original_path for f in project.ungrouped_files], [self.SOURCE, horn.original_path])

    def test_group_fields_the_split_did_not_change_keep_later_edits(self):
        group = Group(name="g", files=[_info(self.SOURCE)], instruments=["Flute", "Oboe"],
                      selected_instruments=[0, 1], piece_name="命運", score_label="總譜")
        project = Project(groups=[group])
        record = self.split(project, "Flute", "Oboe")
        project.update_group(group, piece_name="命運交響曲")
        project.set_instruments(group, ["Flute 1", "Oboe"])
        project.revert_split(record)
        self.assertEqual((group.piece_name, group.instruments), ("命運交響曲", ["Flute 1", "Oboe"]))

    def undo_restores(self, group, *names):
        """把群組裡的合併譜分割成 names，經紀錄檔的內容復原後，專案與分割前完全相同"""
        project = Project(groups=[group])
        before = project.to_data()
        parts = [_info(_path("ws", name + ".pdf")) for name in names]
        result = SplitResult(self.SOURCE, parts, list(names), replaced=[])
        placement = project.apply_split(result, score_label="總譜")
        project.revert_split(result.record(SplitPlacement.from_data(placement.to_data())))
        self.assertEqual(project.to_data(), before)

    def test_undoing_a_split_whose_parts_went_to_existing_voices_restores_the_project(self):
        self.undo_restores(Group(
            name="g", instruments=["笛", "二胡", "琵琶"], selected_instruments=[0, 1, 2], score_label="總譜",
            files=[_info(_path("笛.pdf")), _info(self.SOURCE), _info(_path("琵琶.pdf")), _info(_path("Loose.pdf"))],
        ), "二胡", "笙")

    def test_undoing_a_split_that_refilled_the_merged_score_s_voice_restores_the_project(self):
        self.undo_restores(Group(
            name="g", instruments=["笛", "二胡", "琵琶"], selected_instruments=[0, 1, 2], score_label="總譜",
            files=[_info(_path("笛.pdf")), _info(self.SOURCE), _info(_path("琵琶.pdf"))],
        ), "二胡")

    def test_undoing_a_split_with_a_score_segment_restores_the_project(self):
        self.undo_restores(Group(
            name="g", instruments=["笛"], selected_instruments=[0], score_label="總譜",
            files=[_info(_path("笛.pdf")), _info(self.SOURCE)],
        ), "總譜", "二胡")

    def test_voices_not_in_this_project_are_unchecked_again_after_undoing(self):
        group = Group(name="g", files=[_info(self.SOURCE)], instruments=["Flute", "Oboe", "Horn"], score_label="總譜")
        project = Project(groups=[group])
        project.set_present(group, 1, False)
        project.revert_split(self.split(project, "Violin", "Viola"))
        self.assertEqual((group.instruments, group.present), (["Flute", "Oboe", "Horn"], [True, False, True]))


class TestProjectSettingEdits(unittest.TestCase):
    """命名格式、輸出設定、聲部組與套用搬移結果：每次都發出「已變更」通知"""

    def setUp(self):
        self.project = Project(groups=[Group(name="g", files=_infos("Flute.pdf"), small_template="{樂器}.pdf")])
        self.heard = []
        self.project.subscribe(lambda: self.heard.append(self.project.is_modified()))

    def test_output_settings_keep_the_old_parts_subfolder_flag_in_step(self):
        self.project.set_output_settings(parts_output_mode="parts", output_directory=_path("out"))
        self.assertTrue(self.project.use_parts_subfolder)
        self.project.set_output_settings(parts_output_mode="section")
        self.assertFalse(self.project.use_parts_subfolder)
        self.assertEqual(self.project.output_directory, _path("out"))
        self.assertEqual(self.heard, [True, True])

    def test_unknown_output_setting_is_refused(self):
        with self.assertRaises(ValueError):
            self.project.set_output_settings(master_template="x")

    def test_sections_are_merged_per_voice(self):
        self.project.update_sections({"Flute": "木管"})
        self.project.update_sections({"Horn": "銅管"})
        self.assertEqual(self.project.instrument_sections, {"Flute": "木管", "Horn": "銅管"})
        self.assertEqual(self.heard, [True, True])

    def test_converting_template_language_rewrites_every_template(self):
        self.project.set_output_settings(subfolder_template="{曲名}")
        self.project.set_master_template("{序號}-{樂器}.pdf")
        self.heard.clear()
        self.project.convert_template_language("en")
        self.assertEqual(
            (self.project.master_template, self.project.subfolder_template, self.project.groups[0].small_template),
            ("{Number}-{Instrument}.pdf", "{PieceName}", "{Instrument}.pdf"),
        )
        self.assertEqual(self.heard, [True])

    def test_applying_moves_and_removals_notifies(self):
        path = self.project.groups[0].files[0].original_path
        self.project.replace_paths([UndoMapping(path, _path("out", "01-Flute.pdf"))])
        self.project.remove_paths([_path("out", "01-Flute.pdf")])
        self.assertEqual(self.project.all_file_paths(), [])
        self.assertEqual(self.heard, [True, True])



class TestSectionsByInstrument(unittest.TestCase):
    """聲部組以樂器為單位：改一個聲部的聲部組，專案裡同一樂器的其他編號（各群組都是）跟著改，並回報影響範圍"""

    def setUp(self):
        self.group = Group(name="g", instruments=["高胡", "二胡 I", "二胡 II", "中胡"])
        self.project = Project(groups=[self.group])
        self.heard = []
        self.project.subscribe(lambda: self.heard.append(self.project.is_modified()))

    def test_changing_one_voice_changes_the_other_numbers_and_reports_them(self):
        affected = self.project.set_section("二胡 I", "低音")
        self.assertEqual(affected, ["二胡 II"])
        self.assertEqual(
            [self.project.section_of(v) for v in ("二胡 I", "二胡 II", "二胡1", "二胡", "中胡")],
            ["低音", "低音", "低音", "低音", "拉弦"],
        )
        self.assertEqual(self.heard, [True])

    def test_voices_that_would_change_too_are_listed_before_changing(self):
        self.assertEqual(self.project.same_instrument_voices("二胡 II"), ["二胡 I"])
        self.assertEqual(self.project.same_instrument_voices("中胡"), [])

    def test_voices_of_other_groups_are_listed_once_each(self):
        self.project.groups.append(Group(name="第二樂章", instruments=["二胡1", "二胡 2", "二胡 III", "中胡"]))
        self.assertEqual(self.project.same_instrument_voices("二胡 I"), ["二胡 II", "二胡 III"])
        self.assertEqual(self.project.set_section("二胡 I", "低音"), ["二胡 II", "二胡 III"])

    def test_the_key_keeps_the_spelling_written_first(self):
        self.project.set_section("二胡 II", "低音")
        self.project.set_section("二胡 I", "拉弦")
        self.project.update_sections({"二胡1": "彈撥"})
        self.assertEqual(self.project.instrument_sections, {"二胡 II": "彈撥"})

    def test_an_unknown_orchestra_type_from_a_newer_file_opens_as_the_default(self):
        data = {"version": PROJECT_FILE_VERSION + 1, "orchestra_type": "big_band"}
        self.assertIs(Project.from_data(data, "總譜").orchestra_type, OrchestraType.CHINESE)
        self.assertIs(Project.from_data({"orchestra_type": "western"}, "總譜").orchestra_type, OrchestraType.WESTERN)

    def test_voices_without_a_stored_section_are_detected_for_the_orchestra_type(self):
        self.assertEqual(Project().orchestra_type, "chinese")
        self.assertEqual(self.project.section_of("Cello"), "低音")
        self.project.set_orchestra_type(OrchestraType.WESTERN)
        self.assertEqual(self.project.section_of("Cello"), "弦樂")
        self.assertEqual(self.project.section_options(), ("木管", "銅管", "打擊", "鍵盤", "弦樂", "其他"))
        self.assertEqual(self.heard, [True])


if __name__ == '__main__':
    unittest.main()
