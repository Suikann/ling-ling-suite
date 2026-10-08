# Project

## Project Editing

專案（`core/models.py` 的 `Project`）自己管理修改、檔案引用與未存檔狀態；純記憶體，不匯入 Qt、不做檔案 I/O。

- UI 只表達意圖並立即寫入：群組欄位（`update_group`）、新增／匯入／刪除群組（`add_group`、`add_groups`、`delete_group`）、檔案搬進群組或移回未分組（`move_to_group`、`move_to_ungrouped`）、加入檔案（`add_files`）、指定與清除總譜（`set_score`、`clear_score`）、分譜排序（`reorder_files`）、樂器表（`set_instruments`）、連結樂章（`link_movements`）、大模板（`set_master_template`）、輸出設定（`set_output_settings`）、編制設定（`update_ensemble`）、切換語言改寫模板變數（`convert_template_language`）、套用搬移結果（`replace_paths`）、移除引用（`remove_paths`）、套用分割結果（`apply_split`）、退回分割（`revert_split`）。UI 不直接指定群組、檔案資訊或專案的欄位；群組分頁、樂器表、預覽對話框不呼叫主視窗的私有方法，影響其他分頁的修改以分頁的 `groups_changed` 訊號讓主視窗重建分頁
- 舊欄位鏡像（全選的 `selected_instruments`、`use_parts_subfolder`）由上述操作與載入流程維持，呼叫端不碰
- **未存檔＝快照比對**：`is_modified()` 比對目前內容（`to_data()`，即專案檔會存的內容，不含版本號）與上次存檔或開啟時的快照；改回原值就回到已存檔。舊格式的遷移（總譜標籤留空補上依目前介面語言的預設值、承接專案層級樂器表、勾選子集收成群組樂器表、舊分譜子資料夾旗標）在 `from_data` 內、拍快照之前完成，所以開啟舊專案不標記未存檔。群組的遷移只對舊版程式寫的專案檔做（`version` 在 `LEGACY_PROJECT_FILE_VERSIONS`，沒有這一欄也算；存檔寫 `PROJECT_FILE_VERSION`，其餘欄位不變），新版存的檔裡留空的總譜標籤與樂器表是使用者清掉的，再開啟不會補回。開啟專案、切換分頁不改內容，不標記；切換介面語言改寫了命名格式就標記
- 每個編輯操作與存檔、開啟後都發出同一個「已變更」通知（`subscribe`，純 callback）；主視窗只訂閱它來更新標題的 `*`。存檔與預覽前沒有把畫面收回模型的步驟
- 關閉程式前，再用 `ProjectAccess.matches_file` 把目前內容和磁碟上的專案檔（照開啟方式還原、含遷移）比對一次；不同、讀不到或檔案已不在都照常詢問是否儲存。專案還沒存過檔時沒有檔案可比，照快照判定（新專案沒動過就直接關閉）
- **自動偵測只在檔案進入群組時做一次**：匯入資料夾建立群組、從勾選建立群組、把檔案搬進群組、加入檔案、分割結果加入群組。沒有總譜才依 `SCORE_KEYWORDS` 猜總譜，沒有曲名才由分譜檔名猜曲名，已有的不覆蓋；建立分頁與開啟專案時不偵測，使用者清掉的總譜或曲名不會被設回。想重猜曲名時按「自動偵測」（`guess_piece_name`）
- **總譜標籤在群組建立時寫入**：依當時的介面語言寫入預設值，之後切換介面語言不影響

---

## Project Access

開啟與存檔一律經過專案存取（`services/project_access.py` 的 `ProjectAccess`），主視窗只呼叫它並依結果顯示訊息。它擁有專案檔讀寫、最近專案清單、工作區 meta 的所屬專案與已知專案清單。

- **專案檔本身讀寫成功就算成功**：`open`／`save` 回傳 `AccessResult`，`error` 只反映專案檔本身。最近清單寫不進去（`recent_failed`）或 meta 寫不進去（`owner_failed`）只在狀態列提示，不跳錯誤框，也不擋下關閉、開新專案或開啟其他專案（推翻 #16 原本「最近清單寫不進去也算存檔失敗」的決定）
- 兩個附帶動作各自進行，一個失敗不影響另一個：開啟時最近清單寫不進去，meta 的所屬專案照常更新
- 存檔：專案檔寫成後，專案的快照就是寫出的內容（`ProjectService.save_project` 寫完才 `mark_saved`）；寫不成時顯示「儲存失敗」、不做任何附帶動作，專案仍判為未存檔
- 開啟：讀不到或格式不對時顯示「無法開啟專案」，不換掉目前的專案；專案檔已不存在時另外從最近清單移除（讀不到的留在清單上）。從最近清單開啟時先以 `forget_if_missing` 檢查，不存在就提示「找不到檔案」並移除，不先問是否儲存。開啟結果帶出專案引用、但找不到的檔案（`missing_files`），主視窗據此提醒
- 最近清單：開啟或存檔的專案放到頂端，同一個檔案（任何寫法）只留一筆，最多 `MAX_RECENT_PROJECTS` 筆；存在偏好設定裡（`PreferencesService` 只負責存放，檔案位置 `PREFERENCES_FILE` 由建構時注入，預設值 `DEFAULT_PREFERENCES`）
- 已知專案（`known_projects`）＝最近清單＋各工作區子資料夾 `meta.project_path` 指向的專案檔，是「清理工作區」載入以判定引用關係的候選（`scan_workspace`）；掃描順手把最近清單中已不存在的專案檔移除

---

## File Import

| 匯入方式 | 行為 |
|----------|------|
| 選擇多個 PDF 檔案 | 全部匯入至未分組清單，使用者自行建立群組 |
| 選擇資料夾（無子資料夾） | 匯入該資料夾內所有 PDF，視為一個群組 |
| 選擇資料夾（有子資料夾） | 每個含有 PDF 的子資料夾自動建立一個群組，子資料夾名稱作為群組名稱（`{曲名}` 由檔名偵測，偵測不到時留空） |
| 選擇多個資料夾 | 每個資料夾各自依上述規則處理 |

僅掃描一層子資料夾，不遞迴深入。僅處理 `.pdf` 檔案。
