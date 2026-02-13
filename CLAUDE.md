# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Role

你是一個專業的程式設計師。批判性地解讀用戶的發言，確保能找到問題的核心，而非單純支持用戶的心情。

## Code Style

- 注重可維護性、可讀性與模組化
- 遵循 SOLID 原則（SRP、OCP、LSP、ISP、DIP）
- 遵循 DRY 原則：避免重複邏輯，將共用邏輯抽取為可重用的函數或模組
- Use CRLF line endings
- No consecutive blank lines

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

Ling Ling Suite（泠靈小工具）是一套專為樂團譜務設計的 Python + tkinter 桌面應用程式，用於解決分譜 PDF 檔案的批次重新命名問題。

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
python src/main.py
```

## Architecture

```
src/
  main.py                - 應用程式進入點
  ui/                    - tkinter UI 元件
    main_window.py       - 主視窗
    template_editor.py   - 模板編輯面板
    file_list.py         - 檔案清單（支援拖拉排序）
    instrument_list.py   - 樂器表編輯器
    group_panel.py       - 群組管理面板
    preview_dialog.py    - 預覽與衝突警告對話框
  core/                  - 模板引擎、資料模型、常數定義
    constants.py         - 模板變數定義、預設值等常數
    template_engine.py   - 模板解析與變數替換邏輯
    models.py            - 資料模型（Project、Group、Template、FileInfo）
  services/              - 檔案操作、重新命名服務
    file_service.py      - 檔案系統操作（讀取、重新命名、建立資料夾）
    import_service.py    - 檔案/資料夾匯入與自動分組
    rename_service.py    - 批次重新命名邏輯編排
    project_service.py   - 專案檔儲存/載入
    undo_service.py      - 復原操作管理
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

---

## Data Storage

| 項目 | 位置 |
|------|------|
| 復原紀錄 | `%APPDATA%/LingLingSuite/undo/`（每次操作一個 JSON 檔） |
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
