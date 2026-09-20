# -*- coding: utf-8 -*-
"""
批次搬移進行中紀錄的儲存

MoveService 執行前寫入、每完成一步更新、結束後刪除；啟動時若仍存在，
代表上次搬移中途被中斷（當機、斷電、強制結束），可據以還原。

使用範例：
    store = MoveJournalStore(file_service)
    store.save(journal)
    journal = store.load()
    store.clear()
"""
import json
import os
from typing import Optional

from core.constants import MOVE_JOURNAL_FILE
from core.models import MoveJournal, MoveStep
from services.file_service import FileService


class MoveJournalStore:
    """進行中紀錄的讀寫"""

    def __init__(self, file_service: FileService):
        self.file_service = file_service

    def save(self, journal: MoveJournal) -> None:
        """原子寫入紀錄（覆蓋既有）"""
        self.file_service.write_json_atomic(MOVE_JOURNAL_FILE, {
            "steps": [self._step_to_json(s) for s in journal.steps],
            "pending": self._step_to_json(journal.pending) if journal.pending else None,
            "complete": journal.complete,
            "created_directories": journal.created_directories,
        })

    def exists(self) -> bool:
        """是否有進行中紀錄（不論內容能否讀取）"""
        return os.path.isfile(MOVE_JOURNAL_FILE)

    def load(self) -> Optional[MoveJournal]:
        """讀取紀錄；不存在時回傳 None

        Raises:
            ValueError: 紀錄內容不是合法的 JSON 或缺少必要欄位
        """
        if not os.path.isfile(MOVE_JOURNAL_FILE):
            return None
        with open(MOVE_JOURNAL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        try:
            return MoveJournal(
                steps=[self._step_from_json(s) for s in data["steps"]],
                pending=self._step_from_json(data["pending"]) if data.get("pending") else None,
                complete=bool(data.get("complete", False)),
                created_directories=data.get("created_directories", []),
            )
        except (KeyError, TypeError) as e:
            raise ValueError(str(e)) from e

    @staticmethod
    def _step_to_json(step: MoveStep) -> dict:
        return {"index": step.index, "source": step.source, "target": step.target}

    @staticmethod
    def _step_from_json(data: dict) -> MoveStep:
        return MoveStep(index=data["index"], source=data["source"], target=data["target"])

    def clear(self) -> None:
        """刪除紀錄；不存在時不拋出"""
        try:
            os.remove(MOVE_JOURNAL_FILE)
        except FileNotFoundError:
            pass
