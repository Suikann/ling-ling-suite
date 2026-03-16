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
        "instrument.auto_extract": "從檔名擷取",
        "instrument.auto_extract.empty": "目前沒有已匯入的檔案可供擷取。",
        "instrument.auto_extract.confirm": "偵測到以下樂器，是否取代目前的樂器表？\n\n{instruments}",
        "instrument.auto_extract.title": "從檔名擷取樂器",
        # 連結樂章
        "group.link_movements": "連結為樂章...",
        "group.link_movements.title": "連結群組為同一曲目的樂章",
        "group.link_movements.select": "勾選要連結的群組：",
        "group.link_movements.piece_name": "共用曲名：",
        "group.link_movements.order": "樂章順序（拖曳或使用箭頭調整）：",
        "group.link_movements.confirm": "連結",
        "group.link_movements.cancel": "取消",
        "group.link_movements.need_two": "請至少勾選兩個群組。",
        "group.link_movements.done": "已將 {count} 個群組連結為「{piece}」的樂章。",
        # 預覽對話框
        "preview.title": "預覽重新命名",
        "preview.conflict_warning": "偵測到 {count} 個檔名衝突！選擇「繼續」將自動加後綴區分。",
        "preview.file_count": "共 {count} 個檔案",
        "preview.cancel": "取消",
        "preview.execute": "執行重新命名",
        "preview.execute_with_suffix": "繼續（自動加後綴）",
        # 重新命名服務
        "rename.undo_description": "重新命名 {count} 個檔案",
        # PDF 分割
        "split.title": "PDF 分割",
        "split.select_file": "選擇 PDF 檔案",
        "split.no_file": "尚未選擇檔案",
        "split.browse": "瀏覽...",
        "split.page_count": "共 {count} 頁",
        "split.mode": "分割方式",
        "split.every_n_pages": "每",
        "split.pages_unit": "頁分割一個檔案",
        "split.custom_ranges": "自訂頁面範圍",
        "split.ranges_hint": "格式：1-3, 4-6, 7-10",
        "split.output_dir": "輸出資料夾",
        "split.same_as_source": "（與來源檔案相同）",
        "split.cancel": "取消",
        "split.execute": "執行分割",
        "split.done": "已分割為 {count} 個檔案。",
        "split.invalid_n": "請輸入有效的頁數（正整數）。",
        "split.invalid_ranges": "頁面範圍格式不正確。",
        "split.missing_dependency": "缺少 PyPDF2 套件。\n請執行 pip install PyPDF2 安裝。",
        # PDF 旋轉
        "rotate.title": "PDF 旋轉",
        "rotate.select_file": "選擇 PDF 檔案",
        "rotate.no_file": "尚未選擇檔案",
        "rotate.browse": "瀏覽...",
        "rotate.page_count": "共 {count} 頁",
        "rotate.angle": "旋轉角度",
        "rotate.cw_90": "順時針 90 度",
        "rotate.180": "旋轉 180 度",
        "rotate.ccw_90": "逆時針 90 度",
        "rotate.scope": "套用範圍",
        "rotate.all_pages": "所有頁面",
        "rotate.custom_pages": "指定頁面",
        "rotate.pages_hint": "格式：1-3, 5, 7-10",
        "rotate.overwrite": "直接覆寫原檔案",
        "rotate.cancel": "取消",
        "rotate.execute": "執行旋轉",
        "rotate.save_as": "另存新檔",
        "rotate.done": "PDF 旋轉完成。",
        "rotate.invalid_pages": "頁面範圍格式不正確。",
        # 選單 - 工具
        "menu.tools": "工具",
        "menu.tools.split_pdf": "PDF 分割...",
        "menu.tools.rotate_pdf": "PDF 旋轉...",
        "menu.tools.visual_split_pdf": "PDF 視覺化分譜...",
        # 視覺化分譜
        "vsplit.title": "PDF 視覺化分譜",
        "vsplit.select_file": "來源檔案：",
        "vsplit.no_file": "尚未選擇檔案",
        "vsplit.browse": "瀏覽...",
        "vsplit.page_count": "共 {count} 頁",
        "vsplit.hint_empty": "選擇 PDF 檔案後，頁面縮圖將顯示於此。",
        "vsplit.assignments": "分譜指派",
        "vsplit.click_hint": "點擊頁面縮圖以標記分割點；\n右鍵點擊可放大預覽。",
        "vsplit.use_project_instruments": "使用專案樂器表",
        "vsplit.output_dir": "輸出資料夾：",
        "vsplit.same_as_source": "（與來源檔案相同）",
        "vsplit.cancel": "取消",
        "vsplit.execute": "執行分割",
        "vsplit.done": "已成功分割為 {count} 個檔案。",
        "vsplit.missing_dependency": "缺少 PyMuPDF 套件。\n請執行 pip install PyMuPDF 安裝。",
        "vsplit.page_label": "第 {num} 頁",
        "vsplit.part_default": "分譜 {index}",
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
        "group.piece_name_label": "{PieceName}:",
        "group.auto_detect": "Auto Detect",
        "group.movement_num_label": "{MovementNum}:",
        "group.movement_name_label": "{MovementName}:",
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
        "instrument.auto_extract": "Extract from Files",
        "instrument.auto_extract.empty": "No imported files to extract from.",
        "instrument.auto_extract.confirm": "Detected the following instruments. Replace current list?\n\n{instruments}",
        "instrument.auto_extract.title": "Extract Instruments from Filenames",
        # 連結樂章
        "group.link_movements": "Link as Movements...",
        "group.link_movements.title": "Link Groups as Movements of One Piece",
        "group.link_movements.select": "Select groups to link:",
        "group.link_movements.piece_name": "Shared Piece Name:",
        "group.link_movements.order": "Movement order (drag or use arrows):",
        "group.link_movements.confirm": "Link",
        "group.link_movements.cancel": "Cancel",
        "group.link_movements.need_two": "Please select at least two groups.",
        "group.link_movements.done": "Linked {count} groups as movements of \"{piece}\".",
        # 預覽對話框
        "preview.title": "Preview Rename",
        "preview.conflict_warning": "Detected {count} filename conflict(s)! Choosing 'Continue' will add suffixes automatically.",
        "preview.file_count": "{count} file(s) total",
        "preview.cancel": "Cancel",
        "preview.execute": "Execute Rename",
        "preview.execute_with_suffix": "Continue (auto suffix)",
        # 重新命名服務
        "rename.undo_description": "Renamed {count} file(s)",
        # PDF 分割
        "split.title": "PDF Split",
        "split.select_file": "Select PDF File",
        "split.no_file": "No file selected",
        "split.browse": "Browse...",
        "split.page_count": "{count} page(s)",
        "split.mode": "Split Mode",
        "split.every_n_pages": "Every",
        "split.pages_unit": "page(s) per file",
        "split.custom_ranges": "Custom page ranges",
        "split.ranges_hint": "Format: 1-3, 4-6, 7-10",
        "split.output_dir": "Output Folder",
        "split.same_as_source": "(same as source file)",
        "split.cancel": "Cancel",
        "split.execute": "Split",
        "split.done": "Split into {count} file(s).",
        "split.invalid_n": "Please enter a valid number of pages (positive integer).",
        "split.invalid_ranges": "Invalid page range format.",
        "split.missing_dependency": "PyPDF2 package is missing.\nPlease run: pip install PyPDF2",
        # PDF 旋轉
        "rotate.title": "Rotate PDF",
        "rotate.select_file": "Select PDF File",
        "rotate.no_file": "No file selected",
        "rotate.browse": "Browse...",
        "rotate.page_count": "{count} page(s)",
        "rotate.angle": "Rotation Angle",
        "rotate.cw_90": "Clockwise 90",
        "rotate.180": "Rotate 180",
        "rotate.ccw_90": "Counter-clockwise 90",
        "rotate.scope": "Apply To",
        "rotate.all_pages": "All pages",
        "rotate.custom_pages": "Specific pages",
        "rotate.pages_hint": "Format: 1-3, 5, 7-10",
        "rotate.overwrite": "Overwrite original file",
        "rotate.cancel": "Cancel",
        "rotate.execute": "Rotate",
        "rotate.save_as": "Save As",
        "rotate.done": "PDF rotation complete.",
        "rotate.invalid_pages": "Invalid page range format.",
        # 選單 - 工具
        "menu.tools": "Tools",
        "menu.tools.split_pdf": "Split PDF...",
        "menu.tools.rotate_pdf": "Rotate PDF...",
        "menu.tools.visual_split_pdf": "Visual PDF Split...",
        # Visual PDF Split
        "vsplit.title": "Visual PDF Split",
        "vsplit.select_file": "Source file:",
        "vsplit.no_file": "No file selected",
        "vsplit.browse": "Browse...",
        "vsplit.page_count": "{count} page(s)",
        "vsplit.hint_empty": "Page thumbnails will appear here after selecting a PDF.",
        "vsplit.assignments": "Part Assignment",
        "vsplit.click_hint": "Click a page thumbnail to mark a split point;\nright-click to preview.",
        "vsplit.use_project_instruments": "Use Project Instruments",
        "vsplit.output_dir": "Output folder:",
        "vsplit.same_as_source": "(same as source file)",
        "vsplit.cancel": "Cancel",
        "vsplit.execute": "Split",
        "vsplit.done": "Successfully split into {count} file(s).",
        "vsplit.missing_dependency": "PyMuPDF package is missing.\nPlease run: pip install PyMuPDF",
        "vsplit.page_label": "Page {num}",
        "vsplit.part_default": "Part {index}",
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
