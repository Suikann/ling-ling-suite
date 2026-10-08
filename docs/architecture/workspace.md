# Workspace

分割產生的分譜預設先進工作區（`WORKSPACE_DIR`），重新命名時才搬到輸出位置；也可以指定資料夾。使用者匯入的檔案永遠不進工作區（見 [docs/adr/0001](../adr/0001-workspace-only-for-generated-files.md)）。

分割由分割模組（`services/split_service.py` 的 `SplitService`）負責，分兩段；分割對話框只標記分割點、命名、詢問與顯示：

- `check(SplitRequest)` 不寫任何檔，回傳 `SplitCheck`：要產生的分譜（`plan`，頁面全被刪掉的分段略過）；擋下的問題（`duplicates`：清理非法字元、空名回退為 `Part`、不分大小寫後檔名相同的分段，以分段編號列出；`source_conflicts`：輸出就是合併譜本身）；需確認的事（工作區模式為上次的分譜 `previous_outputs` 與其所屬的另一個專案 `owner`，指定資料夾模式為已有的同名檔案 `overwritten`）。`blocked` 時對話框只列出原因
- `execute(check)` 執行確認過的檢查結果：新分譜先全部寫進輸出資料夾裡的暫用子資料夾 `<代號>.splitting`；寫好後才把位置會被新分譜佔用的被取代檔（`check.replaced` 之中）挪進 `<代號>.replaced`（保留原檔名）、新分譜就位、（工作區模式）以 `WorkspaceService.write_meta` 改寫所屬專案；最後把被取代的檔移到資源回收桶：挪開的從 `<代號>.replaced` 移，其餘從原處移（從資源回收桶還原時回到原處）。任一步失敗就撤回已做的步驟後拋出：被取代的檔原封不動、所屬專案不變、不留新分譜，專案與復原紀錄也不動。移不進資源回收桶的檔留在當時的位置，不影響已完成的分割
- 執行回傳 `SplitResult`（新分譜、聲部名稱、被取代的路徑 `replaced`、它們移到資源回收桶時所在的位置 `trashed`、新建的目錄）。主視窗以 `Project.apply_split` 套用：移除被取代的引用；合併譜不論是分譜還是總譜都移出群組（未分組裡的留在未分組），重新命名才不會搬動它；接手新分譜的是引用被取代檔案的群組，其次是合併譜所在的群組，都沒有才另建以合併譜檔名命名的群組（重新分割不多出群組、不留空群組）。`apply_split` 回傳安置方式 `SplitPlacement`（接手群組的 id、是否另建、被改掉的樂器表與曲名的原值、合併譜原本的位置 `FilePosition`：群組 id、是否為總譜、在分譜清單中的位置，未分組時群組 id 為空），復原分割靠它退回。接著切到接手的群組，把 `result.record(placement)`（`SplitRecord`，含被取代的檔移到資源回收桶時的位置與安置方式）交給 `MoveHistory.record_split`
- 分段自動帶入的聲部名稱讀合併譜所在群組的樂器表；專案層級的舊樂器表只在載入舊專案時遷移
- 指定資料夾模式只把同名的檔當成被取代：自訂資料夾可能放著使用者自己的檔與其他合併譜的分譜，專案檔與資料夾都沒有記下哪些分譜出自哪份合併譜，無法可靠認出上次的輸出

工作區的規則：

- 每個來源合併譜對應一個子資料夾，名稱為來源路徑比對鍵（`path_key`：絕對路徑、不分大小寫）SHA-1 的前 8 碼（`SplitService.output_folder`）；還沒有這個子資料夾、但有子資料夾的 `meta.source_path` 指向同一份來源時（升級前以保留大小寫的路徑雜湊建立，Linux／macOS 上名稱不同）沿用後者（`WorkspaceService.find_folder`）。資料夾內 `meta.json` 記錄 `source_path`、`source_name`、`project_path`、`created_at`（路徑皆為絕對路徑，`project_path` 未存檔時為空字串）
- 子資料夾以來源合併譜為鍵、跨專案共用：另一專案重新分割同一份合併譜會取代前者尚未重新命名的分譜，確認訊息點名所屬專案（`SplitCheck.owner`），專案檔已不存在時加註「（找不到）」；分割成功後所屬專案一律改成目前專案，未存檔時記為空（取代過一次後再分割就是自己的嘗試，不再點名別人）
- 專案開啟與存檔時都由專案存取更新引用到的子資料夾的 `meta.project_path`（`update_project_path` 回傳寫入失敗的子資料夾，UI 只在狀態列提示、不阻止開啟或存檔；見 [project.md](project.md) 的 Project Access）
- 搬空的子資料夾保留 `meta.json`（復原重新命名時分譜會搬回來，需要它辨識來源）；「清理工作區」掃描時才移除既未被目前專案、也未被任何已知專案引用的空資料夾（`meta.json` 是程式自產的中繼資料，直接刪除不走資源回收桶；連同原子寫入殘留 `meta.json.tmp` 一起清，由 `FileService.remove_atomic_residue` 認得暫名）
- 復原紀錄寫入時對來源位於工作區的項目快照其子資料夾的 meta（`UndoRecord.workspace_meta`，同一資料夾的不同寫法只記一份；因此 `MoveHistory` 注入 `WorkspaceService`）；復原後子資料夾若已沒有可讀的 meta 就用快照寫回（`restore_meta`；回滾失敗卡在工作區的檔案、中斷還原搬回工作區的分譜、保留結果的復原也寫回，都在刪除進行中紀錄之前），寫回失敗不影響檔案復原；重做前先以子資料夾目前的 meta 更新快照；舊紀錄沒有此欄照常載入
- 「工具 → 開啟工作區資料夾」以系統檔案總管開啟 `WORKSPACE_DIR`
- 「工具 → 清理工作區」列出各子資料夾的引用狀態（使用中／屬於其他專案／屬於無法讀取的專案／未被引用／來源不明），只有「未被引用」預設勾選，刪除走資源回收桶。「已知專案」= 最近專案清單 + 各 `meta.project_path` 指向的專案檔（`ProjectAccess.known_projects`）；開啟對話框時會順手把最近清單中已不存在的專案檔移除
- 引用關係涵蓋群組內分譜、總譜與未分組檔案（`Project.file_refs()`）
- 分割輸出路徑等於來源合併譜時擋下（分割模組的檢查，`pdf_service.extract_pages` 再擋一層）
- `rename_file` 跨磁碟時複製到 `.part` 再就位，失敗不留半成品
