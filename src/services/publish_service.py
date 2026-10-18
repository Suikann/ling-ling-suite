# -*- coding: utf-8 -*-
"""
發譜服務

發譜到 HuaXia（spec #82、ADR 0003）按下「發譜」之後、開啟發譜預覽之前的檢查：

- 專案層級（project_blocks，擋下整個發譜）：專案有未存檔的改動（只在按下時檢查）、專案不是國樂團
  （HuaXia 的聲部組只接受國樂團那五組）。
- 群組層級（check_groups，擋下那個群組，預覽裡寫出原因）：還沒重新命名（總譜與分譜不全在命名格式算出的位置，
  發譜從輸出位置讀檔）、本專案有的聲部沒填最少人數、樂器表有重名的列。未同步的值（還沒進譜庫）不擋，只標出。

「只在譜庫」的檔（清除本機檔案後只剩譜庫那份，#107）還沒有對應的狀態，之後在 check_groups 的群組結果加上跳過的檔。

使用範例：
    from services.publish_service import check_groups, project_blocks
    if not project_blocks(project):
        checks = check_groups(project)
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List

from core.constants import OrchestraType
from core.models import CatalogChange, Group, Project
from core.naming import UnsafeFolderNameError
from core.paths import same_path
from core.voice_names import duplicate_rows
from services.rename_service import generate_rename_plan


class ProjectBlock(Enum):
    """擋下整個發譜的原因"""

    UNSAVED = "unsaved"                              # 專案有未存檔的改動
    NOT_CHINESE_ORCHESTRA = "not_chinese_orchestra"  # 專案不是國樂團


@dataclass(frozen=True)
class GroupCheck:
    """一個群組發譜前的檢查結果

    Attributes:
        group: 群組
        not_renamed: 還沒重新命名（總譜與分譜不全在命名格式算出的位置，或沒有任何要發的檔）
        unfilled: 本專案有、沒填最少人數的聲部，依樂器表順序
        duplicate_rows: 樂器表裡重名的列（從 0 起算）
        unsynced: 還沒進譜庫的值（只標出，不擋）
    """

    group: Group
    not_renamed: bool = False
    unfilled: List[str] = field(default_factory=list)
    duplicate_rows: List[int] = field(default_factory=list)
    unsynced: List[CatalogChange] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        """這個群組不能發"""
        return self.not_renamed or bool(self.unfilled) or bool(self.duplicate_rows)


def project_blocks(project: Project) -> List[ProjectBlock]:
    """擋下整個發譜的原因（依檢查順序）；沒有時可以發"""
    blocks = []
    if project.is_modified():
        blocks.append(ProjectBlock.UNSAVED)
    if project.orchestra_type is not OrchestraType.CHINESE:
        blocks.append(ProjectBlock.NOT_CHINESE_ORCHESTRA)
    return blocks


def check_groups(project: Project) -> List[GroupCheck]:
    """專案每個群組（依專案中的順序）發譜前的檢查結果；不改專案"""
    return [
        GroupCheck(
            group=group,
            not_renamed=not _renamed(project, group),
            unfilled=[
                voice for voice, present, headcount in zip(group.instruments, group.present, group.headcounts)
                if present and not headcount.filled
            ],
            duplicate_rows=duplicate_rows(group.instruments),
            unsynced=project.unsynced_changes(group),
        )
        for group in project.groups
    ]


def _renamed(project: Project, group: Group) -> bool:
    """群組的總譜與分譜是否都已在命名格式算出的位置（重新命名過、之後沒改命名設定）；沒有要發的檔時為否"""
    try:
        entries = generate_rename_plan(project, {group.id}).entries
    except (KeyError, UnsafeFolderNameError):
        return False
    return bool(entries) and all(same_path(entry.original_path, entry.new_path) for entry in entries)
