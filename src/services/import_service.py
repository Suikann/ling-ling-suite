# -*- coding: utf-8 -*-
"""
匯入服務

提供檔案與資料夾的匯入功能，自動建立群組。
"""
import os
from typing import List, Tuple
from core.models import FileInfo, Group
from core.template_engine import detect_piece_name
from services.file_service import FileService


class ImportService:
    """檔案匯入服務"""

    def __init__(self, file_service: FileService):
        self.file_service = file_service

    def import_files(self, paths: List[str]) -> List[FileInfo]:
        """匯入多個檔案

        Args:
            paths: 檔案路徑清單

        Returns:
            FileInfo 清單
        """
        result = []
        for path in paths:
            if path.lower().endswith('.pdf') and os.path.isfile(path):
                result.append(FileInfo(
                    original_path=path,
                    display_name=os.path.basename(path),
                ))
        return result

    def import_folder(self, folder: str) -> Tuple[List[Group], List[FileInfo]]:
        """匯入資料夾

        以「是否有含 PDF 的子資料夾」決定模式：
        - 有：每個含 PDF 的子資料夾各建立一個群組，根目錄 PDF 歸入未分組
        - 無：根目錄所有 PDF 歸為一個群組

        僅掃描一層子資料夾，不遞迴深入。僅處理 .pdf 檔案。

        Args:
            folder: 資料夾路徑

        Returns:
            (群組清單, 未分組檔案清單) 的元組
        """
        groups = []
        ungrouped = []
        sub_groups = []
        for subdir in self.file_service.list_subdirectories(folder):
            pdfs = self.file_service.list_pdf_files(subdir)
            if not pdfs:
                continue
            files = self.import_files(pdfs)
            dir_name = os.path.basename(subdir)
            filenames = [f.display_name for f in files]
            detected_name = detect_piece_name(filenames) or dir_name
            sub_groups.append(Group(
                name=dir_name,
                files=files,
                piece_name=detected_name,
            ))
        root_pdfs = self.file_service.list_pdf_files(folder)
        if sub_groups:
            groups.extend(sub_groups)
            if root_pdfs:
                ungrouped.extend(self.import_files(root_pdfs))
        elif root_pdfs:
            files = self.import_files(root_pdfs)
            dir_name = os.path.basename(folder)
            filenames = [f.display_name for f in files]
            detected_name = detect_piece_name(filenames) or dir_name
            groups.append(Group(
                name=dir_name,
                files=files,
                piece_name=detected_name,
            ))
        return groups, ungrouped
