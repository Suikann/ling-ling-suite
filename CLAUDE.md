# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Role

你是一個專業的程式設計師。批判性地解讀用戶的發言，確保能找到問題的核心，而非單純支持用戶的心情。

## Code Style

- 注重可維護性、可讀性與模組化
- 遵循 SOLID 原則（SRP、OCP、LSP、ISP、DIP）
- 遵循 DRY 原則：避免重複邏輯，將共用邏輯抽取為可重用的函數或模組
- Use LF line endings
- 函式與方法內不留連續空行；頂層定義之間依 PEP 8 留兩行

### Docstring 格式規範

**模組層級 docstring**：
```python
# -*- coding: utf-8 -*-
"""
模組標題

詳細描述（可選）。

使用範例：
    from xxx import xxx
    result = xxx.method()
"""
```

**類別 docstring**：
```python
class ClassName:
    """類別簡短描述"""
```

**方法/函數 docstring**（Google Style）：
```python
def method_name(param1: str, param2: int) -> Dict:
    """簡短描述

    Args:
        param1: 參數描述
        param2: 參數描述

    Returns:
        返回值描述
    """
```

**重要規則**：
- 簡單方法（如 getter/setter）使用單行 docstring
- 複雜方法必須包含 Args 和 Returns 區塊
- 描述使用繁體中文
- 不使用 :param: 或 :return: 風格（使用 Google Style）

---

## SOLID Principles and DRY Principle

**CRITICAL: You MUST read this section before modifying ANY code.**

### SRP (Single Responsibility Principle)

One class = One responsibility. Split when mixing concerns.

**Bad Example** - UI 類別混入檔案操作：
```python
class MainWindow:
    def rename_files(self):  # WRONG: 業務邏輯不應出現在 UI
        for f in self.files:
            new_name = self._build_name(f)
            os.rename(f.path, new_name)
```

**Good Example** - 分離至 service：
```python
class MainWindow:
    def __init__(self):
        self.renamer = FileRenamer()

    def on_rename_click(self):
        self.renamer.rename_files(self.files, self.template)
```

---

### OCP (Open/Closed Principle)

Open for extension, closed for modification. **NEVER hardcode data.**

**Bad Example** - 寫死變數清單：
```python
def get_template_variables(self):
    return ['序號', '曲名', '樂器']  # 新增變數需要改程式碼
```

**Good Example** - 資料放在常數檔：
```python
# core/constants.py
TEMPLATE_VARIABLES = ['序號', '曲名', '樂器', '樂章編號', '樂章名稱']

# core/template_engine.py
from core.constants import TEMPLATE_VARIABLES
```

---

### DIP (Dependency Inversion Principle)

Depend on abstractions, not concrete implementations.

**Bad Example** - UI 直接呼叫檔案系統：
```python
class TemplateEditor:
    def apply(self):
        os.rename(old_path, new_path)  # 與 OS 緊密耦合
```

**Good Example** - 透過 service 層：
```python
class TemplateEditor:
    def __init__(self, file_service: FileService):
        self.file_service = file_service

    def apply(self):
        self.file_service.rename(old_path, new_path)
```

---

### Pre-Implementation Checklist

Before writing ANY code, answer these questions:

1. **Where does this logic belong?**
   - [ ] Is this UI interaction? → `ui/` only
   - [ ] Is this template processing? → `core/` only
   - [ ] Is this file I/O or PDF processing? → `services/` only

2. **Does this already exist?**
   - [ ] Search `core/` for existing utilities
   - [ ] Search `services/` for existing methods
   - [ ] Check `core/constants.py` for constants

3. **Am I duplicating logic?**
   - [ ] Will UI compute something that services should provide?
   - [ ] Am I defining constants in multiple places?

---

### Post-Implementation Review Checklist

After completing code changes, verify:

1. [ ] **No hardcoded data** - All data in constants files (OCP)
2. [ ] **Single responsibility** - Each class/function does one thing (SRP)
3. [ ] **No duplicate logic** - Search for similar code (DRY)
4. [ ] **UI 層不含業務邏輯** - UI 只負責顯示與使用者互動
5. [ ] **Constants in one place** - 常數統一定義於 `core/constants.py`

---

## Language

- 一律使用中華民國慣用的繁體中文
- 禁止使用 emoji
- 中文內容使用全型標點符號

## Workflow

