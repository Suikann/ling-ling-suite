# Naming

## Template System

### 通用大模板（Master Template）

- 套用於所有未指定小模板的群組
- 定義預設的命名規則
- 範例：`{序號}. {樂器} - {曲名}.pdf`

### 曲目小模板（Track-Specific Template）

- 建立時複製大模板字串作為初始值，使用者自行修改
- 完全取代大模板對該群組內檔案的命名
- 每個群組可各自指定一個小模板

### 群組（Group）

一個群組代表「一首曲目的一個樂章」：
- 每個群組有自己的樂器表（聲部清單）；專案層級的舊樂器表只在載入舊專案時遷移進群組，不傳入命名
- 使用者排列檔案順序，第 N 份分譜對應第 N 個聲部；多於聲部數的分譜不改名
- 各群組獨立設定 `{曲名}`、`{樂章編號}`、`{樂章名稱}`

### 模板變數

| 變數 | 層級 | 來源 | 範例 |
|------|------|------|------|
| `{序號}` | 逐檔不同 | 聲部在樂器表中的位置，至少兩位數（超過 99 個聲部才用三位）；總譜固定 `00` | `01`, `02`；`001`…`100` |
| `{樂器}` | 逐檔不同 | 樂器表，依排序對應；總譜代入總譜標籤 | `Flute`, `Violin I` |
| `{曲名}` | 群組層級 | 檔案進入群組時從分譜檔名共同部分自動偵測一次，使用者可覆寫或按「自動偵測」重猜 | `Beethoven Sym.5` |
| `{樂章編號}` | 群組層級 | 使用者輸入 | `1`, `2`, `3` |
| `{樂章名稱}` | 群組層級 | 使用者輸入 | `Allegro`, `Adagio` |
| `{作曲家}` | 群組層級 | 使用者輸入 | `Beethoven`, `Mozart` |
| `{曲種}` | 群組層級 | 使用者輸入 | `交響曲`, `協奏曲` |

變數清單、中英文名稱與層級都定義在 `core/constants.py` 的 `TEMPLATE_VARIABLES`（`level` 為 `VariableLevel.GROUP`／`FILE`）；新增群組層級變數只改常數（`source` 指向 `Group` 的欄位）。

### 命名格式套用（`core/naming.py`）

總譜與分譜、本機與 Drive 都經過同一個純函式模組，不碰磁碟、不讀介面語言：
- `name_slot(group, slot, settings, voices=None)`：一格（`SCORE_SLOT`＝0 為總譜，N 為第 N 份分譜）→ `SlotName(file_name, folders)`；`name_group` 列出整個群組（總譜在前），多於聲部數的分譜放在 `extra_files`、不改名。明確傳入的樂器表（`voices`）優先於群組的樂器表，空的視同未傳入
- `settings_for(project, group)` 組出命名設定：有小模板用小模板；子資料夾模板只在開啟子資料夾輸出時套用；分譜存放模式（`PartsOutputMode`：根目錄／分譜資料夾／聲部組資料夾）只影響分譜，總譜留在子資料夾那一層
- 命名格式只掃描一次，代入的值不再被替換（曲名是 `{Instrument}` 時原樣保留）；不是模板變數的 `{…}` 從檔名與資料夾名拿掉（`unknown_variables` 列出它們；`variables_in` 列出命名格式裡的全部名稱，配合 `variable_level` 可找出子資料夾模板用了哪些逐檔變數）
- 每個產出都清理非法字元，檔名補 `.pdf`；任何一層資料夾清理後是 `.` 或 `..` 時拋出 `UnsafeFolderNameError`，預覽顯示阻擋警告並停用執行
- 檔名用語跟專案、不跟介面：總譜標籤在群組建立時寫入；聲部組資料夾模式下，還沒有聲部組的聲部在預檢前由 `rename_service.assign_default_sections` 寫進編制設定（`update_ensemble`；語言由呼叫端依當時的介面語言傳入，產生計畫本身不改專案），之後切換介面語言不影響。聲部組的規則只在 `section_for`：已設定且不是空白就用設定的，否則依指定語言偵測（`detect_instrument_section`）；編制設定對話框與匯出編制表也用它
- Drive 重新命名（`services/drive_rename_service.py`）：`generate_drive_rename_plan(groups, template, voices)` 經 `name_group` 產生計畫與撞名（`DriveRenamePlan.entries`／`conflicts`），只改檔名、不放資料夾，不改寫群組（對話框清空樂器框就用群組自己的樂器表）。撞名＝重新命名後同一群組（同一個 Drive 資料夾）內同名，以 `name_key` 比對、不分大小寫，不改名的多出分譜也算；有撞名時對話框停用執行。Google 用戶端程式庫只在 Drive 的 adapter（`drive_service.py`）載入，計畫與撞名偵測不需要它

---

## Subfolder Output

專案層級開關：「將各群組分別放入子資料夾」。

啟用後，各群組的總譜與分譜放進以子資料夾模板命名的資料夾。子資料夾只依群組區分，模板只接受群組層級變數（`{曲名}`、`{樂章編號}`、`{樂章名稱}`、`{作曲家}`、`{曲種}`，含英文名稱）；用了逐檔變數（`{序號}`、`{樂器}`，含英文名稱 `{Number}`、`{Instrument}`）時預覽阻擋並指出要拿掉的變數。哪些變數屬於群組層級由 `TEMPLATE_VARIABLES` 的 `level` 決定。

```
範例：
資料夾名稱模板: {曲名} - 第{樂章編號}樂章

結果:
2026冬季/
  貝多芬第五號交響曲 - 第1樂章/
    01. Flute.pdf
    02. Oboe.pdf
  莫札特第21號鋼琴協奏曲 - 第1樂章/
    01. Flute.pdf
    02. Oboe.pdf
```
