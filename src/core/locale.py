# -*- coding: utf-8 -*-
"""
國際化系統

提供 zh_TW / en 雙語字典與 t(key, **kwargs) 存取函數。

使用範例：
    from core.locale import t, set_locale, get_locale
    set_locale("en")
    label_text = t("menu.file")
    status = t("status.imported_files", count=5)
"""
from typing import Dict, Optional

_current_locale = "zh_TW"

_STRINGS: Dict[str, Dict[str, str]] = {
    "zh_TW": {
        # 應用程式
        "app.title": "泠靈小工具",
        "app.unsaved_project": "未儲存的專案",
        # 選單 - 檔案
        "menu.file": "檔案",
        "menu.file.new": "新增專案",
        "menu.file.open": "開啟專案...",
        "menu.file.save": "儲存專案",
        "menu.file.save_as": "另存新檔...",
        # 選單 - 編輯
        "menu.edit": "編輯",
        "menu.edit.undo": "復原上次操作",
        # 選單 - 匯入
        "menu.import": "匯入",
        "menu.import.files": "匯入檔案...",
        "menu.import.folder": "匯入資料夾...",
        # 選單 - 檢視
        "menu.view": "檢視",
        "menu.view.appearance": "外觀模式",
        "menu.view.appearance.dark": "暗色",
        "menu.view.appearance.light": "亮色",
        "menu.view.appearance.system": "跟隨系統",
        "menu.view.language": "語言",
        "menu.view.language.zh_TW": "繁體中文",
        "menu.view.language.en": "English",
        # 對話框標題
        "dialog.close": "關閉程式",
        "dialog.close.message": "是否儲存目前的專案？",
        "dialog.unsaved": "未儲存的變更",
        "dialog.unsaved.message": "是否儲存目前的專案？",
        "dialog.warning": "警告",
        "dialog.warning.empty_template": "大模板為空，請先設定模板。",
        "dialog.mismatch": "數量不匹配",
        "dialog.mismatch.header": "以下群組的樂器與檔案數量不匹配：",
        "dialog.mismatch.footer": "不匹配的群組將被跳過。是否繼續？",
        "dialog.mismatch.detail": "「{name}」：{n_inst} 個樂器 / {n_files} 個檔案不匹配",
        "dialog.missing_files": "檔案不存在",
        "dialog.missing_files.header": "以下 {count} 個檔案不存在：",
        "dialog.missing_files.more": "...等共 {count} 個",
        "dialog.info": "提示",
        "dialog.info.no_files": "沒有需要重新命名的檔案。",
        "dialog.info.no_undo": "沒有可復原的操作。",
        "dialog.confirm_undo": "確認復原",
        "dialog.confirm_undo.message": "是否復原上次操作？\n{description}",
        "dialog.complete": "完成",
        "dialog.complete.renamed": "已成功重新命名 {count} 個檔案。",
        "dialog.complete.undone": "已成功復原上次操作。",
        "dialog.error": "錯誤",
        "dialog.error.permission": "沒有足夠的權限重新命名檔案：\n{error}",
        "dialog.error.rename_failed": "重新命名失敗：\n{error}",
        "dialog.error.open_failed": "無法開啟專案：\n{error}",
        "dialog.error.save_failed": "儲存失敗：\n{error}",
        "dialog.error.undo_failed": "復原失敗：\n{error}",
        "dialog.long_path": "路徑過長警告",
        "dialog.long_path.message": "以下 {count} 個路徑超過 255 字元，可能導致錯誤：",
        "dialog.long_path.confirm": "是否繼續？",
        "dialog.delete_group": "刪除群組",
        "dialog.delete_group.message": "確定要刪除「{name}」？\n群組內的檔案將移回未分組。",
        "dialog.info.create_group_first": "請先建立群組。",
        "dialog.info.cannot_detect": "無法自動偵測曲名。",
        # 檔案對話框
        "filedialog.select_pdf": "選擇 PDF 檔案",
        "filedialog.pdf_files": "PDF 檔案",
        "filedialog.select_folder": "選擇資料夾",
        "filedialog.open_project": "開啟專案",
        "filedialog.project_files": "泠靈專案檔",
        "filedialog.save_project": "儲存專案",
        # 狀態列
        "status.ready": "就緒",
        "status.imported_files": "已匯入 {count} 個檔案",
        "status.imported_groups": "已匯入 {groups} 個群組，{files} 個未分組檔案",
        "status.renamed": "已重新命名 {count} 個檔案",
        "status.undone": "已復原上次操作",
        "status.opened": "已開啟專案：{path}",
        "status.saved": "已儲存專案：{path}",
        # 底部面板
        "panel.master_template": "大模板：",
        "panel.insert_variable": "插入變數",
        "panel.subfolder": "建立子資料夾",
        "panel.subfolder_template": "  資料夾模板：",
        "panel.preview_rename": "預覽並重新命名",
        # 群組面板
        "group.add": "+ 新增群組",
        "group.ungrouped": "未分組",
        "group.new_name": "群組 {number}",
        "group.delete": "刪除此群組",
        "group.name_label": "群組名稱：",
        "group.piece_name_label": "{曲名}：",
        "group.auto_detect": "自動偵測",
        "group.movement_num_label": "{樂章編號}：",
        "group.movement_name_label": "{樂章名稱}：",
        "group.instrument_check": "樂器勾選",
        "group.file_list": "檔案清單",
        "group.add_files": "+ 加入檔案",
        "group.use_small_template": "使用小模板",
        "group.no_instruments": "請先在左側新增樂器",
        "group.mismatch": "勾選 {n_inst} 個樂器 / {n_files} 個檔案（不匹配）",
        "group.match": "{count} 個樂器 = {count} 個檔案",
        "group.loading": "群組面板（載入中...）",
        "group.move_to_group": "移至群組...",
        # 未分組
        "ungrouped.empty": "沒有未分組的檔案。\n使用「匯入」選單加入 PDF 檔案。",
        # 檔案清單
        "file_list.empty": "尚無檔案",
        # 樂器表
        "instrument.title": "樂器表",
        "instrument.placeholder": "輸入樂器名稱...",
        "instrument.add": "新增",
        # 預覽對話框
        "preview.title": "預覽重新命名",
        "preview.conflict_warning": "偵測到 {count} 個檔名衝突！選擇「繼續」將自動加後綴區分。",
        "preview.file_count": "共 {count} 個檔案",
        "preview.cancel": "取消",
        "preview.execute": "執行重新命名",
        "preview.execute_with_suffix": "繼續（自動加後綴）",
        # 重新命名服務
        "rename.undo_description": "重新命名 {count} 個檔案",
    },
    "en": {
        # 應用程式
        "app.title": "Ling Ling Suite",
        "app.unsaved_project": "Unsaved Project",
        # 選單 - 檔案
        "menu.file": "File",
        "menu.file.new": "New Project",
        "menu.file.open": "Open Project...",
        "menu.file.save": "Save Project",
        "menu.file.save_as": "Save As...",
        # 選單 - 編輯
        "menu.edit": "Edit",
        "menu.edit.undo": "Undo Last Operation",
        # 選單 - 匯入
        "menu.import": "Import",
        "menu.import.files": "Import Files...",
        "menu.import.folder": "Import Folder...",
        # 選單 - 檢視
        "menu.view": "View",
        "menu.view.appearance": "Appearance",
        "menu.view.appearance.dark": "Dark",
        "menu.view.appearance.light": "Light",
        "menu.view.appearance.system": "System",
        "menu.view.language": "Language",
        "menu.view.language.zh_TW": "繁體中文",
        "menu.view.language.en": "English",
        # 對話框標題
        "dialog.close": "Close",
        "dialog.close.message": "Save current project before closing?",
        "dialog.unsaved": "Unsaved Changes",
        "dialog.unsaved.message": "Save current project before closing?",
        "dialog.warning": "Warning",
        "dialog.warning.empty_template": "Master template is empty. Please set a template first.",
        "dialog.mismatch": "Count Mismatch",
        "dialog.mismatch.header": "The following groups have mismatched instrument/file counts:",
        "dialog.mismatch.footer": "Mismatched groups will be skipped. Continue?",
        "dialog.mismatch.detail": '"{name}": {n_inst} instruments / {n_files} files (mismatch)',
        "dialog.missing_files": "Files Not Found",
        "dialog.missing_files.header": "The following {count} file(s) do not exist:",
        "dialog.missing_files.more": "...and {count} more",
        "dialog.info": "Info",
        "dialog.info.no_files": "No files to rename.",
        "dialog.info.no_undo": "No operations to undo.",
        "dialog.confirm_undo": "Confirm Undo",
        "dialog.confirm_undo.message": "Undo last operation?\n{description}",
        "dialog.complete": "Done",
        "dialog.complete.renamed": "Successfully renamed {count} file(s).",
        "dialog.complete.undone": "Successfully undone last operation.",
        "dialog.error": "Error",
        "dialog.error.permission": "Insufficient permissions to rename files:\n{error}",
        "dialog.error.rename_failed": "Rename failed:\n{error}",
        "dialog.error.open_failed": "Cannot open project:\n{error}",
        "dialog.error.save_failed": "Save failed:\n{error}",
        "dialog.error.undo_failed": "Undo failed:\n{error}",
        "dialog.long_path": "Long Path Warning",
        "dialog.long_path.message": "The following {count} path(s) exceed 255 characters and may cause errors:",
        "dialog.long_path.confirm": "Continue?",
        "dialog.delete_group": "Delete Group",
        "dialog.delete_group.message": 'Delete "{name}"?\nFiles will be moved back to ungrouped.',
        "dialog.info.create_group_first": "Please create a group first.",
        "dialog.info.cannot_detect": "Cannot auto-detect piece name.",
        # 檔案對話框
        "filedialog.select_pdf": "Select PDF Files",
        "filedialog.pdf_files": "PDF Files",
        "filedialog.select_folder": "Select Folder",
        "filedialog.open_project": "Open Project",
        "filedialog.project_files": "Ling Ling Project",
        "filedialog.save_project": "Save Project",
        # 狀態列
        "status.ready": "Ready",
        "status.imported_files": "Imported {count} file(s)",
        "status.imported_groups": "Imported {groups} group(s), {files} ungrouped file(s)",
        "status.renamed": "Renamed {count} file(s)",
        "status.undone": "Undone last operation",
        "status.opened": "Opened project: {path}",
        "status.saved": "Saved project: {path}",
        # 底部面板
        "panel.master_template": "Master Template:",
        "panel.insert_variable": "Insert Variable",
        "panel.subfolder": "Create Subfolders",
        "panel.subfolder_template": "  Folder Template:",
        "panel.preview_rename": "Preview & Rename",
        # 群組面板
        "group.add": "+ Add Group",
        "group.ungrouped": "Ungrouped",
        "group.new_name": "Group {number}",
        "group.delete": "Delete Group",
        "group.name_label": "Group Name:",
        "group.piece_name_label": "Piece Name:",
        "group.auto_detect": "Auto Detect",
        "group.movement_num_label": "Movement No.:",
        "group.movement_name_label": "Movement Name:",
        "group.instrument_check": "Instruments",
        "group.file_list": "File List",
        "group.add_files": "+ Add Files",
        "group.use_small_template": "Use Track Template",
        "group.no_instruments": "Add instruments on the left first",
        "group.mismatch": "{n_inst} instruments / {n_files} files (mismatch)",
        "group.match": "{count} instruments = {count} files",
        "group.loading": "Group Panel (loading...)",
        "group.move_to_group": "Move to Group...",
        # 未分組
        "ungrouped.empty": "No ungrouped files.\nUse the Import menu to add PDF files.",
        # 檔案清單
        "file_list.empty": "No files",
        # 樂器表
        "instrument.title": "Instruments",
        "instrument.placeholder": "Enter instrument name...",
        "instrument.add": "Add",
        # 預覽對話框
        "preview.title": "Preview Rename",
        "preview.conflict_warning": "Detected {count} filename conflict(s)! Choosing 'Continue' will add suffixes automatically.",
        "preview.file_count": "{count} file(s) total",
        "preview.cancel": "Cancel",
        "preview.execute": "Execute Rename",
        "preview.execute_with_suffix": "Continue (auto suffix)",
        # 重新命名服務
        "rename.undo_description": "Renamed {count} file(s)",
    },
}


def get_locale() -> str:
    """取得目前語言代碼"""
    return _current_locale


def set_locale(locale_code: str):
    """設定目前語言

    Args:
        locale_code: 語言代碼，"zh_TW" 或 "en"
    """
    global _current_locale
    if locale_code in _STRINGS:
        _current_locale = locale_code


def get_available_locales():
    """取得所有可用語言代碼"""
    return list(_STRINGS.keys())


def t(key: str, **kwargs) -> str:
    """取得翻譯字串

    Args:
        key: 點分階層鍵名，例如 "menu.file.new"
        **kwargs: 用於 str.format_map() 的參數

    Returns:
        翻譯後的字串，找不到時回傳鍵名本身
    """
    strings = _STRINGS.get(_current_locale, _STRINGS["zh_TW"])
    text = strings.get(key)
    if text is None:
        fallback = _STRINGS["zh_TW"]
        text = fallback.get(key, key)
    if kwargs:
        try:
            text = text.format_map(kwargs)
        except (KeyError, ValueError):
            pass
    return text
