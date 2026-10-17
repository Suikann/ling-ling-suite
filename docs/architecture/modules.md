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
| 讀寫譜庫（Google 試算表與 Drive） | `services/catalog_service.py` 的 `CatalogService`：UI 與其他服務只呼叫它，失敗時丟出帶錯誤類別的 `CatalogError`（`core/catalog_errors.py`），不以空值或空清單代表失敗。它對下只經過試算表與 Drive 兩個關口（`services/catalog_ports.py` 的 `SheetsPort`、`DrivePort`，一次呼叫一次請求）；接 Google 的通事是 `services/google_catalog.py`，全程式只有它匯入 `googleapiclient`、把 Google 的例外轉成錯誤類別。寫入的共用規則（值以 RAW 寫入、Drive 名稱搜尋的跳脫、429 退避重試、ID 撞號重抽、新增列重送不重複）都在服務與通事裡。譜庫視窗、Drive 重新命名與譜庫設定的讀寫都經它；要刪改試算表的列時，寫入前一刻才讀列號（`update_record`、`delete_*`） |
| 譜庫讀寫失敗的呈現：失敗的那個視窗頂端的提示列（類別、怎麼辦、處理按鈕、可展開的詳細資料） | `ui/catalog_error_bar.py` 的 `CatalogErrorBar`；各類別的標題、怎麼辦與按鈕照 `core/catalog_errors.py` 的 `CATALOG_ERROR_RULES`，詳細資料是 `CatalogFailure.detail_text()`；建立時傳入紀錄檔（`ActivityLog`），顯示的每一次失敗都以同一份寫進紀錄檔。在背景呼叫譜庫服務經 `ui/catalog_tasks.py` 的 `CatalogTasks`：失敗時交回 `CatalogError` 本身（`BackgroundTask` 只交回訊息），呼叫端才分得出類別 |
| 社團設定的發譜平台（發譜功能顯示與否） | `services/club_settings_service.py` 的 `ClubSettingsService`：`read` 經 `CatalogService.publishing_platform` 讀譜庫的社團設定工作表並記在偏好設定，讀不到時丟出 `CatalogError`、記住的不變；`remembered` 只看偏好設定，啟動時用它決定顯示與否；譜庫設定換了譜庫試算表時以 `forget` 清掉再讀新的 |
| 寫紀錄檔：譜庫的讀寫失敗與重要動作（存入譜庫、撤銷存入、清除本機檔案、下載、發譜） | `services/activity_log.py` 的 `ActivityLog.record`，一筆是 `core/log_entry.py` 的 `LogEntry`：只有固定欄位，不記權杖、密碼與 HuaXia 傳回的團員資料，原始訊息裡像權杖或密碼的部分遮掉。譜庫失敗以 `CatalogFailure.log_entry()` 取得那一筆，與提示列的詳細資料是同一份。位置與保留天數見 [data-storage.md](data-storage.md) |
| 讀寫系統的安全儲存（keyring） | `services/secure_storage.py` 的 `SecureStorage`；backend 依平台明確指定，不用 keyring 的自動選擇 |
| 兩階段批次搬移 | `services/move_service.py`，只有 `services/move_history.py` 使用（見 [move-history.md](move-history.md)） |
| 以預設程式開啟網址或本機的檔案、資料夾（瀏覽器、PDF 閱讀器、檔案管理員） | `services/external_opener.py` 的 `ExternalOpener.open`；Linux 打包版替外部程式還原 `LD_LIBRARY_PATH`（`core/launch_env.py`）。不直接呼叫 `QDesktopServices.openUrl` 或 `xdg-open` |
| PDF 壓縮：級數、三層檢查、暫存的壓縮檔 | `services/compression_service.py` 的 `CompressionService`；PDF 內容的讀寫在 `services/pdf_service.py`（`save_lossless`、`save_downsampled`、`check_compressed`） |
| 使用 PyMuPDF（算繪、壓縮、檢查） | `services/pdf_service.py` 裡掛 `_exclusive` 的函式：全部經同一把鎖，一次只有一個在執行（PyMuPDF 不能多執行緒同時使用，壓縮檢查讀的 MuPDF 警告也是全程式一份）。新的 PyMuPDF 呼叫也要掛上 |
| 壓縮的比較畫面：逐份對照原檔與壓縮後、決定留下或不要、開啟壓縮檔 | `ui/compression_compare.py` 的 `CompressionCompareDialog`：只接（顯示名稱，壓縮結果），由 `kept()` 交回要交出的結果；目前只有獨立壓縮由 `MainWindow._deliver_compressed` 開啟；設計成發譜預覽（spec #82）也能沿用 |
| 壓縮的批次（多選檔、整個資料夾、另存單一檔）、輸出位置規則（預設位置、一批擋下輸入的資料夾、另存單一檔只擋原檔、會被覆蓋的檔）、整批壓縮與交出 | `services/compression_output_service.py` 的 `CompressionOutputService`；單一檔是只有一份的一批。主視窗整批壓完後由 `MainWindow._deliver_compressed` 先開比較畫面，再交出留下的 |
| 在背景執行可取消的長工作：進度與取消鈕、期間鎖住重新命名、分割、旋轉、復原、重做，關閉時詢問要不要取消 | `MainWindow._run_in_background`（各種工作的字串鍵由 `_BackgroundTexts` 傳入），背景執行緒由 `ui/background_task.py` 的 `BackgroundTask` 管：工作交回後在主執行緒刪掉；關閉時等不到它停下就 `abandon`，背景執行緒之後不再碰它 |
| OAuth 登入時開瀏覽器、在 `127.0.0.1` 的隨機埠等回呼（Google 與 Discord 共用） | `services/local_callback.py` 的 `LocalCallback`：回傳回呼的 query 參數，逾時、取消都關掉本機伺服器；兌換權杖與檢查 `state` 由各登入服務做 |
| 這次執行是否為打包版、打包版的資源目錄（`sys.frozen`、`sys._MEIPASS`） | `core/launch_env.py` 的 `current_build`，呼叫當下讀；repo 根目錄是 `core/constants.py` 的 `REPO_ROOT`。不在別處直接讀 `sys.frozen`、`sys._MEIPASS` 或以 `__file__` 推 repo 根目錄 |
| 內附 Google 用戶端檔的位置 | `core/launch_env.py` 的 `bundled_client_secrets_path`：打包版讀資源目錄，從原始碼執行讀 repo 根目錄 |
| 這次執行的版號（「關於」顯示） | `core/launch_env.py` 的 `app_version`：打包版讀資源目錄裡 CD 由 git tag 寫入的 `version.txt`（`VERSION_FILE`，`LingLingSuite.spec` 收進去），讀不到或從原始碼執行時是開發版 |
| 在背景做會卡住的事時顯示可取消的等待視窗（例如等瀏覽器登入回來） | `ui/waiting_dialog.py` 的 `run_while_waiting`，背景執行緒同樣由 `BackgroundTask` 管 |
| 分割、旋轉對話框與比較畫面共用的 UI 輔助 | `ui/widgets.py`：`ThumbnailLoader` 在背景算繪 PDF 頁面（縮圖、比較畫面的頁面），只交出最近一次載入的結果；`ensure_file_exists` 提示找不到檔案 |
| 存檔對話框：沒打副檔名時補上，補上後的檔名已有檔案時先確認取代 | `ui/widgets.py` 的 `ask_save_path` |

