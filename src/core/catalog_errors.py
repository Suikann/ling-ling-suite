# -*- coding: utf-8 -*-
"""
譜庫的錯誤類別

譜庫讀寫失敗時分成六類（斷網、要重新登入 Google、沒有權限、找不到、達到用量上限、其他），
每類有自己的標題、怎麼辦與處理按鈕（CATALOG_ERROR_RULES）。Google 的例外由通事（services/google_catalog.py）
轉成 CatalogError；畫面上的提示列與紀錄檔都以 CatalogFailure 記下的同一份內容呈現。

使用範例：
    from core.catalog_errors import CatalogError, CatalogErrorKind, CatalogFailure, CATALOG_ERROR_RULES
    raise CatalogError(CatalogErrorKind.NOT_FOUND, "HttpError 404 ...")
    failure = CatalogFailure.from_error(error, action=t("catalog.action.read"))
    rule = CATALOG_ERROR_RULES[failure.kind]
    details = failure.detail_text()
"""
import datetime
from dataclasses import dataclass, field
from enum import Enum


class CatalogErrorKind(Enum):
    """譜庫讀寫失敗的類別"""

    OFFLINE = "offline"              # 斷網：連不上 Google
    SIGN_IN = "sign_in"              # 要重新登入 Google：沒登入、權杖過期或被撤銷
    NO_PERMISSION = "no_permission"  # 沒有權限（403）
    NOT_FOUND = "not_found"          # 找不到：試算表或資料夾不存在，或存取被拒（Drive 回 404）
    RATE_LIMITED = "rate_limited"    # 達到用量上限（429 與用量類的 403）
    OTHER = "other"                  # 其他


class CatalogRemedy(Enum):
    """提示列上的處理按鈕"""

    RETRY = "retry"                  # 重試
    SIGN_IN = "sign_in"              # 重新登入 Google
    OPEN_SETTINGS = "open_settings"  # 開啟譜庫設定


@dataclass(frozen=True)
class CatalogErrorRule:
    """一類錯誤的呈現：標題、怎麼辦與處理按鈕"""

    title_key: str
    advice_key: str
    remedy: CatalogRemedy


# 各類錯誤的標題、怎麼辦與處理按鈕（spec #81「錯誤呈現」）
CATALOG_ERROR_RULES = {
    CatalogErrorKind.OFFLINE: CatalogErrorRule(
        "catalog.error.offline.title", "catalog.error.offline.advice", CatalogRemedy.RETRY),
    CatalogErrorKind.SIGN_IN: CatalogErrorRule(
        "catalog.error.sign_in.title", "catalog.error.sign_in.advice", CatalogRemedy.SIGN_IN),
    CatalogErrorKind.NO_PERMISSION: CatalogErrorRule(
        "catalog.error.no_permission.title", "catalog.error.no_permission.advice", CatalogRemedy.SIGN_IN),
    CatalogErrorKind.NOT_FOUND: CatalogErrorRule(
        "catalog.error.not_found.title", "catalog.error.not_found.advice", CatalogRemedy.OPEN_SETTINGS),
    CatalogErrorKind.RATE_LIMITED: CatalogErrorRule(
        "catalog.error.rate_limited.title", "catalog.error.rate_limited.advice", CatalogRemedy.RETRY),
    CatalogErrorKind.OTHER: CatalogErrorRule(
        "catalog.error.other.title", "catalog.error.other.advice", CatalogRemedy.RETRY),
}

# 處理按鈕的介面字串
CATALOG_REMEDY_KEYS = {
    CatalogRemedy.RETRY: "catalog.error.retry",
    CatalogRemedy.SIGN_IN: "catalog.error.sign_in",
    CatalogRemedy.OPEN_SETTINGS: "catalog.error.open_settings",
}


class CatalogError(Exception):
    """譜庫讀寫失敗：帶著類別與原始訊息"""

    def __init__(self, kind: CatalogErrorKind, message: str = ""):
        """
        Args:
            kind: 錯誤類別
            message: 原始訊息（Google 回的錯誤或例外內容），放進「詳細資料」與紀錄檔
        """
        super().__init__(message or kind.value)
        self.kind = kind
        self.message = message


def _now() -> datetime.datetime:
    """現在的本機時間，精確到秒"""
    return datetime.datetime.now().replace(microsecond=0)


@dataclass(frozen=True)
class CatalogFailure:
    """一次譜庫讀寫失敗的紀錄：提示列的「詳細資料」與紀錄檔記的是同一份"""

    kind: CatalogErrorKind
    message: str
    action: str
    file_name: str = ""
    time: datetime.datetime = field(default_factory=_now)

    @classmethod
    def from_error(cls, error: CatalogError, action: str, file_name: str = "") -> "CatalogFailure":
        """以譜庫錯誤建立失敗紀錄，時間為現在

        Args:
            error: 譜庫錯誤
            action: 失敗的動作（介面上的名稱，例如「讀取譜庫」）
            file_name: 正在處理的檔名，沒有時為空字串

        Returns:
            失敗紀錄
        """
        return cls(error.kind, error.message, action, file_name)

    def detail_text(self) -> str:
        """「詳細資料」的內容：第一行是時間、動作與檔名（全形空白分隔），第二行起是原始訊息"""
        heading = "　".join(
            part for part in (self.time.strftime("%Y-%m-%d %H:%M:%S"), self.action, self.file_name) if part
        )
        return f"{heading}\n{self.message}"
