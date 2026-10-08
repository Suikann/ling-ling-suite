# Data Storage

使用者資料目錄由 `core/constants.py` 的 `APPDATA_DIR` 決定：Windows 為 `%APPDATA%/LingLingSuite/`，Linux／macOS 為 `$XDG_CONFIG_HOME/LingLingSuite/`（預設 `~/.config/LingLingSuite/`）。測試時由 `tests/conftest.py` 導到臨時目錄。

| 項目 | 位置（`<使用者資料目錄>/` 之下） | 說明 |
|------|------|------|
| 偏好設定 | `preferences.json`（`PREFERENCES_FILE`） | 語言、最近專案清單、譜庫設定等；鍵與預設值見 `DEFAULT_PREFERENCES` |
| 復原／重做紀錄 | `undo/`、`redo/`（`UNDO_DIR`、`REDO_DIR`） | 每筆紀錄一個 JSON 檔，檔名規則見 [move-history.md](move-history.md) |
| 批次搬移的進行中紀錄 | `pending_move.json`（`MOVE_JOURNAL_FILE`） | 重新命名、復原、重做或中斷還原進行中存在；整批搬完但正式紀錄寫不進去時也留著。啟動時仍在就提示（內容見 [move-history.md](move-history.md)） |
| 單一實例鎖 | `instance.lock`（`INSTANCE_LOCK_FILE`） | 執行期間由作業系統鎖住，結束或當機時自動解除，檔案留著不刪 |
| 工作區 | `workspace/<hash8>/`（`WORKSPACE_DIR`） | 分割輸出與 `meta.json`（見 [workspace.md](workspace.md)） |
| PDF 旋轉備份 | `backups/`（`BACKUP_DIR`） | |
| Google 授權權杖 | `google_token.json`（`TOKEN_FILE`，在 `core/catalog_constants.py`） | |

專案檔（`.llproj`）放在使用者自選的位置。

- 啟動時先取得單一實例鎖，取不到就提示「已在執行中」後結束，不開主視窗、不檢查進行中紀錄
- 復原、重做、進行中紀錄與旋轉備份的位置在建構 `MoveHistory` 時注入，預設為上表的常數
