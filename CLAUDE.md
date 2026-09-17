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

---

## Project Overview

Ling Ling Suite（泠靈小工具）是一套專為樂團譜務設計的 Python + PySide6 桌面應用程式，用於解決分譜 PDF 檔案的批次重新命名問題。

核心功能：
- **雙層級模板系統**：通用大模板（Master Template）設定全域命名規則，曲目小模板（Track-Specific Template）基於大模板複製後編輯、可針對個別群組覆寫
- **群組管理**：一個群組 = 一首曲目的一個樂章，專案內可包含任意數量的群組
- **樂器表與排序對應**：使用者輸入樂器表（順序 = 總譜順序），群組內的檔案透過排序與樂器一一對應，位置決定流水號
- **曲名自動偵測**：從群組內檔名的共同部分自動提取樂曲名稱作為建議值
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
  main.py                        - 應用程式進入點（QApplication、深色主題）
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
    widgets.py                   - 共用增強元件
  core/                          - 模板引擎、資料模型、常數定義
    constants.py                 - 模板變數定義、預設值、應用程式路徑等常數
    catalog_constants.py         - 譜庫相關常數
    template_engine.py           - 模板解析與變數替換邏輯
    models.py                    - 資料模型（Project、Group、Template、FileInfo）
    catalog_models.py            - 譜庫資料模型
    locale.py                    - 國際化系統（zh_TW／en，模板變數雙語轉換）
  services/                      - 檔案操作、PDF 處理、雲端整合
    file_service.py              - 檔案系統操作（讀取、重新命名、建立資料夾）
    import_service.py            - 檔案/資料夾匯入與自動分組
    rename_service.py            - 批次重新命名邏輯編排
    pdf_service.py               - PDF 分割、旋轉、縮圖產生
    project_service.py           - 專案檔儲存/載入
    undo_service.py              - 復原／重做操作管理
    preferences_service.py       - 使用者偏好（語言、外觀）持久化
    workspace_service.py         - 工作區（分割輸出的暫存地）管理：子資料夾、meta.json、掃描、清理
    google_auth_service.py       - Google API OAuth 認證
    sheets_service.py            - Google Sheets 譜庫存取
    drive_service.py             - Google Drive 檔案存取
    drive_rename_service.py      - 透過 Drive API 重新命名譜庫檔案
tests/                           - pytest 測試（template_engine、rename、import、project、undo、workspace）
CONTEXT.md                       - 領域詞彙表（總譜、分譜、合併譜、群組、工作區…）
docs/adr/                        - 架構決策紀錄
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
- 從樂器表中勾選該群組用到的樂器子集
- 群組內的檔案數量必須等於勾選的樂器數量
- 使用者排列檔案順序，位置與勾選的樂器一一對應
- 各群組獨立設定 `{曲名}`、`{樂章編號}`、`{樂章名稱}`

### 模板變數

| 變數 | 層級 | 來源 | 範例 |
|------|------|------|------|
| `{序號}` | 逐檔不同 | 樂器在樂器表中的位置，自動產生，零填充 | `01`, `02` |
| `{樂器}` | 逐檔不同 | 樂器表，依排序對應 | `Flute`, `Violin I` |
| `{曲名}` | 群組層級 | 從檔名共同部分自動偵測，使用者可覆寫 | `Beethoven Sym.5` |
| `{樂章編號}` | 群組層級 | 使用者輸入 | `1`, `2`, `3` |
| `{樂章名稱}` | 群組層級 | 使用者輸入 | `Allegro`, `Adagio` |
| `{作曲家}` | 群組層級 | 使用者輸入 | `Beethoven`, `Mozart` |
| `{曲種}` | 群組層級 | 使用者輸入 | `交響曲`, `協奏曲` |

---

## File Import

| 匯入方式 | 行為 |
|----------|------|
| 選擇多個 PDF 檔案 | 全部匯入至未分組清單，使用者自行建立群組 |
| 選擇資料夾（無子資料夾） | 匯入該資料夾內所有 PDF，視為一個群組 |
| 選擇資料夾（有子資料夾） | 每個含有 PDF 的子資料夾自動建立一個群組，子資料夾名稱作為 `{曲名}` 建議值 |
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

