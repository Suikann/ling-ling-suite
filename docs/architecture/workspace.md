# Workspace

工作區（`WORKSPACE_DIR`）只放程式產生、尚待重新命名的檔案，目前只有分割輸出；使用者匯入的檔案永遠不進工作區（[ADR 0001](../adr/0001-workspace-only-for-generated-files.md)，是設計規則，匯入流程本身不檢查）。分割的分譜預設寫進工作區，重新命名時才搬到輸出位置；也可以改寫到指定資料夾。

## 分割

分割由分割模組（`services/split_service.py` 的 `SplitService`）負責，分 `check` 與 `execute` 兩段；分割對話框只標記分割點、命名、詢問與顯示。

### 檢查

`check(SplitRequest)` 不寫任何檔，回傳 `SplitCheck`：

- `plan`：要產生的分譜；頁面全被刪掉的分段略過
- 擋下的問題（`blocked` 時對話框只列出原因）：`duplicates`，清理非法字元、空名回退為 `Part`、不分大小寫後檔名相同的分段，以分段編號列出；`source_conflicts`，輸出就是合併譜本身
- 需使用者確認的事：工作區模式為上次的分譜 `previous_outputs` 與其所屬的另一個專案 `owner`；指定資料夾模式為已有的同名檔案 `overwritten`
- 指定資料夾模式只把同名的檔當成被取代：自訂資料夾可能放著使用者自己的檔與其他合併譜的分譜，專案檔與資料夾都沒有記下哪些分譜出自哪份合併譜，無法可靠認出上次的輸出
- 分段自動帶入的聲部名稱讀合併譜所在群組的樂器表

### 執行

`execute(check)` 執行確認過的檢查結果，依序：

1. 新分譜全部寫進輸出資料夾裡的暫用子資料夾 `<代號>.splitting`
2. 位置會被新分譜佔用的被取代檔（`check.replaced` 之中）挪進 `<代號>.replaced`，檔名不變
3. 新分譜就位；`FileService.rename_file` 不覆蓋既有檔，是 `source_conflicts` 之後的第二道防線。工作區模式接著以 `WorkspaceService.write_meta` 改寫所屬專案
4. 被取代的檔移到資源回收桶：挪開的從 `<代號>.replaced` 移，其餘從原處移（從資源回收桶還原時回到原處）

1 到 3 任一步失敗就撤回已做的步驟後拋出：被取代的檔原封不動、所屬專案不變、不留新分譜，專案與復原紀錄也不動。第 4 步移不進資源回收桶的檔留在當時的位置，不影響已完成的分割。

### 套用到專案

`execute` 回傳 `SplitResult`（新分譜、聲部名稱、被取代的路徑 `replaced`、它們移到資源回收桶時所在的位置 `trashed`、新建的目錄）。主視窗接著：

1. 以 `Project.apply_split` 套用：移除被取代的引用；合併譜不論是分譜還是總譜都移出群組（未分組裡的留在未分組），重新命名才不會搬動它；接手新分譜的是引用被取代檔案的群組，其次是合併譜所在的群組，都沒有才另建以合併譜檔名命名的群組（重新分割不多出群組、不留空群組）
2. `apply_split` 回傳安置方式 `SplitPlacement`：接手群組的 id、是否另建、被改掉的樂器表與曲名的原值、合併譜原本的位置 `FilePosition`（群組 id、是否為總譜、在分譜清單中的位置；未分組時群組 id 為空）。復原分割靠它退回（見 [move-history.md](move-history.md)）
3. 切到接手的群組，把 `result.record(placement)`（`SplitRecord`，含被取代的檔移到資源回收桶時的位置與安置方式）交給 `MoveHistory.record_split`

## 工作區子資料夾

- 每個來源合併譜對應一個子資料夾，以來源合併譜為鍵、跨專案共用；名稱為來源路徑比對鍵（`path_key`）SHA-1 的前 8 碼（`SplitService.output_folder`）。這個名稱的子資料夾不存在、但有子資料夾的 `meta.source_path` 指向同一份來源時沿用後者（`WorkspaceService.find_folder`；升級前以保留大小寫的路徑雜湊建立，Linux／macOS 上名稱不同）
- `meta.json` 記錄 `source_path`、`source_name`、`project_path`、`created_at`；路徑皆為絕對路徑，專案未存檔時 `project_path` 為空字串
- **所屬專案**：另一專案重新分割同一份合併譜會取代前者尚未重新命名的分譜，確認訊息點名所屬專案（`SplitCheck.owner`），專案檔已不存在時加註「（找不到）」。分割成功後所屬專案一律改成目前專案，未存檔時記為空（取代過一次後再分割就是自己的嘗試，不再點名別人）。專案開啟與存檔時也會更新它（見 [project.md](project.md) 的專案存取）
- 搬空的子資料夾保留 `meta.json`：復原重新命名時分譜會搬回來，需要它辨識來源。復原紀錄裡的 meta 快照見 [move-history.md](move-history.md)

## 清理工作區

- 「工具 → 開啟工作區資料夾」以系統檔案總管開啟 `WORKSPACE_DIR`（資料夾還不存在時改為提示）
- 「工具 → 清理工作區」列出各子資料夾的引用狀態（使用中／屬於其他專案／屬於無法讀取的專案／未被引用／來源不明），只有「未被引用」預設勾選，刪除走資源回收桶
- 引用關係以目前專案與已知專案（見 [project.md](project.md)）的 `Project.file_refs()` 判定，未分組的檔案也算引用
- 掃描時才移除既未被目前專案、也未被任何已知專案引用的空資料夾（「空」指資料夾裡沒有直接放著的 PDF）。`meta.json` 是程式自產的中繼資料，直接刪除、不走資源回收桶，連同原子寫入殘留 `meta.json.tmp` 一起清（`FileService.remove_atomic_residue` 認得暫名）
