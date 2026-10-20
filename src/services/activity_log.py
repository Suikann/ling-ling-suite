# -*- coding: utf-8 -*-
"""
紀錄檔

譜庫的讀寫失敗與重要動作（存入譜庫、撤銷存入、清除本機檔案、下載、發譜）都寫進使用者資料目錄下的同一個資料夾，
一天一個檔（<年-月-日>.log），一筆的內容即 LogEntry.text()，筆與筆之間空一行。保留 LOG_RETENTION_DAYS 天，
程式啟動、取得單一實例鎖後呼叫 remove_expired 刪掉更早的檔。紀錄檔寫不進去或刪不掉都不影響呼叫端。

使用範例：
    from services.activity_log import ActivityLog
    log = ActivityLog()
    log.remove_expired()
    log.record(failure.log_entry())
    log.record(LogEntry("存入譜庫", piece_title="春江花月夜", file_name="春江花月夜_中阮.pdf"))
"""
import datetime
import os
import threading
from typing import Callable, Optional

from core.constants import LOG_DIR, LOG_FILE_EXTENSION, LOG_RETENTION_DAYS
from core.log_entry import LogEntry
from services.file_service import FileService

_DATE_FORMAT = "%Y-%m-%d"


class ActivityLog:
    """寫紀錄檔、刪掉過期的紀錄檔；可從背景執行緒呼叫"""

    def __init__(
        self, file_service: Optional[FileService] = None, folder: str = LOG_DIR,
        clock: Callable[[], datetime.datetime] = datetime.datetime.now,
    ):
        """
        Args:
            file_service: 檔案服務；省略時建立預設的
            folder: 紀錄檔資料夾
            clock: 取得現在時間的函式，決定哪些檔過期（測試傳入固定的時間）
        """
        self.file_service = file_service or FileService()
        self.folder = folder
        self._clock = clock
        self._lock = threading.Lock()

    def record(self, entry: LogEntry) -> None:
        """把一筆附加到它那一天的紀錄檔；寫不進去時略過

        Args:
            entry: 紀錄檔的一筆
        """
        path = os.path.join(self.folder, entry.time.strftime(_DATE_FORMAT) + LOG_FILE_EXTENSION)
        with self._lock:
            try:
                self.file_service.append_text(path, entry.text() + "\n\n")
            except OSError:
                pass

    def remove_expired(self) -> None:
        """刪掉日期早於保留天數的紀錄檔；檔名不是日期的檔不動，資料夾不存在或刪不掉時略過"""
        oldest_kept = self._clock().date() - datetime.timedelta(days=LOG_RETENTION_DAYS)
        with self._lock:
            try:
                paths = self.file_service.list_files(self.folder, LOG_FILE_EXTENSION)
            except OSError:
                return
            for path in paths:
                name = os.path.basename(path)[:-len(LOG_FILE_EXTENSION)]
                try:
                    day = datetime.datetime.strptime(name, _DATE_FORMAT).date()
                except ValueError:
                    continue
                if day < oldest_kept:
                    try:
                        self.file_service.remove_file(path)
                    except OSError:
                        pass
