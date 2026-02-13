# -*- coding: utf-8 -*-
"""
檔案服務

提供檔案系統操作：列出、重新命名、建立目錄等。
"""
import os
import shutil
from typing import List


class FileService:
    """檔案系統操作服務"""

    def rename_file(self, old_path: str, new_path: str) -> None:
        """重新命名檔案

        Args:
            old_path: 原始檔案路徑
            new_path: 新檔案路徑
        """
        os.rename(old_path, new_path)

    def create_directory(self, path: str) -> None:
        """建立目錄（含父目錄）

        Args:
            path: 目錄路徑
        """
        os.makedirs(path, exist_ok=True)

    def file_exists(self, path: str) -> bool:
        """檢查檔案是否存在

        Args:
            path: 檔案路徑

        Returns:
            是否存在
        """
        return os.path.isfile(path)

    def list_pdf_files(self, directory: str) -> List[str]:
        """列出目錄內的 PDF 檔案

        Args:
            directory: 目錄路徑

        Returns:
            PDF 檔案的完整路徑清單，按檔名排序
        """
        files = []
        for entry in os.scandir(directory):
            if entry.is_file() and entry.name.lower().endswith('.pdf'):
                files.append(entry.path)
        files.sort(key=lambda p: os.path.basename(p).lower())
        return files

    def has_subdirectories(self, directory: str) -> bool:
        """檢查目錄是否包含子目錄

        Args:
            directory: 目錄路徑

        Returns:
            是否包含子目錄
        """
        for entry in os.scandir(directory):
            if entry.is_dir():
                return True
        return False

    def list_subdirectories(self, directory: str) -> List[str]:
        """列出目錄內的子目錄

        Args:
            directory: 目錄路徑

        Returns:
            子目錄的完整路徑清單，按名稱排序
        """
        dirs = []
        for entry in os.scandir(directory):
            if entry.is_dir():
                dirs.append(entry.path)
        dirs.sort(key=lambda p: os.path.basename(p).lower())
        return dirs

    def remove_empty_directory(self, path: str) -> None:
        """移除空目錄（若為空）

        Args:
            path: 目錄路徑
        """
        try:
            os.rmdir(path)
        except OSError:
            pass
