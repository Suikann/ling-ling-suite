# -*- coding: utf-8 -*-
"""
HuaXia 的登入與說明

全程式向使用者說明 HuaXia 的事都在這裡：以 Discord 登入（sign_in_huaxia，期間顯示可取消的等待視窗、沒有完成時寫出
原因）、要用 HuaXia 前先問要不要登入（ensure_signed_in，發譜、撤回、收回共用）、不是幹部時的說明（not_officer_text）
與連 HuaXia 失敗的說明（huaxia_error_text）。譜庫設定的登入區、主視窗的發譜與譜庫視窗的撤回、收回都經這裡。

使用範例：
    from ui.huaxia_prompts import ensure_signed_in, huaxia_error_text, not_officer_text
    if ensure_signed_in(window, huaxia_auth, t("retract.title_short"), "retract.sign_in_first"):
        ...
    QMessageBox.warning(window, title, huaxia_error_text(error, "retract"))
"""
from PySide6.QtWidgets import QMessageBox, QWidget

from core.locale import t
from services.huaxia_auth_service import (
    AccountStanding, HuaXiaAuthService, HuaXiaLoginFailed, HuaXiaLoginFailure, NotOfficer,
)
from services.huaxia_client import HuaXiaError, HuaXiaErrorKind
from ui.waiting_dialog import run_while_waiting

# HuaXia 登入沒有完成時提示的介面字串；取消不提示，不是幹部與 FAILED 另組訊息
_HUAXIA_FAILURE_KEYS = {
    HuaXiaLoginFailure.BROWSER_NOT_OPENED: "catalog.auth.browser_not_opened",
    HuaXiaLoginFailure.TIMED_OUT: "huaxia.auth.timed_out",
    HuaXiaLoginFailure.DENIED: "huaxia.auth.denied",
}
# 不是幹部時各種帳號狀況的說明
_NOT_OFFICER_KEYS = {
    AccountStanding.JUST_CREATED: "huaxia.not_officer.just_created",
    AccountStanding.AWAITING_APPROVAL: "huaxia.not_officer.awaiting_approval",
    AccountStanding.DISABLED: "huaxia.not_officer.disabled",
    AccountStanding.NOT_OFFICER: "huaxia.not_officer.not_officer",
}


def not_officer_text(not_officer: NotOfficer) -> str:
    """不是幹部時的說明：帳號的狀況，查得到時前面寫出目前登入的 Discord 帳號名稱（登入與發譜、撤回、收回前重查共用）"""
    reason = t(_NOT_OFFICER_KEYS[not_officer.standing])
    if not not_officer.discord_name:
        return reason
    return t("huaxia.not_officer.with_account", name=not_officer.discord_name, reason=reason)


def huaxia_error_text(error: HuaXiaError, action: str) -> str:
    """連 HuaXia 失敗的說明（發譜、撤回、收回共用）：登入已失效、連不上、沒有登入，其他寫出錯誤

    Args:
        error: HuaXia 請求的錯誤
        action: 介面字串鍵的開頭（"publish"、"retract"、"recall"）：連不上是「<action>.unreachable」，
            其他錯誤是「<action>.failed」（以 error 代入錯誤訊息）
    """
    if error.kind is HuaXiaErrorKind.SESSION_GONE:
        return t("huaxia.auth.session_gone")
    if error.kind is HuaXiaErrorKind.UNREACHABLE:
        return t(f"{action}.unreachable")
    if error.kind is HuaXiaErrorKind.SIGNED_OUT:
        return t("huaxia.auth.not_connected")
    return t(f"{action}.failed", error=str(error))


def sign_in_huaxia(parent: QWidget, auth: HuaXiaAuthService) -> bool:
    """以 Discord 登入 HuaXia，期間顯示可取消的等待視窗；沒有完成時提示原因（取消不提示）

    Args:
        parent: 等待視窗與提示的父元件
        auth: HuaXia 登入服務

    Returns:
        是否登入成功（是幹部）
    """
    try:
        run_while_waiting(
            parent, t("huaxia.auth.title"), t("huaxia.auth.waiting"), auth.authenticate, auth.cancel_login,
        )
    except HuaXiaLoginFailed as e:
        if e.failure is HuaXiaLoginFailure.NOT_OFFICER:
            message = not_officer_text(e.not_officer)
        elif e.failure in _HUAXIA_FAILURE_KEYS:
            message = t(_HUAXIA_FAILURE_KEYS[e.failure])
        elif e.failure is HuaXiaLoginFailure.CANCELLED:
            return False
        else:
            message = t("huaxia.auth.failed", error=str(e))
        QMessageBox.warning(parent, t("huaxia.auth.title"), message)
        return False
    return True


def ensure_signed_in(parent: QWidget, auth: HuaXiaAuthService, title: str, question_key: str) -> bool:
    """要用 HuaXia 之前確認已登入：沒登入時先問（帶連結 Discord 的提醒），同意了才以 Discord 登入

    Args:
        parent: 提問與等待視窗的父元件
        auth: HuaXia 登入服務
        title: 提問的標題
        question_key: 提問的介面字串鍵（以 reminder 代入連結 Discord 的提醒）

    Returns:
        已登入，或這次登入成功
    """
    if auth.status().signed_in:
        return True
    answer = QMessageBox.question(parent, title, t(question_key, reminder=t("huaxia.auth.link_discord_first")))
    return answer == QMessageBox.Yes and sign_in_huaxia(parent, auth)
