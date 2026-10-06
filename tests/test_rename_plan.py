# -*- coding: utf-8 -*-
"""
重新命名計畫測試

計畫的檔名與相對資料夾由命名模組決定（見 test_naming）；這裡只測重新命名服務的接線：
命名結果接在輸出位置（沒指定時為來源檔所在的資料夾）之下，以及聲部組第一次被用到時寫進專案。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from core.constants import PartsOutputMode
from core.locale import get_locale, set_locale
from core.models import FileInfo, Group, Project
from core.naming import UnsafeFolderNameError
from services.file_service import FileService
from services.rename_service import RenameService

IN_DIR = os.path.abspath("in")
OUT_DIR = os.path.abspath("out")


def _info(name):
    return FileInfo(os.path.join(IN_DIR, name), name)


def _group(**fields):
    """總譜 s.pdf、分譜 fl.pdf 與 hn.pdf 對應 Flute 與 Horn 的群組"""
    return Group(
        name="g", files=[_info("fl.pdf"), _info("hn.pdf")], instruments=["Flute", "Horn"],
        score_file=_info("s.pdf"), score_label="Score", piece_name="Sym", **fields,
    )


class TestRenamePlan(unittest.TestCase):
    """RenameService.generate_rename_plan"""

    def setUp(self):
        self.service = RenameService(FileService())

    def _plan(self, project):
        return [(e.original_path, e.new_path) for e in self.service.generate_rename_plan(project)]

    def test_without_output_directory_files_are_renamed_beside_their_source(self):
        project = Project(master_template="{序號}-{樂器}.pdf", groups=[_group()])
        self.assertEqual(self._plan(project), [
            (os.path.join(IN_DIR, "s.pdf"), os.path.join(IN_DIR, "00-Score.pdf")),
            (os.path.join(IN_DIR, "fl.pdf"), os.path.join(IN_DIR, "01-Flute.pdf")),
            (os.path.join(IN_DIR, "hn.pdf"), os.path.join(IN_DIR, "02-Horn.pdf")),
        ])

    def test_named_folders_are_placed_under_the_output_directory(self):
        project = Project(
            master_template="{樂器}.pdf", output_directory=OUT_DIR, use_subfolders=True, subfolder_template="{曲名}",
            parts_output_mode=PartsOutputMode.PARTS, parts_subfolder_name="Parts", groups=[_group()],
        )
        self.assertEqual([new for _old, new in self._plan(project)], [
            os.path.join(OUT_DIR, "Sym", "Score.pdf"),
            os.path.join(OUT_DIR, "Sym", "Parts", "Flute.pdf"),
            os.path.join(OUT_DIR, "Sym", "Parts", "Horn.pdf"),
        ])

    def test_plan_entries_carry_their_group_id(self):
        group = _group()
        plan = self.service.generate_rename_plan(Project(groups=[group]))
        self.assertEqual({e.group_id for e in plan}, {group.id})

    def test_files_beyond_the_voice_count_are_left_out(self):
        group = _group()
        group.files.append(_info("extra.pdf"))
        project = Project(master_template="{樂器}.pdf", groups=[group])
        self.assertNotIn(os.path.join(IN_DIR, "extra.pdf"), [old for old, _new in self._plan(project)])

    def test_legacy_parts_subfolder_flag_does_not_override_the_parts_output_mode(self):
        project = Project(
            master_template="{樂器}.pdf", output_directory=OUT_DIR, use_parts_subfolder=True,
            parts_output_mode=PartsOutputMode.ROOT, groups=[_group()],
        )
        self.assertEqual(self._plan(project)[1][1], os.path.join(OUT_DIR, "Flute.pdf"))

    def test_project_level_legacy_instruments_are_not_used_for_naming(self):
        group = Group(name="g", files=[_info("fl.pdf")])
        project = Project(master_template="{樂器}.pdf", instruments=["Flute"], groups=[group])
        self.assertEqual(self._plan(project), [])

    def test_only_selected_groups_are_planned(self):
        unsafe = Group(name="unsafe", files=[_info("x.pdf")], instruments=["Oboe"], piece_name="..")
        chosen = _group()
        project = Project(
            master_template="{樂器}.pdf", use_subfolders=True, subfolder_template="{曲名}", groups=[unsafe, chosen],
        )
        plan = self.service.generate_rename_plan(project, group_ids={chosen.id})
        self.assertEqual([e.original_path for e in plan], [
            os.path.join(IN_DIR, "s.pdf"), os.path.join(IN_DIR, "fl.pdf"), os.path.join(IN_DIR, "hn.pdf"),
        ])

    def test_unsafe_folder_name_is_rejected(self):
        project = Project(
            master_template="{樂器}.pdf", output_directory=OUT_DIR,
            parts_output_mode=PartsOutputMode.PARTS, parts_subfolder_name="..", groups=[_group()],
        )
        with self.assertRaises(UnsafeFolderNameError):
            self.service.generate_rename_plan(project)



class TestSectionsWrittenOnFirstUse(unittest.TestCase):
    """聲部組資料夾模式下，還沒有聲部組的聲部依當時的介面語言寫進專案的編制設定"""

    def setUp(self):
        self.service = RenameService(FileService())
        self.addCleanup(set_locale, get_locale())

    def _project(self, **fields):
        project = Project(
            master_template="{樂器}.pdf", output_directory=OUT_DIR, parts_output_mode=PartsOutputMode.SECTION,
            groups=[_group()], **fields,
        )
        project.mark_saved()
        return project

    def test_missing_sections_are_written_in_the_current_language(self):
        set_locale("en")
        project = self._project(instrument_sections={"Horn": "Corni"})
        new_paths = [e.new_path for e in self.service.generate_rename_plan(project)]
        self.assertEqual(project.instrument_sections, {"Flute": "Woodwinds", "Horn": "Corni"})
        self.assertEqual(new_paths[1:], [
            os.path.join(OUT_DIR, "Woodwinds", "Flute.pdf"), os.path.join(OUT_DIR, "Corni", "Horn.pdf"),
        ])
        self.assertTrue(project.is_modified())

    def test_written_sections_stay_after_switching_language(self):
        set_locale("en")
        project = self._project()
        self.service.generate_rename_plan(project)
        set_locale("zh_TW")
        new_paths = [e.new_path for e in self.service.generate_rename_plan(project)]
        self.assertEqual(new_paths[1], os.path.join(OUT_DIR, "Woodwinds", "Flute.pdf"))

    def test_chinese_interface_writes_chinese_sections(self):
        set_locale("zh_TW")
        project = self._project()
        self.service.generate_rename_plan(project)
        self.assertEqual(project.instrument_sections, {"Flute": "木管", "Horn": "銅管"})

    def test_voice_without_a_known_family_goes_to_the_other_section(self):
        set_locale("en")
        project = self._project()
        project.set_instruments(project.groups[0], ["Flute", "Theremin"])
        self.service.generate_rename_plan(project)
        self.assertEqual(project.instrument_sections["Theremin"], "Other")

    def test_blank_section_is_replaced_by_the_default(self):
        set_locale("zh_TW")
        project = self._project(instrument_sections={"Flute": "  ", "Horn": "銅管"})
        self.service.generate_rename_plan(project)
        self.assertEqual(project.instrument_sections, {"Flute": "木管", "Horn": "銅管"})

    def test_only_voices_of_selected_groups_are_written(self):
        set_locale("en")
        project = self._project()
        other = Group(name="other", files=[_info("x.pdf")], instruments=["Oboe"])
        project.add_groups([other], score_label="Score")
        self.service.generate_rename_plan(project, group_ids={project.groups[0].id})
        self.assertNotIn("Oboe", project.instrument_sections)

    def test_other_modes_do_not_touch_the_project(self):
        for mode in (PartsOutputMode.ROOT, PartsOutputMode.PARTS):
            with self.subTest(mode=mode):
                project = self._project()
                project.set_output_settings(parts_output_mode=mode)
                project.mark_saved()
                self.service.generate_rename_plan(project)
                self.assertEqual(project.instrument_sections, {})
                self.assertFalse(project.is_modified())


if __name__ == '__main__':
    unittest.main()
