# Naming

命名把群組裡的每一格（總譜，或第 N 份分譜）依命名格式轉成檔名與資料夾。本機重新命名、預覽與 Drive 重新命名都經過 `core/naming.py`；它是純函式，不碰磁碟、不讀介面語言。

## 模板

- **大模板**（Master Template）：專案層級的命名格式，套用於沒有啟用小模板的群組
- **小模板**（Track-Specific Template）：每個群組一個；啟用（`use_small_template`）且不是空字串時，完全取代大模板對該群組的命名，留空就用大模板
- `settings_for(project, group)` 依上述規則組出命名設定（`NamingSettings`）；子資料夾模板只在開啟子資料夾輸出時放進設定

## 模板變數

變數的中英文名稱、層級（`VariableLevel.FILE`／`GROUP`）與值的來源（`source`）都定義在 `core/constants.py` 的 `TEMPLATE_VARIABLES`；命名、變數選單與語言轉換都讀它。

- **逐檔變數**，每一格不同：
  - `{序號}`：聲部在樂器表中的位置，至少兩位數，位數隨樂器表長度增加（100 個聲部以上用三位）；總譜固定 `00`
  - `{樂器}`：依排序對應的聲部名稱；總譜代入該群組的總譜標籤
- **群組層級變數**：其餘變數，值取自 `Group` 的欄位。使用者在群組分頁輸入；`{曲名}` 另會在檔案進入群組時自動偵測（見 [project.md](project.md)），連結樂章（`Project.link_movements`）與 Drive 譜庫的中繼資料也會填入
- 新增群組層級變數：在 `TEMPLATE_VARIABLES` 加一筆，`source` 指向 `Group` 的欄位。`Group` 還沒有這個欄位時，另要加欄位並更新 `Group.EDITABLE_FIELDS`、`Group.to_data`／`from_data`、群組分頁的輸入欄（`ui/group_panel.py`）、Drive 對話框的中繼資料同步（`ui/drive_rename_dialog.py`）與 `tests/test_naming.py` 的 `EXPECTED`

## 套用命名格式

- `name_slot(group, slot, settings, voices=None)` 把一格（`SCORE_SLOT` 為總譜，N 為第 N 份分譜）轉成 `SlotName(file_name, folders)`；`name_group` 列出整個群組，總譜在前
- 第 N 份分譜對應樂器表的第 N 個聲部；多於聲部數的分譜放在 `extra_files`，不改名
- 樂器表：明確傳入的 `voices` 優先，空的視同未傳入；否則用群組自己的樂器表（命名只讀群組的樂器表）
- 分譜存放模式（`PartsOutputMode`：根目錄／分譜資料夾／聲部組資料夾）只影響分譜，總譜留在子資料夾那一層
- 命名格式只掃描一次，代入的值不再被替換（曲名是 `{Instrument}` 時原樣保留）
- 不是模板變數的 `{…}` 從檔名與資料夾名拿掉，`unknown_variables` 列出它們；`variables_in` 列出命名格式裡的全部名稱，配合 `variable_level` 找出子資料夾模板用了哪些逐檔變數
- 每個產出都清理非法字元，檔名補 `.pdf`；任何一層資料夾清理後是 `.` 或 `..` 時拋出 `UnsafeFolderNameError`，預覽顯示阻擋警告並停用執行

## 檔名用語跟專案、不跟介面

命名不讀介面語言；要依語言決定的值在寫進專案時定下，之後切換介面語言不影響檔名：

- **總譜標籤**：群組建立時依當時的介面語言寫入預設值；開啟舊格式專案時，留空的也依當時的語言補上（見 [project.md](project.md) 的舊格式遷移）；使用者可在群組分頁修改
- **聲部組**：聲部組資料夾模式下，預檢前由 `rename_service.assign_default_sections` 把還沒有聲部組的聲部寫進編制設定（`update_ensemble`，語言由主視窗依當時的介面語言傳入）；產生計畫本身不改專案。`name_slot` 直接讀編制設定，所以這一步必須先做
- 聲部組的規則只在 `section_for`：已設定且不是空白就用設定的，否則依指定語言偵測（`detect_instrument_section`）。編制設定對話框與「匯出編制表」也用它；「匯出編制表」在介面為英文時另把中文聲部組名換成英文

## 子資料夾輸出

專案層級開關「將各群組分別放入子資料夾」，在預覽對話框裡。開啟後各群組的總譜與分譜放進以子資料夾模板命名的資料夾。子資料夾只依群組區分，所以子資料夾模板只接受群組層級變數；用了逐檔變數時，預覽阻擋（`RenameVerdict.folder_variables`）並指出要拿掉的變數。

## Drive 重新命名

- `services/drive_rename_service.py` 的 `generate_drive_rename_plan(groups, template, voices=None)` 經 `name_group` 產生計畫與撞名（`DriveRenamePlan.entries`／`conflicts`）：只改檔名、不放資料夾，不改寫群組（對話框清空樂器框就用群組自己的樂器表）
- 撞名＝重新命名後同一群組（同一個 Drive 資料夾）內同名，以 `name_key` 比對、不分大小寫，不改名的多出分譜也算；有撞名時對話框停用執行
- 計畫、撞名偵測與 Drive 重新命名對話框不載入 Google 用戶端程式庫，只有 adapter（`services/drive_service.py`）載入（`tests/test_drive_rename.py` 驗證）
