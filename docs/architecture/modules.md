# Modules

## Architecture

```
src/
  main.py                        - 應用程式進入點（QApplication、深色主題、單一實例）
  assets/                        - 靜態資源（svg 圖示）
  ui/                            - PySide6 UI 元件
    main_window.py               - 主視窗
    file_list.py                 - 檔案清單（支援拖拉排序）
    instrument_list.py           - 樂器表編輯器
    group_panel.py               - 群組管理面板
    preview_dialog.py            - 預覽與衝突警告對話框
    split_dialog.py              - PDF 分割對話框（縮圖標記分割點、命名、詢問與顯示；檢查與執行交給分割模組）
    rotate_dialog.py             - PDF 旋轉對話框（分段設定角度）
    merge_dialog.py              - 連結樂章對話框
    page_preview.py              - 頁面預覽對話框
    catalog_window.py            - 譜庫瀏覽器視窗（Google Sheets／Drive）
    catalog_settings_dialog.py   - 譜庫設定對話框
    drive_rename_dialog.py       - Drive 重新命名對話框
    workspace_dialog.py          - 工作區清理對話框
    widgets.py                   - 共用增強元件與 UI 輔助函式（如找不到檔案的提示；分割與旋轉對話框共用的背景縮圖載入，只交出最近一次載入的結果）
  core/                          - 模板引擎、資料模型、常數定義
    constants.py                 - 模板變數定義（含層級欄位）、分譜存放模式、重新命名問題的處理方式（RENAME_PROBLEM_RULES）、介面語言代碼、專案檔格式版本、預設值、應用程式路徑等常數
    filename.py                  - 檔名清理（非法字元換底線、空名回退），重新命名與分割共用
    naming.py                    - 命名格式套用：群組中的一格（總譜或第 N 份分譜）＋命名設定 → 檔名＋相對資料夾；純函式，本機重新命名、預覽與 Drive 重新命名共用；聲部組預設值的規則（section_for、default_sections）也在這裡
    paths.py                     - 路徑同一性（path_key、same_path：絕對路徑、不分大小寫），全程式比對路徑只用它；name_key 以同一規則比對沒有本機路徑的名稱（Drive 檔名）
    catalog_constants.py         - 譜庫相關常數
    template_engine.py           - 曲名／總譜／樂器偵測、模板變數雙語轉換
    models.py                    - 資料模型（Project、Group、Template、FileInfo）；Project 是專案編輯模組：意圖層級的編輯操作、「已變更」通知、未存檔快照、專案檔內容（to_data／from_data，含舊格式遷移）；Project.file_refs() 是「總譜＋分譜＋未分組」的唯一走訪，依路徑取代／移除引用也在這裡；群組欄位的編輯規則在 Group.update（不屬於專案的 Drive 群組也用它）
    catalog_models.py            - 譜庫資料模型
    locale.py                    - 國際化系統（zh_TW／en 介面字串）；依介面語言挑選中英文版本一律經過 is_english／localized
  services/                      - 檔案操作、PDF 處理、雲端整合
    file_service.py              - 檔案系統操作（讀取、重新命名、建立資料夾、JSON 讀取與原子寫入、直接刪除程式自產的檔）；服務的檔案存取都經過注入的它，測試可從這裡注入失敗
    import_service.py            - 檔案/資料夾匯入與自動分組
    rename_service.py            - 重新命名計畫（RenamePlan）：命名結果接在輸出位置之下，帶出多出的檔、子資料夾模板的逐檔變數與未知變數；重複目標加後綴；產生計畫不改專案，聲部組由預檢前明確呼叫的 assign_default_sections 寫進專案。預檢與執行在 move_history
    move_history.py              - 搬移歷程：重新命名、復原、重做、中斷還原（還原或保留結果）的唯一入口，四個動作回傳同一種結果（MoveResult）；重新命名預檢（check_rename → RenameVerdict）也在這裡，rename 執行的就是判定的計畫；擁有復原／重做堆疊、進行中紀錄、工作區 meta 快照，也組裝分割與旋轉的復原紀錄
    move_service.py              - 搬移歷程內部的兩階段批次搬移引擎（驗證、對調／連鎖、回滾、進行中紀錄的讀寫與中斷後還原）；只有 move_history 使用，執行前驗證的規則（find_problems）與預檢共用
    instance_lock.py             - 單一實例鎖（作業系統檔案鎖，程式結束或當機時自動解除）
    pdf_service.py               - PDF 頁數、頁面擷取、分段旋轉、縮圖產生
    split_service.py             - 分割模組：check 回報要產生的分譜、需確認與擋下的事，execute 一次執行、失敗整批撤回；工作區子資料夾的定位與所屬專案改寫只在這裡
    project_service.py           - 專案檔的序列化讀寫（內容由 Project.to_data／from_data 決定）、與磁碟上的專案檔比對（matches_file）
    project_access.py            - 專案存取：開啟與存檔（專案檔、最近專案清單、工作區 meta 所屬專案）、已知專案清單、清理工作區的掃描；主視窗開啟、存檔、關閉前比對都只經過它
    preferences_service.py       - 使用者偏好（語言、外觀、最近專案清單、譜庫設定）的存放；檔案位置建構時注入，預設值在 constants
    workspace_service.py         - 工作區（分割輸出的暫存地）管理：meta.json 讀寫、檔案所屬的子資料夾、掃描、清理
    google_auth_service.py       - Google API OAuth 認證
    sheets_service.py            - Google Sheets 譜庫存取
    drive_service.py             - Google Drive 檔案存取
    drive_rename_service.py      - 透過 Drive API 重新命名譜庫檔案
tests/                           - pytest 測試（template_engine、naming、filename、rename_plan、rename_preflight、move_history、split_service、import、project、project_access、workspace、pdf_service、locale、instance_lock、drive_rename；main_window、split_dialog、Drive 重新命名對話框、分割與旋轉的選檔清單以 offscreen Qt 測 UI 接線）；conftest 把使用者資料目錄導到暫存目錄；path_spellings 提供同一路徑的不同寫法；failing_writes 提供寫到指定檔案就失敗的檔案服務；other_instance 以子行程扮演另一個持有單一實例鎖的程式
GLOSSARY.md                      - 領域詞彙表（總譜、分譜、合併譜、群組、工作區…）
docs/adr/                        - 架構決策紀錄
docs/notes/                      - 審查報告等史料（檔名帶日期，為當時快照，不隨程式碼更新）
```
