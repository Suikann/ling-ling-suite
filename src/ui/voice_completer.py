# -*- coding: utf-8 -*-
"""
聲部名稱的自動完成（PySide6）

替輸入聲部名稱的欄位掛上自動完成：每打一個字就依 core/voice_suggestions.py 的 suggest 重列候選，
與輸入同一聲部但寫法不同的候選加上標記。選了候選才把它的名稱（不含標記）填進欄位；只打字時不改寫欄位的文字，
填進去的名稱照樣經過各處原本的檢查（同一份樂器表不重名、分割的重名分段）。

編制表的輸入欄、編制表改名的編輯欄（VoiceNameDelegate）與分割的分段名稱都用這裡，不另做一份。

使用範例：
    from ui.voice_completer import attach_voice_completer
    attach_voice_completer(line_edit, lambda: candidates.for_group(project, group))
"""
from typing import Callable, List

from PySide6.QtCore import Qt
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QCompleter, QLineEdit, QStyledItemDelegate

from core.locale import t
from core.voice_suggestions import suggest

# 候選裡存聲部名稱本身的資料角色（顯示的文字可能帶「同一聲部」標記）
_NAME_ROLE = Qt.UserRole + 1


def attach_voice_completer(edit: QLineEdit, candidates: Callable[[], List[str]]) -> QCompleter:
    """替 edit 掛上聲部名稱的自動完成

    Args:
        edit: 輸入聲部名稱的欄位
        candidates: 取得目前候選的函式（依使用次數排序），每打一個字呼叫一次

    Returns:
        掛上的自動完成
    """
    model = QStandardItemModel(edit)
    completer = QCompleter(model, edit)
    completer.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
    completer.setCompletionRole(_NAME_ROLE)
    edit.setCompleter(completer)

    def relist(text: str) -> None:
        """依輸入的文字重列候選；沒有候選時收起選單"""
        model.clear()
        for suggestion in suggest(text, candidates()):
            label = t("voice.suggestion.same_voice", name=suggestion.name) if suggestion.same_voice else suggestion.name
            item = QStandardItem(label)
            item.setData(suggestion.name, _NAME_ROLE)
            item.setEditable(False)
            model.appendRow(item)
        if model.rowCount():
            completer.complete()
        else:
            completer.popup().hide()

    edit.textEdited.connect(relist)
    return completer


class VoiceNameDelegate(QStyledItemDelegate):
    """聲部名稱欄的編輯委派：改名時的編輯欄掛上聲部名稱的自動完成"""

    def __init__(self, candidates: Callable[[], List[str]], parent=None):
        """
        Args:
            candidates: 取得目前候選的函式
            parent: 父物件（通常是表格）
        """
        super().__init__(parent)
        self._candidates = candidates

    def createEditor(self, parent, option, index):
        """建立掛上自動完成的編輯欄"""
        editor = QLineEdit(parent)
        attach_voice_completer(editor, self._candidates)
        return editor
