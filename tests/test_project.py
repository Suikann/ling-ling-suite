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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core.models import FileInfo, Group, Project, SplitResult, UndoMapping
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

    def test_clearing_the_score_puts_it_first_among_parts(self):
        self.project.clear_score(self.group)
        self.assertIsNone(self.group.score_file)
        self.assertEqual(self.names(self.group.files), ["Score.pdf", "Flute.pdf", "Oboe.pdf", "Horn.pdf"])
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
            [("g", None, [_path("Loose.pdf"), _path("ws", "Flute.pdf"), _path("ws", "Oboe.pdf")])], [],
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


class TestProjectSettingEdits(unittest.TestCase):
    """命名格式、輸出設定、編制設定與套用搬移結果：每次都發出「已變更」通知"""

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

    def test_ensemble_settings_are_merged_per_voice(self):
        self.project.update_ensemble(headcounts={"Flute": 2}, sections={"Flute": "木管"})
        self.project.update_ensemble(sections={"Horn": "銅管"})
        self.assertEqual(self.project.instrument_headcounts, {"Flute": 2})
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


if __name__ == '__main__':
    unittest.main()
