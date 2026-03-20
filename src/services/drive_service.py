# -*- coding: utf-8 -*-
"""
Google Drive 檔案服務

透過 Google Drive API 管理譜庫的 PDF 檔案。

使用範例：
    from services.drive_service import DriveService
    service = DriveService(credentials)
    files = service.list_pdfs_in_folder(folder_id)
"""
from typing import Dict, List, Optional
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials


class DriveService:
    """Google Drive 檔案管理服務"""

    _PDF_MIME = "application/pdf"
    _FOLDER_MIME = "application/vnd.google-apps.folder"

    def __init__(self, credentials: Credentials):
        self._credentials = credentials
        self._service = build("drive", "v3", credentials=credentials)

    def list_subfolders(self, folder_id: str) -> List[Dict[str, str]]:
        """列出資料夾內的子資料夾

        Args:
            folder_id: Google Drive 資料夾 ID

        Returns:
            子資料夾清單，每項包含 id 與 name
        """
        query = (
            f"'{folder_id}' in parents"
            f" and mimeType = '{self._FOLDER_MIME}'"
            " and trashed = false"
        )
        return self._query_files(query)

    def list_pdfs_in_folder(self, folder_id: str) -> List[Dict[str, str]]:
        """列出資料夾內的 PDF 檔案

        Args:
            folder_id: Google Drive 資料夾 ID

        Returns:
            PDF 檔案清單，每項包含 id 與 name
        """
        query = (
            f"'{folder_id}' in parents"
            f" and mimeType = '{self._PDF_MIME}'"
            " and trashed = false"
        )
        return self._query_files(query)

    def get_file_info(self, file_id: str) -> Optional[Dict[str, str]]:
        """取得檔案基本資訊

        Args:
            file_id: Google Drive 檔案 ID

        Returns:
            檔案資訊（id, name, mimeType, parents），找不到時回傳 None
        """
        try:
            result = self._service.files().get(
                fileId=file_id,
                fields="id, name, mimeType, parents",
            ).execute()
            return result
        except Exception:
            return None

    def rename_file(self, file_id: str, new_name: str) -> bool:
        """重新命名檔案

        Args:
            file_id: Google Drive 檔案 ID
            new_name: 新檔案名稱

        Returns:
            是否成功
        """
        try:
            self._service.files().update(
                fileId=file_id,
                body={"name": new_name},
            ).execute()
            return True
        except Exception:
            return False

    def move_file(
        self, file_id: str, new_parent_id: str, old_parent_id: str = "",
    ) -> bool:
        """移動檔案至另一個資料夾

        Args:
            file_id: Google Drive 檔案 ID
            new_parent_id: 目標資料夾 ID
            old_parent_id: 原資料夾 ID（未提供時自動查詢）

        Returns:
            是否成功
        """
        try:
            if not old_parent_id:
                info = self.get_file_info(file_id)
                if info and info.get("parents"):
                    old_parent_id = info["parents"][0]
            self._service.files().update(
                fileId=file_id,
                addParents=new_parent_id,
                removeParents=old_parent_id,
            ).execute()
            return True
        except Exception:
            return False

    def create_folder(
        self, name: str, parent_id: str = "",
    ) -> Optional[str]:
        """建立資料夾

        Args:
            name: 資料夾名稱
            parent_id: 父資料夾 ID

        Returns:
            新資料夾的 ID，失敗時回傳 None
        """
        body = {
            "name": name,
            "mimeType": self._FOLDER_MIME,
        }
        if parent_id:
            body["parents"] = [parent_id]
        try:
            result = self._service.files().create(
                body=body, fields="id",
            ).execute()
            return result.get("id")
        except Exception:
            return None

    def find_folder_by_name(
        self, name: str, parent_id: str = "",
    ) -> Optional[str]:
        """依名稱在指定資料夾下搜尋子資料夾

        Args:
            name: 資料夾名稱
            parent_id: 父資料夾 ID

        Returns:
            找到的資料夾 ID，找不到時回傳 None
        """
        escaped = name.replace("'", "\\'")
        query = (
            f"name = '{escaped}'"
            f" and mimeType = '{self._FOLDER_MIME}'"
            " and trashed = false"
        )
        if parent_id:
            query += f" and '{parent_id}' in parents"
        results = self._query_files(query)
        return results[0]["id"] if results else None

    def scan_library_folder(
        self, root_folder_id: str,
    ) -> List[Dict]:
        """掃描譜庫根目錄結構

        預期結構為兩層：作曲家資料夾 > 曲目資料夾 > PDF 檔案。

        Args:
            root_folder_id: 譜庫根目錄的 Google Drive 資料夾 ID

        Returns:
            掃描結果清單，每項包含資料夾資訊與 PDF 檔案
        """
        results = []
        top_folders = self.list_subfolders(root_folder_id)
        for top in top_folders:
            sub_folders = self.list_subfolders(top["id"])
            if sub_folders:
                for sub in sub_folders:
                    pdfs = self.list_pdfs_in_folder(sub["id"])
                    results.append({
                        "composer_folder": top["name"],
                        "piece_folder": sub["name"],
                        "folder_id": sub["id"],
                        "files": pdfs,
                    })
            else:
                pdfs = self.list_pdfs_in_folder(top["id"])
                if pdfs:
                    results.append({
                        "composer_folder": "",
                        "piece_folder": top["name"],
                        "folder_id": top["id"],
                        "files": pdfs,
                    })
        return results

    def _query_files(self, query: str) -> List[Dict[str, str]]:
        """執行 Drive API 檔案查詢

        Args:
            query: Drive API 查詢字串

        Returns:
            檔案清單，每項包含 id 與 name
        """
        items = []
        page_token = None
        while True:
            response = self._service.files().list(
                q=query,
                fields="nextPageToken, files(id, name)",
                orderBy="name",
                pageSize=100,
                pageToken=page_token,
            ).execute()
            items.extend(response.get("files", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return items