預覽階段檢查所有產生的新檔名：
- 若有重複，標記警告並顯示衝突的檔案
- 使用者可選擇取消修改，或繼續執行（自動加後綴區分）
- 若同一來源檔案被多個群組引用，標記警告並停用執行（無法自動修正，需使用者調整群組）

執行階段（`RenameService.execute_rename`）先驗證來源存在、來源未重複、新檔名未重複、目標未被佔用，任一不符即整批取消；
執行中途失敗則將已搬移的檔案回滾至原位；回滾也失敗的檔案以 `RenameRollbackError` 回報，UI 為其寫入復原紀錄並更新專案路徑，不留下無紀錄的半完成狀態。

PDF 分割預設輸出到工作區；重新分割同一份來源時，確認後先清空該來源上次的輸出。
指定資料夾模式下才做同名檔案覆蓋確認。

---

## Workspace

分割產生的分譜先進工作區（`WORKSPACE_DIR`），重新命名時才搬到輸出位置；使用者匯入的檔案永遠不進工作區（見 `docs/adr/0001`）。

- 每個來源合併譜對應一個子資料夾，名稱為來源絕對路徑 SHA-1 的前 8 碼；資料夾內 `meta.json` 記錄 `source_path`、`source_name`、`project_path`、`created_at`
- 專案存檔時更新引用到的子資料夾的 `meta.project_path`
- 搬空的子資料夾保留 `meta.json`（復原重新命名時分譜會搬回來，需要它辨識來源）；「清理工作區」掃描時才移除既未被目前專案、也未被任何已知專案引用的空資料夾（`meta.json` 是程式自產的中繼資料，直接刪除不走資源回收桶）
- 「工具 → 開啟工作區資料夾」以系統檔案總管開啟 `WORKSPACE_DIR`
- 「工具 → 清理工作區」列出各子資料夾的引用狀態（使用中／屬於其他專案／屬於無法讀取的專案／未被引用／來源不明），只有「未被引用」預設勾選，刪除走資源回收桶。「已知專案」= 最近專案清單 + 各 `meta.project_path` 指向的專案檔；開啟對話框時會順手把最近清單中已不存在的專案檔移除
- 引用關係涵蓋群組內分譜、總譜與未分組檔案（`Project.all_file_paths()`）
- 分割輸出路徑等於來源合併譜時拒絕執行（`pdf_service.extract_pages` 與分割對話框各擋一層）；重新分割同一來源時，所有指向舊輸出的群組／未分組項目一併移除
- `rename_file` 跨磁碟時複製到 `.part` 再就位，失敗不留半成品

---

## Data Storage

| 項目 | 位置 |
|------|------|
| 使用者資料目錄 | Windows：`%APPDATA%/LingLingSuite/`；Linux／macOS：`$XDG_CONFIG_HOME/LingLingSuite/`（預設 `~/.config/LingLingSuite/`），由 `core/constants.py` 的 `APPDATA_DIR` 決定 |
| 偏好設定 | `<使用者資料目錄>/preferences.json` |
| 復原／重做紀錄 | `<使用者資料目錄>/undo/`、`redo/`（每次操作一個 JSON 檔） |
| 工作區 | `<使用者資料目錄>/workspace/<hash8>/`（分割輸出與 `meta.json`） |
| PDF 旋轉備份 | `<使用者資料目錄>/backups/` |
| 專案檔 | 使用者自選位置（儲存/載入對話框） |

---

## User Workflow

1. 開啟程式，透過檔案總管匯入 PDF 檔案或資料夾
2. 輸入樂器表（打字，順序 = 總譜順序）
3. 建立或確認群組，將檔案分配至各群組
4. 每個群組內：勾選該群組用到的樂器，排列檔案順序使其與樂器一一對應
5. 填寫各群組的 `{曲名}`、`{樂章編號}`、`{樂章名稱}`（曲名可由程式自動偵測建議）
6. 設定大模板；需要時為特定群組建立小模板
7. （選用）啟用子資料夾輸出，設定資料夾名稱模板
8. 預覽結果，確認無衝突
9. 執行重新命名
