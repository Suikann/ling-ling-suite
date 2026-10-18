# -*- coding: utf-8 -*-
"""
發譜服務

發譜到 HuaXia（spec #82、ADR 0003）按下「發譜」之後、開啟發譜預覽之前的檢查：

- 專案層級（project_blocks，擋下整個發譜）：專案有未存檔的改動（只在按下時檢查）、專案不是國樂團
  （HuaXia 的聲部組只接受國樂團那五組）。
- 群組層級（check_groups，擋下那個群組，預覽裡寫出原因）：還沒重新命名（core.naming.is_renamed，與存入譜庫同一條規則；
  發譜從輸出位置讀檔）、本專案有的聲部沒填最少人數、樂器表有重名的列。未同步的值（還沒進譜庫）不擋，只標出。
- HuaXia 那一側（PublishService.prepare，連網，要在背景執行緒呼叫）：重查幹部身分（不是幹部就登出）、
  HuaXia 發譜函式的介面版本（沒有或不符時整個發譜不能用），兩者都過了才讀可以挑的曲目（未封存的）。

「只在譜庫」的檔（清除本機檔案後只剩譜庫那份，#107）還沒有對應的狀態，之後在 check_groups 的群組結果加上跳過的檔。

使用範例：
    from services.publish_service import PublishService, check_groups, project_blocks
    if not project_blocks(project):
        readiness = PublishService(huaxia_auth).prepare()   # 背景執行緒
        if readiness.ready:
            checks = check_groups(project)
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from core.constants import OrchestraType
from core.models import CatalogChange, Group, Project
from core.naming import is_renamed
from core.voice_names import duplicate_rows
from services.huaxia_auth_service import HuaXiaAuthService, NotOfficer
from services.huaxia_client import PUBLISH_INTERFACE_VERSION, HuaXiaPart, HuaXiaPiece


class ProjectBlock(Enum):
    """擋下整個發譜的原因"""

    UNSAVED = "unsaved"                              # 專案有未存檔的改動
    NOT_CHINESE_ORCHESTRA = "not_chinese_orchestra"  # 專案不是國樂團


class FunctionProblem(Enum):
    """HuaXia 發譜函式不能用的原因"""

    MISSING = "missing"                    # HuaXia 沒有發譜函式（合併請求還沒合併生效）
    VERSION_MISMATCH = "version_mismatch"  # 介面版本與這版泠靈要的不同


@dataclass(frozen=True)
class PublishReadiness:
    """按下發譜時 HuaXia 那一側的檢查結果（PublishService.prepare）

    Attributes:
        not_officer: 不是幹部時的分類（已登出）；是幹部時為 None
        function_problem: 發譜函式不能用的原因；能用時為 None
        function_version: HuaXia 回報的介面版本；沒有發譜函式或沒查到時為 None
        pieces: 可以挑的曲目（未封存，依曲名）；不能發時為空
        parts: 可以挑的曲目上已有的聲部，以曲目編號為鍵（依 HuaXia 的排序）；沒有聲部的曲目不在裡面
        discord_name: 登入的 Discord 帳號名稱
    """

    not_officer: Optional[NotOfficer] = None
    function_problem: Optional[FunctionProblem] = None
    function_version: Optional[int] = None
    pieces: List[HuaXiaPiece] = field(default_factory=list)
    parts: Dict[str, List[HuaXiaPart]] = field(default_factory=dict)
    discord_name: str = ""

    @property
    def ready(self) -> bool:
        """可以開啟發譜預覽"""
        return self.not_officer is None and self.function_problem is None


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
            not_renamed=not is_renamed(project, group),
            unfilled=[
                voice for voice, present, headcount in zip(group.instruments, group.present, group.headcounts)
                if present and not headcount.filled
            ],
            duplicate_rows=duplicate_rows(group.instruments),
            unsynced=project.unsynced_changes(group),
        )
        for group in project.groups
    ]


class PublishService:
    """發譜到 HuaXia：按下發譜時 HuaXia 那一側的檢查"""

    def __init__(self, auth: HuaXiaAuthService):
        """
        Args:
            auth: HuaXia 登入服務（全程式一份）；請求經它的用戶端送出
        """
        self._auth = auth
        self._client = auth.client

    def prepare(self) -> PublishReadiness:
        """重查幹部身分、查發譜函式的版本，都過了才讀可以挑的曲目與它們的聲部；不是幹部時已登出

        Returns:
            檢查結果；不能發時寫明原因

        Raises:
            HuaXiaError: 沒有登入、登入已失效或連不上 HuaXia
        """
        not_officer = self._auth.recheck_officer()
        if not_officer is not None:
            return PublishReadiness(not_officer=not_officer)
        token = self._auth.access_token()
        version = self._client.publish_interface_version(token)
        if version is None:
            return PublishReadiness(function_problem=FunctionProblem.MISSING)
        if version != PUBLISH_INTERFACE_VERSION:
            return PublishReadiness(function_problem=FunctionProblem.VERSION_MISMATCH, function_version=version)
        pieces = self._client.pieces(token)
        parts: Dict[str, List[HuaXiaPart]] = {piece.id: [] for piece in pieces}
        for part in self._client.piece_parts(token):
            if part.piece_id in parts:
                parts[part.piece_id].append(part)
        return PublishReadiness(
            function_version=version, pieces=pieces, parts={piece: found for piece, found in parts.items() if found},
            discord_name=self._auth.status().discord_name,
        )
