# -*- coding: utf-8 -*-
"""
命名模組測試

命名模組是純函式：輸入群組中的一格與命名設定，輸出檔名與相對資料夾。
測試不碰磁碟、不切換介面語言；檔案路徑只是字串。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.constants import TEMPLATE_VARIABLES, PartsOutputMode, VariableLevel
from core.models import FileInfo, Group, Project
from core.naming import (
    SCORE_SLOT, NamingSettings, UnsafeFolderNameError, name_group, name_slot, named_voices, settings_for,
    unknown_variables, variable_level,
)


def _files(*names):
    return [FileInfo(os.path.join("in", name), name) for name in names]


def _group(voices, files=None, **fields):
    """樂器表為 voices、每個聲部各一份分譜的群組"""
    if files is None:
        files = _files(*(f"raw{i}.pdf" for i in range(len(voices))))
    return Group(name="g", instruments=list(voices), files=files, **fields)


def _file_names(naming):
    return [named.name.file_name for named in naming.files]


class TestNumbers(unittest.TestCase):
    """序號至少兩位數，超過 99 個聲部才用三位；總譜固定 00"""

    def test_three_voices_are_numbered_01_to_03(self):
        naming = name_group(_group(["Flute", "Oboe", "Horn"]), NamingSettings("{序號}.pdf"))
        self.assertEqual(_file_names(naming), ["01.pdf", "02.pdf", "03.pdf"])

    def test_hundred_voices_are_numbered_001_to_100(self):
        naming = name_group(_group([f"V{i}" for i in range(100)]), NamingSettings("{序號}.pdf"))
        names = _file_names(naming)
        self.assertEqual(len(names), 100)
        self.assertEqual(names[:2], ["001.pdf", "002.pdf"])
        self.assertEqual(names[98:], ["099.pdf", "100.pdf"])

    def test_score_is_numbered_00_and_named_by_its_score_label(self):
        group = _group(["Flute"], score_file=FileInfo(os.path.join("in", "s.pdf"), "s.pdf"), score_label="Full Score")
        naming = name_group(group, NamingSettings("{序號}-{樂器}.pdf"))
        self.assertEqual(_file_names(naming), ["00-Full Score.pdf", "01-Flute.pdf"])
        self.assertEqual(naming.files[0].file, group.score_file)
        self.assertEqual(naming.files[0].slot, 0)


class TestVariableTable(unittest.TestCase):
    """總譜與分譜用同一份變數表，由模板變數常數推得"""

    # 每個模板變數（以中文名稱為鍵）在總譜與第 2 份分譜代入的值
    EXPECTED = {
        "序號": ("00", "02"),
        "樂器": ("Full Score", "Oboe"),
        "曲名": ("Symphony 5", "Symphony 5"),
        "樂章編號": ("1", "1"),
        "樂章名稱": ("Allegro", "Allegro"),
        "作曲家": ("Beethoven", "Beethoven"),
        "曲種": ("Symphony", "Symphony"),
    }

    def setUp(self):
        self.group = _group(
            ["Flute", "Oboe"], score_file=FileInfo(os.path.join("in", "s.pdf"), "s.pdf"), score_label="Full Score",
            piece_name="Symphony 5", movement_number="1", movement_name="Allegro", composer="Beethoven",
            genre="Symphony",
        )

    def test_expected_values_cover_every_template_variable(self):
        self.assertEqual(set(self.EXPECTED), {var.name for var in TEMPLATE_VARIABLES})

    def test_every_variable_substitutes_for_score_and_part_by_either_name(self):
        for var in TEMPLATE_VARIABLES:
            score_value, part_value = self.EXPECTED[var.name]
            for name in (var.name, var.name_en):
                with self.subTest(variable=name):
                    settings = NamingSettings(f"[{{{name}}}].pdf")
                    self.assertEqual(name_slot(self.group, SCORE_SLOT, settings).file_name, f"[{score_value}].pdf")
                    self.assertEqual(name_slot(self.group, 2, settings).file_name, f"[{part_value}].pdf")



class TestVoices(unittest.TestCase):
    """分譜依序對應聲部；多於聲部數的分譜不改名，明確傳入的樂器表優先於群組的樂器表"""

    def test_files_beyond_the_voice_count_are_listed_as_extra_and_not_named(self):
        files = _files("a.pdf", "b.pdf", "c.pdf")
        naming = name_group(_group(["Flute", "Oboe"], files=files), NamingSettings("{樂器}.pdf"))
        self.assertEqual(_file_names(naming), ["Flute.pdf", "Oboe.pdf"])
        self.assertEqual([named.file for named in naming.files], files[:2])
        self.assertEqual(naming.extra_files, files[2:])

    def test_group_without_voices_names_only_its_score(self):
        score = FileInfo(os.path.join("in", "s.pdf"), "s.pdf")
        files = _files("a.pdf", "b.pdf")
        naming = name_group(_group([], files=files, score_file=score, score_label="Score"), NamingSettings("{樂器}.pdf"))
        self.assertEqual(_file_names(naming), ["Score.pdf"])
        self.assertEqual(naming.extra_files, files)

    def test_explicit_voices_take_precedence_over_the_group_voices(self):
        group = _group(["Flute", "Oboe"])
        naming = name_group(group, NamingSettings("{序號}-{樂器}.pdf"), voices=["Violin I", "Viola"])
        self.assertEqual(_file_names(naming), ["01-Violin I.pdf", "02-Viola.pdf"])
        self.assertEqual(name_slot(group, 2, NamingSettings("{樂器}.pdf"), voices=["Violin I", "Viola"]).file_name, "Viola.pdf")
        self.assertEqual(group.instruments, ["Flute", "Oboe"])

    def test_empty_explicit_voices_fall_back_to_the_group_voices(self):
        naming = name_group(_group(["Flute"]), NamingSettings("{樂器}.pdf"), voices=[])
        self.assertEqual(_file_names(naming), ["Flute.pdf"])

    def test_named_voices_are_the_voices_that_have_a_part(self):
        files = _files("a.pdf", "b.pdf")
        self.assertEqual(named_voices(_group(["Flute", "Oboe", "Horn"], files=files)), ["Flute", "Oboe"])
        self.assertEqual(named_voices(_group(["Flute"], files=files), voices=["Violin", "Viola", "Cello"]), ["Violin", "Viola"])

    def test_slot_beyond_the_voice_count_is_rejected(self):
        with self.assertRaises(ValueError):
            name_slot(_group(["Flute"]), 2, NamingSettings("{樂器}.pdf"))


class TestSubstitution(unittest.TestCase):
    """命名格式只替換一次：代入的值不再被掃描"""

    def test_piece_name_that_looks_like_a_variable_is_kept_as_is(self):
        group = _group(["Flute"], piece_name="{Instrument}")
        naming = name_group(group, NamingSettings("{曲名} - {樂器}.pdf"))
        self.assertEqual(_file_names(naming), ["{Instrument} - Flute.pdf"])

    def test_movement_name_that_looks_like_a_later_variable_is_kept_as_is(self):
        group = _group(["Flute"], movement_name="{作曲家}", composer="Beethoven")
        naming = name_group(group, NamingSettings("{樂章名稱} - {作曲家}.pdf"))
        self.assertEqual(_file_names(naming), ["{作曲家} - Beethoven.pdf"])

    def test_unknown_variables_are_removed_from_file_and_folder_names(self):
        group = _group(["Flute"], piece_name="Sym")
        name = name_slot(group, 1, NamingSettings("{序號}{foo}-{樂器}.pdf", subfolder_template="{曲名}{foo}"))
        self.assertEqual(name, ("01-Flute.pdf", ("Sym",)))

    def test_substituted_value_that_looks_like_an_unknown_variable_is_kept(self):
        group = _group(["Flute"], piece_name="{foo}")
        self.assertEqual(name_slot(group, 1, NamingSettings("{曲名}.pdf")).file_name, "{foo}.pdf")

    def test_unknown_variables_lists_names_not_in_the_template_variables(self):
        self.assertEqual(unknown_variables("{序號}{foo}-{Instrument}{曲名}{bar}{foo}.pdf"), ["foo", "bar"])
        self.assertEqual(unknown_variables("{序號}-{Instrument}.pdf"), [])


class TestFolders(unittest.TestCase):
    """相對資料夾：子資料夾模板（總譜與分譜共用）之下，分譜可再依分譜存放模式分放"""

    def setUp(self):
        self.group = _group(
            ["Flute", "Horn"], score_file=FileInfo(os.path.join("in", "s.pdf"), "s.pdf"), score_label="Score",
            piece_name="Sym", movement_number="1",
        )

    def _folders(self, settings):
        return [named.name.folders for named in name_group(self.group, settings).files]

    def test_without_subfolder_template_files_stay_in_the_output_location(self):
        self.assertEqual(self._folders(NamingSettings("{樂器}.pdf")), [(), (), ()])

    def test_subfolder_template_puts_score_and_parts_in_one_folder(self):
        settings = NamingSettings("{樂器}.pdf", subfolder_template="{曲名} - {樂章編號}")
        self.assertEqual(self._folders(settings), [("Sym - 1",)] * 3)
        self.assertEqual(
            name_slot(self.group, 1, settings).relative_path(), os.path.join("Sym - 1", "Flute.pdf"),
        )

    def test_parts_mode_puts_only_parts_in_the_parts_folder(self):
        settings = NamingSettings(
            "{樂器}.pdf", subfolder_template="{曲名}", parts_mode=PartsOutputMode.PARTS, parts_folder="Parts",
        )
        self.assertEqual(self._folders(settings), [("Sym",), ("Sym", "Parts"), ("Sym", "Parts")])

    def test_parts_mode_without_a_folder_name_keeps_parts_beside_the_score(self):
        settings = NamingSettings("{樂器}.pdf", parts_mode=PartsOutputMode.PARTS, parts_folder="")
        self.assertEqual(self._folders(settings), [(), (), ()])

    def test_section_mode_puts_each_part_in_its_section_folder(self):
        settings = NamingSettings(
            "{樂器}.pdf", parts_mode=PartsOutputMode.SECTION, sections={"Flute": "Woodwinds", "Horn": "Brass"},
        )
        self.assertEqual(self._folders(settings), [(), ("Woodwinds",), ("Brass",)])

    def test_section_mode_needs_a_section_for_every_named_voice(self):
        settings = NamingSettings("{樂器}.pdf", parts_mode=PartsOutputMode.SECTION, sections={"Flute": "Woodwinds"})
        with self.assertRaises(KeyError):
            name_group(self.group, settings)


class TestSafeOutput(unittest.TestCase):
    """每個產出都安全：清理非法字元、補 .pdf、子資料夾不能是 . 或 .."""

    def test_pdf_extension_is_added_when_the_template_lacks_it(self):
        naming = name_group(_group(["Flute"]), NamingSettings("{序號}-{樂器}"))
        self.assertEqual(_file_names(naming), ["01-Flute.pdf"])

    def test_pdf_extension_already_present_in_any_case_is_kept(self):
        naming = name_group(_group(["Flute"]), NamingSettings("{樂器}.PDF"))
        self.assertEqual(_file_names(naming), ["Flute.PDF"])

    def test_illegal_characters_in_file_and_folder_names_become_underscores(self):
        group = _group(["Violin I/II"], piece_name="Sym: No.5?")
        name = name_slot(group, 1, NamingSettings("{曲名} {樂器}.pdf", subfolder_template="{曲名}"))
        self.assertEqual(name, ("Sym_ No.5_ Violin I_II.pdf", ("Sym_ No.5_",)))

    def test_subfolder_template_resulting_in_dot_or_dot_dot_is_rejected(self):
        for piece_name in (".", "..", " .. "):
            with self.subTest(piece_name=piece_name):
                group = _group(["Flute"], piece_name=piece_name)
                with self.assertRaises(UnsafeFolderNameError) as caught:
                    name_group(group, NamingSettings("{樂器}.pdf", subfolder_template="{曲名}"))
                self.assertEqual(caught.exception.name, piece_name.strip())

    def test_literal_dot_dot_subfolder_template_is_rejected_for_the_score_too(self):
        group = _group([], score_file=FileInfo(os.path.join("in", "s.pdf"), "s.pdf"), score_label="Score")
        with self.assertRaises(UnsafeFolderNameError):
            name_slot(group, SCORE_SLOT, NamingSettings("{樂器}.pdf", subfolder_template=".."))

    def test_parts_folder_named_dot_dot_is_rejected(self):
        settings = NamingSettings("{樂器}.pdf", parts_mode=PartsOutputMode.PARTS, parts_folder="..")
        with self.assertRaises(UnsafeFolderNameError):
            name_group(_group(["Flute"]), settings)

    def test_section_named_dot_dot_is_rejected(self):
        settings = NamingSettings("{樂器}.pdf", parts_mode=PartsOutputMode.SECTION, sections={"Flute": ".."})
        with self.assertRaises(UnsafeFolderNameError):
            name_group(_group(["Flute"]), settings)

    def test_names_with_dots_that_are_not_dot_or_dot_dot_are_allowed(self):
        group = _group(["Flute"], piece_name="Op. 67")
        name = name_slot(group, 1, NamingSettings("{樂器}.pdf", subfolder_template="{曲名}..."))
        self.assertEqual(name.folders, ("Op. 67...",))


class TestVariableLevel(unittest.TestCase):
    """變數是群組層級還是逐檔，由模板變數常數上的層級欄位決定"""

    def test_number_and_instrument_are_per_file_and_the_rest_are_group_level(self):
        expected = {
            "序號": VariableLevel.FILE, "Number": VariableLevel.FILE,
            "樂器": VariableLevel.FILE, "Instrument": VariableLevel.FILE,
            "曲名": VariableLevel.GROUP, "PieceName": VariableLevel.GROUP,
            "樂章編號": VariableLevel.GROUP, "MovementNum": VariableLevel.GROUP,
            "樂章名稱": VariableLevel.GROUP, "MovementName": VariableLevel.GROUP,
            "作曲家": VariableLevel.GROUP, "Composer": VariableLevel.GROUP,
            "曲種": VariableLevel.GROUP, "Genre": VariableLevel.GROUP,
        }
        self.assertEqual({name: variable_level(name) for name in expected}, expected)

    def test_unknown_name_has_no_level(self):
        self.assertIsNone(variable_level("foo"))



class TestSettingsFor(unittest.TestCase):
    """專案與群組的命名設定"""

    def test_project_settings_apply_to_a_group_without_small_template(self):
        project = Project(
            master_template="{樂器}.pdf", use_subfolders=True, subfolder_template="{曲名}",
            parts_output_mode=PartsOutputMode.SECTION, parts_subfolder_name="Parts",
            instrument_sections={"Flute": "Woodwinds"},
        )
        self.assertEqual(settings_for(project, Group(small_template="{序號}.pdf")), NamingSettings(
            "{樂器}.pdf", subfolder_template="{曲名}", parts_mode=PartsOutputMode.SECTION, parts_folder="Parts",
            sections={"Flute": "Woodwinds"},
        ))

    def test_small_template_replaces_the_master_template(self):
        group = Group(use_small_template=True, small_template="{序號}.pdf")
        self.assertEqual(settings_for(Project(master_template="{樂器}.pdf"), group).template, "{序號}.pdf")

    def test_blank_small_template_falls_back_to_the_master_template(self):
        group = Group(use_small_template=True, small_template="")
        self.assertEqual(settings_for(Project(master_template="{樂器}.pdf"), group).template, "{樂器}.pdf")

    def test_subfolder_template_applies_only_when_subfolders_are_on(self):
        project = Project(use_subfolders=False, subfolder_template="{曲名}")
        self.assertEqual(settings_for(project, Group()).subfolder_template, "")


if __name__ == '__main__':
    unittest.main()
