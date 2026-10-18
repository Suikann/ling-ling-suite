# Modules

各檔的用途寫在檔首的模組 docstring，分層見 [CODING_STANDARDS.md](../../CODING_STANDARDS.md)。本檔只記 docstring 與檔名看不出的事：哪些職責全程式只有一個入口、測試怎麼寫。

## 唯一入口

下列職責全程式只在一處實作，新程式碼經過它：

| 職責 | 入口 |
|------|------|
| 判斷兩條路徑是否同一個檔（絕對路徑、不分大小寫） | `core/paths.py` 的 `path_key`、`same_path`、`is_inside`；沒有本機路徑的名稱（Drive 檔名）用同一規則的 `name_key` |
| 判斷兩個聲部名稱是否同一聲部（結尾編號互通）或同一樂器（去掉結尾編號） | `core/voice_names.py` 的 `same_voice`、`voice_key`、`same_instrument`、`instrument_key`；規則寫死在模組 docstring。依名稱配對分譜、同一份樂器表不重名（`duplicate_rows` 列出重名的列，`voice_index` 找同一聲部的第一列）、發譜對到 HuaXia 聲部、依聲部配值（`match_by_voice`：舊專案人數展開、換樂器表時沿用人數、複製編制）、自動完成的同名標記都經它，同一樂器只用在聲部組對照的鍵。只用於比對，各處保留自己的寫法；檔名與路徑用上一列的 `name_key`、`path_key` |
| 聲部名稱的自動完成（候選來源、排序與「同一聲部」標記） | 候選在 `core/voice_suggestions.py`：`VoiceCandidates` 全程式一份，由 `MainWindow` 建立後交給編制表、分割視窗與譜庫視窗；譜庫視窗讀到總覽時記下譜庫用過的聲部名稱（`CatalogOverview.voice_names`，依使用次數排序，與總覽同一次試算表讀取），還沒讀過譜庫時候選是專案其他群組的名稱加上預設樂器表；`suggest` 列出包含輸入文字或同一聲部的候選。欄位掛上自動完成經 `ui/voice_completer.py` 的 `attach_voice_completer`（表格的編輯欄用 `VoiceNameDelegate`）。只提示、不改寫輸入，也不參與比對 |
| 聲部清單換成譜庫的時，分譜依聲部名稱帶著走（依名稱配對）與套用前的預覽 | `Project.apply_catalog_voices`；預覽每個聲部配到的分譜是 `Project.merge_rows`，兩者同一套規則（見 [project.md](project.md) 的連到譜庫）。連線選用譜庫的經它，開啟專案時套用譜庫的新清單也沿用，不另寫一份配對 |
| 聲部的聲部組（以樂器為鍵的對照，沒存的依樂團類型偵測） | `core/sections.py` 的 `section_for`（專案裡經 `Project.section_of`）；寫入經 `Project.set_section`、`update_sections`（見 [project.md](project.md)） |
| 編輯一個群組的編制（聲部、本專案有、人數、聲部組、時長；拖動時分譜跟著走） | `ui/instrumentation_table.py` 的 `InstrumentationTable`：主視窗左欄用它，其他視窗要顯示編制（例如譜庫視窗的版本頁）時沿用、不另做一份；帶入專案後以 `show_group` 顯示群組，修改經 `Project` 的編輯操作（見 [project.md](project.md) 的編制表） |
| 人數範圍的整理與 HuaXia 記法（`6`、`6-8`、`2↑`） | `core/models.py` 的 `Headcount`（`normalized`、`text`、`filled`）；合計用 `sum_headcounts`，時長的「分:秒」用 `format_duration` |
| 依介面語言挑選中英文版本 | `core/locale.py` 的 `is_english`、`localized` |
| 檔名清理（非法字元換底線、空名回退） | `core/filename.py`，重新命名與分割共用 |
| 一格（總譜或第 N 份分譜）的檔名與資料夾 | `core/naming.py`（見 [naming.md](naming.md)） |
| 走訪專案引用的檔案（總譜＋分譜＋未分組）；依路徑取代、移除引用 | `Project.file_refs()`、`Project.replace_paths`、`Project.remove_paths` |
| 群組欄位的編輯規則 | `Group.update`；不屬於專案的 Drive 群組也用它 |
| 讀寫譜庫（Google 試算表與 Drive） | `services/catalog_service.py` 的 `CatalogService`：UI 與其他服務只呼叫它，失敗時丟出帶錯誤類別的 `CatalogError`（`core/catalog_errors.py`），不以空值或空清單代表失敗。它對下只經過試算表與 Drive 兩個關口（`services/catalog_ports.py` 的 `SheetsPort`、`DrivePort`，一次呼叫一次請求）；接 Google 的通事是 `services/google_catalog.py`，全程式只有它匯入 `googleapiclient`、把 Google 的例外轉成錯誤類別。寫入的共用規則（值以 RAW 寫入、Drive 名稱搜尋的跳脫、429 退避重試、ID 撞號重抽、新增列重送不重複、建譜庫試算表重送不建第二份）都在服務與通事裡。譜庫視窗、Drive 重新命名、譜庫設定與群組連到譜庫（`read_place`、`create_place`，見 [project.md](project.md) 的連到譜庫）、開啟專案時更新快照（`read_places`，見 [project.md](project.md) 的開啟時更新快照）、存檔時寫回編制（`sync_instrumentation`，格子的拆分與比對在 `core/catalog_sync.py`，見 [project.md](project.md) 的寫回譜庫）、譜庫視窗編輯全社團的聲部組對照（`section_map`、`sync_section_map`，與寫回同一套比對）、版本頁（`edition_page` 讀編制、登記與沒登記的檔，`register_files` 登記檔並把換掉的舊檔丟進 Drive 垃圾桶，見 [project.md](project.md) 的版本頁）、存入譜庫（`deposit_places`、`drive_ids`、`upload_file`、`update_file`、`place_file`、`record_deposits`）的讀寫都經它；要刪改試算表的列時，寫入前一刻才讀列號（`update_record`、`delete_*`） |
| 存入譜庫：哪些群組能存入、每份檔放在版本資料夾的哪裡與要做的事（新上傳、更新、搬移、登記、跳過），以及照計畫上傳與登記 | 規則在 `core/deposit.py`（`deposit_sources`、`plan_group`）；讀本機檔案的 MD5 與照計畫執行在 `services/deposit_service.py` 的 `DepositService`（`preview`、`deposit`），碰譜庫經譜庫服務；存入指紋交回專案經 `Project.record_deposit`（見 [project.md](project.md) 的存入譜庫） |
| 譜庫讀寫失敗的呈現：失敗的那個視窗頂端的提示列（類別、怎麼辦、處理按鈕、可展開的詳細資料） | `ui/catalog_error_bar.py` 的 `CatalogErrorBar`；各類別的標題、怎麼辦與按鈕照 `core/catalog_errors.py` 的 `CATALOG_ERROR_RULES`，詳細資料是 `CatalogFailure.detail_text()`；建立時傳入紀錄檔（`ActivityLog`），顯示的每一次失敗都以同一份寫進紀錄檔。要改的紀錄在寫入前一刻已被另一台電腦刪掉時，`update_record` 丟出「已經不在譜庫」，按鈕是「重新讀取」（重新讀取畫面，不重送寫入）。在背景呼叫譜庫服務經 `ui/catalog_tasks.py` 的 `CatalogTasks`：失敗時交回 `CatalogError` 本身（`BackgroundTask` 只交回訊息），呼叫端才分得出類別 |
| 社團設定的發譜平台（發譜功能顯示與否） | `services/club_settings_service.py` 的 `ClubSettingsService`：`read` 經 `CatalogService.publishing_platform` 讀譜庫的社團設定工作表並記在偏好設定，讀不到時丟出 `CatalogError`、記住的不變；連上譜庫時改用 `refresh`，讀不到時記住的不變、失敗寫進紀錄檔，不顯示提示列；`remembered` 只看偏好設定，啟動時用它決定顯示與否；譜庫設定按「儲存設定」存了另一份譜庫試算表時以 `forget` 清掉再讀新的 |
| 寫紀錄檔：譜庫的讀寫失敗與重要動作（存入譜庫、撤銷存入、清除本機檔案、下載、發譜） | `services/activity_log.py` 的 `ActivityLog.record`，一筆是 `core/log_entry.py` 的 `LogEntry`：只有固定欄位，不記權杖、密碼與 HuaXia 傳回的團員資料，原始訊息裡像權杖或密碼的部分遮掉。譜庫失敗以 `CatalogFailure.log_entry()` 取得那一筆，與提示列的詳細資料是同一份。位置與保留天數見 [data-storage.md](data-storage.md) |
| 碰 HuaXia（Supabase 的 Auth、PostgREST、Storage） | `services/huaxia_client.py` 的 `HuaXiaClient`：全程式只有它送 HuaXia 的請求，以 `requests` 直接打 REST（ADR 0003），HuaXia 的網址與 publishable key 內附在它頂端；失敗丟出帶類別的 `HuaXiaError`（連不上、沒有登入、登入已失效、其他）。它不記登入狀態：Discord 登入與幹部檢查、access token 的續期與輪替（同一時間只有一個續期，新的續期權杖立刻寫回安全儲存）、重查幹部身分與登出都在 `services/huaxia_auth_service.py` 的 `HuaXiaAuthService`，要 access token 的程式經它的 `access_token`、重查幹部身分經 `recheck_officer`。登入服務全程式一份，由 `MainWindow` 建立後傳給譜庫視窗與譜庫設定；登入區在譜庫設定，只在這台電腦記住的社團設定啟用 HuaXia 發譜時出現。以 Discord 登入（等待視窗與沒有完成時的提示）與不是幹部的說明是 `ui/catalog_settings_dialog.py` 的 `sign_in_huaxia`、`not_officer_text` |
| 發譜前的檢查（spec #82）：擋下整個發譜的（未存檔、不是國樂團）、擋下個別群組的（還沒重新命名、沒填最少人數、樂器表重名）與只標出的未同步值；按下發譜時 HuaXia 那一側的重查幹部身分、發譜函式的介面版本與可以挑的曲目 | `services/publish_service.py`：`project_blocks`、`check_groups`、`PublishService.prepare`（經全程式一份的 HuaXia 登入服務與它的用戶端）。發譜函式的版本查詢是 `HuaXiaClient.publish_interface_version`，函式名稱與要的版本在 `services/huaxia_client.py` 頂端（`PUBLISH_VERSION_FUNCTION`、`PUBLISH_INTERFACE_VERSION`，#117 定案前的假設，定案時只改這裡）。按下「發譜」的流程在 `MainWindow._publish`（擋下時寫出原因、重讀社團設定、沒登入先登入），發譜預覽是 `ui/publish_dialog.py` 的 `PublishPreviewDialog`：步驟的外框與「曲目」步，之後的步驟以 `add_step` 接上 |
| 讀寫系統的安全儲存（keyring） | `services/secure_storage.py` 的 `SecureStorage`；backend 依平台明確指定，不用 keyring 的自動選擇 |
| 兩階段批次搬移 | `services/move_service.py`，只有 `services/move_history.py` 使用（見 [move-history.md](move-history.md)） |
| 以預設程式開啟網址或本機的檔案、資料夾（瀏覽器、PDF 閱讀器、檔案管理員） | `services/external_opener.py` 的 `ExternalOpener.open`；Linux 打包版替外部程式還原 `LD_LIBRARY_PATH`（`core/launch_env.py`）。不直接呼叫 `QDesktopServices.openUrl` 或 `xdg-open` |
| PDF 壓縮：級數、三層檢查、暫存的壓縮檔 | `services/compression_service.py` 的 `CompressionService`；PDF 內容的讀寫在 `services/pdf_service.py`（`save_lossless`、`save_downsampled`、`check_compressed`） |
| 使用 PyMuPDF（算繪、壓縮、檢查） | `services/pdf_service.py` 裡掛 `_exclusive` 的函式：全部經同一把鎖，一次只有一個在執行（PyMuPDF 不能多執行緒同時使用，壓縮檢查讀的 MuPDF 警告也是全程式一份）。新的 PyMuPDF 呼叫也要掛上 |
| 壓縮的比較畫面：逐份對照原檔與壓縮後、決定留下或不要、開啟壓縮檔 | `ui/compression_compare.py` 的 `CompressionCompareDialog`：只接（顯示名稱，壓縮結果），由 `kept()` 交回要交出的結果；目前只有獨立壓縮由 `MainWindow._deliver_compressed` 開啟；設計成發譜預覽（spec #82）也能沿用 |
| 壓縮的批次（多選檔、整個資料夾、另存單一檔）、輸出位置規則（預設位置、一批擋下輸入的資料夾、另存單一檔只擋原檔、會被覆蓋的檔）、整批壓縮與交出 | `services/compression_output_service.py` 的 `CompressionOutputService`；單一檔是只有一份的一批。主視窗整批壓完後由 `MainWindow._deliver_compressed` 先開比較畫面，再交出留下的 |
| 在背景執行可取消的長工作：進度與取消鈕、期間鎖住重新命名、分割、旋轉、復原、重做（存入譜庫另以 `locks_project` 連專案編輯與存檔也鎖住），關閉時詢問要不要取消 | `MainWindow._run_in_background`（各種工作的字串鍵由 `_BackgroundTexts` 傳入），背景執行緒由 `ui/background_task.py` 的 `BackgroundTask` 管：工作交回後在主執行緒刪掉；關閉時等不到它停下就 `abandon`，背景執行緒之後不再碰它 |
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
  - `tests/fake_google_catalog.py`：假 Google，記憶體裡的譜庫試算表加 Drive，實作兩個關口，注入給 `CatalogService`；每次呼叫計為一次請求（試算表與 Drive 另外分開計數），可以設定第 N 次請求（或第 N 次上傳）丟出某類錯誤（可選擇請求生效之後才丟，扮演回應沒收到），或在第 N 次請求之前讓另一台電腦改動譜庫。Drive 的檔有各次內容的版本，以預先要到的 ID 建檔重送時回已經存在（409）。譜庫讀寫的測試一律用它，不接真的 Google
  - `tests/fake_browser.py`：開瀏覽器的替身，注入給本機回呼與登入服務；拿到授權網址後在背景真的對其中的 `127.0.0.1` 回呼（`redirect_uri`，沒有時 `redirect_to`）送 HTTP 請求，帶 `code` 或 `error`，或設成永遠不送；建立時傳入提供者（例如假 HuaXia 的 `authorize`）時，授權碼由它依授權網址發給
  - `tests/fake_huaxia.py`：假 HuaXia，記憶體裡的 Supabase，當作 HTTP session 注入 `HuaXiaClient`：Auth（PKCE 兌換、續期與輪替，重用用過的續期權杖會撤銷 session、`scope=local` 與 global 登出）、PostgREST 的讀取（照 HuaXia 的 RLS，含曲目 `add_piece`）、`rpc/is_officer` 與發譜函式的版本查詢（預設沒有，`install_publish_function` 裝上指定版本）；新的 Discord 帳號第一次登入時建立待核准的帳號。可設成連不上，或讓續期卡住直到放行。HuaXia 的測試一律用它，不接真的 HuaXia
  - `tests/synthetic_pdfs.py`：以 PyMuPDF 產生的合成掃描分譜（8-bit 灰階、JPEG、1-bit、未壓縮的串流、加密、宣告錯誤的 JPEG），測壓縮服務與壓縮檢查；壓縮服務的測試不接替身，以注入的大小上限代替 20MB

## 文件

`docs/notes/` 是帶日期的史料（例如架構審查報告），為當時的快照，不隨程式碼更新。
