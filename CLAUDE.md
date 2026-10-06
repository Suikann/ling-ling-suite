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
    split_dialog.py              - PDF 分割對話框（縮圖標記分割點）
    rotate_dialog.py             - PDF 旋轉對話框（分段設定角度）
    merge_dialog.py              - 連結樂章對話框
    page_preview.py              - 頁面預覽對話框
    catalog_window.py            - 譜庫瀏覽器視窗（Google Sheets／Drive）
    catalog_settings_dialog.py   - 譜庫設定對話框
    drive_rename_dialog.py       - Drive 重新命名對話框
    workspace_dialog.py          - 工作區清理對話框
    widgets.py                   - 共用增強元件與 UI 輔助函式（如找不到檔案的提示）
  core/                          - 模板引擎、資料模型、常數定義
    constants.py                 - 模板變數定義（含層級欄位）、分譜存放模式、預設值、應用程式路徑等常數
    filename.py                  - 檔名清理（非法字元換底線、空名回退），重新命名與分割共用
    naming.py                    - 命名格式套用：群組中的一格（總譜或第 N 份分譜）＋命名設定 → 檔名＋相對資料夾；純函式，本機重新命名、預覽與 Drive 重新命名共用
    paths.py                     - 路徑同一性（path_key、same_path：絕對路徑、不分大小寫），全程式比對路徑只用它
    catalog_constants.py         - 譜庫相關常數
    template_engine.py           - 曲名／總譜／樂器偵測、模板變數雙語轉換
    models.py                    - 資料模型（Project、Group、Template、FileInfo）；Project 是專案編輯模組：意圖層級的編輯操作、「已變更」通知、未存檔快照、專案檔內容（to_data／from_data，含舊格式遷移）；Project.file_refs() 是「總譜＋分譜＋未分組」的唯一走訪，依路徑取代／移除引用也在這裡
    catalog_models.py            - 譜庫資料模型
    locale.py                    - 國際化系統（zh_TW／en 介面字串）
  services/                      - 檔案操作、PDF 處理、雲端整合
    file_service.py              - 檔案系統操作（讀取、重新命名、建立資料夾、JSON 原子寫入）
    import_service.py            - 檔案/資料夾匯入與自動分組
    rename_service.py            - 批次重新命名邏輯編排（計畫生成：命名結果接在輸出位置之下、聲部組第一次用到時寫進專案；空檔名檢查）
    move_service.py              - 兩階段批次搬移引擎（驗證、對調／連鎖、回滾、進行中紀錄與中斷後還原）；重新命名、復原、重做共用
    move_journal.py              - 批次搬移進行中紀錄的讀寫（pending_move.json）
    instance_lock.py             - 單一實例鎖（作業系統檔案鎖，程式結束或當機時自動解除）
    pdf_service.py               - PDF 分割計畫組裝、頁面擷取、旋轉、縮圖產生
    project_service.py           - 專案檔的序列化讀寫（內容由 Project.to_data／from_data 決定）、與磁碟上的專案檔比對（matches_file）
    project_access.py            - 專案存取：開啟與存檔（專案檔、最近專案清單、工作區 meta 所屬專案）、已知專案清單、清理工作區的掃描；主視窗開啟、存檔、關閉前比對都只經過它
    undo_service.py              - 復原／重做操作管理
    preferences_service.py       - 使用者偏好（語言、外觀、最近專案清單、譜庫設定）的存放；檔案位置建構時注入，預設值在 constants
    workspace_service.py         - 工作區（分割輸出的暫存地）管理：子資料夾、meta.json、掃描、清理
    google_auth_service.py       - Google API OAuth 認證
    sheets_service.py            - Google Sheets 譜庫存取
    drive_service.py             - Google Drive 檔案存取
    drive_rename_service.py      - 透過 Drive API 重新命名譜庫檔案
tests/                           - pytest 測試（template_engine、naming、filename、rename、rename_plan、move、import、project、project_access、undo、workspace、pdf_service、locale、instance_lock；main_window、split_dialog、分割與旋轉的選檔清單以 offscreen Qt 測 UI 接線）；conftest 把使用者資料目錄導到暫存目錄；path_spellings 提供同一路徑的不同寫法；failing_writes 提供寫到指定檔案就失敗的檔案服務
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
- 檔名用語跟專案、不跟介面：總譜標籤在群組建立時寫入；聲部組資料夾模式下，還沒有聲部組的聲部在第一次產生計畫時依當時的介面語言寫進編制設定（`update_ensemble`），之後切換介面語言不影響

---

## Project Editing

專案（`core/models.py` 的 `Project`）自己管理修改、檔案引用與未存檔狀態；純記憶體，不匯入 Qt、不做檔案 I/O。

