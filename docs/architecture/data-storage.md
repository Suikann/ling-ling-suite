# Data Storage

使用者資料目錄由 `core/constants.py` 的 `APPDATA_DIR` 決定：Windows 為 `%APPDATA%/LingLingSuite/`，Linux／macOS 為 `$XDG_CONFIG_HOME/LingLingSuite/`（預設 `~/.config/LingLingSuite/`）。測試時由 `tests/conftest.py` 導到臨時目錄。

| 項目 | 位置（`<使用者資料目錄>/` 之下） | 說明 |
|------|------|------|
| 偏好設定 | `preferences.json`（`PREFERENCES_FILE`） | 語言、最近專案清單、譜庫設定、上次登入的 Google 帳號 email（`GOOGLE_ACCOUNT_EMAIL_KEY`，不是祕密，不放安全儲存）等；鍵與預設值見 `DEFAULT_PREFERENCES`。另記住譜庫的社團設定裡啟用的發譜平台（`CLUB_PUBLISHING_PLATFORM_KEY`，`PublishingPlatform` 的值；沒有或從沒讀到過為空字串，發譜功能隱藏）：連上譜庫時在背景讀社團設定並寫入，讀不到時沿用原值；啟動時只看這個鍵，不連網、不讀安全儲存。登入在背景執行緒記下 email，`PreferencesService` 的讀、改、存經同一把鎖，與主執行緒同時存檔不會蓋掉彼此的改動 |
| 復原／重做紀錄 | `undo/`、`redo/`（`UNDO_DIR`、`REDO_DIR`） | 每筆紀錄一個 JSON 檔，檔名規則見 [move-history.md](move-history.md) |
| 批次搬移的進行中紀錄 | `pending_move.json`（`MOVE_JOURNAL_FILE`） | 重新命名、復原、重做或中斷還原進行中存在；整批搬完但正式紀錄寫不進去時也留著。啟動時仍在就提示（內容見 [move-history.md](move-history.md)） |
| 單一實例鎖 | `instance.lock`（`INSTANCE_LOCK_FILE`） | 執行期間由作業系統鎖住，結束或當機時自動解除，檔案留著不刪 |
| 工作區 | `workspace/<hash8>/`（`WORKSPACE_DIR`） | 分割輸出與 `meta.json`（見 [workspace.md](workspace.md)） |
| PDF 旋轉備份 | `backups/`（`BACKUP_DIR`） | |
| 暫存資料夾 | `temp/`（`TEMP_DIR`） | 壓縮檔先寫在這裡，每份一個子資料夾；一批壓完、交到輸出位置後一起刪，取消時已壓好的也當場刪。目前給獨立壓縮用，設計成發譜（spec #82）也能沿用 |
| 紀錄檔 | `logs/<年-月-日>.log`（`LOG_DIR`） | 譜庫的讀寫失敗與重要動作，一天一個檔，經 `ActivityLog` 附加；一筆記時間、動作、曲名、檔名、路徑、錯誤類別與原始訊息，與提示列的「詳細資料」相同。不記權杖、密碼與 HuaXia 傳回的團員資料，原始訊息裡像權杖或密碼的部分換成 `***`。保留 30 天（`LOG_RETENTION_DAYS`），寫不進去時略過、不影響操作 |

專案檔（`.llproj`）放在使用者自選的位置。

Google 的登入狀態不在使用者資料目錄：只有續期權杖存在系統的安全儲存（Windows 認證管理員、Linux 的 Secret Service），服務名稱為 `GOOGLE_KEYRING_SERVICE`（HuaXia 那筆用 `HUAXIA_KEYRING_SERVICE`）。backend 依平台明確指定，不受 `keyrings.alt`、`PYTHON_KEYRING_BACKEND`、`keyringrc.cfg` 影響；啟動時不讀，第一次用到 Google 時才讀；解鎖視窗被取消後，看登入狀態不再讀，等下一次要用到憑證才再讀，一次操作最多跳一次解鎖視窗。這台電腦沒有安全儲存或寫不進去時就不保存，譜庫設定的登入狀態旁寫出原因。內容不備份，遺失就重新登入。舊版以明文存的 `google_token.json`（`LEGACY_TOKEN_FILE`）每次啟動都刪；刪不掉就等下次啟動再刪，任何情況都不讀它，也不搬進安全儲存。

- 啟動時先取得單一實例鎖，取不到就提示「已在執行中」後結束，不開主視窗、不檢查進行中紀錄、不清暫存資料夾；鎖檔建不起來或開不了（例如使用者資料目錄沒有寫入權限）時同樣不繼續，顯示錯誤原因後結束
- 取得鎖後清空暫存資料夾：裡面只會是上次當機或壓縮中關閉程式留下的殘檔。暫存資料夾的位置在建構 `CompressionService` 時注入
- 復原、重做、進行中紀錄與旋轉備份的位置在建構 `MoveHistory` 時注入，預設為上表的常數
