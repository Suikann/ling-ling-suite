# -*- coding: utf-8 -*-
"""
檔案服務

提供檔案系統操作：列出、重新命名、建立目錄、原子寫入等。
"""
import errno
import json
import os
import shutil
import time
from typing import Any, List

from core.constants import (
    ATOMIC_WRITE_RETRIES, ATOMIC_WRITE_RETRY_INTERVAL, ATOMIC_WRITE_TEMP_SUFFIX,
)


class FileService:
    """檔案系統操作服務"""

    def rename_file(self, old_path: str, new_path: str) -> None:
        """重新命名（搬移）檔案並更新修改日期

        目的地已有另一個檔案時拒絕（POSIX 的 os.rename 會靜默覆蓋，Windows 則拋出，
        這裡讓兩者一致）；同一個檔案只改大小寫不算。
        同一磁碟內直接 rename；跨磁碟時先複製到 `<new_path>.part`，
        成功後再就位、刪除來源。任一步失敗都不會在目的地留下半成品。

        Args:
            old_path: 原始檔案路徑
            new_path: 新檔案路徑

        Raises:
            FileExistsError: 目的地已有另一個檔案
        """
        if self._is_another_file(old_path, new_path):
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), new_path)
        try:
            os.rename(old_path, new_path)
        except OSError as e:
            if e.errno != errno.EXDEV:
                raise
            self._move_across_devices(old_path, new_path)
        os.utime(new_path)

    @staticmethod
    def _is_another_file(old_path: str, new_path: str) -> bool:
        """目的地是否已有不同於來源的檔案（來源不存在時交由 os.rename 回報）"""
        if not os.path.lexists(new_path) or not os.path.lexists(old_path):
            return False
        try:
            return not os.path.samefile(old_path, new_path)
        except OSError:
            return True

    @staticmethod
    def _move_across_devices(old_path: str, new_path: str) -> None:
        """以「複製到 .part 再就位」的方式跨磁碟搬移檔案"""
        part_path = new_path + ".part"
        try:
            shutil.copy2(old_path, part_path)
            os.replace(part_path, new_path)
        except BaseException:
            if os.path.exists(part_path):
                os.remove(part_path)
            raise
        os.remove(old_path)

    def write_json_atomic(self, path: str, data: Any) -> None:
        """將資料序列化為 JSON 並原子寫入

        Args:
            path: 目標檔案路徑
            data: 可序列化為 JSON 的資料
        """
        self.write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))

    def write_text_atomic(self, path: str, text: str) -> None:
        """將文字原子寫入：磁碟上只會是完整的舊版或完整的新版

        父目錄不存在時先建立；先寫到同資料夾的暫名並 fsync，再以 os.replace() 就位。
        任一步失敗時清掉暫名（清不掉也不蓋過原始例外）、不動原檔。

        Args:
            path: 目標檔案路徑
            text: 要寫入的文字（UTF-8）
        """
        parent = os.path.dirname(path)
        if parent:
            self.create_directory(parent)
        temp_path = path + ATOMIC_WRITE_TEMP_SUFFIX
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            self._replace_with_retry(temp_path, path)
        except BaseException:
            self._remove_quietly(temp_path)
            raise

    def remove_atomic_residue(self, path: str) -> None:
        """清除原子寫入在目標旁留下的暫名（寫到一半當機的殘留）

        殘留不存在時什麼都不做；目標檔本身不動。

        Args:
            path: 原子寫入的目標檔案路徑
        """
        self._remove_quietly(path + ATOMIC_WRITE_TEMP_SUFFIX)

    @staticmethod
    def _remove_quietly(path: str) -> None:
        """移除檔案；不存在或移除失敗都不拋出"""
        try:
            os.remove(path)
        except OSError:
            pass

    @staticmethod
    def _replace_with_retry(src: str, dst: str) -> None:
        """以 os.replace() 就位；遇 PermissionError（短暫鎖定）時重試數次再拋出"""
        for attempt in range(ATOMIC_WRITE_RETRIES + 1):
            try:
                os.replace(src, dst)
                return
            except PermissionError:
                if attempt == ATOMIC_WRITE_RETRIES:
                    raise
                time.sleep(ATOMIC_WRITE_RETRY_INTERVAL)

    def create_directory(self, path: str) -> None:
        """建立目錄（含父目錄）

        Args:
            path: 目錄路徑
        """
        os.makedirs(path, exist_ok=True)

    def directory_exists(self, path: str) -> bool:
        """檢查目錄是否存在"""
        return os.path.isdir(path)

    def file_exists(self, path: str) -> bool:
        """檢查檔案是否存在

        Args:
            path: 檔案路徑

        Returns:
            是否存在
        """
        return os.path.isfile(path)

    def file_exists_exact(self, path: str) -> bool:
        """檢查檔案是否存在且檔名大小寫完全相同

        不分大小寫的檔案系統上 isfile 分不出只改大小寫的檔案，這裡改比對目錄列表的實際名稱。

        Args:
            path: 檔案路徑

        Returns:
            是否存在且名稱完全相同
        """
        directory, name = os.path.split(path)
        try:
            with os.scandir(directory or ".") as entries:
                return any(entry.name == name and entry.is_file() for entry in entries)
        except OSError:
            return False

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

    def delete_file(self, path: str) -> None:
        """將檔案移至資源回收桶"""
        self._move_to_trash(path)

    def delete_directory(self, path: str) -> None:
        """將整個目錄移至資源回收桶"""
        self._move_to_trash(path)

    @staticmethod
    def _move_to_trash(path: str) -> None:
        from send2trash import send2trash
        send2trash(path)

    def remove_empty_directory(self, path: str) -> None:
        """移除空目錄（若為空）

        Args:
            path: 目錄路徑
        """
        try:
            os.rmdir(path)
        except OSError:
            pass