- UI 只表達意圖並立即寫入：群組欄位（`update_group`）、新增／匯入／刪除群組（`add_group`、`add_groups`、`delete_group`）、檔案搬進群組或移回未分組（`move_to_group`、`move_to_ungrouped`）、加入檔案（`add_files`）、指定與清除總譜（`set_score`、`clear_score`）、分譜排序（`reorder_files`）、樂器表（`set_instruments`）、連結樂章（`link_movements`）、大模板（`set_master_template`）、輸出設定（`set_output_settings`）、編制設定（`update_ensemble`）、切換語言改寫模板變數（`convert_template_language`）、套用搬移結果（`replace_paths`）、移除引用（`remove_paths`）、加入分割結果（`add_split_result`）。UI 不直接指定群組、檔案資訊或專案的欄位；群組分頁、樂器表、預覽對話框不呼叫主視窗的私有方法，影響其他分頁的修改以分頁的 `groups_changed` 訊號讓主視窗重建分頁
- 舊欄位鏡像（全選的 `selected_instruments`、`use_parts_subfolder`）由上述操作與載入流程維持，呼叫端不碰
- **未存檔＝快照比對**：`is_modified()` 比對目前內容（`to_data()`，即專案檔會存的內容，不含版本號）與上次存檔或開啟時的快照；改回原值就回到已存檔。舊格式的遷移（總譜標籤留空補上依目前介面語言的預設值、承接專案層級樂器表、勾選子集收成群組樂器表、舊分譜子資料夾旗標）在 `from_data` 內、拍快照之前完成，所以開啟舊專案不標記未存檔。開啟專案、切換分頁不改內容，不標記；切換介面語言改寫了命名格式就標記
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

啟用後，每個群組可設定資料夾名稱模板，支援群組層級變數（`{曲名}`、`{樂章編號}`、`{樂章名稱}`）。

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

預覽階段檢查所有產生的新檔名（路徑是否相同一律以 `core/paths.py` 判定：絕對路徑、不分大小寫）：
- 若有重複，標記警告並顯示衝突的檔案
- 使用者可選擇取消修改，或繼續執行（自動加後綴區分）
- 若同一來源檔案被多個群組引用，標記警告並停用執行（無法自動修正，需使用者調整群組）
- 若目標位置已有不屬於本次計畫的檔案（`find_occupied_targets`）、讓位用暫名已被佔用（`find_taken_staging_names`）、或產生的檔名去掉副檔名後為空（`find_empty_names`），同樣標記警告並停用執行；對調與連鎖的目標是計畫內來源，不算佔用。預覽的阻擋條件與執行前驗證一致，且以實際會執行的計畫（有衝突時為加後綴後）判定

執行階段（`RenameService.execute_rename`）先檢查新檔名不為空，再交給 `MoveService.execute`：驗證來源存在、來源未重複、目標未重複、目標未被計畫外的檔案佔用、讓位用的暫名未被佔用，任一不符即整批取消。
「佔用」指磁碟上存在、且不是本次計畫任何一筆的來源（`find_occupied_targets`），所以對調（A→B、B→A）與連鎖（A→B、B→C）可以執行：
來源同時是其他項目目標的檔案，第一階段先改成同資料夾的 `<原檔名>.moving` 暫名（`RENAME_STAGING_SUFFIX`）讓出位置，第二階段全部就位；復原紀錄只記原始位置到最終位置，暫名不出現。
執行中途失敗則依搬移的反序回滾至原位；回滾也失敗的檔案（含停在暫名者）以 `RenameRollbackError` 回報，UI 為其寫入復原紀錄並更新專案路徑，不留下無紀錄的半完成狀態。
程式被中途關掉（當機、斷電、強制結束）也不留下無紀錄的狀態：`MoveService.execute` 在搬第一個檔案前就把進行中紀錄（`MOVE_JOURNAL_FILE`）原子寫入，內容是「目前仍生效的步驟清單」加「即將執行的下一步（`pending`）」，每完成一步就把該步加進清單、下一步記為 `pending` 重寫；回滾與還原每逆轉一步就從清單移除並存檔，所以紀錄隨時反映每個檔案的實際位置。整批搬完先標記 `complete`、呼叫呼叫端傳入的 `on_complete` 寫正式紀錄（重新命名寫復原紀錄、復原轉入重做堆疊、重做轉回復原堆疊），寫完才刪除進行中紀錄，兩者之間沒有空窗；回滾結束也刪除。紀錄仍在時 `execute` 以 `PendingMoveError` 拒絕執行新批次（否則會蓋掉唯一的紀錄）。啟動時 `MainWindow.prompt_pending_recovery` 若發現紀錄仍在：未搬完的提示「上次重新命名未完成（已搬移 N 個檔案）」，「還原」則 `MoveService.recover` 依生效清單反序搬回（與回滾共用 `_reverse_all`，對調、連鎖、停在暫名者都能還原，還原途中再被中斷也能接續），「稍後」則保留紀錄下次再問；已搬完（`complete`）但正式紀錄未確認寫入的，提示「已完成但復原紀錄未寫入」，多一個「保留結果」（捨棄紀錄、無法復原）。重新命名、復原、重做前也會再問一次。`load_pending` 對照磁碟判定 `pending` 那一步（來源已不在、目標已出現＝已完成），補上「搬完、來不及記就當機」的那一步；比對用 `file_exists_exact`（目錄列表的實際名稱），只改大小寫的那一步在不分大小寫的檔案系統上才判得出。還原時檔案已不在紀錄位置者略過並列出；搬不回去者留在原地，UI 沿 residual 路徑寫入復原紀錄；紀錄損毀無法讀取時提示一次並捨棄。

