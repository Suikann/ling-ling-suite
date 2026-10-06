# -*- coding: utf-8 -*-
"""
專案模型測試

純 Python，不需要 Qt、不碰磁碟：檔案走訪與依路徑套用結果。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from core.models import FileInfo, Group, Project, UndoMapping
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


if __name__ == '__main__':
    unittest.main()
