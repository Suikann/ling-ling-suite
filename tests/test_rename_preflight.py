# -*- coding: utf-8 -*-
"""
重新命名預檢測試

從 MoveHistory 的介面驗證預檢的判定（check_rename、check_plan）與執行吃同一份判定（rename）：
在暫存目錄裡用真檔案，使用者資料目錄由建構時注入。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core.constants import PartsOutputMode, RenameProblem
from core.models import FileInfo, Group, Project, RenameEntry
from services.move_history import RenameVerdict
from services.rename_service import RenamePlan
from test_move_history import MoveHistoryTestCase


class PreflightTestCase(MoveHistoryTestCase):
    """預檢測試的共用骨架：群組的檔案建在 scores/"""

    def group(self, *names: str, voices=(), **fields) -> Group:
        """以 scores/ 下新建的檔案為分譜的群組"""
        files = [FileInfo(self.create(name), name) for name in names]
        return Group(name="g", files=files, instruments=list(voices), **fields)

    def targets(self, verdict):
        """判定計畫的（來源檔名，新路徑相對於 scores/）"""
        return [
            (os.path.basename(e.original_path), os.path.relpath(e.new_path, self.scores)) for e in verdict.plan
        ]


class TestVerdictIsWhatRuns(PreflightTestCase):
    """判定的計畫就是執行的計畫"""

    def test_runnable_verdict_is_executed_as_planned(self):
        project = Project(master_template="{序號}-{樂器}.pdf", groups=[self.group("a.pdf", "b.pdf", voices=["Fl", "Ob"])])
        verdict = self.history.check_rename(project)
        self.assertEqual(self.targets(verdict), [("a.pdf", "01-Fl.pdf"), ("b.pdf", "02-Ob.pdf")])
        self.assertTrue(verdict.runnable)
        result = self.history.rename(verdict)
        self.assertIsNone(result.error)
        self.assertEqual(self.files(), ["01-Fl.pdf", "02-Ob.pdf"])


class TestAdjustments(PreflightTestCase):
    """遺失來源從計畫丟掉、重複目標自動加後綴；兩者都不阻擋"""

    def test_missing_source_is_dropped_from_the_plan(self):
        group = self.group("a.pdf", "b.pdf", voices=["Fl", "Ob"])
        os.remove(self.path("b.pdf"))
        verdict = self.history.check_rename(Project(master_template="{樂器}.pdf", groups=[group]))
        self.assertEqual(self.targets(verdict), [("a.pdf", "Fl.pdf")])
        self.assertEqual(verdict.sources(RenameProblem.MISSING_SOURCE), [self.path("b.pdf")])
        self.assertTrue(verdict.runnable)
        self.assertIsNone(self.history.rename(verdict).error)
        self.assertEqual(self.files(), ["Fl.pdf"])

    def test_duplicate_targets_get_a_suffix_and_the_suffixed_plan_runs(self):
        group = self.group("a.pdf", "b.pdf", "c.pdf", voices=["Fl", "Ob", "Cl"], piece_name="Same")
        verdict = self.history.check_rename(Project(master_template="{曲名}.pdf", groups=[group]))
        self.assertEqual(self.targets(verdict), [
            ("a.pdf", "Same.pdf"), ("b.pdf", "Same (1).pdf"), ("c.pdf", "Same (2).pdf"),
        ])
        self.assertEqual(verdict.sources(RenameProblem.SUFFIXED), [self.path("b.pdf"), self.path("c.pdf")])
        self.assertTrue(verdict.runnable)
        self.assertIsNone(self.history.rename(verdict).error)
        self.assertEqual(self.files(), ["Same (1).pdf", "Same (2).pdf", "Same.pdf"])

    def test_one_target_in_two_spellings_gets_a_suffix(self):
        group = self.group("a.pdf", "b.pdf", voices=["Flute", "FLUTE"])
        verdict = self.history.check_rename(Project(master_template="{樂器}.pdf", groups=[group]))
        self.assertEqual(self.targets(verdict), [("a.pdf", "Flute.pdf"), ("b.pdf", "FLUTE (1).pdf")])
        self.assertTrue(verdict.runnable)

    def test_targets_that_are_sources_in_the_plan_are_not_taken(self):
        for label, voices, after in (
            ("對調", ["b", "a"], ["a.pdf", "b.pdf"]),
            ("連鎖", ["b", "c"], ["b.pdf", "c.pdf"]),
        ):
            with self.subTest(label):
                for name in self.files():
                    os.remove(self.path(name))
                group = self.group("a.pdf", "b.pdf", voices=voices)
                verdict = self.history.check_rename(Project(master_template="{樂器}.pdf", groups=[group]))
                self.assertEqual(verdict.problems, {})
                self.assertIsNone(self.history.rename(verdict).error)
                self.assertEqual(self.files(), after)


class TestReminders(PreflightTestCase):
    """提醒、不阻擋：多於聲部數的分譜不改名、命名格式裡的未知變數"""

    def test_files_beyond_the_voice_count_are_listed_and_left_alone(self):
        group = self.group("a.pdf", "b.pdf", "c.pdf", "d.pdf", voices=["Fl", "Ob"])
        verdict = self.history.check_rename(Project(master_template="{樂器}.pdf", groups=[group]))
        self.assertEqual(verdict.sources(RenameProblem.EXTRA_FILE), [self.path("c.pdf"), self.path("d.pdf")])
        self.assertTrue(verdict.runnable)
        self.assertIsNone(self.history.rename(verdict).error)
        self.assertEqual(self.files(), ["Fl.pdf", "Ob.pdf", "c.pdf", "d.pdf"])

    def test_unknown_variables_are_listed_without_blocking(self):
        group = self.group("a.pdf", voices=["Fl"], piece_name="Sym")
        project = Project(
            master_template="{樂器}{Foo}.pdf", use_subfolders=True, subfolder_template="{曲名}{Bar}{Foo}",
            groups=[group],
        )
        verdict = self.history.check_rename(project)
        self.assertEqual(verdict.unknown_variables, ["Foo", "Bar"])
        self.assertEqual(self.targets(verdict), [("a.pdf", os.path.join("Sym", "Fl.pdf"))])
        self.assertTrue(verdict.runnable)


class TestBlocking(PreflightTestCase):
    """遺失來源與重複目標以外的問題一律阻擋，執行時也被拒絕"""

    def assert_blocked(self, verdict, kind, *names):
        """判定阻擋、指出這些來源，而且照判定執行會被拒絕、不動任何檔案"""
        self.assertEqual(verdict.sources(kind), [self.path(n) for n in names])
        self.assertFalse(verdict.runnable)
        before = self.files()
        result = self.history.rename(verdict)
        self.assertIsNotNone(result.error)
        self.assertEqual(self.files(), before)
        self.assertIsNone(self.history.latest_undo())

    def test_duplicate_after_suffix_blocks_and_names_the_files(self):
        group = self.group("a.pdf", "b.pdf", "c.pdf", voices=["Same", "Same", "Same (1)"])
        verdict = self.history.check_rename(Project(master_template="{樂器}.pdf", groups=[group]))
        self.assertEqual(self.targets(verdict), [
            ("a.pdf", "Same.pdf"), ("b.pdf", "Same (1).pdf"), ("c.pdf", "Same (1).pdf"),
        ])
        self.assert_blocked(verdict, RenameProblem.DUPLICATE_TARGET, "b.pdf", "c.pdf")

    def test_suffix_onto_a_name_taken_outside_the_plan_blocks(self):
        group = self.group("a.pdf", "b.pdf", voices=["Same", "Same"])
        self.create("Same (1).pdf")
        verdict = self.history.check_rename(Project(master_template="{樂器}.pdf", groups=[group]))
        self.assert_blocked(verdict, RenameProblem.TARGET_OCCUPIED, "b.pdf")

    def test_target_taken_outside_the_plan_blocks(self):
        group = self.group("a.pdf", "b.pdf", voices=["Fl", "Ob"])
        self.create("Ob.pdf")
        verdict = self.history.check_rename(Project(master_template="{樂器}.pdf", groups=[group]))
        self.assert_blocked(verdict, RenameProblem.TARGET_OCCUPIED, "b.pdf")

    def test_taken_staging_name_blocks(self):
        group = self.group("a.pdf", "b.pdf", voices=["b", "a"])
        self.create("a.pdf.moving")
        verdict = self.history.check_rename(Project(master_template="{樂器}.pdf", groups=[group]))
        self.assert_blocked(verdict, RenameProblem.STAGING_TAKEN, "a.pdf")

    def test_empty_name_blocks(self):
        group = self.group("a.pdf", voices=["Fl"])
        verdict = self.history.check_rename(Project(master_template="{樂章名稱}.pdf", groups=[group]))
        self.assert_blocked(verdict, RenameProblem.EMPTY_NAME, "a.pdf")

    def test_file_in_two_groups_blocks(self):
        first = self.group("a.pdf", voices=["Fl"])
        second = Group(name="h", files=list(first.files), instruments=["Ob"])
        verdict = self.history.check_rename(Project(master_template="{樂器}.pdf", groups=[first, second]))
        self.assert_blocked(verdict, RenameProblem.DUPLICATE_SOURCE, "a.pdf")


class TestOutputBoundary(PreflightTestCase):
    """每個目標都必須在輸出位置裡（沒指定時為來源檔所在的資料夾），一個不在就整批阻擋"""

    def _plan_with_one_escape(self, output_directory: str, escape: str):
        """兩個檔：a.pdf 的目標是 escape，b.pdf 的目標在輸出位置裡"""
        inside = os.path.join(output_directory or self.scores, "B.pdf")
        return [
            RenameEntry(self.create("a.pdf"), escape, output_directory=output_directory),
            RenameEntry(self.create("b.pdf"), inside, output_directory=output_directory),
        ]

    def test_target_outside_blocks_the_whole_batch_in_preflight_and_execution(self):
        out = os.path.join(self.scores, "out")
        cases = {
            "輸出位置的上一層": (out, os.path.join(out, "..", "A.pdf")),
            "輸出位置旁同名開頭的資料夾": (out, os.path.join(self.scores, "out2", "A.pdf")),
            "沒指定輸出位置時離開來源資料夾": ("", os.path.join(self.temp_dir, "A.pdf")),
        }
        for label, (output_directory, escape) in cases.items():
            with self.subTest(label):
                entries = self._plan_with_one_escape(output_directory, escape)
                verdict = self.history.check_plan(RenamePlan(entries=entries))
                self.assertEqual(verdict.sources(RenameProblem.OUTSIDE_OUTPUT), [self.path("a.pdf")])
                self.assertFalse(verdict.runnable)
                result = self.history.rename(RenameVerdict(plan=entries))
                self.assertIn(escape, str(result.error))
                self.assertEqual(self.files(), ["a.pdf", "b.pdf"])
                self.assertIsNone(self.history.latest_undo())

    def test_every_target_of_normal_settings_is_inside_the_output_location(self):
        group = self.group("fl.pdf", "hn.pdf", voices=["Flute", "Horn"], piece_name="Sym", movement_number="1")
        group.score_file = FileInfo(self.create("score.pdf"), "score.pdf")
        group.score_label = "Score"
        out = os.path.join(self.temp_dir, "out")
        for output_directory, location in (("", self.scores), (out, out)):
            for use_subfolders in (False, True):
                for mode in PartsOutputMode:
                    with self.subTest(output=bool(output_directory), subfolders=use_subfolders, mode=mode):
                        project = Project(
                            master_template="{序號}-{曲名}-{樂器}.pdf", output_directory=output_directory,
                            use_subfolders=use_subfolders, subfolder_template="{曲名} - {樂章編號}",
                            parts_output_mode=mode, parts_subfolder_name="Parts", groups=[group],
                        )
                        verdict = self.history.check_rename(project)
                        self.assertEqual(len(verdict.plan), 3)
                        self.assertEqual(verdict.problems, {})
                        for entry in verdict.plan:
                            self.assertFalse(os.path.relpath(entry.new_path, location).startswith(os.pardir))


class TestSettingsProblems(PreflightTestCase):
    """只看命名設定就知道的問題：資料夾名稱是 . 或 ..、子資料夾模板的逐檔變數、未知變數"""

    def assert_refused(self, verdict):
        """判定不可執行，照判定執行會被拒絕、不動任何檔案"""
        self.assertFalse(verdict.runnable)
        before = self.files()
        self.assertIsNotNone(self.history.rename(verdict).error)
        self.assertEqual(self.files(), before)

    def test_dot_dot_folder_blocks_and_names_the_folder(self):
        project = Project(
            master_template="{樂器}.pdf", parts_output_mode=PartsOutputMode.PARTS, parts_subfolder_name="..",
            groups=[self.group("a.pdf", voices=["Fl"])],
        )
        verdict = self.history.check_rename(project)
        self.assertEqual(verdict.unsafe_folder, "..")
        self.assert_refused(verdict)

    def _subfolder_project(self, subfolder_template: str, use_subfolders: bool = True) -> Project:
        group = self.group("a.pdf", "b.pdf", voices=["Fl", "Ob"], piece_name="Sym", movement_number="1")
        return Project(
            master_template="{樂器}.pdf", use_subfolders=use_subfolders,
            subfolder_template=subfolder_template, groups=[group],
        )

    def test_per_file_variable_in_subfolder_template_blocks_and_names_it(self):
        for name in ("序號", "Number", "樂器", "Instrument"):
            with self.subTest(name):
                verdict = self.history.check_rename(self._subfolder_project(f"{{曲名}} {{{name}}}"))
                self.assertEqual(verdict.folder_variables, [name])
                self.assert_refused(verdict)

    def test_group_level_variables_in_subfolder_template_do_not_block(self):
        project = self._subfolder_project("{曲名} {MovementNum} {樂章名稱} {Composer} {曲種}")
        verdict = self.history.check_rename(project)
        self.assertEqual(verdict.folder_variables, [])
        self.assertTrue(verdict.runnable)

    def test_subfolder_template_is_not_checked_while_subfolders_are_off(self):
        verdict = self.history.check_rename(self._subfolder_project("{序號}", use_subfolders=False))
        self.assertEqual(verdict.folder_variables, [])
        self.assertTrue(verdict.runnable)


if __name__ == '__main__':
    unittest.main()
