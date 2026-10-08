# Data Storage

| 項目 | 位置 |
|------|------|
| 使用者資料目錄 | Windows：`%APPDATA%/LingLingSuite/`；Linux／macOS：`$XDG_CONFIG_HOME/LingLingSuite/`（預設 `~/.config/LingLingSuite/`），由 `core/constants.py` 的 `APPDATA_DIR` 決定 |
| 偏好設定 | `<使用者資料目錄>/preferences.json`（`PREFERENCES_FILE`；語言、外觀、最近專案清單、譜庫設定，預設值 `DEFAULT_PREFERENCES`） |
| 復原／重做紀錄 | `<使用者資料目錄>/undo/`、`redo/`（`UNDO_DIR`、`REDO_DIR`；每筆紀錄一個 JSON 檔，檔名為推入序號＋紀錄 id；舊版以時間戳命名的照常讀取） |
| 批次搬移進行中紀錄 | `<使用者資料目錄>/pending_move.json`（`MOVE_JOURNAL_FILE`；只在重新命名／復原／重做進行中存在，記下操作種類、所屬紀錄的 id 與內容、所屬專案檔；啟動時仍在即為上次中斷） |
| 單一實例鎖 | `<使用者資料目錄>/instance.lock`（`INSTANCE_LOCK_FILE`；執行期間由作業系統鎖住，結束或當機時自動解除，檔案留著不刪。啟動時先取得，取不到就提示「已在執行中」後結束，不開主視窗、不檢查進行中紀錄） |
| 工作區 | `<使用者資料目錄>/workspace/<hash8>/`（分割輸出與 `meta.json`） |
| PDF 旋轉備份 | `<使用者資料目錄>/backups/`（`BACKUP_DIR`） |
| 專案檔 | 使用者自選位置（儲存/載入對話框） |

復原、重做、進行中紀錄與旋轉備份的位置在建構 `MoveHistory` 時注入（預設為上表的常數），測試傳入暫存目錄、不替換全域常數。
