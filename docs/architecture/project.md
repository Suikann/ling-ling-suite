# Project

## 專案編輯

專案（`core/models.py` 的 `Project`）自己管理修改、檔案引用與未存檔狀態，是純記憶體的物件（`core/` 的界線見 [CODING_STANDARDS.md](../../CODING_STANDARDS.md)）。

- UI 只表達意圖並立即寫入：修改一律呼叫 `Project` 的公開編輯操作（`update_group`、`add_group`、`move_to_group`、`apply_split`、`guess_piece_name`…，完整清單看 `Project` 的公開方法），不直接指定 `Project`、`Group`、`FileInfo` 的欄位
- 舊欄位鏡像（`selected_instruments`、`use_parts_subfolder`）由編輯操作與載入流程維持，呼叫端不碰
- 群組分頁、樂器表、預覽對話框不呼叫主視窗的私有方法：群組分頁以 `groups_changed` 讓主視窗重建分頁（影響其他分頁的修改），樂器表發出 `instruments_changed`，預覽對話框用主視窗注入的 callback（`check`、`on_execute`）
- 同一份樂器表不重名：新增（`add_instrument`）或改名（`rename_instrument`）成與其他列同一聲部的名稱（`core/voice_names.py` 的規則，例如已有「二胡 I」時的「二胡1」）時丟出 `DuplicateVoiceError`、樂器表不變，樂器表編輯器顯示說明並還原名稱；改成自己的另一種寫法不算重名。整份換掉樂器表（`set_instruments`：預設樂器表、從檔名擷取）、移動與移除不檢查，舊專案已有的重名原樣保留
- 樂器表顯示目前分頁那個群組的樂器表；目前分頁不是群組（未分組）時清空並停用，輸入的樂器才不會沒有地方寫入（`InstrumentListEditor.show_group`）

### 聲部與分譜的配對

- 群組的樂器表逐列帶「本專案有」（`Group.present`，與 `instruments` 逐列對應，存進專案檔；沒有這一欄的舊檔全都有）。它只存在專案：這場缺某份分譜時取消勾選，不必刪掉聲部
- 分譜依序配給本專案有的聲部：第 N 份分譜配第 N 個本專案有的聲部（`Group.pairing`），本專案沒有的聲部跳過；多出的分譜（`Group.extra_files`）沒有聲部、不改名。序號仍是聲部在樂器表中的位置（見 [naming.md](naming.md)）
- 編輯操作讓分譜跟著聲部：
  - 移動聲部（`move_instruments`）：分譜跟著各自的聲部走。沒有分譜的聲部移到有分譜的聲部之前時自動改為本專案沒有，後面的分譜才不會錯位
  - 單獨排分譜（`reorder_files`）：樂器表不動，只改配對
  - 改名（`rename_instrument`）：分譜留在原位，跟著改名後的聲部；「本專案有」不變
  - 取消勾選（`set_present(…, False)`）：它的分譜移到未分組尾端，其他聲部的分譜不動，回傳移走的分譜。重新勾選的聲部沒有分譜，依上面的規則配到下一份，後面的各往後配一個聲部
  - 移除聲部（`remove_instruments`）：被移除的聲部的分譜移到未分組尾端，其他的不動，回傳移走的分譜
  - 整份換掉樂器表（`set_instruments`）與分割的結果（`apply_split`）照位置配對，每個聲部都有。復原分割時連同「本專案有」退回（`SplitPlacement.present`）

### 樂團類型與聲部組

- 樂團類型（`Project.orchestra_type`，`OrchestraType`）是專案層級：國樂團（吹管、拉弦、彈撥、打擊、低音）或管弦樂團（木管、銅管、打擊、鍵盤、弦樂），兩套都另有「其他」（`ORCHESTRA_SECTIONS`）。新專案是國樂團；主視窗底部的「樂團類型」選單以 `set_orchestra_type` 改它
- 聲部組對照（`instrument_sections`）以樂器為鍵：同一樂器（`core/voice_names.py` 的 `instrument_key`，去掉結尾編號）的各個聲部共用一個值，鍵保留第一次寫入的寫法。改聲部組一律經 `set_section(group, voice, section)` 或 `update_ensemble(sections=…)`，同一樂器已有鍵就改它的值；`set_section` 回傳這個群組裡跟著改的其他編號，`same_instrument_voices` 在改之前列出它們，編輯人數對話框寫在選單旁（例如「二胡 II 也會跟著改」）
- 值一律是中文的聲部組名稱，介面為英文時只在顯示時換成英文（`SECTION_NAMES_EN`）
- 沒有存值的樂器依樂團類型以關鍵字偵測（`core/sections.py` 的 `detect_section`、`SECTION_KEYWORDS`）：國樂團的胡類歸拉弦，大提琴、低音提琴、革胡歸低音，偵測不到的歸「其他」。`section_of` 取存值或偵測值；改樂團類型時已存的值不變，只有沒存的改依新的那套偵測
- 選單的選項是專案樂團類型那一套（`section_options`）；已存的值不在選項裡時另外列出

### 未存檔狀態

- 未存檔＝快照比對：`is_modified()` 比對目前內容（`to_data()`，即專案檔會存的內容，不含版本號）與上次存檔或開啟時的快照；改回原值就回到已存檔
- 開啟專案、切換分頁不改內容，不標記；切換介面語言改寫了命名格式就標記
- 每個編輯操作與存檔、開啟後都發出同一個「已變更」通知（`subscribe`，純 callback），主視窗以它更新標題的 `*`；存檔與預覽前沒有把畫面收回模型的步驟
- 關閉程式前，再用 `ProjectAccess.matches_file` 把目前內容和磁碟上的專案檔（照開啟方式還原、含遷移）比對一次；不同、讀不到或檔案已不在都詢問是否儲存。還沒存過檔的專案照快照判定（新專案沒動過就直接關閉）

