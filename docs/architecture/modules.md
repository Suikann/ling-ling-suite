# Modules

各檔的用途寫在檔首的模組 docstring，分層見 [CODING_STANDARDS.md](../../CODING_STANDARDS.md)。本檔只記 docstring 與檔名看不出的事：哪些職責全程式只有一個入口、測試怎麼寫。

## 唯一入口

下列職責全程式只在一處實作，新程式碼經過它：

| 職責 | 入口 |
|------|------|
| 判斷兩條路徑是否同一個檔（絕對路徑、不分大小寫） | `core/paths.py` 的 `path_key`、`same_path`、`is_inside`；沒有本機路徑的名稱（Drive 檔名）用同一規則的 `name_key` |
| 依介面語言挑選中英文版本 | `core/locale.py` 的 `is_english`、`localized` |
| 檔名清理（非法字元換底線、空名回退） | `core/filename.py`，重新命名與分割共用 |
| 一格（總譜或第 N 份分譜）的檔名與資料夾 | `core/naming.py`（見 [naming.md](naming.md)） |
| 走訪專案引用的檔案（總譜＋分譜＋未分組）；依路徑取代、移除引用 | `Project.file_refs()`、`Project.replace_paths`、`Project.remove_paths` |
| 群組欄位的編輯規則 | `Group.update`；不屬於專案的 Drive 群組也用它 |
| 兩階段批次搬移 | `services/move_service.py`，只有 `services/move_history.py` 使用（見 [move-history.md](move-history.md)） |
| 分割與旋轉對話框共用的 UI 輔助 | `ui/widgets.py`：`ThumbnailLoader` 在背景載入縮圖，只交出最近一次載入的結果；`ensure_file_exists` 提示找不到檔案 |

檔名容易誤會的：`ui/merge_dialog.py` 是連結樂章（`LinkMovementsDialog` → `Project.link_movements`），與合併譜無關。

## 測試

- 一律以 pytest 執行：`tests/conftest.py` 在任何測試模組匯入前把使用者資料目錄（`APPDATA_DIR`）導到臨時目錄，只有 pytest 會載入它（直接執行測試檔時不會，測試可能寫進真實的使用者資料目錄）
- UI 接線測試在匯入 PySide6 之前設定 `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")`，只測與服務層的接線、不測畫面，不需要顯示器
- 共用 fixture：
  - `tests/path_spellings.py`：同一路徑的不同寫法（大小寫、相對路徑、多餘分隔符），測路徑比對
  - `tests/failing_writes.py`：寫到指定檔案就失敗的 `FileService`，注入給要測寫入失敗的服務
  - `tests/other_instance.py`：以子行程扮演另一個持有單一實例鎖的程式

## 文件

`docs/notes/` 是帶日期的史料（例如架構審查報告），為當時的快照，不隨程式碼更新。