- 修改時自動偵測相關檔案是否需要同時修改
- 不生成額外文檔，除非明確要求
- After code changes, automatically run `git add` and use `/commit-format` to do git commit in 繁體中文

## Agent skills

### Issue tracker

issue 與 spec 都在 GitHub Issues（`Suikann/ling-ling-suite`），一律用 `gh` CLI 操作。見 `docs/agents/issue-tracker.md`。

### Triage labels

五個標準 triage 角色對映到中文標籤（`待分類`、`待補資訊`、`待派工`、`待人工`、`不處理`）。見 `docs/agents/triage-labels.md`。

### Domain docs

單一 context：根目錄 `GLOSSARY.md` 加 `docs/adr/`。見 `docs/agents/domain.md`。

---

## Project Overview

Ling Ling Suite（泠靈小工具）是一套專為樂團譜務設計的 Python + PySide6 桌面應用程式，用於解決分譜 PDF 檔案的批次重新命名問題。

核心功能：
- **雙層級模板系統**：通用大模板（Master Template）設定全域命名規則，曲目小模板（Track-Specific Template）基於大模板複製後編輯、可針對個別群組覆寫
- **群組管理**：一個群組 = 一首曲目的一個樂章，專案內可包含任意數量的群組
- **樂器表與排序對應**：使用者輸入樂器表（順序 = 總譜順序），群組內的檔案透過排序與樂器一一對應，位置決定流水號
- **曲名與總譜自動偵測**：檔案進入群組時，從分譜檔名的共同部分提取曲名、依檔名關鍵字找出總譜，各猜一次
- **子資料夾輸出**：可選擇將各群組的檔案分別放入以模板變數命名的子資料夾
- **視覺化編輯介面**：提供即時預覽（含衝突警告），確認後一鍵批次重新命名
- **復原機制**：每次操作自動記錄原始與新檔名的對照，支援復原上次操作
- **專案持久化**：可儲存/載入專案檔，保留模板設定、群組配置與檔案對應

## Commands

```bash
python src/main.py              # 啟動程式
python -m pytest tests/ -v      # 執行測試
```

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
    widgets.py                   - 共用增強元件與 UI 輔助函式（如找不到檔案的提示）
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

### Layer Responsibilities

