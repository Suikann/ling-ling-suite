# -*- coding: utf-8 -*-
"""
主視窗測試（offscreen）

只測主視窗與服務層的接線，不測畫面；以 QT_QPA_PLATFORM=offscreen 執行，不需要顯示器。
"""
import os
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from PySide6.QtWidgets import QApplication

from core.constants import WORKSPACE_META_FILE
from core.locale import t
from core.models import FileInfo, Group, Project
from services.preferences_service import PreferencesService
from services.project_service import ProjectService
from services.workspace_service import WorkspaceService
from ui.main_window import MainWindow


class TestMainWindowOpenProject(unittest.TestCase):
    """開啟專案後工作區 meta 的所屬專案要跟著更新"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.window = MainWindow(PreferencesService())
        self.workspace = WorkspaceService(self.window.file_service, os.path.join(self.temp_dir, "workspace"))
        self.window.workspace_service = self.workspace

    def tearDown(self):
        self.window.close()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create(self, name, directory=None):
        path = os.path.join(directory or self.temp_dir, name)
        with open(path, "w") as f:
            f.write("dummy")
        return path

    def _project_saved_at(self, folder, name):
        """把一份工作區分譜放進專案並存到指定位置"""
        part = self._create("高笙.pdf", folder)
        project = Project()
        project.groups.append(Group(name="g", files=[FileInfo(part, "高笙.pdf")]))
        path = os.path.join(self.temp_dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        ProjectService().save_project(project, path)
        return path

    def test_open_project_records_its_location_in_workspace_meta(self):
        old_path = os.path.join(self.temp_dir, "old.llproj")
        folder = self.workspace.prepare_folder(self._create("合併譜.pdf"), old_path)
        new_path = self._project_saved_at(folder, os.path.join("moved", "new.llproj"))
        self.window._do_open_project(new_path)
        self.assertEqual(self.workspace.read_meta(folder)["project_path"], new_path)
        self.assertEqual(self.window._project_path, new_path)

    def test_open_project_still_opens_when_meta_cannot_be_written(self):
        folder = self.workspace.prepare_folder(self._create("合併譜.pdf"), "/proj/old.llproj")
        new_path = self._project_saved_at(folder, "new.llproj")
        real_write = self.window.file_service.write_json_atomic

        def failing_write(path, data):
            if os.path.basename(path) == WORKSPACE_META_FILE:
                raise OSError("locked")
            real_write(path, data)

        self.window.file_service.write_json_atomic = failing_write
        self.window._do_open_project(new_path)
        self.assertEqual(self.window._project_path, new_path)
        self.assertEqual(self.workspace.read_meta(folder)["project_path"], "/proj/old.llproj")
        self.assertEqual(self.window._status_label.text(), t("status.workspace_owner_failed", count=1))


if __name__ == '__main__':
    unittest.main()