### 舊格式遷移

- 遷移都在 `from_data` 內、拍快照之前完成，所以開啟舊專案不標記未存檔
- 群組的遷移只對舊版程式寫的專案檔做（`version` 在 `LEGACY_PROJECT_FILE_VERSIONS`，沒有這一欄也算）。內容：留空的總譜標籤補上依目前介面語言的預設值、專案層級的樂器表移進群組、勾選子集收成群組樂器表、舊分譜子資料夾旗標
- 專案層級的樂器表只在這裡遷移；命名與分割都只讀群組的樂器表
- **格式 3**（`SECTIONS_BY_INSTRUMENT_FILE_VERSION`）：比它舊的檔（含上一條的舊版）另遷移聲部組（`core/sections.py`）。專案層級的建議人數（`instrument_headcounts`）不動
  - 樂團類型（檔裡沒有這一欄時）：已存的聲部組優先，沒存的聲部以國樂團的關鍵字偵測；出現吹管或彈撥就是國樂團，其餘為管弦樂團（`guess_orchestra_type`）
  - 聲部組（`migrate_sections`）：英文值先對回中文；國樂團中不在五組內的值（舊的弦樂、自訂文字等）依國樂團重新偵測，偵測不到的歸「其他」；管弦樂團的值不動。轉成以樂器為鍵時，同一樂器有多個值就依樂器表順序取第一個出現的值，鍵是那個聲部的寫法；留空的值丟掉
- 存檔寫 `PROJECT_FILE_VERSION`，其餘欄位不變；新版存的檔裡留空的總譜標籤與樂器表是使用者清掉的，再開啟不補回

### 較新的格式

- `version` 比 `PROJECT_FILE_VERSION` 新的專案檔由較新版本的泠靈建立：照樣開啟，但 `Project.read_only`，看不懂的欄位不讀進來
- 唯讀的專案不能存檔，也不提供另存（另存同樣會丟掉看不懂的欄位）：`ProjectService.save_project` 丟出 `ReadOnlyProjectError`、什麼都不寫；主視窗開啟時寫明「這個專案檔由較新版本的泠靈建立」，停用「儲存專案」與「另存新檔」，標題標「唯讀」，開新專案、開啟其他專案與關閉時不詢問是否儲存
- 已發佈的舊版泠靈沒有這道檢查，所以 README 寫明升級後不要再用舊版開專案

### 自動偵測

- 偵測只在檔案進入群組時做：匯入資料夾建立群組、從勾選建立群組、把檔案搬進群組、加入檔案、分割結果加入群組。沒有總譜才依 `SCORE_KEYWORDS` 猜總譜，沒有曲名才由分譜檔名猜曲名，已有的不覆蓋
- 建立分頁與開啟專案時不偵測：使用者清掉的總譜或曲名，要等下一次有檔案進入該群組才會照上一條重猜
- 「自動偵測」按鈕（`guess_piece_name`）重猜曲名，取代目前的值

## 專案存取

開啟與存檔一律經過專案存取（`services/project_access.py` 的 `ProjectAccess`），主視窗只呼叫它並依結果顯示訊息。它擁有專案檔讀寫、最近專案清單、工作區 meta 的所屬專案與已知專案清單。

- **專案檔本身讀寫成功就算成功**：`open`／`save` 回傳 `AccessResult`，`error` 只反映專案檔本身。附帶動作寫不進去（最近清單 `recent_failed`、meta 的所屬專案 `owner_failed`）只在狀態列提示，不跳錯誤框，也不擋下關閉、開新專案或開啟其他專案（推翻 #16 原本「最近清單寫不進去也算存檔失敗」的決定）。兩個附帶動作各自進行，一個失敗不影響另一個
- 存檔：專案檔寫成後，專案的快照就是寫出的內容（`ProjectService.save_project` 寫完才 `mark_saved`）；寫不成時顯示「儲存失敗」、不做任何附帶動作，專案仍判為未存檔
- 開啟：讀不到或格式不對時顯示「無法開啟專案」，不換掉目前的專案；專案檔已不存在時另外從最近清單移除（讀不到的留在清單上）。從最近清單開啟時先以 `forget_if_missing` 檢查，不存在就提示「找不到檔案」並移除，不先問是否儲存。開啟結果帶出專案引用、但找不到的檔案（`missing_files`），主視窗據此提醒
- 開啟與存檔時更新引用到的工作區子資料夾的 `meta.project_path`；`update_project_path` 回傳寫入失敗的子資料夾，就是上面的 `owner_failed`
- 最近清單：開啟或存檔的專案放到頂端，同一個檔案（任何寫法）只留一筆，最多 `MAX_RECENT_PROJECTS` 筆；規則由 `ProjectAccess` 維護，存在偏好設定裡（`PreferencesService` 只負責存放）
- **已知專案**（`known_projects`）＝最近清單＋各工作區子資料夾 `meta.project_path` 指向的專案檔，是「清理工作區」判定引用關係時載入的候選（`scan_workspace`，見 [workspace.md](workspace.md)）；掃描順手把最近清單中已不存在的專案檔移除

## 匯入

- 匯入檔案（可多選）：只收存在的 `.pdf`，全部進未分組
- 匯入資料夾（一次一個，`ImportService.import_folder`）：有含 PDF 的子資料夾時，每個這樣的子資料夾建一個以它命名的群組，根目錄的 PDF 進未分組；沒有時整個資料夾建一個以它命名的群組。只掃一層子資料夾、只收 `.pdf`；總譜、曲名與總譜標籤在群組加進專案時處理（見上方自動偵測）