| Layer | Allowed | FORBIDDEN |
|-------|---------|-----------|
| **ui/** | 使用者互動、顯示資料、呼叫 services | 業務邏輯、直接檔案操作 |
| **core/** | 模板解析、常數定義、資料模型 | UI 操作、直接檔案操作 |
| **services/** | 檔案操作、匯入、重新命名、專案管理 | UI 操作 |

### Data Flow

```
使用者操作 UI
    ↓ (匯入檔案/資料夾、設定模板、排序檔案)
ui/ 層
    ↓ (呼叫 services)
services/ 層
    ↓ (使用 core/ 的模板引擎解析模板)
core/ 模板引擎
    ↓ (產生新檔名)
services/ 層
    ↓ (執行檔案重新命名、建立子資料夾)
檔案系統
```

---

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

---

## Conflict Handling

重新命名前的檢查只做一次：`MoveHistory.check_rename(project, group_ids)` 回傳判定（`RenameVerdict`）——實際會執行的計畫（`plan`），加上以來源為鍵、分類好的問題（`problems`：來源 → `RenameProblem`）。預覽只顯示這份判定；按下執行時 `MoveHistory.rename(verdict)` 執行的就是它的計畫。預檢與執行前驗證共用搬移引擎的同一套規則（`MoveService.find_problems`），預覽與執行不可能判得不一樣。路徑是否相同一律以 `core/paths.py` 判定（絕對路徑、不分大小寫）。
- 來源已不在（`MISSING_SOURCE`）：從計畫丟掉，預覽列出，不阻擋
- 目標重複：自動加後綴（`SUFFIXED`，只加一次），預覽警告並把執行鈕改為「繼續（自動加後綴）」；加後綴後的計畫再檢查一次，仍重複（例如兩個檔要改成 Same.pdf、第三個要改成 Same (1).pdf）就阻擋並列出撞名的檔
- 多於聲部數的分譜不改名（`EXTRA_FILE`）、命名格式與子資料夾模板裡的未知變數（`unknown_variables`）從名稱拿掉：預覽列出，不阻擋。不阻擋的種類只有這三種，其餘一律阻擋、停用執行，預覽顯示原因。每種問題是否阻擋、預覽的訊息、執行前驗證拒絕時的訊息與檢查順序都只記在 `core/constants.py` 的 `RENAME_PROBLEM_RULES`，預檢、預覽與搬移引擎都查它：
  - **目標路徑邊界**（`OUTSIDE_OUTPUT`）：每個目標都必須在該項的輸出位置之內（`RenameEntry.output_location()`，沒指定輸出位置時為來源檔所在的資料夾；以 `core.paths.is_inside` 判定），只要有一個不在就整批阻擋。這是命名模組擋 `.`、`..`（`unsafe_folder`）之外的第二道防線，預檢與執行前驗證共用
  - **子資料夾模板的逐檔變數**（`folder_variables`）：用了 `{序號}`、`{樂器}`（含英文名稱）時阻擋並指出要拿掉的變數（見「Subfolder Output」）
  - 同一來源檔案被多個群組引用（無法自動修正，需使用者調整群組）、產生的檔名去掉副檔名後為空、目標位置已有不屬於本次計畫的檔案、讓位用暫名已被佔用；對調與連鎖的目標是計畫內來源，不算佔用
- `check_rename` 由 `rename_service.generate_rename_plan` 產生計畫（`RenamePlan`）後交給 `check_plan`，給定的計畫也能直接用 `check_plan` 判定；`rename` 收到有阻擋的判定時不搬動任何檔案

執行由搬移歷程（`services/move_history.py` 的 `MoveHistory`）負責：重新命名 `rename`、復原 `undo`、重做 `redo`、中斷還原（「還原」`recover`、「保留結果」`keep_result`）四個動作都回傳 `MoveResult`——`changes`（照計畫搬好的檔，動作前位置 → 目前位置）、`skipped`（已不在預期位置而略過的檔）、`residual`（搬不回原位的檔）、`operation`（操作種類）、`error`（失敗原因）、`record_error`（檔案已搬好但紀錄寫不進去的原因），復原分割時另有 `split`（該次分割的 `SplitRecord`）。主視窗只詢問與顯示，把 `changes + residual` 交給 `Project.replace_paths`、`split` 交給 `Project.revert_split` 套用。
搬移本身交給內部的兩階段引擎（`services/move_service.py` 的 `MoveService`）：先以 `find_problems` 驗證目標在邊界內（只有重新命名有邊界，復原、重做沒有）、新檔名不為空、來源存在、來源未重複、目標未重複、目標未被計畫外的檔案佔用、讓位用的暫名未被佔用，任一不符即整批取消（結果帶 `error`、沒有路徑變動）。
「佔用」指磁碟上存在、且不是本次計畫任何一筆的來源，所以對調（A→B、B→A）與連鎖（A→B、B→C）可以執行：
來源同時是其他項目目標的檔案，第一階段先改成同資料夾的 `<原檔名>.moving` 暫名（`RENAME_STAGING_SUFFIX`）讓出位置，第二階段全部就位；復原紀錄只記原始位置到最終位置，暫名不出現。
執行中途失敗則依搬移的反序回滾至原位；回滾也失敗的檔案（含停在暫名者）列在 `residual`，搬移歷程替它們寫一筆殘留紀錄（`residual` 為真，`operation_type` 為留下它的操作：重新命名、復原或重做，描述如「復原失敗後留下的 N 個檔案」）放上復原堆疊、不清空重做堆疊，UI 套用其路徑，不留下無紀錄的半完成狀態。殘留紀錄與（復原時的）工作區 meta 寫回都經引擎的 `on_rollback` 在刪除進行中紀錄之前完成。
檔案已搬好、只有紀錄寫不進去（例如磁碟已滿）時，結果仍帶 `changes`，專案路徑照樣更新到檔案的實際位置，`record_error` 只提示；進行中紀錄保留，下次會依操作種類提示「上次…已完成」，可選保留結果或還原。主視窗記住這批搬移已套用到哪個專案，之後在同一次執行中選「保留結果」時不再套用一次（對調與連鎖套用兩次會換回去）。
程式被中途關掉（當機、斷電、強制結束）也不留下無紀錄的狀態：引擎在搬第一個檔案前就把進行中紀錄原子寫入，內容是「目前仍生效的步驟清單」加「即將執行的下一步（`pending`）」，以及這批搬移的來歷（`BatchContext`）：操作種類（`operation`：重新命名、復原或重做）、所屬復原紀錄的 id（`record_id`）、該紀錄在搬移開始時的內容（`record`，含工作區 meta 快照；重新命名在搬移前就快照）與所屬的專案檔（`project_path`：`rename`、`undo`、`redo` 由主視窗傳入，專案尚未存檔為空字串），每完成一步就把該步加進清單、下一步記為 `pending` 重寫；回滾與還原每逆轉一步就從清單移除並存檔，所以紀錄隨時反映每個檔案的實際位置，開始逆轉時也取消 `complete`。整批搬完先標記 `complete`、由搬移歷程寫正式紀錄（重新命名寫復原紀錄並清空重做堆疊；復原寫回工作區 meta、轉入重做堆疊；重做轉回復原堆疊），寫完才刪除進行中紀錄，兩者之間沒有空窗；回滾、還原與保留結果也都在寫完紀錄與 meta 後才刪除，途中寫不進去就保留進行中紀錄、下次再處理。紀錄仍在時引擎以 `PendingMoveError` 拒絕執行新批次（否則會蓋掉唯一的紀錄），訊息請使用者先處理中斷提示。啟動時 `MainWindow.prompt_pending_recovery` 以 `MoveHistory.pending()` 查看紀錄是否仍在，提示依被中斷的操作種類說明（「上次重新命名／復原／重做未完成」）：未搬完的提示「上次…未完成（已搬移 N 個檔案）」，「還原」則 `MoveHistory.recover()` 依生效清單反序搬回（與回滾共用 `_reverse_all`，對調、連鎖、停在暫名者都能還原，還原途中再被中斷也能接續），「稍後」則保留紀錄下次再問；已搬完（`complete`）但正式紀錄未確認寫入的，多一個「保留結果」（`MoveHistory.keep_result()`）。兩個選擇都讓堆疊與檔案位置一致（已寫過的紀錄不重寫）：「還原」讓堆疊回到這批搬移開始前——被中斷的復原其紀錄留在（或放回）復原堆疊、不在重做堆疊，重做反之，重新命名的紀錄移除——並用 `record` 的快照寫回工作區 meta；「保留結果」補做整批搬完後該寫的紀錄——復原寫回 meta 並轉入重做堆疊、重做轉回復原堆疊；重新命名照提示所說不補寫復原紀錄（無法復原），只清空重做堆疊——結果的 `changes`（原位置 → 目前位置）交給這批搬移所屬的專案套用、標記未存檔：所屬專案不是目前專案時（例如啟動時還沒開啟任何專案）照一般開啟流程開啟它（目前專案有未存檔的修改先詢問是否儲存，選取消則什麼都不做；`ProjectAccess.open` 開啟後先套用路徑變動，不把剛搬走的檔列成找不到）；所屬專案當時尚未存檔、目前專案也沒有引用這批檔案時，提示路徑無法更新；沒有記所屬專案的舊版紀錄套用到目前專案。沒有操作種類的舊版進行中紀錄視為重新命名（也沒有 `record`，不動堆疊、不寫 meta）；無法讀取（含 `record` 損毀）時提示一次並捨棄。重新命名、復原、重做前也會再問一次。`load_pending` 對照磁碟判定 `pending` 那一步（來源已不在、目標已出現＝已完成），補上「搬完、來不及記就當機」的那一步；比對用 `file_exists_exact`（目錄列表的實際名稱），只改大小寫的那一步在不分大小寫的檔案系統上才判得出。還原時檔案已不在紀錄位置者略過並列出；搬不回去者留在原地，搬移歷程為其寫殘留紀錄（標示被中斷的操作種類），UI 套用其路徑。

復原與重做堆疊後進先出：每筆紀錄有唯一 `id`（`UndoRecord.id`），檔名為「推入序號_id.json」，序號越大越靠近頂端，在兩個堆疊間轉移時 id 不變；舊版以時間戳命名、沒有 id 的紀錄（`undo_YYYYMMDD_HHMMSS.json`）照常載入並能執行，排在新紀錄之下，以檔名當 id。新的操作（重新命名、分割、旋轉）放上復原堆疊並清空重做堆疊。復原把整批搬移類紀錄（`MOVE_OPERATIONS`）的檔案搬回原位，已不在新位置的檔略過並列出，專案裡的路徑維持原樣，只有實際搬回的對照轉入重做堆疊；重做同理，已不在原位的檔略過。分割與旋轉的紀錄也由搬移歷程組裝（`record_split`、`record_rotate`；旋轉覆蓋原檔前以 `create_backup` 備份），主視窗與對話框不自己組紀錄：復原時分割的分譜與旋轉另存出的檔移到資源回收桶，覆蓋原檔的以備份蓋回，之後移除紀錄、不進重做堆疊（無法重做）。復原分割同時退回分割前的專案：結果的 `split` 交給 `Project.revert_split`——移除新分譜的引用；分割時另建的群組移除（之後才放進去的檔移回未分組）；接手的既有群組被分割改掉的樂器表與曲名改回（分割沒改的欄位不動，分割後的修改保留）；合併譜已不在專案裡就放回原位置（同一個群組、同樣是分譜或總譜，分譜依其餘分譜間的原順序；原群組已刪除就放回未分組；原本在未分組的本來就沒移動）。復原後標記未存檔、分頁立即重建。重新分割時被取代的舊分譜不找回，主視窗列出它們移到資源回收桶時所在的位置（`replaced_files`）並提示仍在資源回收桶（ADR-0001 的取代進資源回收桶不變）。沒有合併譜與安置資訊的舊版分割紀錄照常載入，復原時刪除新分譜並移除其引用。操作種類定義在 `core/constants.py` 的 `OperationKind`。新版寫出的紀錄不保證舊版能正確處理（不支援降版）。

---

## Workspace

分割產生的分譜預設先進工作區（`WORKSPACE_DIR`），重新命名時才搬到輸出位置；也可以指定資料夾。使用者匯入的檔案永遠不進工作區（見 `docs/adr/0001`）。

分割由分割模組（`services/split_service.py` 的 `SplitService`）負責，分兩段；分割對話框只標記分割點、命名、詢問與顯示：

- `check(SplitRequest)` 不寫任何檔，回傳 `SplitCheck`：要產生的分譜（`plan`，頁面全被刪掉的分段略過）；擋下的問題（`duplicates`：清理非法字元、空名回退為 `Part`、不分大小寫後檔名相同的分段，以分段編號列出；`source_conflicts`：輸出就是合併譜本身）；需確認的事（工作區模式為上次的分譜 `previous_outputs` 與其所屬的另一個專案 `owner`，指定資料夾模式為已有的同名檔案 `overwritten`）。`blocked` 時對話框只列出原因
- `execute(check)` 執行確認過的檢查結果：新分譜先全部寫進輸出資料夾裡的暫用子資料夾 `<代號>.splitting`；寫好後才把位置會被新分譜佔用的被取代檔（`check.replaced` 之中）挪進 `<代號>.replaced`（保留原檔名）、新分譜就位、（工作區模式）以 `WorkspaceService.write_meta` 改寫所屬專案；最後把被取代的檔移到資源回收桶：挪開的從 `<代號>.replaced` 移，其餘從原處移（從資源回收桶還原時回到原處）。任一步失敗就撤回已做的步驟後拋出：被取代的檔原封不動、所屬專案不變、不留新分譜，專案與復原紀錄也不動。移不進資源回收桶的檔留在當時的位置，不影響已完成的分割
- 執行回傳 `SplitResult`（新分譜、聲部名稱、被取代的路徑 `replaced`、它們移到資源回收桶時所在的位置 `trashed`、新建的目錄）。主視窗以 `Project.apply_split` 套用：移除被取代的引用；合併譜不論是分譜還是總譜都移出群組（未分組裡的留在未分組），重新命名才不會搬動它；接手新分譜的是引用被取代檔案的群組，其次是合併譜所在的群組，都沒有才另建以合併譜檔名命名的群組（重新分割不多出群組、不留空群組）。`apply_split` 回傳安置方式 `SplitPlacement`（接手群組的 id、是否另建、被改掉的樂器表與曲名的原值、合併譜原本的位置 `FilePosition`：群組 id、是否為總譜、在分譜清單中的位置，未分組時群組 id 為空），復原分割靠它退回。接著切到接手的群組，把 `result.record(placement)`（`SplitRecord`，含被取代的檔移到資源回收桶時的位置與安置方式）交給 `MoveHistory.record_split`
- 分段自動帶入的聲部名稱讀合併譜所在群組的樂器表；專案層級的舊樂器表只在載入舊專案時遷移
- 指定資料夾模式只把同名的檔當成被取代：自訂資料夾可能放著使用者自己的檔與其他合併譜的分譜，專案檔與資料夾都沒有記下哪些分譜出自哪份合併譜，無法可靠認出上次的輸出

工作區的規則：

- 每個來源合併譜對應一個子資料夾，名稱為來源路徑比對鍵（`path_key`：絕對路徑、不分大小寫）SHA-1 的前 8 碼（`SplitService.output_folder`）；還沒有這個子資料夾、但有子資料夾的 `meta.source_path` 指向同一份來源時（升級前以保留大小寫的路徑雜湊建立，Linux／macOS 上名稱不同）沿用後者（`WorkspaceService.find_folder`）。資料夾內 `meta.json` 記錄 `source_path`、`source_name`、`project_path`、`created_at`（路徑皆為絕對路徑，`project_path` 未存檔時為空字串）
- 子資料夾以來源合併譜為鍵、跨專案共用：另一專案重新分割同一份合併譜會取代前者尚未重新命名的分譜，確認訊息點名所屬專案（`SplitCheck.owner`），專案檔已不存在時加註「（找不到）」；分割成功後所屬專案一律改成目前專案，未存檔時記為空（取代過一次後再分割就是自己的嘗試，不再點名別人）
- 專案開啟與存檔時都由專案存取更新引用到的子資料夾的 `meta.project_path`（`update_project_path` 回傳寫入失敗的子資料夾，UI 只在狀態列提示、不阻止開啟或存檔；見 Project Access）
- 搬空的子資料夾保留 `meta.json`（復原重新命名時分譜會搬回來，需要它辨識來源）；「清理工作區」掃描時才移除既未被目前專案、也未被任何已知專案引用的空資料夾（`meta.json` 是程式自產的中繼資料，直接刪除不走資源回收桶；連同原子寫入殘留 `meta.json.tmp` 一起清，由 `FileService.remove_atomic_residue` 認得暫名）
- 復原紀錄寫入時對來源位於工作區的項目快照其子資料夾的 meta（`UndoRecord.workspace_meta`，同一資料夾的不同寫法只記一份；因此 `MoveHistory` 注入 `WorkspaceService`）；復原後子資料夾若已沒有可讀的 meta 就用快照寫回（`restore_meta`；回滾失敗卡在工作區的檔案、中斷還原搬回工作區的分譜、保留結果的復原也寫回，都在刪除進行中紀錄之前），寫回失敗不影響檔案復原；重做前先以子資料夾目前的 meta 更新快照；舊紀錄沒有此欄照常載入
- 「工具 → 開啟工作區資料夾」以系統檔案總管開啟 `WORKSPACE_DIR`
- 「工具 → 清理工作區」列出各子資料夾的引用狀態（使用中／屬於其他專案／屬於無法讀取的專案／未被引用／來源不明），只有「未被引用」預設勾選，刪除走資源回收桶。「已知專案」= 最近專案清單 + 各 `meta.project_path` 指向的專案檔（`ProjectAccess.known_projects`）；開啟對話框時會順手把最近清單中已不存在的專案檔移除
- 引用關係涵蓋群組內分譜、總譜與未分組檔案（`Project.file_refs()`）
- 分割輸出路徑等於來源合併譜時擋下（分割模組的檢查，`pdf_service.extract_pages` 再擋一層）
- `rename_file` 跨磁碟時複製到 `.part` 再就位，失敗不留半成品

---

## Data Storage

| 項目 | 位置 |
|------|------|
| 使用者資料目錄 | Windows：`%APPDATA%/LingLingSuite/`；Linux／macOS：`$XDG_CONFIG_HOME/LingLingSuite/`（預設 `~/.config/LingLingSuite/`），由 `core/constants.py` 的 `APPDATA_DIR` 決定 |
| 偏好設定 | `<使用者資料目錄>/preferences.json`（`PREFERENCES_FILE`；語言、外觀、最近專案清單、譜庫設定，預設值 `DEFAULT_PREFERENCES`） |
| 復原／重做紀錄 | `<使用者資料目錄>/undo/`、`redo/`（`UNDO_DIR`、`REDO_DIR`；每筆紀錄一個 JSON 檔，檔名為推入序號＋紀錄 id；舊版以時間戳命名的照常讀取） |
| 批次搬移進行中紀錄 | `<使用者資料目錄>/pending_move.json`（`MOVE_JOURNAL_FILE`；只在重新命名／復原／重做進行中存在，記下操作種類、所屬紀錄的 id 與內容、所屬專案檔；啟動時仍在即為上次中斷） |
| 單一實例鎖 | `<使用者資料目錄>/instance.lock`（`INSTANCE_LOCK_FILE`；執行期間由作業系統鎖住，結束或當機時自動解除，檔案留著不刪。啟動時先取得，取不到就提示「已在執行中」後結束，不開主視窗、不檢查進行中紀錄） |
| 工作區 | `<使用者資料目錄>/workspace/<hash8>/`（分割輸出與 `meta.json`） |
| PDF 旋轉備份 | `<使用者資料目錄>/backups/`（`BACKUP_DIR`） |
| 專案檔 | 使用者自選位置（儲存/載入對話框） |

復原、重做、進行中紀錄與旋轉備份的位置在建構 `MoveHistory` 時注入（預設為上表的常數），測試傳入暫存目錄、不替換全域常數。

---

## User Workflow

1. 開啟程式，透過檔案總管匯入 PDF 檔案或資料夾
2. 建立或確認群組，將檔案分配至各群組
3. 切到群組分頁，輸入該群組的樂器表（打字，順序 = 總譜順序；目前分頁不是群組時樂器表停用）
4. 每個群組內：排列檔案順序使其與樂器一一對應
5. 填寫各群組的 `{曲名}`、`{樂章編號}`、`{樂章名稱}`（檔案進入群組時已自動偵測一次曲名與總譜；想重猜曲名時按「自動偵測」）
6. 設定大模板；需要時為特定群組建立小模板
7. （選用）啟用子資料夾輸出，設定資料夾名稱模板
8. 預覽結果，確認無衝突
9. 執行重新命名

<!-- lingling:git-workflow trunk=develop guarded=main -->

## Git workflow

Merge a PR only when the user says to, and only with `lingling-merge <n>`, which ships in the `lingling` plugin's `bin/` and is on `PATH` while the plugin is enabled. When it refuses, its second line is the next step: do that, then run it again. When that line hands the merge to the user, stop and tell them; don't merge any other way.

Before running it, if the user has not yet explained the PR in their own words in this session (what changed, why, and how it was verified), ask them to, once. If they say to skip it, merge. When they explain, check what they said against the diff and name anything wrong or missing, then let them choose: explain again, ask for a re-explanation (`/wait-what`, `/teach`, `/show-me`), or merge. Never refuse to merge over it.

After merging a feature branch into `develop`, delete that branch as part of the same workflow — both local (`git branch -d`) and remote. GitHub deletes the remote branch on merge where `delete_branch_on_merge` is on, so run `git push origin --delete <branch>` only when `git ls-remote --exit-code --heads origin <branch>` still finds it, then `git fetch --prune origin` to drop the stale `origin/<branch>`. Don't leave merged branches around and don't ask first; cleanup is the final step of any commit → push → merge request.

After merging a ticket's PR, in the same workflow as the branch cleanup, run `/lingling:spec-closeout`: it finds the ticket's spec and tells the user when every ticket under that spec is closed. It never closes the spec; close it only when the user says so. A PR opened by `/implement-spec` is the exception: it closes the spec along with its tickets when it merges, and merging it is the user's call. A ticket with no parent needs nothing here.

Before any commit — including at the start of `/implement` — if the current branch is `develop`, first branch off the latest `develop` (`git fetch origin develop && git switch -c <name> origin/develop`, so the branch starts from `origin/develop`, not from a possibly stale local `develop`) and work there; never commit on `develop` directly. The agent picks the branch name, and the name must not contain digits. Upstream `implement` only says "commit to the current branch" and never opens a branch itself; this rule fills that gap.

No merge commit may land inside a branch, and `lingling-merge` refuses a branch that holds one. A branch catches up with `develop` only by rebasing onto it (`git fetch origin && git rebase origin/develop`, then `git push --force-with-lease`), resolving any conflict during the rebase; one branch takes another's commits only by fast-forward (`git merge --ff-only`).

<!-- /lingling:git-workflow -->