檔名容易誤會的：`ui/merge_dialog.py` 是連結樂章（`LinkMovementsDialog` → `Project.link_movements`），與合併譜無關。

## 測試

- 一律以 pytest 執行：`tests/conftest.py` 在任何測試模組匯入前把使用者資料目錄（`APPDATA_DIR`）導到臨時目錄，只有 pytest 會載入它（直接執行測試檔時不會，測試可能寫進真實的使用者資料目錄）。它也關掉自動垃圾回收，改在每個測試收尾後於主執行緒回收：測試留下的 Qt 物件不會在背景執行緒（例如壓縮）被解構而 segfault（`tests/test_garbage_collection.py` 釘住）
- UI 接線測試在匯入 PySide6 之前設定 `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")`，只測與服務層的接線、不測畫面，不需要顯示器
- 共用 fixture：
  - `tests/path_spellings.py`：同一路徑的不同寫法（大小寫、相對路徑、多餘分隔符），測路徑比對
  - `tests/failing_writes.py`：寫到（原子寫入或附加）指定檔案就失敗的 `FileService`，注入給要測寫入失敗的服務
  - `tests/other_instance.py`：以子行程扮演另一個持有單一實例鎖的程式
  - `tests/memory_keyring.py`：記憶體裡的 keyring backend，注入給 `SecureStorage`；可設成沒有安全儲存、被鎖住、寫入或刪除失敗。測試一律注入它，不碰系統的安全儲存
  - `tests/fake_google.py`：Google 權杖端點的替身（兌換授權碼時回傳指定的續期權杖、範圍與帳號 email；續期時回傳指定權杖、拒絕續期、連不上），以及寫出測試用用戶端檔的 `write_client_secrets`
  - `tests/fake_google_catalog.py`：假 Google，記憶體裡的譜庫試算表加 Drive，實作兩個關口，注入給 `CatalogService`；每次呼叫計為一次請求，可以設定第 N 次請求丟出某類錯誤（可選擇請求生效之後才丟，扮演回應沒收到），或在第 N 次請求之前讓另一台電腦改動譜庫。譜庫讀寫的測試一律用它，不接真的 Google
  - `tests/fake_browser.py`：開瀏覽器的替身，注入給本機回呼與登入服務；拿到授權網址後在背景真的對其中的 `127.0.0.1` 回呼送 HTTP 請求，帶 `code` 或 `error`，或設成永遠不送
  - `tests/synthetic_pdfs.py`：以 PyMuPDF 產生的合成掃描分譜（8-bit 灰階、JPEG、1-bit、未壓縮的串流、加密、宣告錯誤的 JPEG），測壓縮服務與壓縮檢查；壓縮服務的測試不接替身，以注入的大小上限代替 20MB

## 文件

`docs/notes/` 是帶日期的史料（例如架構審查報告），為當時的快照，不隨程式碼更新。