PDF 分割預設輸出到工作區；重新分割同一份來源時，確認後先清空該來源上次的輸出。
指定資料夾模式下才做同名檔案覆蓋確認。

---

## Workspace

分割產生的分譜先進工作區（`WORKSPACE_DIR`），重新命名時才搬到輸出位置；使用者匯入的檔案永遠不進工作區（見 `docs/adr/0001`）。

- 每個來源合併譜對應一個子資料夾，名稱為來源路徑比對鍵（`path_key`：絕對路徑、不分大小寫）SHA-1 的前 8 碼；資料夾內 `meta.json` 記錄 `source_path`、`source_name`、`project_path`、`created_at`（路徑皆為絕對路徑，`project_path` 未存檔時為空字串）
- 子資料夾以來源合併譜為鍵、跨專案共用：另一專案重新分割同一份合併譜會取代前者尚未重新命名的分譜，確認訊息點名所屬專案（`WorkspaceService.other_owner`），專案檔已不存在時加註「（找不到）」；`prepare_folder` 一律把所屬專案改成目前專案，未存檔時記為空（取代過一次後再分割就是自己的嘗試，不再點名別人）
- 專案開啟與存檔時都由專案存取更新引用到的子資料夾的 `meta.project_path`（`update_project_path` 回傳寫入失敗的子資料夾，UI 只在狀態列提示、不阻止開啟或存檔；見 Project Access）
- 搬空的子資料夾保留 `meta.json`（復原重新命名時分譜會搬回來，需要它辨識來源）；「清理工作區」掃描時才移除既未被目前專案、也未被任何已知專案引用的空資料夾（`meta.json` 是程式自產的中繼資料，直接刪除不走資源回收桶；連同原子寫入殘留 `meta.json.tmp` 一起清，由 `FileService.remove_atomic_residue` 認得暫名）
- 復原紀錄寫入時對來源位於工作區的項目快照其子資料夾的 meta（`UndoRecord.workspace_meta`，因此 `UndoService` 注入 `WorkspaceService`）；復原後子資料夾若已沒有可讀的 meta 就用快照寫回（`restore_meta`；回滾失敗卡在工作區的檔案也寫回），寫回失敗不影響檔案復原；重做前先以子資料夾目前的 meta 更新快照；舊紀錄沒有此欄照常載入
- 「工具 → 開啟工作區資料夾」以系統檔案總管開啟 `WORKSPACE_DIR`
- 「工具 → 清理工作區」列出各子資料夾的引用狀態（使用中／屬於其他專案／屬於無法讀取的專案／未被引用／來源不明），只有「未被引用」預設勾選，刪除走資源回收桶。「已知專案」= 最近專案清單 + 各 `meta.project_path` 指向的專案檔（`ProjectAccess.known_projects`）；開啟對話框時會順手把最近清單中已不存在的專案檔移除
- 引用關係涵蓋群組內分譜、總譜與未分組檔案（`Project.file_refs()`）
- 分割輸出路徑等於來源合併譜時拒絕執行（`pdf_service.extract_pages` 與分割對話框各擋一層）；重新分割同一來源時，所有指向舊輸出的群組／未分組項目一併移除
- `rename_file` 跨磁碟時複製到 `.part` 再就位，失敗不留半成品

---

## Data Storage

| 項目 | 位置 |
|------|------|
| 使用者資料目錄 | Windows：`%APPDATA%/LingLingSuite/`；Linux／macOS：`$XDG_CONFIG_HOME/LingLingSuite/`（預設 `~/.config/LingLingSuite/`），由 `core/constants.py` 的 `APPDATA_DIR` 決定 |
| 偏好設定 | `<使用者資料目錄>/preferences.json`（`PREFERENCES_FILE`；語言、外觀、最近專案清單、譜庫設定，預設值 `DEFAULT_PREFERENCES`） |
| 復原／重做紀錄 | `<使用者資料目錄>/undo/`、`redo/`（每次操作一個 JSON 檔） |
| 批次搬移進行中紀錄 | `<使用者資料目錄>/pending_move.json`（只在重新命名／復原／重做進行中存在；啟動時仍在即為上次中斷） |
| 單一實例鎖 | `<使用者資料目錄>/instance.lock`（`INSTANCE_LOCK_FILE`；執行期間由作業系統鎖住，結束或當機時自動解除，檔案留著不刪。啟動時先取得，取不到就提示「已在執行中」後結束，不開主視窗、不檢查進行中紀錄） |
| 工作區 | `<使用者資料目錄>/workspace/<hash8>/`（分割輸出與 `meta.json`） |
| PDF 旋轉備份 | `<使用者資料目錄>/backups/` |
| 專案檔 | 使用者自選位置（儲存/載入對話框） |

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
